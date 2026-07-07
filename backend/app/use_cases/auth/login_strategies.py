import os
import asyncio
import tempfile
import logging
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from app.domain.ports.browser import IBrowserService
from app.domain.ports.email import IEmailService
from app.domain.entities.account import TikTokAccount

logger = logging.getLogger("LoginStrategies")

class ITikTokLoginStrategy(ABC):
    """Lớp cơ sở trừu tượng cho mọi chiến lược đăng nhập TikTok"""
    
    @abstractmethod
    async def login(
        self, 
        browser: IBrowserService, 
        account: TikTokAccount,
        step_logger: Optional[Any] = None,
        email_service: Optional[IEmailService] = None,
        custom_avatar_path: Optional[str] = None
    ) -> bool:
        """Giao thức thực thi đăng nhập chung"""
        pass


class CookieLoginStrategy(ITikTokLoginStrategy):
    """
    CHIẾN LƯỢC 1: ĐĂNG NHẬP BẰNG COOKIES
    Sử dụng mảng Cookie JSON có sẵn để khôi phục phiên làm việc.
    """
    async def login(
        self, 
        browser: IBrowserService, 
        account: TikTokAccount,
        step_logger: Optional[Any] = None,
        email_service: Optional[IEmailService] = None,
        custom_avatar_path: Optional[str] = None
    ) -> bool:
        if not account.cookies:
            if step_logger:
                await step_logger("[-] Tài khoản không chứa dữ liệu Cookies để đăng nhập.")
            return False
        
        await browser.navigate_to("https://www.tiktok.com/?lang=en")
        
        if step_logger:
            await step_logger("Đang dọn sạch cache & nạp mảng Cookies JSON vào trình duyệt...")
        await browser.inject_cookies(account.cookies)
        
        await browser.navigate_to("https://www.tiktok.com/?lang=en")
        await asyncio.sleep(3)
        
        is_logged_in = await browser.check_login_status()
        return is_logged_in


class CredentialEmailOtpLoginStrategy(ITikTokLoginStrategy):
    """
    CHIẾN LƯỢC 2: ĐĂNG NHẬP BẰNG TÀI KHOẢN + MẬT KHẨU & OTP DONGVANFB
    Mô phỏng hành động thực tế của con người, gõ phím từ từ và bóc tách thư qua OAuth2
    """
    async def login(
        self, 
        browser: IBrowserService, 
        account: TikTokAccount,
        step_logger: Optional[Any] = None,
        email_service: Optional[IEmailService] = None,
        custom_avatar_path: Optional[str] = None
    ) -> bool:
        if not account.username or not account.password:
            if step_logger:
                await step_logger("[-] Tài khoản thiếu Username hoặc Password để đăng nhập Form.")
            return False

        # Bước 1: Đi tới trang chủ của TikTok
        if step_logger:
            await step_logger("Đang truy cập trang chủ TikTok...")
        await browser.navigate_to("https://www.tiktok.com/?lang=en")
        await asyncio.sleep(10)

        page = browser._page

        try:
            # Bước 2: Bấm vào nút Log in ngoài trang chủ của TikTok
            if step_logger:
                await step_logger("Đang tìm và nhấp vào nút Log in ngoài trang chủ...")
            login_home_btn = page.locator('div.TUXButton-content:has-text("Log in"), div.TUXButton-label:has-text("Log in")')
            await login_home_btn.first.wait_for(state="visible", timeout=15000)
            await login_home_btn.first.click()
            await asyncio.sleep(10) 

            # Bước 3: Nhấp vào nút "Use phone or email" trên cửa sổ popup
            if step_logger:
                await step_logger("Đang chọn phương thức 'Use phone or email'...")
            channel_btn = page.locator('[data-e2e="channel-item"]').filter(has_text="Use phone")
            await channel_btn.first.wait_for(state="visible", timeout=10000)
            await channel_btn.first.click()
            await asyncio.sleep(2.5) 

            # Bước 4: Nhấp vào nút chuyển tab "Use email or username"
            if step_logger:
                await step_logger("Đang chuyển sang tab 'Use email or username'...")
            tab_btn = page.locator('a[href*="/login/phone-or-email/email"], a:has-text("Use email or username"), .elfe54h0, span:has-text("Username or email")')
            await tab_btn.first.wait_for(state="visible", timeout=10000)
            await tab_btn.first.click()
            await asyncio.sleep(3) 

            # Bước 5: Điền Email/Username từ từ từng phím một (delay 120ms)
            if step_logger:
                await step_logger(f"Đang tự động gõ Email: {account.username}...")
            email_input = page.locator('input[placeholder*="Email"], input[name="username"], .eapcad11')
            await email_input.first.wait_for(state="visible", timeout=10000)
            await email_input.first.click()
            await asyncio.sleep(1.2) 
            
            await email_input.first.press_sequentially(account.username, delay=120)
            await asyncio.sleep(1.5)

            # Bước 6: Điền Password từ từ từng phím một (delay 140ms)
            if step_logger:
                await step_logger("Đang nhập Password từ từ...")
            pass_input = page.locator('input[type="password"], [placeholder="Password"]')
            await pass_input.first.wait_for(state="visible", timeout=10000)
            await pass_input.first.click()
            await asyncio.sleep(1.0) 
            
            await pass_input.first.press_sequentially(account.password, delay=140)
            await asyncio.sleep(2.0)

            # Bước 7: Bấm nút Log in để gửi thông tin
            if step_logger:
                await step_logger("Đang gửi lệnh Đăng nhập...")
            login_btn = page.locator('[data-e2e="login-button"]')
            await login_btn.first.wait_for(state="visible", timeout=10000)
            await login_btn.first.click()
            await asyncio.sleep(15) # Đợi TikTok điều hướng và xử lý captcha

            # =================================================================
            # BƯỚC 8: BỘ PHÁT HIỆN MÀN HÌNH ĐA NHÁNH (Xử lý các tình huống OTP)
            # =================================================================
            
            # Nhánh A: Nhánh hiển thị Danh sách phương thức nhận OTP (pc-home-item có chữ Email)
            email_channel_locator = page.locator('[class*="pc-home-item"], .pc-home-item-IxNc0F').filter(has_text="Email")
            
            # Nhánh B: Nhánh hiển thị trực tiếp ô điền mã xác thực 6 số
            direct_otp_locator = page.locator('input[placeholder*="code"], input.tux-input__element-zY3KBY')

            is_email_channel_active = False
            is_direct_otp_active = False

            # Quét liên tục trong tối đa 10 giây để phát hiện màn hình tương ứng
            for _ in range(10):
                if await email_channel_locator.count() > 0 and await email_channel_locator.first.is_visible():
                    is_email_channel_active = True
                    break
                if await direct_otp_locator.count() > 0 and await direct_otp_locator.first.is_visible():
                    is_direct_otp_active = True
                    break
                await asyncio.sleep(1)

            # 8.1 XỬ LÝ NHÁNH A: Chọn phương thức gửi OTP qua Email
            if is_email_channel_active:
                if step_logger:
                    await step_logger("Phát hiện màn hình chọn hòm thư nhận mã. Đang nhấp chọn Email...")
                # Nhấp trực tiếp vào khoang Email tĩnh bạn cung cấp
                await email_channel_locator.first.click()
                await asyncio.sleep(4) # Đợi TikTok gửi mail và load trang nhập mã
                # Chuyển trạng thái để luồng sau tự động lấy và điền OTP
                is_direct_otp_active = True

            # 8.2 XỬ LÝ NHÁNH B: Bóc tách mã OTP từ dongvanfb và gõ xác minh
            if is_direct_otp_active:
                otp_input = page.locator('input[placeholder*="code"], input.tux-input__element-zY3KBY, input[placeholder="Enter 6-digit code"]')
                await otp_input.first.wait_for(state="visible", timeout=10000)

                if not account.email or not account.refresh_token or not account.client_id:
                    if step_logger:
                        await step_logger("[-] TikTok đòi OTP nhưng tài khoản thiếu cấu hình hòm thư hoặc OAuth2 tokens.")
                    return False

                if not email_service:
                    if step_logger:
                        await step_logger("[-] Email Service của DONGVANFB chưa được nạp.")
                    return False

                # =================================================================
                # ĐỒNG BỘ: Chờ đồng hồ đếm ngược gửi mã xuất hiện rồi mới gọi API
                # =================================================================
                if step_logger:
                    await step_logger("Đang đợi TikTok phát lệnh gửi mã OTP và kích hoạt đếm ngược...")
                
                # Tìm nút Resend code chuẩn theo đúng data-testid và cấu trúc class bạn cung cấp
                resend_btn = page.locator('button.tux-button__element-ZBq38f:has-text("Resend"), button:has-text("Resend"), button:has-text("Gửi lại")')
                # Đợi cho nút này được đính kèm vào DOM (attached) biểu thị đếm ngược bắt đầu chạy
                await resend_btn.first.wait_for(state="attached", timeout=15000)
                
                # Chờ thêm 2 giây trễ an toàn để email chắc chắn truyền thành công đến máy chủ DONGVANFB
                await asyncio.sleep(10)

                if step_logger:
                    await step_logger(f"Đồng hồ đếm ngược đã kích hoạt. Đang quét hòm thư {account.email} qua API dongvanfb...")
                
                # Gọi API bóc tách mã OTP của dongvanfb bằng cơ chế Polling ngầm
                otp_code = await email_service.fetch_last_tiktok_otp(
                    email=account.email, 
                    refresh_token=account.refresh_token, 
                    client_id=account.client_id
                )

                if not otp_code:
                    if step_logger:
                        await step_logger("[-] Không tìm thấy thư chứa mã OTP gửi về hòm thư của bạn.")
                    return False

                if step_logger:
                    await step_logger(f"[+] Lấy OTP thành công: {otp_code}. Đang gõ xác minh...")
                
                # Điền OTP từ từ vào ô nhập liệu
                await otp_input.first.click()
                await asyncio.sleep(0.8)
                await otp_input.first.press_sequentially(otp_code, delay=100)
                await asyncio.sleep(2.0)

                # Bấm nút Next để hoàn tất (Sử dụng đúng class tux-button__element-ZBq38f và text Next bạn cung cấp)
                if step_logger:
                    await step_logger("Đang nhấn Next để hoàn tất xác minh...")
                next_btn = page.locator('button.tux-button__element-ZBq38f:has-text("Next"), button:has-text("Next")')
                # Playwright click() sẽ tự động chờ nút Next hết trạng thái disabled (sau khi điền đủ 6 chữ số) rồi click!
                await next_btn.first.click(timeout=10000)
                await asyncio.sleep(5)

            # Bước 9: Xác minh trạng thái cuối cùng
            is_logged_in = await browser.check_login_status()
            return is_logged_in

        except Exception as e:
            if step_logger:
                await step_logger(f"Lỗi luồng đăng nhập Form: {str(e)}")
            logger.error(f"[-] Lỗi đăng nhập Form: {str(e)}")
            return False