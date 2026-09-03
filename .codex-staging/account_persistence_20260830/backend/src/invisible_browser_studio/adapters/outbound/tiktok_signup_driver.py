from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, date, datetime
from typing import Any

from invisible_browser_studio.application.dto import (
    SignupDriverOutcome,
    SignupDriverResult,
    SignupFormInput,
)
from invisible_browser_studio.application.ports import SignupAutomationDriver
from invisible_browser_studio.domain import SignupTestPhase

from .invisible_runtime import InvisiblePlaywrightRuntime

_CAPTCHA_TEXT = re.compile(
    r"captcha|verify\s+(?:to\s+)?continue|drag\s+the\s+puzzle|security\s+check",
    re.IGNORECASE,
)
_EMAIL_REJECTED_TEXT = re.compile(
    r"(?:email|address).{0,100}(?:already\s+(?:registered|exists|used)|in\s+use|linked)"
    r"|(?:already\s+(?:registered|exists|used)|in\s+use|linked).{0,100}(?:email|address)"
    r"|you(?:'|’)?re\s+already\s+signed\s+up|already\s+signed\s+up"
    r"|try\s+(?:using\s+)?another\s+email|email\s+(?:is\s+)?not\s+available"
    r"|log\s*in\s+(?:instead|to\s+continue)",
    re.IGNORECASE,
)
_LOGIN_TEXT = re.compile(r"^\s*log\s*in\s*$", re.IGNORECASE)
_CODE_SENT_TEXT = re.compile(
    r"verification\s+code\s+(?:was\s+)?sent|code\s+sent|check\s+your\s+(?:email|inbox)",
    re.IGNORECASE,
)
_OTP_REJECTED_TEXT = re.compile(
    r"(?:verification\s+)?code.{0,80}(?:invalid|incorrect|expired|wrong)"
    r"|(?:invalid|incorrect|expired|wrong).{0,80}(?:verification\s+)?code"
    r"|too\s+many\s+(?:attempts|requests)|try\s+again\s+later",
    re.IGNORECASE,
)
_CAPTCHA_SOLVER_TIMEOUT_SECONDS = 90
_CAPTCHA_CLEAR_STABLE_SECONDS = 1.5


class TikTokSignupDriver(SignupAutomationDriver):
    """One-shot TikTok UI driver using accessibility locators, never CSS/XPath."""

    def __init__(self, runtime: InvisiblePlaywrightRuntime) -> None:
        self._runtime = runtime

    async def begin_signup(
        self,
        session_id: str,
        form: SignupFormInput,
        progress: Callable[[SignupTestPhase, str], Awaitable[None]],
    ) -> SignupDriverResult:
        page = await self._runtime.page_for(session_id)
        await self._settle(page, seconds=0.1)
        if await self._captcha_blocking(page):
            return self._result(page, SignupDriverOutcome.CAPTCHA_REQUIRED)

        if await self._open_email_signup_form(page, form.birth_date, progress):
            return self._result(page, SignupDriverOutcome.CAPTCHA_REQUIRED)
        return await self._submit_email(page, form, progress)

    async def retry_signup_email(
        self,
        session_id: str,
        form: SignupFormInput,
        progress: Callable[[SignupTestPhase, str], Awaitable[None]],
    ) -> SignupDriverResult:
        page = await self._runtime.page_for(session_id)
        await self._settle(page, seconds=0.05)
        if await self._captcha_blocking(page):
            return self._result(page, SignupDriverOutcome.CAPTCHA_REQUIRED)

        email = await self._find_field(
            page,
            labels=("Email", "Email address"),
            placeholders=("Email address", "Enter email address", "Email"),
            timeout_ms=2_000,
            required=False,
        )
        send_code = await self._find_named(
            page,
            ("Send code", "Send Code"),
            roles=("button",),
            timeout_ms=1_000,
        )
        if email is None or send_code is None:
            await progress(
                SignupTestPhase.METHOD,
                "Reopening the email signup form for the next mailbox",
            )
            if await self._open_email_signup_form(page, form.birth_date, progress):
                return self._result(page, SignupDriverOutcome.CAPTCHA_REQUIRED)
        return await self._submit_email(page, form, progress)

    async def _open_email_signup_form(
        self,
        page: Any,
        birth_date: date,
        progress: Callable[[SignupTestPhase, str], Awaitable[None]],
    ) -> bool:
        await progress(SignupTestPhase.SIGN_UP, "Clicking Sign up on the current page")
        await self._click_named(page, ("Sign up",), timeout_ms=20_000)
        await self._settle(page, seconds=0.1)
        if await self._captcha_blocking(page):
            return True

        await progress(
            SignupTestPhase.METHOD,
            "Choosing Use phone or email through its accessible name",
        )
        await self._click_named(
            page,
            (
                "Use phone or email",
                "Use phone or email / username",
                "Use phone or email or username",
            ),
            timeout_ms=20_000,
        )
        await self._settle(page, seconds=0.1)
        if await self._captcha_blocking(page):
            return True

        await progress(
            SignupTestPhase.BIRTHDAY,
            "Scrolling Month, Day and Year to the configured adult birth date",
        )
        await self._select_birth_date(page, birth_date)

        email_tab = await self._find_named(
            page,
            ("Sign up with email", "Email"),
            roles=("button", "tab", "link"),
            timeout_ms=8_000,
        )
        if email_tab is None:
            raise RuntimeError("Sign up with email was not found by accessible name")
        await email_tab.click()
        await self._settle(page, seconds=0.05)
        return await self._captcha_blocking(page)

    async def _submit_email(
        self,
        page: Any,
        form: SignupFormInput,
        progress: Callable[[SignupTestPhase, str], Awaitable[None]],
    ) -> SignupDriverResult:
        await progress(
            SignupTestPhase.EMAIL,
            "Filling the current owned Hotmail address and account password",
        )
        email = await self._find_field(
            page,
            labels=("Email", "Email address"),
            placeholders=("Email address", "Enter email address", "Email"),
            timeout_ms=15_000,
        )
        password = await self._find_field(
            page,
            labels=("Password", "Create password"),
            placeholders=("Password", "Enter password", "Create password"),
            timeout_ms=15_000,
        )
        login_count_before = await self._visible_login_count(page)
        await email.fill("")
        await email.press_sequentially(form.email, delay=35)
        await password.fill("")
        await password.press_sequentially(form.account_password, delay=55)
        await password.press("Tab")
        if await self._captcha_blocking(page):
            return self._result(page, SignupDriverOutcome.CAPTCHA_REQUIRED)
        if await self._email_rejected_visible(
            page,
            email_field=email,
            login_count_before=login_count_before,
        ):
            return SignupDriverResult(
                outcome=SignupDriverOutcome.EMAIL_REJECTED,
                message="TikTok reports that this email is already in use",
                current_url=str(page.url),
            )

        send_code = await self._find_named(
            page,
            ("Send code", "Send Code"),
            roles=("button",),
            timeout_ms=15_000,
        )
        if send_code is None:
            raise RuntimeError("Send code button was not found by accessible name")
        enable_deadline = asyncio.get_running_loop().time() + 15
        while asyncio.get_running_loop().time() < enable_deadline:
            if await send_code.is_enabled():
                break
            await asyncio.sleep(0.25)
        else:
            raise RuntimeError(
                "Send code remained disabled after entering email and password"
            )

        await progress(
            SignupTestPhase.EMAIL,
            "Clicking Send code and waiting for TikTok to acknowledge the request",
        )
        send_label_before = (await send_code.inner_text()).strip()
        requested_at = datetime.now(UTC)
        await send_code.click()

        outcome = await self._wait_after_send_code(
            page,
            email_field=email,
            send_code=send_code,
            send_label_before=send_label_before,
            login_count_before=login_count_before,
        )
        return SignupDriverResult(
            outcome=outcome,
            message=(
                "TikTok requested CAPTCHA; the automated test stopped"
                if outcome is SignupDriverOutcome.CAPTCHA_REQUIRED
                else "TikTok reports that this email is already in use"
                if outcome is SignupDriverOutcome.EMAIL_REJECTED
                else "Verification code requested successfully"
            ),
            current_url=str(page.url),
            otp_requested_at=(
                requested_at if outcome is SignupDriverOutcome.OTP_REQUESTED else None
            ),
        )

    async def finish_signup(
        self,
        session_id: str,
        *,
        otp: str,
        username: str,
        progress: Callable[[SignupTestPhase, str], Awaitable[None]],
    ) -> SignupDriverResult:
        page = await self._runtime.page_for(session_id)
        if await self._captcha_blocking(page):
            return self._result(page, SignupDriverOutcome.CAPTCHA_REQUIRED)

        await progress(SignupTestPhase.OTP, "Entering the fresh six-digit code")
        code = await self._find_field(
            page,
            labels=("Verification code", "Code"),
            placeholders=("Enter 6-digit code", "Verification code", "Code"),
            timeout_ms=15_000,
        )
        await code.fill("")
        await code.press_sequentially(otp, delay=75)
        await code.press("Tab")
        try:
            entered_code = re.sub(r"\D", "", await code.input_value())
        except Exception:
            entered_code = ""
        if entered_code != otp:
            raise RuntimeError("The OTP field did not retain all six entered digits")
        next_button = await self._find_named(
            page,
            ("Next", "Continue"),
            roles=("button",),
            timeout_ms=15_000,
        )
        if next_button is None:
            raise RuntimeError("Next was not found after entering the OTP")
        enable_deadline = asyncio.get_running_loop().time() + 10
        while asyncio.get_running_loop().time() < enable_deadline:
            if await next_button.is_enabled():
                break
            await asyncio.sleep(0.25)
        else:
            raise RuntimeError("Next remained disabled after entering the six-digit OTP")
        await progress(
            SignupTestPhase.OTP,
            "Clicking Next and waiting for TikTok to validate the code",
        )
        await next_button.click()
        validation_deadline = asyncio.get_running_loop().time() + 20
        while asyncio.get_running_loop().time() < validation_deadline:
            if await self._captcha_visible(page):
                if await self._captcha_blocking(page):
                    return self._result(page, SignupDriverOutcome.CAPTCHA_REQUIRED)
                validation_deadline = asyncio.get_running_loop().time() + 20
            if await self._text_visible(page, _OTP_REJECTED_TEXT):
                raise RuntimeError("TikTok rejected the submitted verification code")
            remaining_code = await self._find_field(
                page,
                labels=("Verification code", "Code"),
                placeholders=("Enter 6-digit code", "Verification code", "Code"),
                timeout_ms=250,
                required=False,
            )
            if remaining_code is None:
                break
            await asyncio.sleep(0.25)
        else:
            raise RuntimeError("TikTok kept the OTP form open after Next was clicked")

        await progress(
            SignupTestPhase.USERNAME,
            "Checking whether TikTok requests a username after OTP",
        )
        username_field = await self._find_field(
            page,
            labels=("Username", "TikTok ID"),
            placeholders=("Username", "Create username", "TikTok ID"),
            timeout_ms=8_000,
            required=False,
        )
        if username_field is None:
            return SignupDriverResult(
                outcome=SignupDriverOutcome.COMPLETED,
                message="OTP submitted and TikTok accepted Next",
                current_url=str(page.url),
            )
        await username_field.fill(username)
        await self._click_named(
            page,
            ("Sign up", "Next", "Continue"),
            roles=("button",),
            timeout_ms=15_000,
        )

        outcome = await self._wait_for_completion(page)
        return SignupDriverResult(
            outcome=outcome,
            message=(
                "Signup completed and TikTok left the registration dialog"
                if outcome is SignupDriverOutcome.COMPLETED
                else "TikTok requested CAPTCHA; the automated test stopped"
            ),
            current_url=str(page.url),
        )

    async def resend_otp(
        self,
        session_id: str,
        progress: Callable[[SignupTestPhase, str], Awaitable[None]],
    ) -> SignupDriverResult:
        page = await self._runtime.page_for(session_id)
        if await self._captcha_blocking(page):
            return self._result(page, SignupDriverOutcome.CAPTCHA_REQUIRED)

        await progress(
            SignupTestPhase.OTP,
            "No fresh message yet; waiting for Resend code and retrying once",
        )
        resend = await self._find_code_send_action(page, timeout_ms=45_000)
        if resend is None:
            raise RuntimeError("Send/Resend code was not available after the OTP timeout")

        enable_deadline = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < enable_deadline:
            if await resend.is_enabled():
                break
            await asyncio.sleep(0.25)
        else:
            raise RuntimeError("Resend code remained disabled after the OTP timeout")

        requested_at = datetime.now(UTC)
        send_label_before = (await resend.inner_text()).strip()
        await resend.click()
        if not await self._wait_for_code_request_ack(
            page,
            send_action=resend,
            label_before=send_label_before,
            timeout_seconds=20,
        ):
            raise RuntimeError("TikTok did not acknowledge the Send/Resend code click")
        if await self._captcha_blocking(page):
            return self._result(page, SignupDriverOutcome.CAPTCHA_REQUIRED)
        return SignupDriverResult(
            outcome=SignupDriverOutcome.OTP_REQUESTED,
            message="Verification code resent once",
            current_url=str(page.url),
            otp_requested_at=requested_at,
        )

    @staticmethod
    async def _find_code_send_action(page: Any, *, timeout_ms: int) -> Any | None:
        """Find TikTok's send action across ready and countdown labels."""
        name = re.compile(r"^\s*(?:re)?send\s+code(?:\s+.*)?$", re.IGNORECASE)
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            candidate = page.get_by_role("button", name=name).first
            try:
                if await candidate.is_visible():
                    return candidate
            except Exception:
                pass
            await asyncio.sleep(0.1)
        return None

    @staticmethod
    async def _settle(page: Any, *, seconds: float = 0.1) -> None:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=10_000)
        except Exception:
            pass
        if seconds > 0:
            await asyncio.sleep(seconds)

    async def _select_birth_date(self, page: Any, birth_date: date) -> None:
        month_names = (birth_date.strftime("%B"), birth_date.strftime("%b"))
        specs = (
            (
                ("Month",),
                month_names,
                (str(birth_date.month), f"{birth_date.month:02d}"),
                birth_date.month - 1,
            ),
            (
                ("Day",),
                (str(birth_date.day), f"{birth_date.day:02d}"),
                (str(birth_date.day), f"{birth_date.day:02d}"),
                birth_date.day - 1,
            ),
            (
                ("Year",),
                (str(birth_date.year),),
                (str(birth_date.year),),
                max(0, date.today().year - birth_date.year - 1),
            ),
        )
        all_combos = page.get_by_role("combobox")
        for index, (names, labels, values, keyboard_steps) in enumerate(specs):
            combo = await self._find_role_by_names(
                page, "combobox", names, timeout_ms=2_000
            )
            if combo is None:
                if await all_combos.count() <= index:
                    raise RuntimeError("Birth-date comboboxes were not found")
                combo = all_combos.nth(index)
                await combo.wait_for(state="visible", timeout=8_000)
            await self._choose_option(
                page,
                combo,
                labels,
                values,
                keyboard_steps=keyboard_steps,
            )

    @staticmethod
    async def _choose_option(
        page: Any,
        combo: Any,
        labels: Iterable[str],
        values: Iterable[str],
        *,
        keyboard_steps: int,
    ) -> None:
        labels = tuple(labels)
        for label in labels:
            try:
                await combo.select_option(label=label, timeout=500)
                return
            except Exception:
                continue
        for value in values:
            try:
                await combo.select_option(value=value, timeout=500)
                return
            except Exception:
                continue

        # TikTok's picker virtualizes plain-text rows without option semantics.
        # Once opened, keyboard navigation selects deterministically from the top.
        try:
            await combo.click(timeout=3_000)
            await asyncio.sleep(0.15)
            await page.keyboard.press("Home", delay=25)
            for _ in range(keyboard_steps):
                await page.keyboard.press("ArrowDown", delay=25)
            await page.keyboard.press("Enter", delay=25)
        except Exception as exc:
            raise RuntimeError(
                f"Could not select birth-date option {' / '.join(labels)}"
            ) from exc

    async def _wait_after_send_code(
        self,
        page: Any,
        *,
        email_field: Any,
        send_code: Any,
        send_label_before: str,
        login_count_before: int,
    ) -> SignupDriverOutcome:
        deadline = asyncio.get_running_loop().time() + 20
        acknowledged_at: float | None = None
        while asyncio.get_running_loop().time() < deadline:
            if await self._captcha_visible(page):
                if await self._captcha_blocking(page):
                    return SignupDriverOutcome.CAPTCHA_REQUIRED
                deadline = asyncio.get_running_loop().time() + 20
            if await self._email_rejected_visible(
                page,
                email_field=email_field,
                login_count_before=login_count_before,
            ):
                return SignupDriverOutcome.EMAIL_REJECTED
            if await self._code_request_acknowledged(
                page,
                send_action=send_code,
                label_before=send_label_before,
            ):
                now = asyncio.get_running_loop().time()
                if acknowledged_at is None:
                    acknowledged_at = now
                elif now - acknowledged_at >= 2:
                    # TikTok can disable the button before rendering its
                    # "already signed up" validation. Give that higher-priority
                    # state a short window to appear before mailbox polling.
                    return SignupDriverOutcome.OTP_REQUESTED
            await asyncio.sleep(0.25)
        raise TimeoutError(
            "TikTok did not acknowledge Send code or expose an email validation result"
        )

    async def _wait_for_code_request_ack(
        self,
        page: Any,
        *,
        send_action: Any,
        label_before: str,
        timeout_seconds: float,
    ) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if await self._code_request_acknowledged(
                page,
                send_action=send_action,
                label_before=label_before,
            ):
                return True
            await asyncio.sleep(0.25)
        return False

    async def _code_request_acknowledged(
        self,
        page: Any,
        *,
        send_action: Any,
        label_before: str,
    ) -> bool:
        if await self._text_visible(page, _CODE_SENT_TEXT):
            return True
        try:
            label_after = (await send_action.inner_text()).strip()
            if label_after and label_after.casefold() != label_before.casefold():
                return True
            return not await send_action.is_enabled()
        except Exception:
            # Replacing the original button with a countdown/resend control is
            # also a positive acknowledgement of the request.
            resend = page.get_by_role(
                "button",
                name=re.compile(r"^\s*resend\s+code(?:\s+.*)?$", re.IGNORECASE),
            ).first
            try:
                return await resend.is_visible()
            except Exception:
                return False

    async def _email_rejected_visible(
        self,
        page: Any,
        *,
        email_field: Any,
        login_count_before: int,
    ) -> bool:
        if await self._text_visible(page, _EMAIL_REJECTED_TEXT):
            return True
        try:
            if await email_field.get_attribute("aria-invalid") == "true":
                return True
        except Exception:
            pass
        return await self._visible_login_count(page) > login_count_before

    @staticmethod
    async def _visible_login_count(page: Any) -> int:
        candidates = page.get_by_text(_LOGIN_TEXT, exact=True)
        try:
            count = await candidates.count()
        except Exception:
            return 0
        visible = 0
        for index in range(min(count, 20)):
            try:
                if await candidates.nth(index).is_visible():
                    visible += 1
            except Exception:
                continue
        return visible

    async def _wait_for_completion(self, page: Any) -> SignupDriverOutcome:
        deadline = asyncio.get_running_loop().time() + 30
        while asyncio.get_running_loop().time() < deadline:
            if await self._captcha_visible(page):
                if await self._captcha_blocking(page):
                    return SignupDriverOutcome.CAPTCHA_REQUIRED
                deadline = asyncio.get_running_loop().time() + 30
            dialog = page.get_by_role("dialog").first
            try:
                dialog_visible = await dialog.is_visible()
            except Exception:
                dialog_visible = False
            url = str(page.url).casefold()
            if not dialog_visible and "/signup" not in url and "/login" not in url:
                return SignupDriverOutcome.COMPLETED
            await asyncio.sleep(0.5)
        raise TimeoutError("TikTok did not confirm that registration completed")

    async def _captcha_visible(self, page: Any) -> bool:
        if await self._text_visible(page, _CAPTCHA_TEXT):
            return True
        for frame in getattr(page, "frames", ()):
            frame_url = str(getattr(frame, "url", "")).casefold()
            if any(token in frame_url for token in ("captcha", "verify", "challenge")):
                return True
        return False

    async def _captcha_blocking(self, page: Any) -> bool:
        """Wait for the configured solver; return True only if challenge persists."""
        if not await self._captcha_visible(page):
            return False
        deadline = (
            asyncio.get_running_loop().time() + _CAPTCHA_SOLVER_TIMEOUT_SECONDS
        )
        cleared_at: float | None = None
        while asyncio.get_running_loop().time() < deadline:
            if await self._captcha_visible(page):
                cleared_at = None
            else:
                now = asyncio.get_running_loop().time()
                if cleared_at is None:
                    cleared_at = now
                elif now - cleared_at >= _CAPTCHA_CLEAR_STABLE_SECONDS:
                    return False
            await asyncio.sleep(0.5)
        return True

    @staticmethod
    async def _text_visible(page: Any, pattern: re.Pattern[str]) -> bool:
        candidates = page.get_by_text(pattern)
        try:
            count = await candidates.count()
        except Exception:
            return False
        for index in range(min(count, 20)):
            try:
                if await candidates.nth(index).is_visible():
                    return True
            except Exception:
                continue
        return False

    async def _click_named(
        self,
        page: Any,
        names: tuple[str, ...],
        *,
        roles: tuple[str, ...] = ("button", "link"),
        timeout_ms: int,
    ) -> None:
        candidate = await self._find_named(
            page, names, roles=roles, timeout_ms=timeout_ms
        )
        if candidate is None:
            raise RuntimeError(f"Visible action was not found: {' / '.join(names)}")
        await candidate.click()

    async def _find_named(
        self,
        page: Any,
        names: tuple[str, ...],
        *,
        roles: tuple[str, ...],
        timeout_ms: int,
    ) -> Any | None:
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            for role in roles:
                candidate = await self._find_role_by_names(
                    page, role, names, timeout_ms=100
                )
                if candidate is not None:
                    return candidate
            for name in names:
                candidate = page.get_by_text(
                    re.compile(f"^{re.escape(name)}$", re.IGNORECASE), exact=True
                ).first
                try:
                    if await candidate.is_visible():
                        return candidate
                except Exception:
                    continue
            await asyncio.sleep(0.1)
        return None

    @staticmethod
    async def _find_role_by_names(
        page: Any,
        role: str,
        names: tuple[str, ...],
        *,
        timeout_ms: int,
    ) -> Any | None:
        for name in names:
            candidate = page.get_by_role(
                role, name=re.compile(f"^{re.escape(name)}$", re.IGNORECASE)
            ).first
            try:
                await candidate.wait_for(state="visible", timeout=timeout_ms)
                return candidate
            except Exception:
                continue
        return None

    async def _find_field(
        self,
        page: Any,
        *,
        labels: tuple[str, ...],
        placeholders: tuple[str, ...],
        timeout_ms: int,
        required: bool = True,
    ) -> Any | None:
        deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
        while asyncio.get_running_loop().time() < deadline:
            for label in labels:
                exact_label = re.compile(
                    f"^\\s*{re.escape(label)}\\s*$",
                    re.I,
                )
                candidate = page.get_by_label(exact_label).first
                try:
                    if await candidate.is_visible():
                        return candidate
                except Exception:
                    continue
                candidate = page.get_by_role(
                    "textbox", name=exact_label
                ).first
                try:
                    if await candidate.is_visible():
                        return candidate
                except Exception:
                    continue
            for placeholder in placeholders:
                candidate = page.get_by_placeholder(
                    re.compile(re.escape(placeholder), re.I)
                ).first
                try:
                    if await candidate.is_visible():
                        return candidate
                except Exception:
                    continue
            await asyncio.sleep(0.1)
        if required:
            raise RuntimeError(f"Input field was not found: {' / '.join(labels)}")
        return None

    @staticmethod
    def _result(page: Any, outcome: SignupDriverOutcome) -> SignupDriverResult:
        return SignupDriverResult(
            outcome=outcome,
            message="TikTok requested CAPTCHA; the automated test stopped",
            current_url=str(page.url),
        )


class SimulatedSignupDriver(SignupAutomationDriver):
    """Deterministic adapter for API/application tests."""

    async def begin_signup(
        self,
        session_id: str,
        form: SignupFormInput,
        progress: Callable[[SignupTestPhase, str], Awaitable[None]],
    ) -> SignupDriverResult:
        del session_id, form
        await progress(SignupTestPhase.BIRTHDAY, "Birth date selected")
        await progress(SignupTestPhase.EMAIL, "Verification code requested")
        return SignupDriverResult(
            outcome=SignupDriverOutcome.OTP_REQUESTED,
            message="Verification code requested",
            current_url="https://www.tiktok.com/signup?lang=en",
            otp_requested_at=datetime.now(UTC),
        )

    async def retry_signup_email(
        self,
        session_id: str,
        form: SignupFormInput,
        progress: Callable[[SignupTestPhase, str], Awaitable[None]],
    ) -> SignupDriverResult:
        del session_id, form
        await progress(SignupTestPhase.EMAIL, "Next email candidate submitted")
        return SignupDriverResult(
            outcome=SignupDriverOutcome.OTP_REQUESTED,
            message="Verification code requested",
            current_url="https://www.tiktok.com/signup?lang=en",
            otp_requested_at=datetime.now(UTC),
        )

    async def resend_otp(
        self,
        session_id: str,
        progress: Callable[[SignupTestPhase, str], Awaitable[None]],
    ) -> SignupDriverResult:
        del session_id
        await progress(SignupTestPhase.OTP, "Verification code resent")
        return SignupDriverResult(
            outcome=SignupDriverOutcome.OTP_REQUESTED,
            message="Verification code resent",
            current_url="https://www.tiktok.com/signup?lang=en",
            otp_requested_at=datetime.now(UTC),
        )

    async def finish_signup(
        self,
        session_id: str,
        *,
        otp: str,
        username: str,
        progress: Callable[[SignupTestPhase, str], Awaitable[None]],
    ) -> SignupDriverResult:
        del session_id, otp, username
        await progress(SignupTestPhase.USERNAME, "Username submitted")
        return SignupDriverResult(
            outcome=SignupDriverOutcome.COMPLETED,
            message="Signup completed",
            current_url="https://www.tiktok.com/foryou?lang=en",
        )
