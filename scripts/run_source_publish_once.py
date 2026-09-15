"""Run one guarded real upload through the current source backend classes.

This diagnostic deliberately checks Studio Posts before publishing.  If the
same media title is already present, it exits without clicking Post so a test
rerun cannot create a duplicate.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Make the script runnable directly from either the repository root or backend.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlmodel import Session

from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter
from app.infrastructure.database.connection import engine
from app.infrastructure.database.sqlite_repository import (
    SQLiteAccountRepository,
    SQLiteProxyRepository,
)
from app.infrastructure.email.email_service_factory import create_email_service
from app.use_cases.auth.login_strategies import CookieThenCredentialLoginStrategy
from app.use_cases.orchestration.task_dispatcher import _uuid_to_seed
from app.use_cases.upload.tiktok_upload_video import TikTokUploadMediaUseCase


def _find_account(repo: SQLiteAccountRepository, username: str):
    wanted = username.strip().lstrip("@").casefold()
    return next(
        (
            account
            for account in repo.get_all()
            if str(account.username or "").strip().lstrip("@").casefold() == wanted
        ),
        None,
    )


async def _run(
    username: str,
    video_path: Path,
    *,
    check_only: bool = False,
    screenshot_path: Path | None = None,
) -> int:
    if not video_path.is_file():
        raise FileNotFoundError(video_path)

    caption = video_path.stem
    browser = InvisiblePlaywrightAdapter()
    result_sink: list[dict[str, object]] = []

    async def log_step(message: str) -> None:
        print(f"SOURCE_UPLOAD_LOG {message}", flush=True)

    with Session(engine) as session:
        repo = SQLiteAccountRepository(session)
        account = _find_account(repo, username)
        if account is None:
            raise RuntimeError(f"Account username not found: {username}")

        account_id = account.id
        proxy = (
            SQLiteProxyRepository(session).get_by_id(account.proxy_id)
            if account.proxy_id
            else None
        )
        if account.proxy_id and proxy is None:
            raise RuntimeError("Assigned proxy no longer exists")
        proxy_config = None
        if proxy is not None:
            proxy_config = {
                "server": proxy.connection_string,
                "username": proxy.username,
                "password": proxy.password,
            }
        print(
            "SOURCE_UPLOAD_TARGET "
            + json.dumps(
                {
                    "account_id": account_id,
                    "username": account.username,
                    "video_path": str(video_path),
                    "caption": caption,
                    "proxy": proxy.connection_string if proxy else "DIRECT",
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        email_service = create_email_service()
        login_strategy = CookieThenCredentialLoginStrategy()
        use_case = TikTokUploadMediaUseCase(
            account_repo=repo,
            browser_service=browser,
            login_strategy=login_strategy,
            email_service=email_service,
            step_logger=log_step,
        )

        try:
            await browser.initialize(
                proxy_config=proxy_config,
                seed=_uuid_to_seed(account_id),
            )

            await log_step("Kiểm tra đăng nhập trước khi đối chiếu bài trùng...")
            logged_in = await login_strategy.login(
                browser,
                account,
                step_logger=log_step,
                email_service=email_service,
            )
            if not logged_in:
                raise RuntimeError("Không đăng nhập được để kiểm tra bài đã tồn tại.")

            # The guarded precheck performs the first credential/OTP login when
            # the stored cookie is stale. Persist that authenticated snapshot
            # before execute_video_batch reloads the account from the database.
            account = await use_case._persist_cookies_after_login_if_needed(
                account_id, account
            )

            ready = await browser.prepare_foryou_home(step_logger=log_step)
            if not ready:
                raise RuntimeError("Trang For You chưa sẵn sàng cho kiểm tra trước đăng.")

            already_present = await browser._verify_post_in_studio(
                caption,
                media_name=video_path.name,
                step_logger=log_step,
                timeout_seconds=20,
            )
            print(
                "SOURCE_UPLOAD_PRECHECK "
                + json.dumps({"already_present": already_present}),
                flush=True,
            )
            if screenshot_path is not None:
                await browser._page.screenshot(
                    path=str(screenshot_path), full_page=True
                )
                print(f"SOURCE_UPLOAD_SCREENSHOT {screenshot_path}", flush=True)
            if already_present:
                print(
                    "SOURCE_UPLOAD_RESULT "
                    + json.dumps(
                        {
                            "posted": False,
                            "skipped_duplicate": True,
                            "backend_success": True,
                        }
                    ),
                    flush=True,
                )
                return 0

            if check_only:
                print(
                    "SOURCE_UPLOAD_RESULT "
                    + json.dumps(
                        {
                            "posted": False,
                            "check_only": True,
                            "already_present": False,
                            "backend_success": True,
                        }
                    ),
                    flush=True,
                )
                return 0

            # execute_video_batch is the exact source-backend path. It performs
            # its own For You readiness check before opening Studio Upload.
            success = await use_case.execute_video_batch(
                account_id,
                video_paths=[str(video_path)],
                captions=[caption],
                result_sink=result_sink,
            )
            print(
                "SOURCE_UPLOAD_RESULT "
                + json.dumps(
                    {
                        "posted": True,
                        "skipped_duplicate": False,
                        "backend_success": bool(success),
                        "last_publish_acknowledged": bool(
                            browser.last_publish_acknowledged
                        ),
                        "distribution_status": (
                            browser.last_publish_distribution_status
                        ),
                        "items": result_sink,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 0 if success else 2
        finally:
            await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--username", required=True)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    return asyncio.run(
        _run(
            args.username,
            args.video.resolve(),
            check_only=args.check_only,
            screenshot_path=(args.screenshot.resolve() if args.screenshot else None),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
