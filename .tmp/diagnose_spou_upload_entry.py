import asyncio
import json
import sys
import time
from pathlib import Path

from sqlmodel import Session

sys.path.insert(0, r"D:\tiktok_auto\backend")

from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter
from app.infrastructure.database.connection import engine
from app.infrastructure.database.sqlite_repository import SQLiteAccountRepository
from app.infrastructure.streaming.win_capture import capture_hwnd_jpeg
from app.use_cases.auth.login_strategies import CookieLoginStrategy
from app.use_cases.orchestration.task_dispatcher import _uuid_to_seed


ACCOUNT_ID = "copianomiyettal@hotmail.com"
SCREENSHOT = Path(r"D:\tiktok_auto\.tmp\spou_upload_entry_diagnostic.jpg")


async def main() -> None:
    with Session(engine) as session:
        account = SQLiteAccountRepository(session).get_by_id(ACCOUNT_ID)
        if account is None:
            raise RuntimeError("Account not found")

    browser = InvisiblePlaywrightAdapter()
    observations = []
    try:
        await browser.initialize(proxy_config=None, seed=_uuid_to_seed(ACCOUNT_ID))
        logged_in = await CookieLoginStrategy().login(browser, account)
        print(json.dumps({"logged_in": logged_in}, ensure_ascii=False), flush=True)
        if not logged_in:
            return
        home_ready = await browser.prepare_foryou_home()
        print(json.dumps({"foryou_ready": home_ready}, ensure_ascii=False), flush=True)
        network_events = []
        console_events = []

        def on_response(response):
            if response.status >= 400 or "tiktokstudio" in response.url:
                network_events.append({"status": response.status, "url": response.url[:500]})

        def on_request_failed(request):
            network_events.append({
                "failed": True,
                "url": request.url[:500],
                "failure": str(request.failure)[:300],
            })

        def on_console(message):
            if message.type in {"error", "warning"}:
                console_events.append({"type": message.type, "text": message.text[:500]})

        browser._page.on("response", on_response)
        browser._page.on("requestfailed", on_request_failed)
        browser._page.on("console", on_console)
        browser._page.on(
            "pageerror",
            lambda error: console_events.append({"type": "pageerror", "text": str(error)[:500]}),
        )

        await browser.navigate_to("https://www.tiktok.com/tiktokstudio/upload?lang=en")
        print(json.dumps({"studio_url": browser._page.url, "hwnd": browser._hwnd}, ensure_ascii=False), flush=True)
        await asyncio.sleep(20)
        frame = await asyncio.to_thread(
            capture_hwnd_jpeg,
            browser._hwnd,
            1280,
            92,
        )
        if frame:
            SCREENSHOT.write_bytes(frame)
        frame_metadata = [
            {"index": index, "name": item.name, "url": item.url[:700]}
            for index, item in enumerate(browser._page.frames)
        ]
        print(json.dumps({"frames": frame_metadata}, ensure_ascii=False), flush=True)
        frame_dom = []
        for index, item in enumerate(browser._page.frames):
            try:
                body_text = await asyncio.wait_for(
                    item.locator("body").inner_text(timeout=3000),
                    timeout=5,
                )
                file_count = await asyncio.wait_for(
                    item.locator('input[type="file"]').count(),
                    timeout=5,
                )
                select_count = await asyncio.wait_for(
                    item.get_by_text("Select video", exact=False).count(),
                    timeout=5,
                )
                frame_dom.append({
                    "index": index,
                    "file_inputs": file_count,
                    "select_video_matches": select_count,
                    "text": " ".join(body_text.split())[:1000],
                })
            except Exception as exc:
                frame_dom.append({
                    "index": index,
                    "error": f"{type(exc).__name__}: {exc}",
                })
        recovered = None
        if any("error" in item for item in frame_dom):
            old_page = browser._page
            try:
                browser._page = await asyncio.wait_for(browser._browser.new_page(), timeout=20)
                try:
                    await asyncio.wait_for(old_page.close(), timeout=5)
                except Exception:
                    pass
                await browser.navigate_to("https://www.tiktok.com/tiktokstudio/upload?lang=en")
                await asyncio.sleep(15)
                recovered_text = await asyncio.wait_for(
                    browser._page.locator("body").inner_text(timeout=5000),
                    timeout=7,
                )
                recovered = {
                    "url": browser._page.url,
                    "file_inputs": await asyncio.wait_for(
                        browser._page.locator('input[type="file"]').count(), timeout=5
                    ),
                    "select_video_matches": await asyncio.wait_for(
                        browser._page.get_by_text("Select video", exact=False).count(), timeout=5
                    ),
                    "text": " ".join(recovered_text.split())[:1200],
                }
            except Exception as exc:
                recovered = {"error": f"{type(exc).__name__}: {exc}"}
        print(json.dumps({
            "screenshot": str(SCREENSHOT) if frame else "",
            "frame_dom": frame_dom,
            "fresh_tab_recovery": recovered,
            "network_events": network_events[-80:],
            "console_events": console_events[-80:],
        }, ensure_ascii=False), flush=True)
    finally:
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
