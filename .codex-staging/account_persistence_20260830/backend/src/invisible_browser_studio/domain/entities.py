from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from ipaddress import ip_address
from urllib.parse import urlparse

from .errors import InvalidStateTransition, InvalidUrl


def utc_now() -> datetime:
    return datetime.now(UTC)


class BrowserMode(StrEnum):
    HIDDEN = "hidden"
    VISIBLE = "visible"


class SessionStatus(StrEnum):
    QUEUED = "queued"
    STARTING = "starting"
    RUNNING = "running"
    CLOSING = "closing"
    CLOSED = "closed"
    FAILED = "failed"


class SessionPhase(StrEnum):
    QUEUED = "queued"
    ROTATING_PROXY = "rotating_proxy"
    LAUNCHING = "launching"
    ACTIVE = "active"
    CLEANUP = "cleanup"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class BatchStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    CANCELLED = "cancelled"
    FAILED = "failed"


class QueueStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SignupTestStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_OTP = "waiting_otp"
    COMPLETED = "completed"
    CAPTCHA_REQUIRED = "captcha_required"
    EMAIL_REJECTED = "email_rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"


class SignupTestPhase(StrEnum):
    OPENING = "opening"
    SIGN_UP = "sign_up"
    METHOD = "method"
    BIRTHDAY = "birthday"
    EMAIL = "email"
    OTP = "otp"
    USERNAME = "username"
    COMPLETE = "complete"
    CLEANUP = "cleanup"


@dataclass(frozen=True, slots=True)
class ImportedAccount:
    """An imported mailbox row with secrets retained for internal use only."""

    id: str
    email: str
    email_password: str = field(repr=False)
    refresh_token: str = field(repr=False)
    client_id: str = field(repr=False)
    source_name: str = ""
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        normalized_email = self.email.strip().casefold()
        local, separator, domain = normalized_email.partition("@")
        if not separator or not local or "." not in domain:
            raise ValueError("email must be a valid address")
        if not self.email_password:
            raise ValueError("email_password cannot be empty")
        if not self.refresh_token:
            raise ValueError("refresh_token cannot be empty")
        if not self.client_id:
            raise ValueError("client_id cannot be empty")
        object.__setattr__(self, "email", normalized_email)
        object.__setattr__(self, "source_name", self.source_name.strip()[:255])


_ALLOWED_TRANSITIONS: dict[SessionStatus, frozenset[SessionStatus]] = {
    SessionStatus.QUEUED: frozenset(
        {SessionStatus.STARTING, SessionStatus.CLOSING, SessionStatus.FAILED}
    ),
    SessionStatus.STARTING: frozenset(
        {SessionStatus.RUNNING, SessionStatus.CLOSING, SessionStatus.FAILED}
    ),
    SessionStatus.RUNNING: frozenset({SessionStatus.CLOSING, SessionStatus.FAILED}),
    SessionStatus.CLOSING: frozenset({SessionStatus.CLOSED, SessionStatus.FAILED}),
    SessionStatus.FAILED: frozenset({SessionStatus.CLOSING, SessionStatus.CLOSED}),
    SessionStatus.CLOSED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    server: str
    username: str | None = None
    password: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        parsed = urlparse(self.server)
        if parsed.scheme not in {"http", "https", "socks5"} or not parsed.hostname:
            raise InvalidUrl("Proxy server must use http, https, or socks5 and include a host")
        if parsed.username or parsed.password:
            raise ValueError("Proxy credentials must use dedicated fields, not URL userinfo")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("Proxy server cannot contain a path, query, or fragment")
        if (self.username is None) != (self.password is None):
            raise ValueError("Proxy username and password must be supplied together")

    def safe_server(self) -> str:
        """Return a log-safe endpoint. Credentials are never embedded in server."""
        parsed = urlparse(self.server)
        host = parsed.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}"


@dataclass(slots=True)
class BrowserSession:
    id: str
    tenant_id: str
    start_url: str
    mode: BrowserMode
    display_name: str = "Browser session"
    locale: str = "auto"
    timezone: str = "auto"
    status: SessionStatus = SessionStatus.QUEUED
    proxy: ProxyConfig | None = field(default=None, repr=False)
    current_url: str | None = None
    idempotency_key: str | None = field(default=None, repr=False)
    priority: int = 50
    error_code: str | None = None
    error_message: str | None = None
    batch_id: str | None = None
    ephemeral: bool = False
    phase: SessionPhase = SessionPhase.QUEUED
    auto_close_after_seconds: float | None = None
    rotation_attempts: int = 0
    rotation_succeeded: bool | None = None
    extensions_enabled: bool = True
    humanize: bool = True
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    revision: int = 0

    def __post_init__(self) -> None:
        self._validate_url(self.start_url)
        if not self.tenant_id.strip():
            raise ValueError("tenant_id cannot be empty")
        self.display_name = self.display_name.strip()
        if not self.display_name or len(self.display_name) > 128:
            raise ValueError("display_name must contain between 1 and 128 characters")
        self.locale = self.locale.strip()
        if not self.locale or len(self.locale) > 64:
            raise ValueError("locale must contain between 1 and 64 characters")
        self.timezone = self.timezone.strip()
        if not self.timezone or len(self.timezone) > 128:
            raise ValueError("timezone must contain between 1 and 128 characters")
        if not 0 <= self.priority <= 100:
            raise ValueError("priority must be between 0 and 100")
        if self.batch_id is not None and not self.batch_id.strip():
            raise ValueError("batch_id cannot be empty")
        if self.auto_close_after_seconds is not None and not (
            0.1 <= self.auto_close_after_seconds <= 86_400
        ):
            raise ValueError("auto_close_after_seconds must be between 0.1 and 86400")
        if self.current_url is None:
            self.current_url = self.start_url

    @staticmethod
    def _validate_url(value: str) -> None:
        if value == "about:blank":
            return
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise InvalidUrl("URL must be an absolute http(s) URL")
        if parsed.username or parsed.password:
            raise InvalidUrl("URL credentials are not allowed")
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise InvalidUrl("Loopback destinations are not allowed")
        try:
            address = ip_address(hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise InvalidUrl("Private, loopback, link-local, and reserved IPs are not allowed")

    def transition(self, target: SessionStatus) -> None:
        if target == self.status:
            return
        if target not in _ALLOWED_TRANSITIONS[self.status]:
            raise InvalidStateTransition(
                f"Cannot transition browser session from {self.status} to {target}"
            )
        self.status = target
        self._touch()

    def mark_running(self, current_url: str | None = None) -> None:
        self.transition(SessionStatus.RUNNING)
        self.current_url = current_url or self.start_url

    def record_navigation(self, url: str) -> None:
        if self.status is not SessionStatus.RUNNING:
            raise InvalidStateTransition("Only a running browser session can navigate")
        self._validate_url(url)
        self.current_url = url
        self._touch()

    def mark_failed(self, code: str, message: str) -> None:
        if self.status not in {SessionStatus.FAILED, SessionStatus.CLOSED}:
            self.transition(SessionStatus.FAILED)
        self.error_code = code
        self.error_message = message[:1000]
        self.phase = SessionPhase.FAILED
        self._touch()

    def set_phase(self, phase: SessionPhase) -> None:
        if self.phase == phase:
            return
        self.phase = phase
        self._touch()

    def record_rotation(self, *, succeeded: bool, attempts: int) -> None:
        self.rotation_succeeded = succeeded
        self.rotation_attempts = max(0, attempts)
        self._touch()

    def _touch(self) -> None:
        self.updated_at = utc_now()
        self.revision += 1


@dataclass(slots=True)
class AutomationBatch:
    id: str
    tenant_id: str
    display_name: str
    start_url: str
    mode: BrowserMode
    total_jobs: int
    concurrency: int
    active_seconds: float
    proxy_server: str | None = None
    proxy_servers: list[str] = field(default_factory=list)
    proxy_auth_required: bool = False
    rotation_enabled: bool = False
    status: BatchStatus = BatchStatus.QUEUED
    session_ids: list[str] = field(default_factory=list)
    completed_jobs: int = 0
    failed_jobs: int = 0
    cancelled_jobs: int = 0
    error_message: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        BrowserSession._validate_url(self.start_url)
        if not self.tenant_id.strip():
            raise ValueError("tenant_id cannot be empty")
        self.display_name = self.display_name.strip()
        if not self.display_name or len(self.display_name) > 128:
            raise ValueError("display_name must contain between 1 and 128 characters")
        if not 1 <= self.total_jobs <= 1000:
            raise ValueError("total_jobs must be between 1 and 1000")
        if not 1 <= self.concurrency <= self.total_jobs:
            raise ValueError("concurrency must be between 1 and total_jobs")
        if not 0.1 <= self.active_seconds <= 86_400:
            raise ValueError("active_seconds must be between 0.1 and 86400")

    @property
    def finished_jobs(self) -> int:
        return self.completed_jobs + self.failed_jobs + self.cancelled_jobs

    @property
    def queue_status(self) -> QueueStatus:
        if self.status is BatchStatus.QUEUED:
            return QueueStatus.QUEUED
        if self.status in {BatchStatus.RUNNING, BatchStatus.CANCELLING}:
            return QueueStatus.RUNNING
        if self.status is BatchStatus.COMPLETED:
            return QueueStatus.SUCCEEDED
        return QueueStatus.FAILED

    def attach_sessions(self, session_ids: list[str]) -> None:
        if self.session_ids:
            raise InvalidStateTransition("Batch sessions have already been attached")
        if len(session_ids) != self.total_jobs or len(set(session_ids)) != len(session_ids):
            raise ValueError("Batch must receive one unique session per job")
        self.session_ids = list(session_ids)
        self._touch()

    def mark_running(self) -> None:
        if self.status is not BatchStatus.QUEUED:
            raise InvalidStateTransition(f"Cannot run batch from {self.status}")
        self.status = BatchStatus.RUNNING
        self.started_at = utc_now()
        self._touch()

    def mark_cancelling(self) -> None:
        if self.status not in {BatchStatus.QUEUED, BatchStatus.RUNNING}:
            return
        self.status = BatchStatus.CANCELLING
        self._touch()

    def reconcile(self, *, completed: int, failed: int, cancelled: int) -> None:
        if min(completed, failed, cancelled) < 0:
            raise ValueError("Batch counters cannot be negative")
        if completed + failed + cancelled > self.total_jobs:
            raise ValueError("Batch counters cannot exceed total_jobs")
        self.completed_jobs = completed
        self.failed_jobs = failed
        self.cancelled_jobs = cancelled
        self._touch()

    def mark_finished(self) -> None:
        if self.status not in {BatchStatus.RUNNING, BatchStatus.CANCELLING}:
            raise InvalidStateTransition(f"Cannot finish batch from {self.status}")
        self.status = (
            BatchStatus.COMPLETED_WITH_ERRORS
            if self.failed_jobs or self.cancelled_jobs
            else BatchStatus.COMPLETED
        )
        self.finished_at = utc_now()
        self._touch()

    def mark_cancelled(self) -> None:
        self.status = BatchStatus.CANCELLED
        self.finished_at = utc_now()
        self._touch()

    def mark_failed(self, message: str) -> None:
        self.status = BatchStatus.FAILED
        self.error_message = message[:1000]
        self.finished_at = utc_now()
        self._touch()

    def _touch(self) -> None:
        self.updated_at = utc_now()
        self.revision += 1


@dataclass(slots=True)
class SignupTest:
    id: str
    session_id: str
    start_url: str
    email_masked: str
    requested_username: str
    status: SignupTestStatus = SignupTestStatus.QUEUED
    phase: SignupTestPhase = SignupTestPhase.OPENING
    message: str = "Signup test queued"
    error_code: str | None = None
    email_attempts: int = 0
    total_email_candidates: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    finished_at: datetime | None = None
    revision: int = 0

    def __post_init__(self) -> None:
        BrowserSession._validate_url(self.start_url)
        if not self.session_id.strip():
            raise ValueError("session_id cannot be empty")
        if not self.email_masked.strip():
            raise ValueError("email_masked cannot be empty")
        if not 1 <= self.total_email_candidates <= 10:
            raise ValueError("total_email_candidates must be between 1 and 10")
        if not 0 <= self.email_attempts <= self.total_email_candidates:
            raise ValueError("email_attempts must be between 0 and total_email_candidates")

    def begin_email_attempt(
        self,
        *,
        email_masked: str,
        attempt: int,
        phase: SignupTestPhase,
        message: str,
    ) -> None:
        if self.finished_at is not None:
            raise InvalidStateTransition("Signup test has already finished")
        if attempt != self.email_attempts + 1:
            raise InvalidStateTransition("Email attempts must be recorded sequentially")
        if attempt > self.total_email_candidates:
            raise InvalidStateTransition("No email candidate remains")
        self.email_masked = email_masked
        self.email_attempts = attempt
        self.status = SignupTestStatus.RUNNING
        self.phase = phase
        self.message = message[:500]
        self._touch()

    def advance(
        self,
        *,
        status: SignupTestStatus | None = None,
        phase: SignupTestPhase | None = None,
        message: str,
    ) -> None:
        if self.finished_at is not None:
            raise InvalidStateTransition("Signup test has already finished")
        if status is not None:
            self.status = status
        if phase is not None:
            self.phase = phase
        self.message = message[:500]
        self._touch()

    def finish(
        self,
        status: SignupTestStatus,
        *,
        phase: SignupTestPhase,
        message: str,
        error_code: str | None = None,
    ) -> None:
        if status not in {
            SignupTestStatus.COMPLETED,
            SignupTestStatus.CAPTCHA_REQUIRED,
            SignupTestStatus.EMAIL_REJECTED,
            SignupTestStatus.CANCELLED,
            SignupTestStatus.FAILED,
        }:
            raise ValueError("finish requires a terminal signup-test status")
        self.status = status
        self.phase = phase
        self.message = message[:500]
        self.error_code = error_code
        self.finished_at = utc_now()
        self._touch()

    def _touch(self) -> None:
        self.updated_at = utc_now()
        self.revision += 1
