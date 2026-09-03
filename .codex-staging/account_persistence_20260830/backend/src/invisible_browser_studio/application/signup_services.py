from __future__ import annotations

import asyncio
import uuid
from copy import deepcopy
from datetime import UTC, datetime

from invisible_browser_studio.domain import (
    BrowserMode,
    SessionStatus,
    SignupTest,
    SignupTestPhase,
    SignupTestStatus,
)

from .dto import (
    CreateSessionCommand,
    CreateSignupTestCommand,
    SignupDriverOutcome,
    SignupFormInput,
)
from .errors import SignupTestAlreadyConsumed, SignupTestNotFound
from .ports import DomainEvent, EventPublisher, OtpReader, SignupAutomationDriver
from .services import BrowserSessionService


class SignupTestService:
    """Runs at most one controlled signup test during one backend lifetime."""

    def __init__(
        self,
        *,
        sessions: BrowserSessionService,
        driver: SignupAutomationDriver,
        otp_reader: OtpReader,
        events: EventPublisher,
    ) -> None:
        self._sessions = sessions
        self._driver = driver
        self._otp_reader = otp_reader
        self._events = events
        self._record: SignupTest | None = None
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def create(self, command: CreateSignupTestCommand) -> SignupTest:
        async with self._lock:
            if self._record is not None:
                raise SignupTestAlreadyConsumed(
                    "Only one signup test is allowed per backend lifetime"
                )

            session = await self._sessions.create(
                CreateSessionCommand(
                    tenant_id="signup-test",
                    display_name="One-shot signup test",
                    start_url=command.start_url,
                    mode=BrowserMode.HIDDEN,
                    locale="en-US",
                    timezone="auto",
                    proxy=command.proxy,
                    priority=100,
                    extensions_enabled=True,
                    humanize=True,
                )
            )
            record = SignupTest(
                id=str(uuid.uuid4()),
                session_id=session.id,
                start_url=command.start_url,
                email_masked=_mask_email(command.email),
                requested_username=command.username,
                total_email_candidates=len(command.mailbox_candidates),
            )
            self._record = record
            created = deepcopy(record)
            self._task = asyncio.create_task(
                self._run(record.id, command),
                name=f"one-shot-signup-{record.id}",
            )

        await self._publish("signup_test.created", created)
        return created

    async def get(self, test_id: str | None = None) -> SignupTest:
        async with self._lock:
            if self._record is None or (test_id and self._record.id != test_id):
                raise SignupTestNotFound("No signup test exists in this backend lifetime")
            return deepcopy(self._record)

    async def cancel(self, test_id: str) -> SignupTest:
        record = await self.get(test_id)
        if record.finished_at is not None:
            return record
        async with self._lock:
            task = self._task
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return await self.get(test_id)

    async def shutdown(self) -> None:
        async with self._lock:
            task = self._task
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _run(self, test_id: str, command: CreateSignupTestCommand) -> None:
        sensitive_values = [
            command.account_password,
            command.proxy.password if command.proxy and command.proxy.password else "",
            command.proxy.username if command.proxy and command.proxy.username else "",
            *(mailbox.email for mailbox in command.mailbox_candidates),
            *(mailbox.refresh_token for mailbox in command.mailbox_candidates),
            *(mailbox.client_id for mailbox in command.mailbox_candidates),
        ]
        try:
            await self._advance(
                test_id,
                status=SignupTestStatus.RUNNING,
                phase=SignupTestPhase.OPENING,
                message="Opening the configured current page",
            )
            await self._wait_until_running(test_id)
            await self._advance(
                test_id,
                phase=SignupTestPhase.SIGN_UP,
                message="Looking for Sign up using accessible roles and text",
            )
            record = await self.get(test_id)
            mailboxes = command.mailbox_candidates
            selected_mailbox = None
            checkpoint = None
            for attempt, mailbox in enumerate(mailboxes, start=1):
                await self._record_email_attempt(
                    test_id,
                    email=mailbox.email,
                    attempt=attempt,
                    total=len(mailboxes),
                )
                form = SignupFormInput(
                    email=mailbox.email,
                    account_password=command.account_password,
                    birth_date=command.birth_date,
                )
                if attempt == 1:
                    checkpoint = await self._driver.begin_signup(
                        record.session_id,
                        form,
                        lambda phase, message: self._advance(
                            test_id, phase=phase, message=message
                        ),
                    )
                else:
                    checkpoint = await self._driver.retry_signup_email(
                        record.session_id,
                        form,
                        lambda phase, message: self._advance(
                            test_id, phase=phase, message=message
                        ),
                    )

                if checkpoint.outcome is SignupDriverOutcome.CAPTCHA_REQUIRED:
                    await self._finish(
                        test_id,
                        SignupTestStatus.CAPTCHA_REQUIRED,
                        phase=SignupTestPhase.EMAIL,
                        message=checkpoint.message,
                        error_code="captcha_required",
                    )
                    return
                if checkpoint.outcome is SignupDriverOutcome.EMAIL_REJECTED:
                    if attempt < len(mailboxes):
                        await self._advance(
                            test_id,
                            phase=SignupTestPhase.EMAIL,
                            message=(
                                f"Email candidate {attempt} is already in use; "
                                "trying the next owned Hotmail mailbox"
                            ),
                        )
                        continue
                    await self._finish(
                        test_id,
                        SignupTestStatus.EMAIL_REJECTED,
                        phase=SignupTestPhase.EMAIL,
                        message="All configured email candidates are already in use",
                        error_code="email_candidates_exhausted",
                    )
                    return
                if checkpoint.outcome is not SignupDriverOutcome.OTP_REQUESTED:
                    raise RuntimeError(
                        f"Unexpected signup checkpoint: {checkpoint.outcome}"
                    )
                selected_mailbox = mailbox
                break

            if selected_mailbox is None or checkpoint is None:
                raise RuntimeError("No signup email candidate reached the OTP checkpoint")

            requested_at = checkpoint.otp_requested_at or datetime.now(UTC)
            await self._advance(
                test_id,
                status=SignupTestStatus.WAITING_OTP,
                phase=SignupTestPhase.OTP,
                message=(
                    "Verification code requested; checking the selected owned "
                    "Hotmail mailbox"
                ),
            )
            otp = None
            for otp_attempt in range(2):
                otp = await self._otp_reader.fetch_tiktok_code(
                    email=selected_mailbox.email,
                    refresh_token=selected_mailbox.refresh_token,
                    client_id=selected_mailbox.client_id,
                    requested_at=requested_at,
                )
                if otp:
                    break
                if otp_attempt == 0:
                    resent = await self._driver.resend_otp(
                        record.session_id,
                        lambda phase, message: self._advance(
                            test_id, phase=phase, message=message
                        ),
                    )
                    if resent.outcome is SignupDriverOutcome.CAPTCHA_REQUIRED:
                        await self._finish(
                            test_id,
                            SignupTestStatus.CAPTCHA_REQUIRED,
                            phase=SignupTestPhase.OTP,
                            message=resent.message,
                            error_code="captcha_required",
                        )
                        return
                    if resent.outcome is not SignupDriverOutcome.OTP_REQUESTED:
                        raise RuntimeError(resent.message)
                    requested_at = resent.otp_requested_at or datetime.now(UTC)
                    await self._advance(
                        test_id,
                        status=SignupTestStatus.WAITING_OTP,
                        phase=SignupTestPhase.OTP,
                        message=(
                            "Verification code resent once; checking Hotmail "
                            "for a newer message"
                        ),
                    )
            if not otp:
                await self._finish(
                    test_id,
                    SignupTestStatus.FAILED,
                    phase=SignupTestPhase.OTP,
                    message="No fresh TikTok verification code arrived before timeout",
                    error_code="otp_timeout",
                )
                return
            sensitive_values.append(otp)

            await self._advance(
                test_id,
                status=SignupTestStatus.RUNNING,
                phase=SignupTestPhase.OTP,
                message="Fresh verification code received; submitting it once",
            )
            final = await self._driver.finish_signup(
                record.session_id,
                otp=otp,
                username=command.username,
                progress=lambda phase, message: self._advance(
                    test_id, phase=phase, message=message
                ),
            )
            if final.outcome is SignupDriverOutcome.CAPTCHA_REQUIRED:
                await self._finish(
                    test_id,
                    SignupTestStatus.CAPTCHA_REQUIRED,
                    phase=SignupTestPhase.OTP,
                    message=final.message,
                    error_code="captcha_required",
                )
                return
            if final.outcome is not SignupDriverOutcome.COMPLETED:
                raise RuntimeError(final.message)
            await self._finish(
                test_id,
                SignupTestStatus.COMPLETED,
                phase=SignupTestPhase.COMPLETE,
                message=final.message,
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                self._finish(
                    test_id,
                    SignupTestStatus.CANCELLED,
                    phase=SignupTestPhase.CLEANUP,
                    message="Signup test cancelled",
                    error_code="cancelled",
                )
            )
            raise
        except Exception as exc:
            await self._finish(
                test_id,
                SignupTestStatus.FAILED,
                phase=SignupTestPhase.CLEANUP,
                message=_redact_signup_error(exc, sensitive_values),
                error_code="signup_runtime_failed",
            )
        finally:
            try:
                record = await self.get(test_id)
                await asyncio.shield(self._sessions.close(record.session_id))
            except Exception:
                pass
            async with self._lock:
                self._task = None

    async def _wait_until_running(self, test_id: str) -> None:
        deadline = asyncio.get_running_loop().time() + 75
        while asyncio.get_running_loop().time() < deadline:
            record = await self.get(test_id)
            session = await self._sessions.get(record.session_id)
            if session.status is SessionStatus.RUNNING:
                return
            if session.status is SessionStatus.FAILED:
                raise RuntimeError(session.error_message or "Browser failed to start")
            await asyncio.sleep(0.25)
        raise TimeoutError("Browser did not become ready within 75 seconds")

    async def _advance(
        self,
        test_id: str,
        *,
        message: str,
        status: SignupTestStatus | None = None,
        phase: SignupTestPhase | None = None,
    ) -> SignupTest:
        async with self._lock:
            if self._record is None or self._record.id != test_id:
                raise SignupTestNotFound(test_id)
            self._record.advance(status=status, phase=phase, message=message)
            record = deepcopy(self._record)
        await self._publish("signup_test.progress", record)
        return record

    async def _record_email_attempt(
        self,
        test_id: str,
        *,
        email: str,
        attempt: int,
        total: int,
    ) -> SignupTest:
        async with self._lock:
            if self._record is None or self._record.id != test_id:
                raise SignupTestNotFound(test_id)
            self._record.begin_email_attempt(
                email_masked=_mask_email(email),
                attempt=attempt,
                phase=(
                    SignupTestPhase.SIGN_UP
                    if attempt == 1
                    else SignupTestPhase.EMAIL
                ),
                message=f"Trying owned Hotmail mailbox {attempt} of {total}",
            )
            record = deepcopy(self._record)
        await self._publish("signup_test.progress", record)
        return record

    async def _finish(
        self,
        test_id: str,
        status: SignupTestStatus,
        *,
        phase: SignupTestPhase,
        message: str,
        error_code: str | None = None,
    ) -> SignupTest:
        async with self._lock:
            if self._record is None or self._record.id != test_id:
                raise SignupTestNotFound(test_id)
            if self._record.finished_at is not None:
                return deepcopy(self._record)
            session_id = self._record.session_id

        cleanup_error: str | None = None
        try:
            await self._sessions.close(session_id)
        except Exception as exc:
            cleanup_error = str(exc)

        async with self._lock:
            if self._record is None or self._record.id != test_id:
                raise SignupTestNotFound(test_id)
            if self._record.finished_at is None:
                if cleanup_error:
                    status = SignupTestStatus.FAILED
                    phase = SignupTestPhase.CLEANUP
                    message = f"Browser cleanup failed: {cleanup_error}"
                    error_code = "cleanup_failed"
                self._record.finish(
                    status,
                    phase=phase,
                    message=message,
                    error_code=error_code,
                )
            record = deepcopy(self._record)
        await self._publish("signup_test.finished", record)
        return record

    async def _publish(self, event_type: str, record: SignupTest) -> None:
        await self._events.publish(
            DomainEvent(
                type=event_type,
                session_id=record.session_id,
                tenant_id="signup-test",
                payload={
                    "signup_test_id": record.id,
                    "status": record.status.value,
                    "phase": record.phase.value,
                    "message": record.message,
                    "severity": (
                        "error"
                        if record.status
                        in {
                            SignupTestStatus.FAILED,
                            SignupTestStatus.CAPTCHA_REQUIRED,
                            SignupTestStatus.EMAIL_REJECTED,
                        }
                        else "success"
                        if record.status is SignupTestStatus.COMPLETED
                        else "info"
                    ),
                },
                occurred_at=datetime.now(UTC).isoformat(),
            )
        )


def _mask_email(value: str) -> str:
    local, separator, domain = value.strip().partition("@")
    if not separator:
        return "***"
    visible = local[:2]
    return f"{visible}{'*' * max(3, len(local) - len(visible))}@{domain}"


def _redact_signup_error(exc: Exception, sensitive_values: list[str]) -> str:
    message = str(exc)
    for value in sensitive_values:
        if value:
            message = message.replace(value, "[REDACTED]")
    return message
