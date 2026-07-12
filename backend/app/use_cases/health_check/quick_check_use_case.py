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
from datetime import datetime
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

        # =================================================================
        # CHE DO LIEN TUC (Continuous Mode): tu dong lap lai quet toan bo
        # account dang co health_status="ALIVE", hoan toan tach biet, khong
        # dinh gi toi ConcurrentTaskDispatcher/InteractionScheduler. Chi la
        # 1 vong lap asyncio don gian, tu goi lai run_batch() theo chu ky.
        # =================================================================
        self._continuous_task: Optional[asyncio.Task] = None
        self._continuous_active: bool = False
        self._continuous_gap_seconds: int = 3   # Nghỉ tối thiểu giữa 2 vòng quét - KHÔNG phải chu kỳ chờ dài
        self._continuous_concurrency: int = 15  # Nhiều luồng song song để quét nhanh
        self._cycle_count: int = 0
        self._last_cycle_at: Optional[str] = None

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_running": self.is_running,
            "total": self.total,
            "completed": self.completed,
        }

    def get_continuous_status(self) -> Dict[str, Any]:
        return {
            "is_active": self._continuous_active,
            "gap_seconds": self._continuous_gap_seconds,
            "concurrency_limit": self._continuous_concurrency,
            "cycle_count": self._cycle_count,
            "last_cycle_at": self._last_cycle_at,
            "is_running_now": self.is_running,
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
                        # THONG NHAT VOI LUONG LOGIN: dung chung gia tri "BANNED"
                        # (dung y het khi AccountBannedException duoc bat trong
                        # task_dispatcher.py), khong con tach rieng "DEAD" nua -
                        # ca 2 luong deu chi con dung DUY NHAT 1 tap gia tri
                        # health_status: ALIVE / BANNED / (giu nguyen cu neu loi mang).
                        account.health_status = "BANNED"
                        account.current_step = "☠️ Check nhanh: DIE (đánh dấu BANNED)"
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

    async def _continuous_loop(self) -> None:
        """Vòng lặp chạy nền LIÊN TỤC KHÔNG NGHỈ: hết 1 vòng quét toàn bộ
        account ALIVE là chạy ngay vòng kế tiếp (chỉ nghỉ tối thiểu
        _continuous_gap_seconds để nhường event loop / tránh spam DB liên
        tục), lặp mãi tới khi stop_continuous() được gọi."""
        while self._continuous_active:
            try:
                with Session(engine) as session:
                    repo = SQLiteAccountRepository(session)
                    all_accounts = repo.get_all()
                    alive_ids = [a.id for a in all_accounts if a.health_status == "ALIVE"]

                if alive_ids:
                    logger.info(
                        f"[*] [Continuous Check] Bắt đầu vòng #{self._cycle_count + 1} "
                        f"cho {len(alive_ids)} account đang ALIVE ({self._continuous_concurrency} luồng song song)."
                    )
                    await self.run_batch(alive_ids, concurrency_limit=self._continuous_concurrency)
                    self._cycle_count += 1
                    self._last_cycle_at = datetime.now().isoformat()
                    await ws_manager.broadcast({
                        "event": "QUICK_CHECK_CONTINUOUS_CYCLE_DONE",
                        "data": self.get_continuous_status()
                    })
                else:
                    logger.info("[*] [Continuous Check] Không có account nào đang ALIVE, đợi ít giây rồi kiểm tra lại.")
            except Exception as e:
                logger.error(f"[-] Lỗi trong vòng lặp Check nhanh liên tục: {str(e)}")

            # CHỈ nghỉ tối thiểu vài giây (KHÔNG phải chờ hàng chục phút) -
            # đủ để nhường event loop và tránh vòng lặp trắng (rỗng account)
            # ăn CPU liên tục, vẫn giữ tinh thần "chạy liên tục" như yêu cầu.
            for _ in range(max(1, self._continuous_gap_seconds)):
                if not self._continuous_active:
                    break
                await asyncio.sleep(1)

        logger.info("[-] [Continuous Check] Vòng lặp liên tục đã dừng hẳn.")

    def start_continuous(self, gap_seconds: int = 3, concurrency_limit: int = 15) -> bool:
        """Bật chế độ quét LIÊN TỤC toàn bộ account ALIVE (đa luồng, hết vòng
        chạy ngay vòng kế). Trả về False nếu đã đang bật sẵn (idempotent,
        không tạo task chồng chéo)."""
        if self._continuous_active:
            return False
        self._continuous_active = True
        self._continuous_gap_seconds = max(0, gap_seconds)
        self._continuous_concurrency = max(1, concurrency_limit)
        self._cycle_count = 0
        self._continuous_task = asyncio.create_task(self._continuous_loop())
        logger.info(
            f"[+] [Continuous Check] Đã bật chế độ quét LIÊN TỤC "
            f"({self._continuous_concurrency} luồng song song, nghỉ {self._continuous_gap_seconds}s giữa các vòng)."
        )
        return True

    def stop_continuous(self) -> bool:
        if not self._continuous_active:
            return False
        self._continuous_active = False
        # KHÔNG cancel() task đang chạy dở run_batch() giữa chừng - để nó tự
        # hoàn tất đợt hiện tại cho gọn gàng, chỉ ngăn nó lặp thêm chu kỳ mới
        # (vòng poll mỗi giây phía trên sẽ tự thoát trong tối đa 1 giây).
        logger.info("[-] [Continuous Check] Đã tắt chế độ liên tục (sẽ dừng hẳn sau khi xong chu kỳ hiện tại, tối đa vài giây).")
        return True

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
