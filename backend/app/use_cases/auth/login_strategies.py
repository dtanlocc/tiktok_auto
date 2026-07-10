import os
import asyncio
import random
import tempfile
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Dict, Any, Optional
from app.domain.ports.browser import IBrowserService
from app.domain.ports.email import IEmailService
from app.domain.entities.account import TikTokAccount
from app.core.exceptions import AccountBannedException
logger = logging.getLogger("LoginStrategies")

class ITikTokLoginStrategy(ABC):
    """Lop co so truu tuong cho moi chien luoc dang nhap TikTok"""

    @abstractmethod
    async def login(
        self,
        browser: IBrowserService,
        account: TikTokAccount,
        step_logger: Optional[Any] = None,
        email_service: Optional[IEmailService] = None,
        custom_avatar_path: Optional[str] = None
    ) -> bool:
        """Giao thuc thuc thi dang nhap chung"""
        pass


class CookieLoginStrategy(ITikTokLoginStrategy):
    """
    CHIEN LUOC 1: DANG NHAP BANG COOKIES
    Su dung mang Cookie JSON co san de khoi phuc phien lam viec.
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
                await step_logger("[-] Tai khoan khong chua du lieu Cookies de dang nhap.")
            return False

        await browser.navigate_to("https://www.tiktok.com/?lang=en")

        if step_logger:
            await step_logger("Dang don sach cache & nap mang Cookies JSON vao trinh duyet...")
        await browser.inject_cookies(account.cookies)

        await browser.navigate_to("https://www.tiktok.com/?lang=en")
        await asyncio.sleep(3)

        is_logged_in = await browser.check_login_status()
        return is_logged_in


class CredentialEmailOtpLoginStrategy(ITikTokLoginStrategy):
    """
    CHIEN LUOC 2: DANG NHAP BANG TAI KHOAN + MAT KHAU & OTP DONGVANFB
    Mo phong hanh dong thuc te cua con nguoi, go phim tu tu va boc tach thu qua OAuth2
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
                await step_logger("[-] Tai khoan thieu Username hoac Password de dang nhap Form.")
            return False

        # Buoc 1: Di toi trang chu cua TikTok
        if step_logger:
            await step_logger("Dang truy cap trang chu TikTok...")
        await browser.navigate_to("https://www.tiktok.com/?lang=en")
        await asyncio.sleep(10)

        page = browser._page

        # Moc thoi gian OTP THAT SU duoc gui - se duoc GAN LAI (ghi de) ngay tai
        # dong hanh dong nao kich hoat TikTok gui mail (xem 2 diem danh dau
        # ">>> MOC OTP" o Nhanh A va Nhanh B ben duoi). Neu vi ly do nao do khong
        # nhanh nao chay toi (truong hop la), gia tri None se khien email_service
        # tu fallback ve datetime.now() cua chinh no (kem canh bao trong log).
        otp_requested_at: Optional[datetime] = None

        try:
            # Buoc 2: Bam vao nut Log in ngoai trang chu cua TikTok
            if step_logger:
                await step_logger("Dang tim va nhap vao nut Log in ngoai trang chu...")
            login_home_btn = page.locator('div.TUXButton-content:has-text("Log in"), div.TUXButton-label:has-text("Log in")')
            await login_home_btn.first.wait_for(state="visible", timeout=15000)
            await login_home_btn.first.click()
            await asyncio.sleep(15)

            # Buoc 3: Nhap vao nut "Use phone or email" tren cua so popup
            if step_logger:
                await step_logger("Dang chon phuong thuc 'Use phone or email'...")
            channel_btn = page.locator('[data-e2e="channel-item"]').filter(has_text="Use phone")
            await channel_btn.first.wait_for(state="visible", timeout=10000)
            await channel_btn.first.click()
            await asyncio.sleep(10)

            # Buoc 4: Nhap vao nut chuyen tab "Use email or username"
            if step_logger:
                await step_logger("Dang chuyen sang tab 'Use email or username'...")
            tab_btn = page.locator('a[href*="/login/phone-or-email/email"], a:has-text("Use email or username"), .elfe54h0, span:has-text("Username or email")')
            await tab_btn.first.wait_for(state="visible", timeout=10000)
            await tab_btn.first.click()
            await asyncio.sleep(3)

            # Buoc 5: Dien Email/Username tu tu tung phim mot (delay 120ms)
            if step_logger:
                await step_logger(f"Dang tu dong go Email: {account.username}...")
            email_input = page.locator('input[placeholder*="Email"], input[name="username"], .eapcad11')
            await email_input.first.wait_for(state="visible", timeout=10000)
            await email_input.first.click()
            await asyncio.sleep(1.2)

            await email_input.first.press_sequentially(account.username, delay=random.randint(100, 200))
            await asyncio.sleep(1.5)

            # Buoc 6: Dien Password tu tu tung phim mot (delay 140ms)
            if step_logger:
                await step_logger("Dang nhap Password tu tu...")
            pass_input = page.locator('input[type="password"], [placeholder="Password"]')
            await pass_input.first.wait_for(state="visible", timeout=10000)
            await pass_input.first.click()
            await asyncio.sleep(1.0)

            await pass_input.first.press_sequentially(account.password, delay=random.randint(100, 200))
            await asyncio.sleep(2.0)

            # =================================================================
            # Buoc 7: Bam nut Log in de gui thong tin (Da co lap chong trung voi Search)
            # =================================================================
            if step_logger:
                await step_logger("Dang gui lenh Dang nhap...")

            login_btn = page.locator(
                '[data-e2e="login-button"]:visible, '
                '[data-e2e="continue-button"]:visible, '
                'div[class*="StyledLoginButton"] button:visible, '
                'div[class*="ContinueButtonWrapper"] button:visible, '
                'form button:has-text("Log in"):visible, '
                'form button:has-text("Continue"):visible, '
                'form button:has-text("Dang nhap"):visible, '
                'form button:has-text("Tiep tuc"):visible'
            )
            await login_btn.first.wait_for(state="visible", timeout=15000)
            await login_btn.first.click()
            await asyncio.sleep(10)  # Doi TikTok dieu huong va xu ly captcha

            # =================================================================
            # BUOC 8: BO PHAT HIEN MAN HINH DA NHANH (Xu ly cac tinh huong OTP)
            # =================================================================

            email_channel_locator = page.locator('[class*="pc-home-item"], .pc-home-item-IxNc0F').filter(has_text="Email")
            await asyncio.sleep(10)
            direct_otp_locator = page.locator('input[placeholder*="code"], input.tux-input__element-zY3KBY')

            is_email_channel_active = False
            is_direct_otp_active = False

            for _ in range(10):
                if await email_channel_locator.count() > 0 and await email_channel_locator.first.is_visible():
                    is_email_channel_active = True
                    break
                if await direct_otp_locator.count() > 0 and await direct_otp_locator.first.is_visible():
                    is_direct_otp_active = True
                    # >>> MOC OTP (Nhanh B): man hinh nhap OTP da hien san khi
                    # phat hien duoc, nghia la TikTok da gui mail truoc/ngay luc
                    # man hinh nay xuat hien. Day la thoi diem SOM NHAT ma code
                    # co the xac nhan chac chan viec gui da xay ra.
                    otp_requested_at = datetime.now()
                    break
                await asyncio.sleep(1)

            # 8.1 XU LY NHANH A: Chon phuong thuc gui OTP qua Email
            if is_email_channel_active:
                if step_logger:
                    await step_logger("Phat hien man hinh chon hom thu nhan ma. Dang nhap chon Email...")
                await email_channel_locator.first.click()
                # >>> MOC OTP (Nhanh A): NGAY SAU cu click nay la thoi diem
                # TikTok THAT SU phat lenh gui mail OTP. Day la moc chinh xac
                # nhat co the bat duoc trong toan bo luong dang nhap.
                otp_requested_at = datetime.now()
                await asyncio.sleep(20)  # Doi TikTok gui mail va load trang nhap ma
                is_direct_otp_active = True

            # 8.2 XU LY NHANH B: Boc tach ma OTP tu dongvanfb va go xac minh
            if is_direct_otp_active:
                otp_input = page.locator('input[placeholder*="code"], input.tux-input__element-zY3KBY, input[placeholder="Enter 6-digit code"]')
                await otp_input.first.wait_for(state="visible", timeout=10000)

                if not account.email or not account.refresh_token or not account.client_id:
                    if step_logger:
                        await step_logger("[-] TikTok doi OTP nhung tai khoan thieu cau hinh hom thu hoac OAuth2 tokens.")
                    return False

                if not email_service:
                    if step_logger:
                        await step_logger("[-] Email Service cua DONGVANFB chua duoc nap.")
                    return False

                if step_logger:
                    await step_logger("Dang doi TikTok phat lenh gui ma OTP va kich hoat dem nguoc...")

                resend_btn = page.locator('button.tux-button__element-ZBq38f:has-text("Resend"), button:has-text("Resend"), button:has-text("Gui lai")')
                await resend_btn.first.wait_for(state="attached", timeout=15000)

                await asyncio.sleep(20)

                if step_logger:
                    await step_logger(f"Dong ho dem nguoc da kich hoat. Dang quet hom thu {account.email} qua API dongvanfb...")

                if otp_requested_at is None:
                    # Truong hop cuc hiem: khong roi vao Nhanh A lan Nhanh B nao ca
                    # nhung van toi duoc day (khong nen xay ra binh thuong).
                    logger.warning("[!] Khong xac dinh duoc moc thoi gian gui OTP chinh xac, dung thoi diem hien tai lam du phong.")
                    otp_requested_at = datetime.now()

                # Goi API boc tach ma OTP cua dongvanfb, TRUYEN DUNG moc thoi gian
                # THAT SU da kich hoat gui mail (khong de service tu doan datetime.now()
                # cua chinh no, vi luc do da tre nhieu sleep() so voi thoi diem gui that).
                otp_code = await email_service.fetch_last_tiktok_otp(
                    email=account.email,
                    refresh_token=account.refresh_token,
                    client_id=account.client_id,
                    otp_requested_at=otp_requested_at,
                )

                if not otp_code:
                    if step_logger:
                        await step_logger("[-] Khong tim thay thu chua ma OTP gui ve hom thu cua ban.")
                    return False

                if step_logger:
                    await step_logger(f"[+] Lay OTP thanh cong: {otp_code}. Dang go xac minh...")

                await otp_input.first.click()
                await asyncio.sleep(0.8)
                await otp_input.first.press_sequentially(otp_code, delay=random.randint(100, 200))
                await asyncio.sleep(2.0)

                if step_logger:
                    await step_logger("Dang nhan Next de hoan tat xac minh...")
                next_btn = page.locator('button.tux-button__element-ZBq38f:has-text("Next"), button:has-text("Next")')
                await next_btn.first.click(timeout=10000)
                await asyncio.sleep(25)

            try:
                for _ in range(3):
                    login_btn = page.locator(
                        '[data-e2e="login-button"]:visible, '
                        '[data-e2e="continue-button"]:visible, '
                        'div[class*="StyledLoginButton"] button:visible, '
                        'div[class*="ContinueButtonWrapper"] button:visible, '
                        'form button:has-text("Log in"):visible, '
                        'form button:has-text("Continue"):visible, '
                        'form button:has-text("Dang nhap"):visible, '
                        'form button:has-text("Tiep tuc"):visible'
                    )
                    await login_btn.first.wait_for(state="visible", timeout=1000)
                    await login_btn.first.click()
                    asyncio.sleep(2)
            except:
                pass

            is_logged_in = await browser.check_login_status()
            return is_logged_in

        except AccountBannedException as e_ban:
            raise e_ban

        except Exception as e:
            if step_logger:
                await step_logger(f"Loi luong dang nhap Form: {str(e)}")
            logger.error(f"[-] Loi dang nhap Form: {str(e)}")
            return False