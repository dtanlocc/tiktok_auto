from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

from sqlmodel import Session


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.infrastructure.automation import playwright_adapter as browser_module  # noqa: E402
from app.infrastructure.automation.playwright_adapter import (  # noqa: E402
    InvisiblePlaywrightAdapter,
)
from app.infrastructure.database.connection import engine  # noqa: E402
from app.infrastructure.database.sqlite_repository import (  # noqa: E402
    SQLiteAccountRepository,
    SQLiteProxyRepository,
)
from app.infrastructure.email.email_service_factory import (  # noqa: E402
    create_email_service,
)
from app.use_cases.auth.login_strategies import (  # noqa: E402
    CookieThenCredentialLoginStrategy,
)
from app.use_cases.orchestration.task_dispatcher import _uuid_to_seed  # noqa: E402
from app.use_cases.upload.tiktok_upload_video import (  # noqa: E402
    TikTokUploadMediaUseCase,
)


def _load_cookies(database: Path, username: str) -> list[dict]:
    connection = sqlite3.connect(
        f"file:{database.resolve().as_posix()}?mode=ro", uri=True
    )
    try:
        row = connection.execute(
            "SELECT cookies_json FROM accounts WHERE username = ?", (username,)
        ).fetchone()
    finally:
        connection.close()
    if row is None:
        raise RuntimeError(f"Account not found: {username}")
    value = json.loads(row[0])
    if not isinstance(value, list):
        raise RuntimeError("Account cookies are invalid.")
    return value


async def _describe(locator) -> dict:
    return await locator.evaluate(
        """element => ({
            tag: element.tagName,
            id: element.id,
            className: String(element.className || '').slice(0, 300),
            text: String(element.innerText || element.textContent || '').trim().slice(0, 300),
            accept: element.getAttribute('accept'),
            type: element.getAttribute('type'),
            ariaLabel: element.getAttribute('aria-label'),
            dataE2e: element.getAttribute('data-e2e'),
            disabled: Boolean(element.disabled),
            connected: element.isConnected,
            rect: (() => { const r = element.getBoundingClientRect(); return {
                x: r.x, y: r.y, width: r.width, height: r.height
            }; })()
        })""",
        timeout=3_000,
    )


async def run(
    database: Path,
    username: str,
    video: Path,
    *,
    prepare_caption: bool = False,
    use_proxy: bool = False,
    proxy_id: str = "",
    studio_only: bool = False,
) -> None:
    cookies = _load_cookies(database, username)
    browser = InvisiblePlaywrightAdapter()
    session = Session(engine)
    repo = SQLiteAccountRepository(session)
    wanted = username.strip().lstrip("@").casefold()
    account = next(
        (
            candidate
            for candidate in repo.get_all()
            if str(candidate.username or "").strip().lstrip("@").casefold()
            == wanted
        ),
        None,
    )
    if account is None:
        session.close()
        raise RuntimeError(f"Account not found: {username}")
    proxy_config = None
    if use_proxy:
        proxy = SQLiteProxyRepository(session).get_by_id(
            proxy_id.strip() or account.proxy_id or ""
        )
        if proxy is None:
            session.close()
            raise RuntimeError(f"Account has no assigned proxy: {username}")
        proxy_config = {
            "server": proxy.connection_string,
            "username": proxy.username,
            "password": proxy.password,
        }
        print(
            f"PROXY_ROUTE={proxy.protocol}://{proxy.host}:{proxy.port}",
            flush=True,
        )

    async def step(message: str) -> None:
        print(message, flush=True)

    use_case = TikTokUploadMediaUseCase(
        account_repo=repo,
        browser_service=browser,
        login_strategy=CookieThenCredentialLoginStrategy(),
        email_service=create_email_service(),
        step_logger=step,
    )

    try:
        await browser.initialize(
            proxy_config=proxy_config,
            seed=_uuid_to_seed(account.id),
            force_visible=False,
        )
        await browser.inject_cookies(cookies)
        if not await browser.prepare_foryou_home(step_logger=step):
            raise RuntimeError("For You readiness check failed.")
        studio_ready = False
        for studio_attempt in range(2):
            await browser.navigate_to(
                "https://www.tiktok.com/tiktokstudio/upload?lang=en"
            )
            deadline = time.monotonic() + 135
            requires_login = False
            while time.monotonic() < deadline:
                await browser._handle_upload_interruptions(step_logger=step)
                current_url = str(getattr(browser._page, "url", "") or "")
                if "login" in current_url.casefold() and "redirect_url" in current_url.casefold():
                    requires_login = True
                    break
                if await browser._video_upload_entry_ready():
                    studio_ready = True
                    break
                await asyncio.sleep(0.5)
            if studio_ready:
                break
            if studio_only and requires_login:
                print("STUDIO_COOKIE_REUSE=False", flush=True)
                return
            if not requires_login or studio_attempt > 0:
                current_url = str(getattr(browser._page, "url", "") or "")
                raise RuntimeError(
                    f"TikTok upload input did not become ready (URL={current_url[:200]})."
                )
            account = await use_case._reauthenticate_for_studio(
                account.id,
                account,
            )

        if studio_only:
            print("STUDIO_COOKIE_REUSE=True", flush=True)
            return

        inputs = browser._page.locator('input[type="file"]')
        count = await inputs.count()
        print(f"INPUT_COUNT={count}", flush=True)
        target = inputs.first
        for index in range(count):
            candidate = inputs.nth(index)
            accept = ((await candidate.get_attribute("accept")) or "").lower()
            if "video" in accept or ".mp4" in accept or ".mov" in accept:
                target = candidate
                break
        trigger = await browser._resolve_native_upload_trigger(target, "video")
        print("TARGET=" + json.dumps(await _describe(target), ensure_ascii=False), flush=True)
        print("TRIGGER=" + json.dumps(await _describe(trigger), ensure_ascii=False), flush=True)
        attached = await browser._set_file_via_native_dialog(str(video.resolve()))
        print(f"ATTACHED={attached}", flush=True)
        print(f"ERROR={browser._last_native_upload_error}", flush=True)
        if not attached:
            raise RuntimeError(browser._last_native_upload_error or "native upload failed")
        if prepare_caption:
            editor = browser._page.locator(
                '.public-DraftEditor-content, [contenteditable="true"]'
            ).first
            post_button = browser._publish_button()
            reached_high = False
            reached_100 = False
            deadline = time.monotonic() + 420
            while time.monotonic() < deadline:
                state = await browser._read_video_upload_state()
                percent = state["percent"]
                reached_high = reached_high or bool(
                    percent is not None and percent >= 95
                )
                reached_100 = reached_100 or bool(
                    percent is not None and percent >= 100
                )
                ready = bool(
                    await editor.count()
                    and await editor.is_visible()
                    and await post_button.count()
                    and await post_button.is_visible()
                    and await post_button.is_enabled()
                    and browser_module._video_upload_finished(
                        state,
                        reached_high=reached_high,
                        reached_100=reached_100,
                    )
                )
                if ready:
                    break
                await asyncio.sleep(0.5)
            else:
                raise RuntimeError("Video did not reach 100% before caption diagnostic.")

            final_caption = await browser._prepare_video_caption(
                video.stem,
                video.name,
                step_logger=step,
            )
            snapshot = await editor.evaluate("""element => ({
              text: element.innerText || element.textContent || '',
              html: element.innerHTML.slice(0, 8000),
              hashtagNodes: Array.from(element.querySelectorAll('*')).map(node => {
                const text = String(node.innerText || node.textContent || '').trim();
                if (!text.startsWith('#')) return null;
                const style = getComputedStyle(node);
                return {
                  tag: node.tagName,
                  text,
                  role: node.getAttribute('role'),
                  href: node.getAttribute('href'),
                  className: String(node.className || '').slice(0, 240),
                  contentEditable: node.getAttribute('contenteditable'),
                  fontWeight: style.fontWeight,
                  color: style.color
                };
              }).filter(Boolean).slice(0, 30)
            })""")
            print(
                "CAPTION_DIAGNOSTIC "
                + json.dumps(
                    {"expected": video.stem, "final": final_caption, **snapshot},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            expected_hashtags = set(re.findall(r"#[^\s]+", video.stem))
            rich_hashtags = {
                str(node.get("text") or "").strip()
                for node in snapshot.get("hashtagNodes", [])
                if "mention" in str(node.get("className") or "").split()
            }
            if not expected_hashtags.issubset(rich_hashtags):
                raise RuntimeError(
                    "Caption text matched but not every hashtag became a rich entity."
                )
            print("HASHTAG_ENTITY_MATCH=True", flush=True)
        print(
            "TikTok source native upload diagnostic passed; no publish action was taken.",
            flush=True,
        )
    finally:
        await browser.close()
        session.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--username", required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--prepare-caption", action="store_true")
    parser.add_argument("--use-proxy", action="store_true")
    parser.add_argument("--proxy-id", default="")
    parser.add_argument("--studio-only", action="store_true")
    args = parser.parse_args()
    if not args.video.is_file():
        raise SystemExit(f"Video does not exist: {args.video}")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    asyncio.run(run(
        args.database,
        args.username,
        args.video,
        prepare_caption=args.prepare_caption,
        use_proxy=args.use_proxy,
        proxy_id=args.proxy_id,
        studio_only=args.studio_only,
    ))


if __name__ == "__main__":
    main()
