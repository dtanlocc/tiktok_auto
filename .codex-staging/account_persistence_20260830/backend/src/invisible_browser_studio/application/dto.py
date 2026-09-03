from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

from invisible_browser_studio.domain import BrowserMode, ProxyConfig


@dataclass(frozen=True, slots=True)
class ImportAccountCommand:
    email: str
    email_password: str = field(repr=False)
    refresh_token: str = field(repr=False)
    client_id: str = field(repr=False)
    source_name: str = ""


@dataclass(frozen=True, slots=True)
class ImportAccountsResult:
    imported: int
    duplicates: int
    total: int


@dataclass(frozen=True, slots=True)
class CreateSessionCommand:
    tenant_id: str
    start_url: str
    display_name: str = "Browser session"
    mode: BrowserMode = BrowserMode.HIDDEN
    locale: str = "auto"
    timezone: str = "auto"
    proxy: ProxyConfig | None = None
    priority: int = 50
    idempotency_key: str | None = None
    batch_id: str | None = None
    ephemeral: bool = False
    auto_close_after_seconds: float | None = None
    extensions_enabled: bool = True
    humanize: bool = True


@dataclass(frozen=True, slots=True)
class CreateAutomationBatchCommand:
    tenant_id: str
    display_name: str
    start_url: str
    mode: BrowserMode
    total_jobs: int
    concurrency: int
    active_seconds: float
    locale: str = "en-US"
    timezone: str = "auto"
    proxy: ProxyConfig | None = field(default=None, repr=False)
    proxies: tuple[ProxyConfig, ...] = field(default_factory=tuple, repr=False)
    rotation_url: str | None = field(default=None, repr=False)
    priority: int = 50


@dataclass(frozen=True, slots=True)
class ProxyRotationResult:
    attempts: int
    elapsed_seconds: float
    status_code: int


@dataclass(frozen=True, slots=True)
class SignupMailbox:
    email: str
    refresh_token: str = field(repr=False)
    client_id: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class CreateSignupTestCommand:
    start_url: str
    email: str
    account_password: str = field(repr=False)
    refresh_token: str = field(repr=False)
    client_id: str = field(repr=False)
    username: str
    birth_date: date
    proxy: ProxyConfig | None = field(default=None, repr=False)
    fallback_mailboxes: tuple[SignupMailbox, ...] = field(
        default_factory=tuple,
        repr=False,
    )

    @property
    def mailbox_candidates(self) -> tuple[SignupMailbox, ...]:
        primary = SignupMailbox(
            email=self.email,
            refresh_token=self.refresh_token,
            client_id=self.client_id,
        )
        return (primary, *self.fallback_mailboxes)


@dataclass(frozen=True, slots=True)
class SignupFormInput:
    email: str
    account_password: str = field(repr=False)
    birth_date: date


class SignupDriverOutcome(StrEnum):
    OTP_REQUESTED = "otp_requested"
    COMPLETED = "completed"
    CAPTCHA_REQUIRED = "captcha_required"
    EMAIL_REJECTED = "email_rejected"


@dataclass(frozen=True, slots=True)
class SignupDriverResult:
    outcome: SignupDriverOutcome
    message: str
    current_url: str
    otp_requested_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class RuntimeStartResult:
    current_url: str


@dataclass(frozen=True, slots=True)
class UploadResult:
    filename: str
    bytes_received: int
