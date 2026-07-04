import logging
from typing import Callable, Awaitable, Optional, Any

from app.domain.ports.repository import IAccountRepository
from app.domain.ports.browser import IBrowserService
from app.use_cases.auth.login_strategies import ITikTokLoginStrategy, CookieLoginStrategy, CredentialLoginStrategy

logger = logging.getLogger("TikTokLoginUseCase")

class LoginStrategyFactory:
    """Creational Pattern: Factory khởi tạo chiến lược login dựa trên phương thức truyền vào"""
    @staticmethod
    def get_strategy(method: str) -> ITikTokLoginStrategy:
        if method == "COOKIE":
            return CookieLoginStrategy()
        elif method == "CREDENTIAL":
            return CredentialLoginStrategy()
        else:
            raise ValueError(f"Chiến lược đăng nhập '{method}' không được hỗ trợ.")


class TikTokLoginUseCase:
    """Luồng nghiệp vụ xử lý chính cho việc Đăng nhập TikTok"""
    def __init__(
        self, 
        account_repo: IAccountRepository, 
        browser_service: IBrowserService, 
        step_logger: Optional[Callable[[str], Awaitable[None]]] = None
    ):
        self.account_repo = account_repo
        self.browser_service = browser_service
        self.step_logger = step_logger  # Hàm callback bất đồng bộ để truyền log realtime về Web UI

    async def execute(self, account_id: str, login_method: str, custom_avatar_path: Optional[str] = None) -> bool:
        """Thực thi kịch bản đăng nhập TikTok, hỗ trợ nạp đường dẫn ảnh tuỳ chỉnh"""
        account = self.account_repo.get_by_id(account_id)
        if not account:
            raise ValueError("Tài khoản không tồn tại trên hệ thống.")

        try:
            # Khởi tạo chiến lược đăng nhập thông qua Factory
            strategy = LoginStrategyFactory.get_strategy(login_method)
            
            # Thực thi chiến lược đăng nhập tương ứng, chuyển tiếp đường dẫn ảnh động
            success = await strategy.login(
                self.browser_service, 
                account,
                step_logger=self.step_logger,
                custom_avatar_path=custom_avatar_path
            )
            
            if success:
                # Trích xuất cập nhật Cookie mới nhất từ trình duyệt để lưu lại
                new_cookies = await self.browser_service.extract_cookies()
                account.cookies = new_cookies
                account.status = "LOGGED_IN"
            else:
                account.status = "ERROR"
            
            self.account_repo.save(account)
            return success
            
        except Exception as e:
            logger.error(f"[-] Lỗi thực thi đăng nhập TikTok: {str(e)}")
            account.status = "ERROR"
            self.account_repo.save(account)
            raise e