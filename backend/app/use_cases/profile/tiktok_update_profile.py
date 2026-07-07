import asyncio
import logging
from typing import Callable, Awaitable, Optional, Any
from app.domain.ports.repository import IAccountRepository
from app.domain.ports.browser import IBrowserService

logger = logging.getLogger("TikTokUpdateProfileUseCase")

class TikTokUpdateProfileUseCase:
    """Chỉ làm duy nhất nhiệm vụ nạp Cookies có sẵn để đổi Avatar & Bio"""
    def __init__(
        self, 
        account_repo: IAccountRepository, 
        browser_service: IBrowserService, 
        step_logger: Optional[Callable[[str], Awaitable[None]]] = None
    ):
        self.account_repo = account_repo
        self.browser_service = browser_service
        self.step_logger = step_logger

    async def execute(self, account_id: str, avatar_path: Optional[str] = None) -> bool:
        account = self.account_repo.get_by_id(account_id)
        if not account:
            raise ValueError("Tài khoản không tồn tại trên hệ thống.")

        # Kiểm tra điều kiện bắt buộc: Phải có cookies hợp lệ mới cho phép chạy đổi thông tin
        if not account.cookies:
            if self.step_logger:
                await self.step_logger("[-] Lỗi: Tài khoản chưa có Cookies hoạt động. Vui lòng Đăng nhập trước.")
            return False

        try:
            if self.step_logger:
                await self.step_logger("Đang khởi động môi trường trình duyệt tàng hình...")
            await self.browser_service.initialize()

            if self.step_logger:
                await self.step_logger("Đang nạp Cookies để khôi phục phiên đăng nhập...")
            await self.browser_service.inject_cookies(account.cookies)

            # Đi tới trang chủ để đồng bộ phiên làm việc
            await self.browser_service.navigate_to("https://www.tiktok.com")
            await asyncio.sleep(4)

            # Thực thi kịch bản cập nhật hồ sơ
            test_bio_content = "Developer | Automation Bot v2.1 🚀"
            success = await self.browser_service.update_profile(
                avatar_path=avatar_path,
                bio=test_bio_content,
                step_logger=self.step_logger
            )

            if success:
                # Trích xuất và cập nhật lại cookie mới nhất sau khi đổi thông tin thành công
                new_cookies = await self.browser_service.extract_cookies()
                account.cookies = new_cookies
                account.status = "LOGGED_IN"
                account.current_step = "Đổi thông tin thành công"
            else:
                account.status = "ERROR"
                account.current_step = "Lỗi đổi thông tin"

            self.account_repo.save(account)
            return success

        except Exception as e:
            logger.error(f"[-] Lỗi đổi thông tin hồ sơ: {str(e)}")
            account.status = "ERROR"
            account.current_step = "Lỗi đổi thông tin"
            self.account_repo.save(account)
            raise e