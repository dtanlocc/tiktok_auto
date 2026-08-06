# File: backend/app/use_cases/upload/tiktok_upload_video.py
"""
Use case ĐĂNG VIDEO lên TikTok cho 1 account.
Luồng (đã verify end-to-end):
  1. Đăng nhập (ưu tiên cookie, fallback OTP) — dùng CookieThenCredentialLoginStrategy.
  2. Lưu lại cookie mới.
  3. browser_service.upload_video(video_path, caption, schedule_at):
     vào trang upload -> omocaptcha tự giải captcha -> đưa FILE THẬT qua hộp thoại
     Windows -> chờ xử lý -> caption -> (đặt lịch nếu có) -> Đăng -> xác nhận.
Trình duyệt đã được dispatcher khởi tạo (đúng proxy + seed vân tay) trước khi gọi.
"""
import logging
from typing import Optional, Any

logger = logging.getLogger("UploadVideoUseCase")


class TikTokUploadVideoUseCase:
    def __init__(self, account_repo, browser_service, login_strategy, email_service, step_logger=None):
        self.account_repo = account_repo
        self.browser_service = browser_service
        self.login_strategy = login_strategy
        self.email_service = email_service
        self.step_logger = step_logger

    async def _log(self, msg: str):
        if self.step_logger:
            await self.step_logger(msg)

    async def execute(self, account_id: str, video_path: str, caption: str = "",
                      schedule_at: Optional[str] = None, **_ignore: Any) -> bool:
        account = self.account_repo.get_by_id(account_id)
        if not account:
            raise Exception("Không tìm thấy tài khoản.")
        if not video_path:
            raise Exception("Chưa chỉ định đường dẫn video (video_path).")

        # 1) Đăng nhập (cookie -> OTP fallback)
        await self._log("Đăng nhập trước khi đăng video...")
        logged_in = await self.login_strategy.login(
            self.browser_service, account,
            step_logger=self.step_logger, email_service=self.email_service,
        )
        if not logged_in:
            raise Exception("Đăng nhập thất bại — không thể đăng video.")

        # 2) Lưu cookie mới nhất
        try:
            fresh = await self.browser_service.extract_cookies()
            if fresh:
                account = self.account_repo.get_by_id(account_id)
                account.cookies = fresh
                account.health_status = "ALIVE"
                self.account_repo.save(account)
        except Exception as e:
            logger.warning(f"[Upload] Không lưu được cookie {account_id}: {e}")

        # 3) Đăng video
        ok = await self.browser_service.upload_video(
            video_path=video_path, caption=caption,
            schedule_at=schedule_at, step_logger=self.step_logger,
        )
        if ok:
            account = self.account_repo.get_by_id(account_id)
            account.status = "SUCCESS"
            account.current_step = "✅ Đã lên lịch đăng" if schedule_at else "✅ Đã đăng video"
            self.account_repo.save(account)
        return ok
