import logging
from typing import Callable, Awaitable, Optional, Any

# Nhập các Port và Entity từ tầng Domain
from app.domain.ports.repository import IAccountRepository
from app.domain.ports.browser import IBrowserService
from app.domain.ports.email import IEmailService
from app.use_cases.auth.login_strategies import ITikTokLoginStrategy, CookieLoginStrategy, CredentialEmailOtpLoginStrategy

# Định nghĩa biến logger toàn cục của mô-đun
logger = logging.getLogger("TikTokLoginUseCase")

class LoginStrategyFactory:
    """Creational Pattern: Factory khởi tạo chiến lược login dựa trên phương thức truyền vào"""
    @staticmethod
    def get_strategy(method: str) -> ITikTokLoginStrategy:
        if method == "COOKIE":
            return CookieLoginStrategy()
        elif method == "CREDENTIAL" or method == "NORMAL":
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
            strategy = LoginStrategyFactory.get_strategy(login_method)
            
            success = await strategy.login(
                self.browser_service, 
                account,
                step_logger=self.step_logger,
                email_service=self.email_service
            )
            
            if success:
                new_cookies = await self.browser_service.extract_cookies()
                account.cookies = new_cookies
                account.status = "LOGGED_IN"
                account.current_step = "Đăng nhập thành công"
            else:
                account.status = "ERROR"
                account.current_step = "Đăng nhập thất bại"
            
            self.account_repo.save(account)
            return success
            
        except Exception as e:
            logger.error(f"[-] Lỗi thực thi đăng nhập TikTok: {str(e)}")
            account.status = "ERROR"
            self.account_repo.save(account)
            raise e