import os
import asyncio
import base64
import tempfile
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

from app.domain.ports.browser import IBrowserService
from app.domain.entities.account import TikTokAccount

class ITikTokLoginStrategy(ABC):
    @abstractmethod
    async def login(
        self, 
        browser: IBrowserService, 
        account: TikTokAccount,
        step_logger: Optional[Any] = None,
        custom_avatar_path: Optional[str] = None  # Nhận tham số từ Use Case
    ) -> bool:
        pass

class CookieLoginStrategy(ITikTokLoginStrategy):
    async def login(
        self, 
        browser: IBrowserService, 
        account: TikTokAccount,
        step_logger: Optional[Any] = None,
        custom_avatar_path: Optional[str] = None  # Nhận tham số từ Use Case
    ) -> bool:
        if not account.cookies:
            return False
        
        # 1. Điều hướng và nạp Cookies
        await browser.navigate_to("https://www.tiktok.com")
        await browser.inject_cookies(account.cookies)
        
        # 2. Điều hướng lại trang chủ để kích hoạt Cookies hoạt động
        await browser.navigate_to("https://www.tiktok.com")
        
        # 3. TIẾN HÀNH XÁC MINH COOKIES CHỜ THÔNG MINH
        is_logged_in = await browser.check_login_status()
        
        if not is_logged_in:
            return False
            
        # 4. CHỈ KHI COOKIES SỐNG -> THỰC HIỆN THAO TÁC PHỤ
        try:
            # ƯU TIÊN SỬ DỤNG ẢNH ĐỘNG DO BỘ ĐIỀU PHỐI GỬI SANG
            test_avatar_path = custom_avatar_path

            # Nếu không có ảnh truyền sang (chạy thử nghiệm thủ công), tự sinh ảnh tạm
            if not test_avatar_path:
                temp_dir = tempfile.gettempdir()
                test_avatar_path = os.path.join(temp_dir, "avatar_test.png")

                if not os.path.exists(test_avatar_path):
                    if step_logger:
                        await step_logger("[*] Khởi tạo ảnh đại diện mẫu trong thư mục tạm hệ thống...")
                    teal_png_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
                    with open(test_avatar_path, "wb") as f:
                        f.write(base64.b64decode(teal_png_base64))

            test_bio_content = "Every Day. Hello Baby <3"
            
            # Chuyển tiếp đường dẫn ảnh động thật sự vào Adapter
            await browser.update_profile(
                avatar_path=test_avatar_path,
                bio=test_bio_content,
                step_logger=step_logger
            )
        except Exception as e:
            if step_logger:
                await step_logger(f"[!] Lỗi thao tác đổi Profile phụ nhưng Cookies vẫn sống: {str(e)}")
            
        return True

class CredentialLoginStrategy(ITikTokLoginStrategy):
    async def login(
        self, 
        browser: IBrowserService, 
        account: TikTokAccount,
        step_logger: Optional[Any] = None,
        custom_avatar_path: Optional[str] = None
    ) -> bool:
        await browser.navigate_to("https://www.tiktok.com/login/phone-or-email")
        return True