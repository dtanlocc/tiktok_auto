# File: backend/app/use_cases/debug/debug_login_service.py
"""
CHE DO DEBUG (thao tac tay):
    Mo trinh duyet HIEN (khong an off-screen), dang nhap vao 1 account (uu tien
    cookie, fallback OTP), roi GIU cua so mo de user tu tay thao tac. Phien chi
    ket thuc khi user DONG cua so (hoac bam nut "Dung debug" tren UI).

Hoan toan TACH RIENG voi ConcurrentTaskDispatcher (luong mo trinh duyet AN) va
voi quick_health_check_service. Moi account chi co toi da 1 phien debug tai 1
thoi diem.
"""
import asyncio
import logging
import hashlib
from typing import Dict, Optional

from sqlmodel import Session

from app.infrastructure.database.connection import engine
from app.infrastructure.database.sqlite_repository import (
    SQLiteAccountRepository,
    SQLiteProxyRepository,
)
from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter
from app.infrastructure.websocket.socket_manager import ws_manager

logger = logging.getLogger("DebugLoginService")


def _uuid_to_seed(uuid_str: str) -> int:
    """Giong het dispatcher: seed van tay co dinh theo id account."""
    if not uuid_str:
        return 42
    return int(hashlib.sha256(uuid_str.encode("utf-8")).hexdigest()[:8], 16)


class DebugLoginService:
    """Quan ly cac phien debug dang mo. Singleton (khoi tao 1 lan o cuoi file)."""

    def __init__(self) -> None:
        # account_id -> browser adapter dang mo (de co the dong tu xa qua stop()).
        self._sessions: Dict[str, InvisiblePlaywrightAdapter] = {}
        # account_id -> asyncio.Task dang chay phien debug.
        self._tasks: Dict[str, asyncio.Task] = {}

    def is_running(self, account_id: str) -> bool:
        return account_id in self._sessions

    def active_ids(self) -> list:
        return list(self._sessions.keys())

    async def _broadcast_log(self, account_id: str, username: str, message: str) -> None:
        try:
            await ws_manager.broadcast({
                "event": "TERMINAL_LOG",
                "data": {"account_id": account_id, "username": username, "message": message},
            })
        except Exception:
            pass

    async def _broadcast_status(self, account_id: str, status: str, step: str,
                                health_status: Optional[str] = None) -> None:
        try:
            await ws_manager.broadcast({
                "event": "ACCOUNT_STATUS_CHANGED",
                "data": {
                    "id": account_id,
                    "status": status,
                    "current_step": step,
                    **({"health_status": health_status} if health_status else {}),
                },
            })
        except Exception:
            pass

    def start(self, account_id: str) -> bool:
        """Bat dau 1 phien debug moi (chay ngam). Tra ve False neu account nay
        da co phien debug dang chay."""
        if account_id in self._sessions or account_id in self._tasks:
            return False
        task = asyncio.create_task(self._run(account_id))
        self._tasks[account_id] = task
        return True

    async def stop(self, account_id: str) -> bool:
        """Dong cua so debug tu xa (nut 'Dung debug' tren UI). Viec dong page se
        khien wait_for_event('close') o _run tra ve -> phien tu ket thuc sach se.
        Tra ve False neu khong co phien nao dang chay cho account nay."""
        browser = self._sessions.get(account_id)
        if browser is None:
            return False
        try:
            await browser.close()
        except Exception as e:
            logger.warning(f"[DEBUG] Loi khi dong browser {account_id}: {e}")
        return True

    async def _run(self, account_id: str) -> None:
        with Session(engine) as session:
            account_repo = SQLiteAccountRepository(session)
            proxy_repo = SQLiteProxyRepository(session)

            account = account_repo.get_by_id(account_id)
            if not account:
                self._tasks.pop(account_id, None)
                return

            uname = account.username or account_id

            # Proxy cach ly giong dispatcher.
            proxy_config = None
            if account.proxy_id:
                proxy = proxy_repo.get_by_id(account.proxy_id)
                if proxy:
                    proxy_config = {
                        "server": proxy.connection_string,
                        "username": proxy.username,
                        "password": proxy.password,
                    }

            from app.infrastructure.email.dongvan_service import DongVanEmailService
            from app.use_cases.auth.login_strategies import CookieThenCredentialLoginStrategy

            email_service = DongVanEmailService()
            browser = InvisiblePlaywrightAdapter()
            self._sessions[account_id] = browser

            async def slog(msg: str):
                await self._broadcast_log(account_id, uname, msg)

            try:
                await self._broadcast_status(account_id, "RUNNING", "🐛 Debug: đang mở trình duyệt HIỆN...")
                await slog("🐛 DEBUG: Đang mở trình duyệt HIỆN (không ẩn) để bạn thao tác tay...")

                seed_val = _uuid_to_seed(account_id)
                # force_visible=True: cua so ra HIEN + foreground, KHONG day off-screen.
                await browser.initialize(proxy_config=proxy_config, seed=seed_val, force_visible=True)

                await slog("🐛 DEBUG: Đang đăng nhập (ưu tiên cookie, fallback OTP)...")
                login_strategy = CookieThenCredentialLoginStrategy()
                ok = await login_strategy.login(
                    browser, account, step_logger=slog, email_service=email_service
                )

                if ok:
                    # Luu lai cookie moi nhat de lan sau dung lai.
                    try:
                        fresh_cookies = await browser.extract_cookies()
                        if fresh_cookies:
                            account = account_repo.get_by_id(account_id)
                            account.cookies = fresh_cookies
                            account.health_status = "ALIVE"
                            account_repo.save(account)
                    except Exception as e_ck:
                        logger.warning(f"[DEBUG] Khong luu duoc cookie {account_id}: {e_ck}")

                    await self._broadcast_status(
                        account_id, "SUCCESS", "🐛 Debug: đang MỞ để thao tác tay", health_status="ALIVE"
                    )
                    await slog("✅ DEBUG: Đăng nhập XONG. Cửa sổ đang MỞ — thao tác tự do. "
                               "ĐÓNG cửa sổ (hoặc bấm '⏹ Dừng debug') khi xong.")
                else:
                    await self._broadcast_status(account_id, "SUCCESS", "🐛 Debug: đăng nhập lỗi — cửa sổ vẫn MỞ")
                    await slog("⚠️ DEBUG: Đăng nhập KHÔNG thành công, nhưng vẫn GIỮ cửa sổ mở "
                               "để bạn tự thao tác. ĐÓNG cửa sổ khi xong.")

                # GIU CUA SO MO: cho toi khi user dong page (hoac bam Dung debug ->
                # stop() goi browser.close() -> page dong -> event 'close' bat ra).
                try:
                    if browser._page:
                        await browser._page.wait_for_event("close", timeout=0)
                except Exception:
                    pass

                await slog("🛑 DEBUG: Cửa sổ đã đóng. Kết thúc phiên debug.")
                await self._broadcast_status(account_id, "SUCCESS", "Đã đóng phiên debug")

            except asyncio.CancelledError:
                await slog("🛑 DEBUG: Phiên debug bị hủy.")
                raise
            except Exception as e:
                logger.exception(f"[DEBUG] Loi phien debug {account_id}")
                await slog(f"❌ DEBUG lỗi: {type(e).__name__}: {str(e)}")
                await self._broadcast_status(account_id, "ERROR", f"Debug lỗi: {str(e)[:60]}")
            finally:
                try:
                    await browser.close()
                except Exception:
                    pass
                self._sessions.pop(account_id, None)
                self._tasks.pop(account_id, None)


# Singleton dung chung toan app (giong quick_health_check_service).
debug_login_service = DebugLoginService()
