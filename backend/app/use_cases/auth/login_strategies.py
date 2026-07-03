import os
import asyncio
from pathlib import Path
from abc import ABC, abstractmethod
from typing import List, Dict, Any
from app.domain.ports.browser import IBrowserService
from app.domain.entities.account import TikTokAccount
from typing import Optional

class ITikTokLoginStrategy(ABC):
    @abstractmethod
    async def login(
        self, 
        browser: IBrowserService, 
        account: TikTokAccount,
        step_logger: Optional[Any] = None # Thêm nhận tham số
    ) -> bool:
        pass

import os
import asyncio
import base64
import tempfile  # Thư viện quản lý thư mục tạm hệ thống
from pathlib import Path
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional

class ITikTokLoginStrategy(ABC):
    @abstractmethod
    async def login(
        self, 
        browser: IBrowserService, 
        account: TikTokAccount,
        step_logger: Optional[Any] = None
    ) -> bool:
        pass

class CookieLoginStrategy(ITikTokLoginStrategy):
    async def login(
        self, 
        browser: IBrowserService, 
        account: TikTokAccount,
        step_logger: Optional[Any] = None
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
            # GIẢI PHÁP ĐỈNH CAO: Sử dụng thư mục tạm của hệ điều hành (tempfile) để lưu file ảnh.
            # Giúp giải quyết triệt để lỗi phân quyền Sandbox của trình duyệt trên Linux/Windows khi đọc file ngoài thư mục hệ thống.
            temp_dir = tempfile.gettempdir() # Sẽ trả về /tmp trên Linux
            test_avatar_path = os.path.join(temp_dir, "avatar_test.png")

            # Tự động tạo ảnh đại diện mẫu nếu chưa tồn tại trong thư mục tạm hệ thống
            if not os.path.exists(test_avatar_path):
                if step_logger:
                    await step_logger("[*] Khởi tạo ảnh đại diện mẫu trong thư mục tạm hệ thống...")
                teal_png_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
                with open(test_avatar_path, "wb") as f:
                    f.write(base64.b64decode(teal_png_base64))

            test_bio_content = "Developer | Automation Bot v4 🚀"
            
            # Thực thi cập nhật profile
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
    async def login(self, browser: IBrowserService, account: TikTokAccount) -> bool:
        await browser.navigate_to("https://www.tiktok.com/login/phone-or-email")
        return True