"""Read-only TikTok cookie-session probe for one stored account."""

import asyncio
import hashlib
import sys
from pathlib import Path
from urllib.parse import urlsplit

from sqlmodel import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.core.exceptions import AuthenticationPageNotReady
from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter
from app.infrastructure.database.connection import engine
from app.infrastructure.database.sqlite_repository import (
    SQLiteAccountRepository,
    SQLiteProxyRepository,
)
from app.use_cases.orchestration.task_dispatcher import _uuid_to_seed


async def main(username: str) -> int:
    with Session(engine) as session:
        accounts = SQLiteAccountRepository(session)
        proxies = SQLiteProxyRepository(session)
        account = next(
            (item for item in accounts.get_all() if item.username == username),
            None,
        )
        if account is None:
            print(f"ACCOUNT_NOT_FOUND username={username}")
            return 2
        proxy = proxies.get_by_id(account.proxy_id) if account.proxy_id else None
        proxy_config = None
        if proxy is not None:
            proxy_config = {
                "server": proxy.connection_string,
                "username": proxy.username,
                "password": proxy.password,
            }

    failures = []
    browser = InvisiblePlaywrightAdapter()

    async def log(message: str) -> None:
        print(message, flush=True)

    async def auth_cookie_names() -> list[str]:
        return sorted(
            {
                str(cookie.get("name") or "")
                for cookie in await browser.extract_cookies()
                if str(cookie.get("name") or "")
                in {"sessionid", "sessionid_ss", "sid_tt", "sid_guard"}
                and cookie.get("value")
            }
        )

    try:
        await browser.initialize(
            proxy_config=proxy_config,
            seed=_uuid_to_seed(account.id),
            force_visible=False,
        )

        def record_response(response) -> None:
            try:
                status = int(response.status)
                resource_type = str(response.request.resource_type or "")
                if status < 400 and resource_type != "document":
                    return
                parsed = urlsplit(response.url)
                failures.append(
                    {
                        "status": status,
                        "type": resource_type,
                        "host": parsed.hostname,
                        "path": parsed.path,
                    }
                )
            except Exception:
                pass

        browser._page.on("response", record_response)
        await browser.navigate_to("https://www.tiktok.com/foryou?lang=en")
        await browser.inject_cookies(account.cookies)
        auth_after_inject = await auth_cookie_names()
        await browser.navigate_to("https://www.tiktok.com/foryou?lang=en")
        auth_after_navigation = await auth_cookie_names()
        try:
            authenticated = await browser.check_login_status()
            verdict = "AUTHENTICATED" if authenticated else "CONFIRMED_GUEST"
        except AuthenticationPageNotReady:
            authenticated = False
            verdict = "PAGE_NOT_READY"
        identity_matches = bool(
            authenticated
            and await browser.validate_authenticated_identity(account.username)
        )

        state = await browser._page.evaluate(
            r"""() => {
              const visible = el => !!(el && (
                el.offsetParent !== null || el.getClientRects().length
              ));
              const login = Array.from(document.querySelectorAll(
                '[data-e2e="nav-login-button"],button'
              )).some(el => visible(el) && /^(log in|đăng nhập)$/i.test(
                String(el.innerText || el.textContent || '').trim()
              ));
              const account = Array.from(document.querySelectorAll(
                '[data-e2e="profile-icon"],[data-e2e="nav-profile"],'
                + '[data-e2e="messages-icon"],[data-e2e="inbox-icon"]'
              )).some(visible);
              return {
                href: location.href,
                title: document.title,
                ready: document.readyState,
                loginVisible: login,
                accountVisible: account,
                bodyPrefix: String(document.body?.innerText || '').slice(0, 240)
              };
            }"""
        )
        live_cookies = await browser.extract_cookies()
        auth_names = await auth_cookie_names()
        session_cookie = next(
            (
                cookie
                for cookie in live_cookies
                if cookie.get("name") in {"sessionid", "sessionid_ss"}
            ),
            {},
        )
        session_hash = hashlib.sha256(
            str(session_cookie.get("value") or "").encode("utf-8")
        ).hexdigest()[:10]
        print(
            {
                "verdict": verdict,
                "identity_matches": identity_matches,
                "proxy": proxy.connection_string if proxy else "DIRECT",
                "page": state,
                "auth_after_inject": auth_after_inject,
                "auth_after_navigation": auth_after_navigation,
                "live_auth_cookie_names": auth_names,
                "session_hash": session_hash,
                "http_observations": failures[-30:],
            },
            flush=True,
        )
        return 0
    finally:
        await browser.close()


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/diagnose_cookie_session.py <username>")
    raise SystemExit(asyncio.run(main(sys.argv[1])))
