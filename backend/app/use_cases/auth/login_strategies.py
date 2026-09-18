import os
import asyncio
import random
import re
import tempfile
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Dict, Any, Optional
from app.domain.ports.browser import IBrowserService
from app.domain.ports.email import IEmailService
from app.domain.entities.account import TikTokAccount
from app.core.exceptions import AccountBannedException, AuthenticationPageNotReady
logger = logging.getLogger("LoginStrategies")


async def _open_email_login_form(
    browser: IBrowserService,
    step_logger: Optional[Any] = None,
):
    """Open TikTok's email form across both current login UI variants."""
    page = browser._page
    email_selector = (
        'input[placeholder*="Email"], input[name="username"], '
        'input[autocomplete="username"], .eapcad11'
    )

    async def visible_email_input(timeout: int):
        candidate = page.locator(email_selector)
        try:
            await candidate.first.wait_for(state="visible", timeout=timeout)
            return candidate
        except Exception:
            return None

    existing = await visible_email_input(1500)
    if existing is not None:
        return page, existing

    try:
        if step_logger:
            await step_logger("Dang tim va nhap vao nut Log in ngoai trang chu...")
        login_home_btn = page.locator(
            'div.TUXButton-content:has-text("Log in"), '
            'div.TUXButton-label:has-text("Log in"), '
            '[data-e2e="nav-login-button"]:visible, '
            'button:has-text("Log in"):visible'
        )
        await login_home_btn.first.wait_for(state="visible", timeout=8000)
        await login_home_btn.first.click()
        await asyncio.sleep(1.5)

        direct = await visible_email_input(2000)
        if direct is not None:
            return page, direct

        if step_logger:
            await step_logger("Dang chon phuong thuc 'Use phone or email'...")
        channel_btn = page.locator('[data-e2e="channel-item"]').filter(
            has_text="Use phone"
        )
        await channel_btn.first.wait_for(state="visible", timeout=10000)
        await channel_btn.first.click()
        await asyncio.sleep(1.5)

        direct = await visible_email_input(2000)
        if direct is not None:
            return page, direct

        if step_logger:
            await step_logger("Dang chuyen sang tab 'Use email or username'...")
        tab_btn = page.locator(
            'a[href*="/login/phone-or-email/email"], '
            'a:has-text("Use email or username"), .elfe54h0, '
            'span:has-text("Username or email")'
        )
        await tab_btn.first.wait_for(state="visible", timeout=10000)
        await tab_btn.first.click()
        await asyncio.sleep(1.5)

        email_input = await visible_email_input(10000)
        if email_input is not None:
            return page, email_input
    except Exception as exc:
        logger.info("[Login] Home login UI unavailable; using direct email form: %s", exc)

    if step_logger:
        await step_logger(
            "Khong thay nut Log in tren For You; dang mo truc tiep trang dang nhap Email..."
        )
    await browser.navigate_to(
        "https://www.tiktok.com/login/phone-or-email/email?lang=en&enter_method=direct"
    )
    page = browser._page
    email_input = page.locator(email_selector)
    await email_input.first.wait_for(state="visible", timeout=20000)
    return page, email_input

_LOGIN_SUBMIT_SELECTOR = (
    '[data-e2e="login-button"]:visible, '
    '[data-e2e="continue-button"]:visible, '
    'div[class*="StyledLoginButton"] button:visible, '
    'div[class*="ContinueButtonWrapper"] button:visible, '
    'form button:has-text("Log in"):visible, '
    'form button:has-text("Continue"):visible, '
    'form button:has-text("Dang nhap"):visible, '
    'form button:has-text("Tiep tuc"):visible'
)


async def _type_login_field(page, field, text: str, label: str, attempts: int = 3) -> None:
    """Type into a login field with real key presses and check TikTok kept it.

    ⛔ NOT fill(). On TikTok's email form (2026-09-18, 5 accounts on
    209.145.57.39) fill() left the "Email or username" field EMPTY while the
    password kept its value, so "Log in" stayed disabled and every OTP login
    died on "not actionable ... missing enabled". The same text typed key by
    key filled both fields and enabled the button.
    """
    for attempt in range(1, attempts + 1):
        await field.first.click()
        await asyncio.sleep(0.4)
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        await field.first.press_sequentially(text, delay=random.randint(60, 130))
        await asyncio.sleep(0.6)
        try:
            if await field.first.input_value() == text:
                return
        except Exception:
            pass
        logger.warning("[Login] O %s chua giu dung noi dung sau lan go %d; go lai.", label, attempt)
    raise RuntimeError(f"TikTok khong giu noi dung o {label} sau {attempts} lan go.")


async def _fill_login_form(
    page, email_input, pass_input, identifier: str, password: str,
    step_logger=None, rounds: int = 3, hold_seconds: float = 3.0,
) -> None:
    """Type both fields, then make sure the form still holds them once settled.

    ⛔ THE FORM IS REBUILT AFTER IT APPEARS. On the direct email login page
    (2026-09-18, treft21664) both fields were typed and read back correctly,
    then 1.5-3s later TikTok re-mounted the form and BOTH were empty again -
    "Log in" stayed disabled. The original "email empty, password kept" was
    the same thing: the email was typed before the rebuild, the password after.
    """
    for round_no in range(1, rounds + 1):
        await _type_login_field(page, email_input, identifier, "Email")
        await asyncio.sleep(0.5)
        await _type_login_field(page, pass_input, password, "Password")
        kept = True
        loop = asyncio.get_running_loop()
        deadline = loop.time() + hold_seconds
        while loop.time() < deadline:
            await asyncio.sleep(0.5)
            try:
                kept = (
                    await email_input.first.input_value() == identifier
                    and await pass_input.first.input_value() == password
                )
            except Exception:
                kept = False
            if not kept:
                break
        if kept:
            return
        logger.warning("[Login] TikTok xoa noi dung form dang nhap (lan %d); go lai.", round_no)
        if step_logger:
            await step_logger("[!] TikTok vua tai lai form dang nhap va xoa noi dung; dang go lai...")
    raise RuntimeError(
        f"TikTok xoa noi dung form dang nhap {rounds} lan lien tiep; khong gui duoc Email/Password."
    )


_MASKED_EMAIL = re.compile(r"([A-Za-z0-9._%+-]*\*+[A-Za-z0-9._%+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")


def masked_email_matches(masked: str, email: str) -> bool:
    """Could TikTok's "g***1@hotmail.com" be this mailbox?"""
    try:
        masked_local, masked_domain = masked.lower().rsplit("@", 1)
        local, domain = email.lower().strip().rsplit("@", 1)
    except ValueError:
        return True   # cannot tell: do not block the login on it
    prefix = masked_local.split("*", 1)[0]
    suffix = masked_local.rsplit("*", 1)[-1]
    return domain == masked_domain and local.startswith(prefix) and local.endswith(suffix)


async def _otp_destination(page) -> str:
    """The masked address TikTok says it sent the code to, or ""."""
    try:
        text = await page.evaluate("() => document.body.innerText.slice(0, 4000)")
    except Exception:
        return ""
    match = _MASKED_EMAIL.search(str(text or ""))
    return match.group(1) if match else ""


#: The red line TikTok prints under the login form when it refuses a login.
_ACCOUNT_MISSING = re.compile(r"account doesn.?t exist|t[aà]i kho[aả]n kh[oô]ng t[oồ]n t[aạ]i", re.I)
_LOGIN_FORM_ERRORS = re.compile(
    r"account doesn.?t exist|incorrect (account|username|email|password)|"
    r"maximum number of attempts|too many attempts|try again later|"
    r"t[aà]i kho[aả]n kh[oô]ng t[oồ]n t[aạ]i|sai (m[aậ]t kh[aẩ]u|t[aà]i kho[aả]n)",
    re.I,
)


async def _login_form_error(page) -> str:
    """TikTok's refusal message on the login page, or "".

    The whole page, not <form>: on the email login page the <form> element
    holds only "Forgot password? / Log in"; the fields and the red error line
    sit outside it (bren49_ki63, 2026-09-18 - the error was missed).
    """
    try:
        text = await page.evaluate("() => document.body.innerText.slice(0, 4000)")
    except Exception:
        return ""
    for line in str(text or "").splitlines():
        if _LOGIN_FORM_ERRORS.search(line):
            return line.strip()
    return ""


_NEXT_SCREEN_JS = r"""() => {
  if (!/\/login/.test(location.pathname)) return true;
  const text = document.body.innerText || '';
  return /verify identity|enter 6-digit code|verification code|x[aá]c minh/i.test(text)
      || !!document.querySelector('[class*="pc-home-item"], input[placeholder*="code" i]');
}"""


async def _await_login_response(page, timeout_seconds: float = 12.0) -> str:
    """After submit: TikTok's refusal message, or "" once it moved on (or time ran out).

    ⛔ NOT A SINGLE LOOK. Through a slow proxy (CDN refused 69 requests,
    2026-09-18) "Incorrect account or password. 3 attempts remaining" appeared
    after the one-off check at 3s had already found nothing.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        error = await _login_form_error(page)
        if error:
            return error
        try:
            if await page.evaluate(_NEXT_SCREEN_JS):
                return ""
        except Exception:
            pass
        if loop.time() >= deadline:
            return ""
        await asyncio.sleep(0.8)


async def _wait_submit_enabled(button, timeout_seconds: float = 8.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        try:
            if await button.first.is_enabled():
                return True
        except Exception:
            pass
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.4)


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

        await browser.navigate_to("https://www.tiktok.com/foryou?lang=en")

        if step_logger:
            await step_logger("Dang don sach cache & nap mang Cookies JSON vao trinh duyet...")
        await browser.inject_cookies(account.cookies)

        is_logged_in = False
        for attempt in range(2):
            await browser.navigate_to("https://www.tiktok.com/foryou?lang=en")
            if step_logger:
                await step_logger(
                    "Đã nạp Cookies; đang chờ trang For You tải đầy đủ và ổn định "
                    "trước khi xác minh đăng nhập..."
                )
            try:
                is_logged_in = await browser.check_login_status()
                break
            except AuthenticationPageNotReady:
                if attempt == 0:
                    if step_logger:
                        await step_logger(
                            "Trang For You chưa tải ổn định; đang mở lại một lần, "
                            "chưa xóa Cookies và chưa chuyển OTP..."
                        )
                    continue
                raise
        if not is_logged_in:
            return False

        identity_validator = getattr(
            browser, "validate_authenticated_identity", None
        )
        if identity_validator is not None:
            identity_matches = await identity_validator(account.username)
            if not identity_matches:
                if step_logger:
                    await step_logger(
                        "[!] Cookies không xác minh được đúng username; chuyển sang OTP."
                    )
                return False
        return True


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
        login_identifier = account.email or account.username
        missing_fields = []
        if not login_identifier:
            missing_fields.append("Email/Username")
        if not account.password:
            missing_fields.append("Password")
        if missing_fields:
            if step_logger:
                await step_logger(
                    "[-] Không thể fallback OTP: tài khoản thiếu "
                    + " và ".join(missing_fields)
                    + "."
                )
            return False

        # Buoc 1: Di toi trang chu cua TikTok
        if step_logger:
            await step_logger("Dang truy cap trang chu TikTok...")
        await browser.navigate_to("https://www.tiktok.com/foryou?lang=en")
        await asyncio.sleep(2)   # settle nhe; buoc sau da co wait_for theo trang thai

        page = browser._page

        # Moc thoi gian OTP THAT SU duoc gui - se duoc GAN LAI (ghi de) ngay tai
        # dong hanh dong nao kich hoat TikTok gui mail (xem 2 diem danh dau
        # ">>> MOC OTP" o Nhanh A va Nhanh B ben duoi). Neu vi ly do nao do khong
        # nhanh nao chay toi (truong hop la), gia tri None se khien email_service
        # tu fallback ve datetime.now() cua chinh no (kem canh bao trong log).
        otp_requested_at: Optional[datetime] = None

        try:
            # Buoc 2-4: TikTok co luc hien modal tren For You, co luc bo han
            # nut Log in khoi DOM. Ho tro ca hai va fallback sang URL email truc tiep.
            page, email_input = await _open_email_login_form(
                browser, step_logger=step_logger
            )

            # Buoc 5: Dien EMAIL tu tu tung phim mot (delay 120ms).
            # Dung account.email de dang nhap (thay vi username) - on dinh hon.
            # Fallback ve username neu account thieu email.
            if step_logger:
                await step_logger(f"Dang tu dong go Email dang nhap: {login_identifier}...")
            await email_input.first.wait_for(state="visible", timeout=10000)

            # Buoc 6: Dien Password tu tu tung phim mot
            if step_logger:
                await step_logger("Dang nhap Password tu tu...")
            pass_input = page.locator('input[type="password"], [placeholder="Password"]')
            await pass_input.first.wait_for(state="visible", timeout=10000)
            await _fill_login_form(
                page, email_input, pass_input, login_identifier, account.password,
                step_logger=step_logger,
            )

            # =================================================================
            # Buoc 7: Bam nut Log in de gui thong tin (Da co lap chong trung voi Search)
            # =================================================================
            if step_logger:
                await step_logger("Dang gui lenh Dang nhap...")

            login_btn = page.locator(_LOGIN_SUBMIT_SELECTOR)
            await login_btn.first.wait_for(state="visible", timeout=15000)
            if not await _wait_submit_enabled(login_btn):
                raise RuntimeError(
                    "Nut Log in van bi khoa sau khi go Email/Password: TikTok chua nhan "
                    "thong tin dang nhap."
                )
            await login_btn.first.click()
            await asyncio.sleep(3)  # cho trang phan hoi sau khi submit

            # ---- CAPTCHA sau submit: DUNG cho extension solver xu ly roi moi di tiep ----
            # Day la diem hay xuat hien captcha (geetest/slider) nhat trong luong login.
            await browser.wait_captcha_cleared(timeout=120, step_logger=step_logger)

            # TikTok refuses on the form itself. "Account doesn't exist" for the
            # imported email while @username is alive (treft21664, 2026-09-18):
            # the email is not a login for that account, the username may be.
            form_error = await _await_login_response(page)
            if (
                form_error
                and _ACCOUNT_MISSING.search(form_error)
                and account.username
                and login_identifier != account.username
            ):
                if step_logger:
                    await step_logger(
                        f"[!] TikTok bao '{form_error}' voi Email; thu dang nhap bang username @{account.username}..."
                    )
                await _fill_login_form(
                    page, email_input, pass_input, account.username, account.password,
                    step_logger=step_logger,
                )
                if not await _wait_submit_enabled(login_btn):
                    raise RuntimeError("Nut Log in van bi khoa sau khi go username.")
                await login_btn.first.click()
                await asyncio.sleep(3)
                await browser.wait_captcha_cleared(timeout=120, step_logger=step_logger)
                form_error = await _await_login_response(page)
            if form_error:
                if step_logger:
                    await step_logger(f"[-] TikTok tu choi dang nhap: {form_error}")
                logger.error("[-] TikTok tu choi dang nhap %s: %s", account.username, form_error)
                return False

            # THOAT SOM neu nick da bi BAN (khong phi thoi gian qua cac buoc OTP).
            # Ngoai le se duoc use case bat -> ghi health_status=BANNED nhu thuong.
            if await browser.is_account_banned():
                if step_logger:
                    await step_logger("[!] Phat hien tai khoan bi BAN ngay sau dang nhap.")
                raise AccountBannedException("Tai khoan bi ban (phat hien sau khi submit dang nhap).")

            # =================================================================
            # BUOC 8: BO PHAT HIEN MAN HINH DA NHANH (Xu ly cac tinh huong OTP)
            # =================================================================

            email_channel_locator = page.locator('[class*="pc-home-item"], .pc-home-item-IxNc0F').filter(has_text="Email")
            await asyncio.sleep(2)   # trang chuyen man; vong poll ben duoi da cho tiep
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
                # Co the dinh captcha sau khi chon Email -> cho extension solver xu ly.
                await browser.wait_captcha_cleared(timeout=120, step_logger=step_logger)
                # Cho O NHAP MA hien ra (state-based) thay cho sleep(20) cung.
                await browser.wait_first_visible(
                    ['input[placeholder*="code"]', 'input.tux-input__element-zY3KBY',
                     'input[placeholder="Enter 6-digit code"]'],
                    timeout=25,
                )
                is_direct_otp_active = True

            # 8.2 XU LY NHANH B: Boc tach ma OTP tu HOM THU (Microsoft Graph) va go xac minh
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

                # KHONG can sleep(20) cho mail o day: fetch_last_tiktok_otp ben duoi
                # da tu poll 15 lan x 4s VA loc dung ma MOI theo otp_requested_at,
                # nen no tu doi mail toi. Chi settle nhe cho dem nguoc kich hoat.
                await asyncio.sleep(2)

                if step_logger:
                    await step_logger(f"Dong ho dem nguoc da kich hoat. Dang quet hom thu {account.email} truc tiep qua Microsoft Graph...")

                # TikTok names the mailbox it sent the code to. If that is not
                # this account's mailbox, no amount of polling will find the code:
                # treft21664 (2026-09-18) - code sent to g***1@hotmail.com while
                # the account's saved email was garrikbilliob@hotmail.com.
                destination = await _otp_destination(page)
                if destination and not masked_email_matches(destination, account.email):
                    message = (
                        f"[-] TikTok gui ma toi {destination}, KHONG phai hom thu cua account "
                        f"({account.email}). Email luu trong app khong phai email gan voi "
                        "tai khoan TikTok nay - can cap nhat dung email/hom thu."
                    )
                    if step_logger:
                        await step_logger(message)
                    logger.error(message)
                    return False

                if otp_requested_at is None:
                    # Truong hop cuc hiem: khong roi vao Nhanh A lan Nhanh B nao ca
                    # nhung van toi duoc day (khong nen xay ra binh thuong).
                    logger.warning("[!] Khong xac dinh duoc moc thoi gian gui OTP chinh xac, dung thoi diem hien tai lam du phong.")
                    otp_requested_at = datetime.now()

                # Goi dich vu doc OTP (mac dinh Microsoft Graph), TRUYEN DUNG moc thoi gian
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
                await asyncio.sleep(3)
                # Sau khi xac minh OTP, TikTok co the hien captcha lan nua -> cho giai.
                await browser.wait_captcha_cleared(timeout=120, step_logger=step_logger)

            try:
                for _ in range(3):
                    login_btn = page.locator(_LOGIN_SUBMIT_SELECTOR)
                    await login_btn.first.wait_for(state="visible", timeout=1000)
                    await login_btn.first.click()
                    await asyncio.sleep(2)   # FIX: truoc day thieu 'await' -> lenh cho vo hieu
            except:
                pass

            is_logged_in = await browser.check_login_status()
            identity_validator = getattr(
                browser, "validate_authenticated_identity", None
            )
            if is_logged_in and identity_validator is not None:
                is_logged_in = await identity_validator(account.username)
                if not is_logged_in and step_logger:
                    await step_logger(
                        "[!] Phiên đăng nhập không khớp username cần chạy."
                    )
            return is_logged_in

        except AccountBannedException as e_ban:
            raise e_ban

        except Exception as e:
            if step_logger:
                await step_logger(f"Loi luong dang nhap Form: {str(e)}")
            logger.error(f"[-] Loi dang nhap Form: {str(e)}")
            return False


class CookieThenCredentialLoginStrategy(ITikTokLoginStrategy):
    """
    CHIEN LUOC TONG HOP (uu tien toc do + tiet kiem OTP):
      1. Neu account CO cookies -> THU dang nhap bang Cookie truoc (nhanh, mien phi).
      2. Neu Cookie THANH CONG -> dung luon, KHONG can OTP.
      3. Neu Cookie that bai (het han/khong co) -> FALLBACK sang Credential + OTP.
      4. Neu Cookie phat hien tai khoan BANNED -> NEM luon (khong fallback vo ich).
    Dung cho luong doi Profile/Avatar de tranh login OTP khong can thiet.
    """
    def __init__(self) -> None:
        self.last_login_method: Optional[str] = None

    async def login(
        self,
        browser: IBrowserService,
        account: TikTokAccount,
        step_logger: Optional[Any] = None,
        email_service: Optional[IEmailService] = None,
        custom_avatar_path: Optional[str] = None
    ) -> bool:
        self.last_login_method = None
        # 1. Uu tien Cookie neu co
        if account.cookies:
            if step_logger:
                await step_logger("Thu dang nhap bang COOKIES truoc (tiet kiem OTP)...")
            try:
                ok = await CookieLoginStrategy().login(
                    browser, account, step_logger=step_logger, email_service=email_service
                )
                if ok:
                    self.last_login_method = "COOKIE"
                    if step_logger:
                        await step_logger("[+] Dang nhap COOKIES thanh cong -> bo qua OTP.")
                    return True
                if step_logger:
                    await step_logger(
                        "[!] Trang đã tải ổn định nhưng vẫn hiện trạng thái chưa đăng nhập; "
                        "Cookies không còn hiệu lực -> chuyển sang login OTP."
                    )
            except AccountBannedException as e_ban:
                # Banned -> khong fallback (login OTP cung se banned).
                raise e_ban
            except AuthenticationPageNotReady as exc:
                # Network/render uncertainty is not proof that the cookie is
                # expired. Keep the stored session and do not burn an OTP.
                if step_logger:
                    await step_logger(
                        "[!] Trang TikTok chưa tải ổn định để xác minh Cookies; "
                        "giữ nguyên Cookies và dừng account này, không chuyển OTP."
                    )
                raise exc
            except Exception as e:
                logger.warning(f"[!] Loi khi thu Cookie login: {str(e)} -> fallback OTP.")
                if step_logger:
                    await step_logger(f"[!] Loi login Cookie: {str(e)} -> chuyen sang OTP.")
        else:
            if step_logger:
                await step_logger("Tai khoan chua co Cookies -> dung login OTP.")

        # 2. Fallback: Credential + OTP. Cookie login may already have injected
        # an expired or identity-mismatched session into this context. Remove it
        # before typing credentials so the OTP flow always starts cleanly.
        if account.cookies:
            if step_logger:
                await step_logger("Dang xoa phien Cookies hong truoc khi login OTP...")
            await browser.clear_auth_session()

        result = await CredentialEmailOtpLoginStrategy().login(
            browser, account, step_logger=step_logger, email_service=email_service
        )
        if result:
            self.last_login_method = "CREDENTIAL"
        return result
