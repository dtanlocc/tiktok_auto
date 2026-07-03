import logging
from typing import Callable, Awaitable, Optional

# Nhập các Port và Entity từ tầng Domain
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

    async def execute(self, account_id: str, login_method: str) -> bool:
        account = self.account_repo.get_by_id(account_id)
        if not account:
            raise ValueError("Tài khoản không tồn tại trên hệ thống.")

        try:
            # Khởi tạo chiến lược đăng nhập thông qua Factory
            strategy = LoginStrategyFactory.get_strategy(login_method)
            
            # Gửi log bắt đầu luồng trình duyệt
            if self.step_logger:
                await self.step_logger("Thiết lập môi trường tàng hình Firefox-13...")
            
            # --- DÒNG BỊ XÓA BỎ Ở ĐÂY ---
            # Không gọi: await self.browser_service.initialize() vì Dispatcher đã mở rồi
            # ----------------------------

            if self.step_logger:
                await self.step_logger(f"Đang chuẩn bị chiến lược đăng nhập: {login_method}...")

            if login_method == "COOKIE" and self.step_logger:
                await self.step_logger("Đang dọn sạch cache & nạp mảng Cookies JSON vào trình duyệt...")
            
            # Thực thi chiến lược đăng nhập tương ứng
            # Tìm kiếm dòng này trong tiktok_login.py và cập nhật tham số step_logger:
            success = await strategy.login(
                self.browser_service, 
                account,
                step_logger=self.step_logger # Chuyển tiếp callback
            )
            
            if success:
                if self.step_logger:
                    await self.step_logger("Xác minh Cookies thành công! Cập nhật trạng thái...")
                
                # Trích xuất cập nhật Cookie mới nhất từ trình duyệt để lưu lại
                new_cookies = await self.browser_service.extract_cookies()
                account.cookies = new_cookies
                account.status = "LOGGED_IN"
                account.current_step = "Đăng nhập thành công"
            else:
                if self.step_logger:
                    await self.step_logger("Xác minh thất bại. Cookies hết hạn hoặc không đúng thiết bị.")
                account.status = "ERROR"
                account.current_step = "Lỗi xác minh"
            
            # Lưu lại trạng thái tài khoản vào cơ sở dữ liệu
            self.account_repo.save(account)
            return success
            
        except Exception as e:
            if self.step_logger:
                await self.step_logger(f"Gặp lỗi hệ thống: {str(e)}")
            logger.error(f"[-] Lỗi thực thi đăng nhập TikTok: {str(e)}")
            account.status = "ERROR"
            account.current_step = "Gặp lỗi hệ thống"
            self.account_repo.save(account)
            raise e