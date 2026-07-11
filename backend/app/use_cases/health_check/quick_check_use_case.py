"""
LUONG XU LY DOC LAP HOAN TOAN VOI ConcurrentTaskDispatcher (theo yeu cau:
"tach rieng hoan toan, khong qua Dispatcher/Semaphore hien tai").

Dung Chromium THUONG (khong phai Invisible Firefox chong do van tay), vi
day chi la thao tac DOC CONG KHAI title trang tiktok.com/@username - khong
can dang nhap, khong can gia lap nguoi that, nen khong can toi bo may
chong-bot nang cua invisible_playwright.

Co che dong thoi RIENG: 1 browser Chromium DUY NHAT duoc mo (nhe, dung
chung 1 tien trinh), moi account check chay trong 1 BrowserContext rieng
(cach ly cookie/cache nhu 1 "tab an danh" doc lap), gioi han so luong
context chay song song bang asyncio.Semaphore rieng cua chinh module nay -
HOAN TOAN KHONG dinh gi toi self.semaphore cua ConcurrentTaskDispatcher.
"""
import asyncio
import logging
from typing import List, Dict, Any, Optional

from playwright.async_api import async_playwright, Page
from sqlmodel import Session

from app.infrastructure.database.connection import engine
from app.infrastructure.database.sqlite_repository import SQLiteAccountRepository
from app.infrastructure.websocket.socket_manager import ws_manager

logger = logging.getLogger("QuickHealthCheck")


class QuickHealthCheckService:
    """Singleton doc lap - khong lien quan gi toi ConcurrentTaskDispatcher."""

    def __init__(self):
        self.is_running: bool = False
        self.total: int = 0
        self.completed: int = 0

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_running": self.is_running,
            "total": self.total,
            "completed": self.completed,
        }

    async def _check_one_page(self, page: Page, username: str) -> Optional[str]:
        """Ban dich async cua ham check_tiktok_playwright() trong script goc.
        Tra ve: 'SONG_DA_TUONG_TAC' | 'SONG_TRANG' | 'DIE' | None (loi mang/captcha)."""
        url = f"https://www.tiktok.com/@{username}"
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=20000)

            title = ""
            for _ in range(5):
                await page.wait_for_timeout(1000)
                title = await page.title()
                if title not in ("TikTok", "TikTok - Make Your Day", ""):
                    break

            logger.info(f"   [Log] @{username} - Title bat duoc: '{title}'")

            if "Just a moment" in title or "Please wait" in title:
                return None  # Ket WAF/Captcha -> bo qua, khong ket luan

            is_alive = False
            if f"@{username.lower()}" in title.lower() or username.lower() in title.lower():
                is_alive = True
            elif title in ("TikTok", "TikTok - Make Your Day"):
                return "DIE"
            else:
                return "DIE"

            if is_alive:
                co_video = False
                doi_avatar = False
                try:
                    await page.wait_for_selector(
                        '[data-e2e="user-post-item"], [data-e2e="user-avatar"]', timeout=3000
                    )
                    so_luong_video = await page.locator('[data-e2e="user-post-item"]').count()
                    if so_luong_video > 0:
                        co_video = True

                    avatar_elements = page.locator('[data-e2e="user-avatar"] img')
                    if await avatar_elements.count() > 0:
                        avatar_src = await avatar_elements.first.get_attribute('src')
                        if avatar_src:
                            is_default_avatar = (
                                "tiktok-obj" in avatar_src or
                                "100x100" in avatar_src or
                                "default" in avatar_src.lower() or
                                "musically-maliva-obj" in avatar_src or
                                "1594805258216454" in avatar_src
                            )
                            if not is_default_avatar:
                                doi_avatar = True
                except Exception:
                    pass

                return "SONG_DA_TUONG_TAC" if (co_video or doi_avatar) else "SONG_TRANG"

        except Exception as e:
            logger.warning(f"⚠️ Lỗi mạng khi check @{username}: {str(e)}")
            return None

    async def _process_one_account(self, browser, semaphore: asyncio.Semaphore, account_id: str) -> None:
        async with semaphore:
            # Moi account tu mo Session DB rieng - SQLAlchemy Session KHONG
            # an toan de dung chung giua cac coroutine chay song song.
            with Session(engine) as session:
                repo = SQLiteAccountRepository(session)
                account = repo.get_by_id(account_id)
                if not account or not account.username:
                    self.completed += 1
                    return

                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    viewport={'width': 1280, 'height': 720}
                )
                page = await context.new_page()

                try:
                    ket_qua = await self._check_one_page(page, account.username)

                    if ket_qua == "SONG_DA_TUONG_TAC":
                        account.health_status = "ALIVE"
                        account.profile_status = "COMPLETED"
                        account.current_step = "🔍 Check nhanh: SỐNG (đã đổi Avatar/có Video)"
                    elif ket_qua == "SONG_TRANG":
                        account.health_status = "ALIVE"
                        account.current_step = "🔍 Check nhanh: SỐNG (nick trắng, chưa tương tác)"
                    elif ket_qua == "DIE":
                        account.health_status = "DEAD"
                        account.current_step = "☠️ Check nhanh: DIE"
                    else:
                        # Loi mang/Captcha - KHONG doi health_status cu, chi ghi chu
                        account.current_step = "⏸️ Check nhanh: Lỗi mạng/Captcha (đã bỏ qua, giữ nguyên trạng thái cũ)"

                    repo.save(account)

                    await ws_manager.broadcast({
                        "event": "ACCOUNT_STATUS_CHANGED",
                        "data": {
                            "id": account.id,
                            "status": account.status,
                            "health_status": account.health_status,
                            "profile_status": account.profile_status,
                            "current_step": account.current_step
                        }
                    })
                finally:
                    await context.close()
                    self.completed += 1

    async def run_batch(self, account_ids: List[str], concurrency_limit: int = 5) -> None:
        if self.is_running:
            logger.warning("[!] Da co 1 dot Check nhanh dang chay, bo qua yeu cau moi.")
            return

        self.is_running = True
        self.total = len(account_ids)
        self.completed = 0

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=['--disable-blink-features=AutomationControlled']
                )
                try:
                    semaphore = asyncio.Semaphore(max(1, concurrency_limit))
                    tasks = [
                        self._process_one_account(browser, semaphore, acc_id)
                        for acc_id in account_ids
                    ]
                    await asyncio.gather(*tasks, return_exceptions=True)
                finally:
                    await browser.close()
        except Exception as e:
            logger.error(f"[-] Lỗi tổng quát khi chạy Check nhanh hàng loạt: {str(e)}")
        finally:
            self.is_running = False
            await ws_manager.broadcast({
                "event": "QUICK_CHECK_FINISHED",
                "data": self.get_status()
            })
            logger.info(f"[+] Hoan tat dot Check nhanh: {self.completed}/{self.total} tai khoan.")


# Singleton dung chung cho toan app (import truc tiep, khong qua app.state
# de giu dung tinh than "tach rieng hoan toan" ma ban chon)
quick_health_check_service = QuickHealthCheckService()
