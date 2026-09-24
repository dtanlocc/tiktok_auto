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
from app.core.tiktok_cookies import has_tiktok_auth_cookies
logger = logging.getLogger("LoginStrategies")


_PAGE_LOAD_STATE_JS = r"""() => {
  const res = performance.getEntriesByType('resource');
  const lastEnd = res.reduce((m, r) => Math.max(m, r.responseEnd || 0), 0);
  return {ready: document.readyState, quietMs: Math.round(performance.now() - lastEnd)};
}"""


async def _wait_page_fully_loaded(
    page, step_logger=None, what: str = "trang", timeout_seconds: float = 30.0,
    quiet_ms: int = 1500,
) -> bool:
    """Wait for the page's load event plus a quiet network before touching it.

    ⛔ THE FORM SHOWS LONG BEFORE THE PAGE IS DONE. Measured 2026-09-18 on the
    email login page: the fields appeared at ~1-2s ("interactive"), scripts
    kept arriving (28 -> 38) and the load event came at 18.4s. Typing at 1-2s
    was wiped when the late scripts re-set the form. Through a proxy whose CDN
    is blocked the page can sit at "interactive" for good, hence the cap.
    """
    if step_logger:
        await step_logger(f"Dang cho {what} tai xong hoan toan truoc khi thao tac...")
    loop = asyncio.get_running_loop()
    started = loop.time()
    deadline = started + timeout_seconds
    complete_since: Optional[float] = None
    while True:
        try:
            state = await page.evaluate(_PAGE_LOAD_STATE_JS)
            if state.get("ready") == "complete":
                complete_since = complete_since if complete_since is not None else loop.time()
                # A feed that streams video is never quiet; once loaded, a few
                # seconds of settling is enough.
                if state.get("quietMs", 0) >= quiet_ms or loop.time() - complete_since >= 3.0:
                    logger.info("[Login] %s tai xong sau %.1fs.", what, loop.time() - started)
                    return True
            else:
                complete_since = None
        except Exception:
            pass   # the page may be navigating; look again
        if loop.time() >= deadline:
            logger.warning("[Login] %s chua tai xong sau %.0fs; van tiep tuc.", what, timeout_seconds)
            if step_logger:
                await step_logger(
                    f"[!] {what[:1].upper() + what[1:]} chua tai xong sau {timeout_seconds:.0f}s; "
                    "van tiep tuc (se kiem tra lai noi dung form sau khi go)."
                )
            return False
        await asyncio.sleep(0.5)


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

    # ⛔ THE FOR YOU MODAL IS THE MAIN WAY IN; the direct URL below is only
    # the fallback. Measured 2026-09-18 through 209.145.57.39: with For You
    # fully loaded, Log in -> the "Log in to TikTok" modal came ~6s later ->
    # "Use phone or email" ([data-e2e=channel-item]) -> "Use email or
    # username" -> both fields, all inside the modal. Clicking Log in before
    # the page had loaded, or looking for the modal after 1.5s, missed it.
    try:
        if step_logger:
            await step_logger("Dang tim va nhap vao nut Log in ngoai trang chu...")
        login_home_btn = page.locator(
            '[data-e2e="nav-login-button"]:visible, '
            '[data-e2e="top-login-button"]:visible, '
            'div.TUXButton-content:has-text("Log in"), '
            'div.TUXButton-label:has-text("Log in"), '
            'button:has-text("Log in"):visible'
        )
        await login_home_btn.first.wait_for(state="visible", timeout=15000)
        login_modal = page.locator('[data-e2e="login-modal"]')
        for press in range(2):
            await login_home_btn.first.click()
            if await _wait_visible(login_modal, timeout_seconds=15.0):
                break
            if step_logger and press == 0:
                await step_logger("Chua thay hop thoai Log in; bam Log in lai...")

        direct = await visible_email_input(2000)
        if direct is not None:
            return page, direct

        if step_logger:
            await step_logger("Dang chon phuong thuc 'Use phone or email'...")
        channel_btn = page.locator('[data-e2e="channel-item"]').filter(
            has_text=re.compile(r"use phone", re.I)
        )
        await channel_btn.first.wait_for(state="visible", timeout=15000)
        await channel_btn.first.click()
        await asyncio.sleep(1.5)

        direct = await visible_email_input(3000)
        if direct is not None:
            return page, direct

        if step_logger:
            await step_logger("Dang chuyen sang tab 'Use email or username'...")
        tab_btn = page.locator(
            'a[href*="/login/phone-or-email/email"], '
            'a:has-text("Use email or username"), .elfe54h0, '
            'span:has-text("Username or email")'
        )
        await tab_btn.first.wait_for(state="visible", timeout=15000)
        await tab_btn.first.click()
        await asyncio.sleep(1.5)

        email_input = await visible_email_input(15000)
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
        # ⛔ NO `delay=`: the engine draws a keystroke rhythm - dwell and gap
        # per character - from this session's seed, and a `delay` REPLACES it
        # with one flat interval. Every install that passed a number shared
        # that interval, which is a key linking them; a password field is
        # exactly where a site already listens (_juggler/keyboard.py).
        await field.first.press_sequentially(text)
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
    r"internal server error|something went wrong|"
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


#: TikTok's own hiccups: nothing is wrong with the account, pressing Log in again helps.
_TRANSIENT_LOGIN_ERROR = re.compile(
    r"internal server error|something went wrong|network error|server is busy|"
    r"please try again later|l[oỗ]i m[aá]y ch[uủ]",
    re.I,
)
#: Refusals where pressing again only burns attempts or cannot help.
_FINAL_LOGIN_ERROR = re.compile(
    r"maximum number of attempts|too many attempts|attempts remaining|"
    r"incorrect|doesn.?t exist|kh[oô]ng t[oồ]n t[aạ]i|sai (m[aậ]t kh[aẩ]u|t[aà]i kho[aả]n)",
    re.I,
)


async def _page_summary(page, limit: int = 200) -> str:
    """The first words the account is looking at, for a failure that has none."""
    try:
        text = str(await page.evaluate("() => document.body.innerText"))
        return " ".join(text.split())[:limit]
    except Exception:
        return "(khong doc duoc noi dung trang)"


def is_transient_login_error(message: str) -> bool:
    return bool(
        message
        and _TRANSIENT_LOGIN_ERROR.search(message)
        and not _FINAL_LOGIN_ERROR.search(message)
    )


async def _submit_login(
    page, browser, login_btn, step_logger=None, transient_retries: int = 1,
    refill=None,
) -> str:
    """Press Log in and return TikTok's refusal, or "" once it moved on.

    "Internal server error. Please try again later." (mo91trow4_spau,
    2026-09-18) is TikTok's server, not the account: press again, with a pause.
    A wrong password or a locked account is never pressed again - every
    press spends one of the few attempts TikTok allows.

    ⛔ AND THE PRESSES THEMSELVES ARE THE BUDGET. Measured 2026-09-22 on
    LÔ_20260922: press one answered "Internal server error", the immediate
    second press answered "Maximum number of attempts reached". Three quick
    retries spend an allowance TikTok currently counts in single digits, so
    there is ONE retry and it waits long enough to be a second try rather than
    a burst.
    """
    previous_error = ""
    for press in range(transient_retries + 1):
        if press and await _moved_past_login_form(page):
            return ""   # the last press went through while its old error line stayed
        if not await _wait_submit_enabled(login_btn):
            # A dead button is a form React had not bound yet, not a dead end.
            # Type it again now the page has had longer, and give up only if it
            # is still dead after that.
            if refill is None:
                raise RuntimeError(
                    "Nut Log in van bi khoa sau khi go Email/Password: TikTok chua nhan "
                    "thong tin dang nhap."
                )
            if step_logger:
                await step_logger(
                    "[!] Nut Log in con khoa (trang chua gan xong form); go lai Email/Password..."
                )
            await refill()
            refill = None      # one re-fill per login, never a loop
            if not await _wait_submit_enabled(login_btn, timeout_seconds=20.0):
                raise RuntimeError(
                    "Nut Log in van bi khoa sau khi go lai Email/Password: TikTok chua "
                    "nhan thong tin dang nhap."
                )
        try:
            await login_btn.first.click()
        except Exception:
            # Covered by the Email-choice dialog: TikTok already moved on.
            if await _moved_past_login_form(page):
                return ""
            raise
        await asyncio.sleep(3)  # cho trang phan hoi sau khi submit
        # Day la diem hay xuat hien captcha (geetest/slider) nhat trong luong login.
        await browser.wait_captcha_cleared(timeout=120, step_logger=step_logger)
        # After a re-press the previous line may still be on screen; only a
        # different message (or the same one after TikTok cleared it) counts.
        error = await _await_login_response(page, ignore_error=previous_error)
        if not is_transient_login_error(error) or press == transient_retries:
            return error
        previous_error = error
        if step_logger:
            await step_logger(
                f"[!] TikTok bao loi tam thoi '{error}'; bam Log in lai "
                f"(lan {press + 1}/{transient_retries})..."
            )
        await asyncio.sleep(random.uniform(20.0, 35.0))
    return error


async def _server_session(browser) -> Dict[str, str]:
    """TikTok's own answer about the browser's session (see read_session_account)."""
    reader = getattr(browser, "read_session_account", None)
    if reader is None:
        return {"state": "unknown", "username": "", "detail": ""}
    try:
        return await reader()
    except Exception as exc:
        return {"state": "unknown", "username": "", "detail": str(exc)[:120]}


def _server_session_note(server: Dict[str, str]) -> str:
    state = server.get("state")
    if state == "alive":
        return f" (May chu TikTok: phien @{server.get('username')} VAN CON hieu luc.)"
    if state == "signed_out":
        return f" (May chu TikTok: phien da bi huy/het han - '{server.get('detail')}'.)"
    return ""


async def _has_session_cookie(browser) -> bool:
    try:
        return has_tiktok_auth_cookies(await browser.extract_cookies())
    except Exception:
        return False


async def _confirm_logged_in(browser, step_logger=None, reloads: int = 3) -> tuple[bool, bool]:
    """(logged_in, page_rendered) after the login steps.

    ⛔ A BLANK PAGE IS NOT A FAILED LOGIN. valentine2212foe (2026-09-18):
    the login went through, then the proxy refused TikTok's CDN, the page
    could not paint and the whole login was reported failed. Reload a few
    times; if it still cannot render, the session cookie TikTok set after
    the credentials typed in this very browser is the proof.
    """
    last_error: Optional[AuthenticationPageNotReady] = None
    for attempt in range(reloads + 1):
        try:
            if await browser.check_login_status():
                return True, True
            # ⛔ A GUEST NAVBAR AFTER THE CODE IS NOT A FAILED LOGIN. The For
            # You login modal signs in on /foryou without reloading it, so the
            # "Log in" button painted before the modal opened is still there
            # when the check runs (adanavid168, 2026-09-18: Next pressed, the
            # login reported failed 17s later; the same account re-run was
            # signed in). Only a reload shows whether TikTok kept the session.
            if attempt == reloads or not await _has_session_cookie(browser):
                return False, True
            if step_logger:
                await step_logger(
                    "[!] Trang van hien nut Log in cu nhung trinh duyet da co phien dang nhap; "
                    f"tai lai For You (F5) de xac nhan (lan {attempt + 1}/{reloads})..."
                )
        except AuthenticationPageNotReady as exc:
            last_error = exc
            if attempt == reloads:
                break
            if step_logger:
                await step_logger(
                    f"[!] Trang sau dang nhap chua hien duoc ({str(exc)[:90]}); "
                    f"tai lai trang (F5) lan {attempt + 1}/{reloads}..."
                )
        await asyncio.sleep(random.uniform(3.0, 5.0))
        await browser.navigate_to("https://www.tiktok.com/foryou?lang=en")
    if await _has_session_cookie(browser):
        if step_logger:
            await step_logger(
                "[+] Trang van khong hien (proxy chan CDN) nhung trinh duyet da co phien "
                "dang nhap (sessionid) -> coi la dang nhap thanh cong."
            )
        logger.warning("[Login] Chap nhan dang nhap theo cookie phien vi trang khong render: %s", last_error)
        return True, False
    raise last_error


#: ⛔ NOT THE URL. The For You login modal logs in on /foryou, so "no /login in
#: the URL" was read as "already past the form" the moment Log in was pressed,
#: the wait for the Email choice was skipped and the login failed silently
#: (adanavid168, 2026-09-18). Moved on = a verification screen is showing, or
#: no login form is left on the page.
_NEXT_SCREEN_JS = r"""() => {
  const vis = el => !!(el && (el.offsetParent !== null || el.getClientRects().length));
  const text = document.body.innerText || '';
  if (/verify identity|enter 6-digit code|verification code/i.test(text)) return true;
  if ([...document.querySelectorAll('[class*="pc-home-item"], input[placeholder*="code" i]')].some(vis)) return true;
  const form = [...document.querySelectorAll(
    '[data-e2e="login-modal"], input[name="username"], input[type="password"]')].some(vis);
  return !form && !/\/login/.test(location.pathname);
}"""

#: The inputs alone, with no opinion about the URL. TikTok can close the form
#: and leave the address at /login with the feed showing behind it.
_LOGIN_INPUTS_GONE_JS = r"""() => {
  const vis = el => !!(el && (el.offsetParent !== null || el.getClientRects().length));
  return ![...document.querySelectorAll(
    '[data-e2e="login-modal"], input[name="username"], input[type="password"], '
    + 'input[placeholder*="code" i]')].some(vis);
}"""

_LOGIN_FORM_GONE_JS = r"""() => {
  const vis = el => !!(el && (el.offsetParent !== null || el.getClientRects().length));
  const form = [...document.querySelectorAll(
    '[data-e2e="login-modal"], input[name="username"], input[type="password"]')].some(vis);
  return !form && !/\/login/.test(location.pathname);
}"""


async def _moved_past_login_form(page) -> bool:
    try:
        return bool(await page.evaluate(_NEXT_SCREEN_JS))
    except Exception:
        return False


async def _await_login_response(
    page, timeout_seconds: float = 12.0, ignore_error: str = ""
) -> str:
    """After submit: TikTok's refusal message, or "" once it moved on (or time ran out).

    ⛔ NOT A SINGLE LOOK. Through a slow proxy (CDN refused 69 requests,
    2026-09-18) "Incorrect account or password. 3 attempts remaining" appeared
    after the one-off check at 3s had already found nothing.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    cleared = not ignore_error
    while True:
        # The next screen first: an old red line can stay on the page under
        # the Email-choice dialog (mo91trow4_spau, 2026-09-18).
        if await _moved_past_login_form(page):
            return ""
        error = await _login_form_error(page)
        if not error:
            cleared = True      # the same line shown again after this is a new answer
        elif error != ignore_error or cleared:
            return error
        if loop.time() >= deadline:
            return ""
        await asyncio.sleep(0.8)


#: TikTok refusing the typed code, as opposed to still checking it.
_OTP_REFUSED = re.compile(
    r"\b(code|m[aã])\b.{0,40}(expired|incorrect|invalid|wrong|h[eế]t h[aạ]n|"
    r"kh[oô]ng (ch[ií]nh x[aá]c|h[oợ]p l[eệ])|\bsai\b)|"
    r"too many attempts|maximum number of attempts",
    re.I,
)


async def _await_otp_result(page, otp_input, timeout_seconds: float = 30.0) -> str:
    """After Next: TikTok's refusal of the code, or "" once the code box is gone
    (or time ran out - the login check after this is what decides)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        try:
            text = await page.evaluate("() => document.body.innerText.slice(0, 4000)")
        except Exception:
            text = ""
        for line in str(text or "").splitlines():
            if _OTP_REFUSED.search(line):
                return line.strip()
        if not await _is_visible(otp_input):
            return ""
        if loop.time() >= deadline:
            return ""
        await asyncio.sleep(1.0)


async def _is_visible(locator) -> bool:
    try:
        return bool(await locator.count()) and await locator.first.is_visible()
    except Exception:
        return False


async def _wait_visible(locator, timeout_seconds: float) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        if await _is_visible(locator):
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(1.0)


async def _wait_verification_screen(
    page, browser, email_channel, otp_input, step_logger=None,
    timeout_seconds: float = 150.0,
) -> str:
    """After Log in: "email" (choose where to send the code), "otp" (code box
    already there), "none" (TikTok went straight on / nothing came) or
    "error:<TikTok's message>"."""
    loop = asyncio.get_running_loop()
    started = loop.time()
    next_note = started + 15.0
    dismissed = 0          # consecutive looks with no login inputs on screen
    while True:
        captcha_present = getattr(browser, "is_captcha_present", None)
        if captcha_present is not None:
            try:
                if await captcha_present():
                    await browser.wait_captcha_cleared(timeout=120, step_logger=step_logger)
                    continue
            except Exception:
                pass
        if await _is_visible(email_channel):
            return "email"
        if await _is_visible(otp_input):
            return "otp"
        error = await _login_form_error(page)
        # A server hiccup line left over from an earlier press is not a
        # refusal - that press may be the one that went through.
        if error and not is_transient_login_error(error):
            return f"error:{error}"
        try:
            if await page.evaluate(_LOGIN_FORM_GONE_JS):
                return "none"   # signed straight in, no code asked
            # ⛔ AND THE FORM CAN VANISH WITHOUT THE URL MOVING. Measured
            # 24/09/2026 on @spou70_we10shan: after the press TikTok closed
            # the login form and left the For You feed showing, signed out,
            # with the address still on /login - so the check above stayed
            # false and this wait ran its full 150s for a code screen that
            # was never coming. No inputs and no session is a login that went
            # nowhere, and it is decidable right here.
            if await page.evaluate(_LOGIN_INPUTS_GONE_JS):
                dismissed += 1
                if dismissed >= 3 and not await _has_session_cookie(browser):
                    return (
                        "error:TikTok dong form dang nhap ma khong bao loi "
                        "(quay lai trang For You, van chua dang nhap)."
                    )
            else:
                dismissed = 0
        except Exception:
            pass
        now = loop.time()
        if now - started >= timeout_seconds:
            # ⛔ SAY WHAT WAS ON SCREEN INSTEAD. Measured 24/09/2026 on
            # @stor1285: the password went through, this wait ran its full
            # 150s and reported only that the Email choice never came - so
            # nobody could tell a captcha from a different code channel from a
            # page that never finished loading. The text the account was
            # actually looking at costs one evaluate and settles it.
            on_screen = await _page_summary(page)
            logger.warning(
                "[Login] Khong thay lua chon Email / o nhap ma sau %.0fs. Man hinh dang hien: %s",
                timeout_seconds,
                on_screen,
            )
            if step_logger:
                await step_logger(
                    f"[-] Sau {timeout_seconds:.0f}s TikTok van chua hien lua chon nhan ma qua "
                    f"Email. Man hinh dang hien: {on_screen}"
                )
            return "none"
        if now >= next_note and step_logger:
            await step_logger(
                f"Dang cho TikTok hien lua chon nhan ma qua Email ({now - started:.0f}s)..."
            )
            next_note = now + 15.0
        await asyncio.sleep(1.0)


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

    #: What TikTok (or the mailbox) answered when the login did not go through.
    #: ⛔ "Đăng nhập thất bại" IS NOT A REASON. Measured 24/09/2026 on the
    #: THAITEST batch: three accounts failed for three different reasons -
    #: "Incorrect account or password. 5 attempts remaining", "Maximum number
    #: of attempts reached", and a login that was fine and only needed its
    #: 2-step code - and all three were written to the operator as the same
    #: sentence. One of them costs an attempt every time it is retried.
    last_refusal: str = ""

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
        server: Dict[str, str] = {}
        # One ordinary look, plus up to two more reloads while TikTok's server
        # still honours the session the page failed to show.
        for attempt in range(3):
            await browser.navigate_to("https://www.tiktok.com/foryou?lang=en")
            if step_logger:
                await step_logger(
                    "Đã nạp Cookies; đang chờ trang For You tải đầy đủ và ổn định "
                    "trước khi xác minh đăng nhập..."
                )
            try:
                is_logged_in = await browser.check_login_status()
            except AuthenticationPageNotReady:
                server = await _server_session(browser)
                if attempt == 0:
                    if step_logger:
                        await step_logger(
                            "Trang For You chưa tải ổn định; đang mở lại một lần, "
                            "chưa xóa Cookies và chưa chuyển OTP..."
                            + _server_session_note(server)
                        )
                    continue
                if step_logger and server.get("state") != "unknown":
                    await step_logger("[!] Trang For You vẫn không tải được." + _server_session_note(server))
                raise
            if is_logged_in:
                break
            # ⛔ A GUEST-LOOKING PAGE IS NOT A DEAD COOKIE. Ask TikTok first:
            # clearing a session it still honours and logging in again by OTP
            # is what made accounts "log out when the upload started".
            server = await _server_session(browser)
            if server.get("state") != "alive" or attempt == 2:
                break
            if step_logger:
                await step_logger(
                    "[!] Trang hien nhu chua dang nhap nhung may chu TikTok xac nhan phien "
                    f"@{server.get('username')} van con hieu luc; tai lai For You "
                    f"(lan {attempt + 1}/2), giu nguyen Cookies..."
                )
        if not is_logged_in:
            if server.get("state") == "alive":
                raise AuthenticationPageNotReady(
                    f"TikTok xác nhận phiên @{server.get('username')} vẫn còn hiệu lực "
                    "nhưng trang For You không hiện giao diện đã đăng nhập sau 3 lần tải. "
                    "Giữ nguyên Cookies, không login OTP; thử lại sau hoặc đổi proxy."
                )
            if step_logger and server.get("state") == "signed_out":
                await step_logger(
                    "[-] May chu TikTok xac nhan phien Cookies da bi huy/het han "
                    f"('{server.get('detail')}') -> can dang nhap lai."
                )
            return False

        identity_validator = getattr(
            browser, "validate_authenticated_identity", None
        )
        if identity_validator is not None:
            identity_matches = await identity_validator(account.username)
            if not identity_matches:
                # The nav may not name the account yet; the server does.
                server = await _server_session(browser)
                expected = str(account.username or "").lstrip("@").casefold()
                if (
                    server.get("state") == "alive"
                    and expected
                    and server.get("username", "").casefold() == expected
                ):
                    return True
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
        page = browser._page
        # Its "Log in" button opens the login modal only once the feed's scripts
        # have loaded: ~45s through 209.145.57.39 (2026-09-18). A 10s cap made
        # the click open nothing and every login fell back to the direct URL.
        await _wait_page_fully_loaded(
            page, step_logger, "trang chu TikTok", timeout_seconds=60.0
        )

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
            # Nothing is typed until the login page has fully loaded: typing
            # while its scripts were still arriving was wiped.
            # ⛔ 30s IS NOT ENOUGH ON A THROTTLED ROUTE. These proxies refuse
            # tens of CDN requests under load, so the form paints late and is
            # still unbound when the old cap expired: the typed text went
            # nowhere and the login died on "Log in is still disabled"
            # (LÔ_20260922 through 151.244.238.42, 2026-09-22).
            await _wait_page_fully_loaded(
                page, step_logger, "trang dang nhap", timeout_seconds=90.0
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
            # Captcha after submit is waited out inside; transient server
            # errors are pressed again.
            async def refill_login_form():
                await _fill_login_form(
                    page, email_input, pass_input, login_identifier, account.password,
                    step_logger=step_logger,
                )

            form_error = await _submit_login(
                page, browser, login_btn, step_logger, refill=refill_login_form
            )

            # TikTok refuses on the form itself. "Account doesn't exist" for the
            # imported email while @username is alive (treft21664, 2026-09-18):
            # the email is not a login for that account, the username may be.
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
                form_error = await _submit_login(page, browser, login_btn, step_logger)
            if form_error:
                self.last_refusal = form_error
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
            direct_otp_locator = page.locator(
                'input[placeholder*="code" i], input.tux-input__element-zY3KBY, '
                'input[placeholder="Enter 6-digit code"]'
            )

            # ⛔ WAIT FOR THE EMAIL CHOICE, DO NOT GLANCE FOR IT. After Log in
            # TikTok can take a long while to offer "Email" as the place to
            # send the code (mo91trow4_spau, 2026-09-18); looking for 10s and
            # moving on skipped the whole OTP step and failed the login.
            verify_screen = await _wait_verification_screen(
                page, browser, email_channel_locator, direct_otp_locator, step_logger
            )
            if verify_screen.startswith("error:"):
                message = verify_screen[len("error:"):]
                self.last_refusal = message
                if step_logger:
                    await step_logger(f"[-] TikTok tu choi dang nhap: {message}")
                return False
            is_email_channel_active = verify_screen == "email"
            is_direct_otp_active = verify_screen == "otp"
            if is_direct_otp_active:
                # >>> MOC OTP (Nhanh B): man hinh nhap OTP da hien san khi
                # phat hien duoc, nghia la TikTok da gui mail truoc/ngay luc
                # man hinh nay xuat hien.
                otp_requested_at = datetime.now()

            # 8.1 XU LY NHANH A: Chon phuong thuc gui OTP qua Email
            if is_email_channel_active:
                if step_logger:
                    await step_logger("Phat hien man hinh chon hom thu nhan ma. Dang nhan chon Email...")
                for press in range(1, 4):
                    await email_channel_locator.first.click()
                    # >>> MOC OTP (Nhanh A): NGAY SAU cu click nay la thoi diem
                    # TikTok THAT SU phat lenh gui mail OTP.
                    otp_requested_at = datetime.now()
                    # Co the dinh captcha sau khi chon Email -> cho extension solver xu ly.
                    await browser.wait_captcha_cleared(timeout=120, step_logger=step_logger)
                    # Only once the code box is there has TikTok sent the mail.
                    if await _wait_visible(direct_otp_locator, timeout_seconds=45.0):
                        break
                    if not await _is_visible(email_channel_locator):
                        break
                    if step_logger:
                        await step_logger(
                            f"[!] Da nhan Email nhung chua thay o nhap ma; nhan lai Email (lan {press + 1})..."
                        )
                if not await _is_visible(direct_otp_locator):
                    if step_logger:
                        await step_logger("[-] Da chon Email nhung TikTok khong hien o nhap ma OTP.")
                    self.last_refusal = 'Da chon Email nhung TikTok khong hien o nhap ma OTP.'
                    return False
                is_direct_otp_active = True

            # 8.2 XU LY NHANH B: Boc tach ma OTP tu HOM THU (Microsoft Graph) va go xac minh
            if is_direct_otp_active:
                otp_input = page.locator('input[placeholder*="code"], input.tux-input__element-zY3KBY, input[placeholder="Enter 6-digit code"]')
                await otp_input.first.wait_for(state="visible", timeout=10000)

                if not account.email or not account.refresh_token or not account.client_id:
                    if step_logger:
                        await step_logger("[-] TikTok doi OTP nhung tai khoan thieu cau hinh hom thu hoac OAuth2 tokens.")
                    self.last_refusal = 'Thieu cau hinh hom thu / OAuth2 token de lay OTP.'
                    return False

                if not email_service:
                    if step_logger:
                        await step_logger("[-] Email Service cua DONGVANFB chua duoc nap.")
                    self.last_refusal = 'Email service chua duoc nap, khong lay duoc OTP.'
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
                    self.last_refusal = 'Khong tim thay thu chua ma OTP trong hom thu.'
                    return False

                if step_logger:
                    await step_logger(f"[+] Lay OTP thanh cong: {otp_code}. Dang go xac minh...")

                await otp_input.first.click()
                await asyncio.sleep(0.8)
                # No `delay=`: the engine's own per-session rhythm types this (a flat `delay=` would replace it with one interval every install shares).
                await otp_input.first.press_sequentially(otp_code)
                await asyncio.sleep(2.0)

                if step_logger:
                    await step_logger("Dang nhan Next de hoan tat xac minh...")
                next_btn = page.locator(
                    'button.tux-button__element-ZBq38f:has-text("Next"):visible, '
                    'button:has-text("Next"):visible'
                )
                await next_btn.first.click(timeout=10000)
                await asyncio.sleep(3)
                # Sau khi xac minh OTP, TikTok co the hien captcha lan nua -> cho giai.
                await browser.wait_captcha_cleared(timeout=120, step_logger=step_logger)
                otp_error = await _await_otp_result(page, otp_input)
                if otp_error:
                    self.last_refusal = f"TikTok tu choi ma OTP: {otp_error}"
                    if step_logger:
                        await step_logger(f"[-] TikTok tu choi ma OTP {otp_code}: {otp_error}")
                    logger.error("[-] TikTok tu choi ma OTP cua %s: %s", account.username, otp_error)
                    return False

            try:
                for _ in range(3):
                    login_btn = page.locator(_LOGIN_SUBMIT_SELECTOR)
                    await login_btn.first.wait_for(state="visible", timeout=1000)
                    await login_btn.first.click()
                    await asyncio.sleep(2)   # FIX: truoc day thieu 'await' -> lenh cho vo hieu
            except:
                pass

            is_logged_in, page_rendered = await _confirm_logged_in(browser, step_logger)
            if not is_logged_in and not self.last_refusal:
                # Nobody refused out loud and no code screen ever came. Keep
                # what the account was looking at, or this row reaches the
                # operator as a nameless failure like every other one.
                self.last_refusal = (
                    f"Khong vao duoc va TikTok khong bao loi. Man hinh: "
                    f"{await _page_summary(page)}"
                )
            # ⛔ NO USERNAME VERDICT HERE. The email, password and mailbox typed
            # above belong to this row, so whoever they signed into IS this
            # account; a different name means the SAVED username is out of
            # date. Refusing here (mo91trow4_spau -> @maryannfranze,
            # 2026-09-18) also skipped the username sync the login use case
            # runs next: it opens the profile with the Profile button, reads
            # the real name and updates the app. Cookie logins still check.
            identity_reader = getattr(browser, "validate_authenticated_identity", None)
            if is_logged_in and page_rendered and identity_reader is not None:
                try:
                    if not await identity_reader(account.username):
                        observed = getattr(browser, "last_observed_identity", "") or ""
                        if observed and step_logger:
                            await step_logger(
                                f"[!] Da dang nhap vao @{observed} (dang luu @{account.username}); "
                                "se mo trang Profile de doi chieu va cap nhat username trong app."
                            )
                except Exception as exc:
                    logger.debug("[Login] Khong doc duoc username sau dang nhap: %s", exc)
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
        self.last_refusal = ""

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

        credential = CredentialEmailOtpLoginStrategy()
        result = await credential.login(
            browser, account, step_logger=step_logger, email_service=email_service
        )
        # The reason belongs to whoever asked for the login, not to the
        # strategy that happened to run.
        self.last_refusal = credential.last_refusal
        if result:
            self.last_login_method = "CREDENTIAL"
        return result
