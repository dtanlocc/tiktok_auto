import logging
from typing import Callable, Awaitable, Optional, Any

# Nhập các Port và Entity từ tầng Domain
from app.domain.ports.repository import IAccountRepository
from app.domain.ports.browser import IBrowserService
from app.domain.ports.email import IEmailService
from app.use_cases.auth.login_strategies import (
    ITikTokLoginStrategy, CookieLoginStrategy, CredentialEmailOtpLoginStrategy,
    CookieThenCredentialLoginStrategy,
)
from app.core.exceptions import AccountBannedException
from app.infrastructure.websocket.socket_manager import ws_manager

# Định nghĩa biến logger toàn cục của mô-đun
logger = logging.getLogger("TikTokLoginUseCase")

class LoginStrategyFactory:
    """Creational Pattern: Factory khởi tạo chiến lược login dựa trên phương thức truyền vào"""
    @staticmethod
    def get_strategy(method: str) -> ITikTokLoginStrategy:
        if method == "COOKIE":
            # THEO YEU CAU: bam Login -> THU cookie truoc, cookie HONG thi TU DONG
            # chay tiep login OTP (khong dung lai). Banned thi noi ngoai le -> xuat
            # trang thai BANNED binh thuong (khong fallback vo ich).
            return CookieThenCredentialLoginStrategy()
        elif method == "CREDENTIAL" or method == "NORMAL":
            # Ep buoc login OTP truc tiep (bo qua cookie).
            return CredentialEmailOtpLoginStrategy()
        else:
            raise ValueError(f"Chiến lược đăng nhập '{method}' không được hỗ trợ.")


class TikTokLoginUseCase:
    """Chỉ làm duy nhất nhiệm vụ Đăng nhập & lưu Cookies sống vào DB"""
    def __init__(
        self, 
        account_repo: IAccountRepository, 
        browser_service: IBrowserService, 
        step_logger: Optional[Callable[[str], Awaitable[None]]] = None,
        email_service: Optional[IEmailService] = None
    ):
        self.account_repo = account_repo
        self.browser_service = browser_service
        self.step_logger = step_logger
        self.email_service = email_service

    async def execute(self, account_id: str, login_method: str, custom_avatar_path: Optional[str] = None) -> bool:
        """Thực thi kịch bản đăng nhập TikTok, hỗ trợ nạp đường dẫn ảnh tuỳ chỉnh"""
        account = self.account_repo.get_by_id(account_id)
        if not account:
            raise ValueError("Tài khoản không tồn tại trên hệ thống.")

        try:
            username_changed = False
            strategy = LoginStrategyFactory.get_strategy(login_method)
            success = await strategy.login(
                self.browser_service, account, step_logger=self.step_logger, email_service=self.email_service
            )
            
            if success:
                new_cookies = await self.browser_service.extract_cookies()
                account.cookies = new_cookies

                # Synchronize the TikTok username without touching avatar or
                # bio. The legacy rules live in update_profile(): userxxxx is
                # replaced with the DB username; a real differing TikTok name
                # is persisted back to DB; an equal name is left unchanged.
                # This must also run after cookie/OTP login, otherwise uploads
                # keep displaying a stale imported username indefinitely.
                try:
                    _sync_ok, _username_for_db = await self.browser_service.update_profile(
                        avatar_path=None,
                        bio=None,
                        step_logger=self.step_logger,
                        db_username=account.username,
                    )
                    if _sync_ok and _username_for_db and _username_for_db != account.username:
                        logger.info(
                            "[Username Sync] %s -> %s",
                            account.username,
                            _username_for_db,
                        )
                        account.username = _username_for_db
                        username_changed = True
                except Exception as sync_exc:
                    # Username sync is corrective metadata; never invalidate a
                    # valid login solely because the profile editor is gated.
                    logger.warning("[Username Sync] Bo qua dong bo username: %s", sync_exc)
                
                # CẬP NHẬT TRẠNG THÁI PHÂN RÃ SẠCH SẼ
                account.status = "SUCCESS"               # Phiên chạy thành công
                account.health_status = "ALIVE"          # Nick sống
                account.current_step = "Đăng nhập thành công"
            else:
                account.status = "ERROR"
                account.current_step = "Đăng nhập thất bại"
            
            displaced_account = None
            if username_changed:
                _, displaced_account = self.account_repo.save_prioritizing_username(account)
            else:
                self.account_repo.save(account)

            if displaced_account:
                logger.warning(
                    "[Username Sync] Uu tien %s -> %s; chuyen %s -> %s",
                    account.email,
                    account.username,
                    displaced_account.email,
                    displaced_account.username,
                )
                await ws_manager.broadcast({
                    "event": "ACCOUNT_UPDATED",
                    "data": {
                        "id": displaced_account.id,
                        "username": displaced_account.username,
                    },
                })
            return success
            
        except AccountBannedException as e_ban:
            logger.error(f"[!] Tài khoản {account.username} bị Banned!")
            account.status = "ERROR"                    # Phiên chạy lỗi
            account.health_status = "BANNED"            # Ghi nhận trạng thái Banned vĩnh viễn
            account.cookies = []  
            account.current_step = "Tài khoản bị Banned"
            self.account_repo.save(account)
            raise e_ban
