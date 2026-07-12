# File: backend/app/use_cases/profile/tiktok_update_profile.py
import os
import asyncio
import base64
import tempfile
import logging
import random
from pathlib import Path
from typing import Callable, Awaitable, Optional, Any

from app.domain.ports.repository import IAccountRepository
from app.domain.ports.browser import IBrowserService
from app.domain.ports.email import IEmailService
from app.use_cases.auth.login_strategies import ITikTokLoginStrategy
from app.core.exceptions import AccountBannedException  

logger = logging.getLogger("TikTokUpdateProfileUseCase")

class TikTokUpdateProfileUseCase:
    """Nghiệp vụ đổi thông tin hồ sơ: Bảo vệ vĩnh viễn trạng thái PROFILE_UPDATED trong Database"""
    def __init__(
        self, 
        account_repo: IAccountRepository, 
        browser_service: IBrowserService, 
        login_strategy: ITikTokLoginStrategy,
        email_service: IEmailService,
        step_logger: Optional[Callable[[str], Awaitable[None]]] = None
    ):
        self.account_repo = account_repo
        self.browser_service = browser_service
        self.login_strategy = login_strategy
        self.email_service = email_service
        self.step_logger = step_logger

    async def execute(self, account_id: str, avatar_path: Optional[str] = None) -> bool:
        account = self.account_repo.get_by_id(account_id)
        if not account:
            raise ValueError("Tài khoản không tồn tại trên hệ thống.")

        # =====================================================================
        # GIÁP BẢO VỆ: ĐỌC ĐÚNG CỘT TRẠNG THÁI PROFILE_STATUS TRONG DATABASE
        # =====================================================================
        if account.profile_status == "COMPLETED":
            if self.step_logger:
                await self.step_logger("[!] Tài khoản đã đổi Avatar & Bio thành công trước đó. Bỏ qua để tiết kiệm OTP.")
            logger.info(f"[*] [Bypass Guard] Tài khoản {account.username} đã có profile_status=COMPLETED. Bỏ qua.")
            return True 

        try:
            if self.step_logger:
                await self.step_logger("Đang khởi động môi trường trình duyệt tàng hình...")

            # 1. Ép chạy luồng đăng nhập (Form OTP hoặc Cookie)
            login_success = await self.login_strategy.login(
                self.browser_service,
                account,
                step_logger=self.step_logger,
                email_service=self.email_service
            )

            if not login_success:
                if self.step_logger:
                    await self.step_logger("[-] Đăng nhập xác thực thất bại. Không thể tiến hành đổi thông tin.")
                return False

            # =================================================================
            # NÂNG CẤP DEFENSIVE SAVE: SAO LƯU COOKIES PHÒNG THỦ NGAY LẬP TỨC
            # =================================================================
            if self.step_logger:
                await self.step_logger("[*] Đăng nhập thành công! Đang tự động lưu trữ Cookies phiên mới...")
            
            fresh_cookies = await self.browser_service.extract_cookies()
            if fresh_cookies:
                account.cookies = fresh_cookies
                account.status = "LOGGED_IN"
                account.health_status = "ALIVE"  # <-- ĐỒNG BỘ TRẠNG THÁI SỐNG Ở ĐÂY
                account.current_step = "Đã sao lưu Cookies thành công"
                self.account_repo.save(account)
                logger.info(f"[+] [Defensive Save] Đã sao lưu phòng thủ Cookies thành công cho {account.username}")

            # 2. Xử lý ảnh đại diện dự phòng nếu cần thiết
            test_avatar_path = avatar_path
            if not test_avatar_path:
                temp_dir = tempfile.gettempdir()
                test_avatar_path = os.path.join(temp_dir, "avatar_test.png")
                if not os.path.exists(test_avatar_path):
                    if self.step_logger:
                        await self.step_logger("[*] Khởi tạo ảnh đại diện mẫu dự phòng...")
                    teal_png_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
                    with open(test_avatar_path, "wb") as f:
                        f.write(base64.b64decode(teal_png_base64))

            # 3. Đọc dữ liệu Bio ngẫu nhiên từ file bios.txt
            backend_dir = Path(__file__).resolve().parent.parent.parent.parent
            bios_file_path = backend_dir / "bios.txt"

            if not os.path.exists(bios_file_path):
                default_bios = ["Happy Day 🚀", "Living life one code at a time 💻", "Keep moving forward ⚡"]
                with open(bios_file_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(default_bios))

            with open(bios_file_path, "r", encoding="utf-8") as f:
                bio_lines = [line.strip() for line in f if line.strip()]

            random_bio = random.choice(bio_lines) if bio_lines else "Developer | Automation Bot v4 🚀"
            logger.info(f"[+] Đã chọn Bio ngẫu nhiên: '{random_bio}'")

            # 4. Thực thi kịch bản cập nhật hồ sơ (Đổi avatar & Bio)
            success = await self.browser_service.update_profile(
                avatar_path=test_avatar_path, 
                bio=random_bio,
                step_logger=self.step_logger
            )

            # 5. Cập nhật kết quả cuối cùng sau khi hoàn tất trọn vẹn kịch bản
            if success:
                new_cookies = await self.browser_service.extract_cookies()
                account.cookies = new_cookies
                
                # Ghi nhận trạng thái hoàn thành tối thượng vào Database
                account.status = "SUCCESS"               
                account.health_status = "ALIVE"          # <-- ĐỒNG BỘ TRẠNG THÁI SỐNG Ở ĐÂY
                account.profile_status = "COMPLETED"     
                account.current_step = "Đổi thông tin thành công"
            else:
                account.status = "LOGGED_IN"
                account.health_status = "ALIVE"          # <-- ĐỒNG BỘ TRẠNG THÁI SỐNG Ở ĐÂY
                account.current_step = "Lỗi đổi thông tin (Cookies đã bảo toàn)"

            self.account_repo.save(account)
            return success

        # =====================================================================
        # PHỄU LỌC LỖI TƯỜNG MINH: Xử lý Banned trước, Exception chung sau
        # =====================================================================
        except AccountBannedException as e_ban:
            logger.error(f"[!] Nhận diện tài khoản bị cấm (Banned) khi đổi Profile: {str(e_ban)}")
            # SỬA LỖI GÁN NHẦM FIELD: "BANNED" phải nằm ở health_status (giống
            # hệt tiktok_login.py) - "status" là field khác, dùng cho vòng đời
            # phiên chạy (RUNNING/QUEUED/SUCCESS/ERROR), không phải sức khỏe nick.
            account.status = "ERROR"
            account.health_status = "BANNED"
            account.cookies = [] # Xóa sạch cookies hỏng
            account.current_step = "Tài khoản bị Banned"
            self.account_repo.save(account)
            raise e_ban

        except Exception as e:
            logger.error(f"[-] Lỗi đổi thông tin hồ sơ: {str(e)}")
            account.current_step = "Lỗi đổi thông tin"
            self.account_repo.save(account)
            raise e