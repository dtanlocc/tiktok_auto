import os
import asyncio
import base64
import tempfile
import logging
import random # Thêm import random
from pathlib import Path
from typing import Callable, Awaitable, Optional, Any

from app.domain.ports.repository import IAccountRepository
from app.domain.ports.browser import IBrowserService
from app.use_cases.auth.login_strategies import CredentialEmailOtpLoginStrategy
from app.infrastructure.email.dongvan_service import DongVanEmailService

logger = logging.getLogger("TikTokUpdateProfileUseCase")

class TikTokUpdateProfileUseCase:
    """Nghiệp vụ đổi thông tin hồ sơ: Bắt buộc đi qua luồng Đăng nhập Form + OTP trước để tăng tối đa độ tin cậy"""
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

        try:
            if self.step_logger:
                await self.step_logger("Đang khởi động môi trường trình duyệt tàng hình...")
            # await self.browser_service.initialize()

            # 1. Ép chạy luồng Đăng nhập Form + OTP DongVanFB trước
            strategy = CredentialEmailOtpLoginStrategy()
            email_service = DongVanEmailService()

            login_success = await strategy.login(
                self.browser_service,
                account,
                step_logger=self.step_logger,
                email_service=email_service
            )

            if not login_success:
                if self.step_logger:
                    await self.step_logger("[-] Đăng nhập Form bằng OTP thất bại. Không thể tiến hành đổi thông tin.")
                return False

            # 2. Đồng bộ: Tự sinh ảnh đại diện mẫu nếu bạn để trống ô đường dẫn thư mục ảnh trên Web UI
            test_avatar_path = avatar_path
            if not test_avatar_path:
                temp_dir = tempfile.gettempdir()
                test_avatar_path = os.path.join(temp_dir, "avatar_test.png")

                # Tự động tạo ảnh đại diện mẫu nếu chưa tồn tại trong thư mục tạm hệ thống
                if not os.path.exists(test_avatar_path):
                    if self.step_logger:
                        await self.step_logger("[*] Khởi tạo ảnh đại diện mẫu dự phòng...")
                    teal_png_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
                    with open(test_avatar_path, "wb") as f:
                        f.write(base64.b64decode(teal_png_base64))

            # =================================================================
            # 3. ĐỒNG BỘ ĐỌC VÀ BỐC BIO NGẪU NHIÊN TỪ FILE bios.txt
            # =================================================================
            backend_dir = Path(__file__).resolve().parent.parent.parent.parent
            bios_file_path = backend_dir / "bios.txt"

            # Tự động tạo file bios.txt mẫu nếu chưa tồn tại
            if not os.path.exists(bios_file_path):
                default_bios = [
                    "Happy Day 🚀",
                    "Living life one code at a time 💻",
                    "Keep moving forward ⚡",
                    "Have a nice day 🤖",
                    "Can you help me 🌟"
                ]
                with open(bios_file_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(default_bios))
                if self.step_logger:
                    await self.step_logger("[*] Đã khởi tạo file 'bios.txt' mẫu chứa nhiều dòng tiểu sử.")

            # Đọc toàn bộ các dòng trong file bios.txt
            with open(bios_file_path, "r", encoding="utf-8") as f:
                bio_lines = [line.strip() for line in f if line.strip()]

            # Bốc ngẫu nhiên 1 dòng làm Bio mới
            random_bio = random.choice(bio_lines) if bio_lines else "Developer | Automation Bot v4 🚀"
            logger.info(f"[+] Đã chọn Bio ngẫu nhiên: '{random_bio}'")

            # 4. Thực thi kịch bản đổi profile
            success = await self.browser_service.update_profile(
                avatar_path=test_avatar_path, 
                bio=random_bio, # Gửi Bio ngẫu nhiên chuẩn xác vào Adapter
                step_logger=self.step_logger
            )

            if success:
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