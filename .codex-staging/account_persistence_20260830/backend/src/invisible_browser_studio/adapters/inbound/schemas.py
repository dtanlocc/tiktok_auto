from __future__ import annotations

import re
from datetime import date, datetime

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    SecretStr,
    field_validator,
    model_validator,
)

from invisible_browser_studio.domain import (
    AutomationBatch,
    BatchStatus,
    BrowserMode,
    BrowserSession,
    ImportedAccount,
    QueueStatus,
    SessionPhase,
    SessionStatus,
    SignupTest,
    SignupTestPhase,
    SignupTestStatus,
)


class ProxyInput(BaseModel):
    server: str = Field(examples=["socks5://127.0.0.1:1080"])
    username: str | None = Field(default=None, min_length=1, max_length=256)
    password: SecretStr | None = None


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$")
    display_name: str = Field(default="Browser session", min_length=1, max_length=128)
    start_url: str = Field(
        default="about:blank",
        min_length=1,
        max_length=4096,
        validation_alias=AliasChoices("initial_url", "start_url"),
    )
    mode: BrowserMode = BrowserMode.HIDDEN
    locale: str = Field(default="auto", min_length=2, max_length=64)
    timezone: str = Field(default="auto", min_length=1, max_length=128)
    proxy: ProxyInput | None = None
    priority: int = Field(default=50, ge=0, le=100)


class CreateAutomationBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(
        default="automation", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"
    )
    display_name: str = Field(default="Ephemeral navigation batch", min_length=1, max_length=128)
    start_url: str = Field(
        default="https://www.tiktok.com/tiktokstudio/upload?lang=en",
        min_length=1,
        max_length=4096,
    )
    mode: BrowserMode = BrowserMode.HIDDEN
    total_jobs: int = Field(default=4, ge=1, le=1000)
    concurrency: int = Field(default=4, ge=1, le=64)
    active_seconds: float = Field(default=30, ge=0.1, le=86_400)
    locale: str = Field(default="en-US", min_length=2, max_length=64)
    timezone: str = Field(default="auto", min_length=1, max_length=128)
    proxy: ProxyInput | None = None
    proxies: list[ProxyInput] = Field(default_factory=list, max_length=64)
    rotation_url: SecretStr | None = Field(default=None, min_length=8, max_length=4096)
    priority: int = Field(default=50, ge=0, le=100)
    auto_start: bool = True

    @model_validator(mode="after")
    def require_one_proxy_input_style(self) -> CreateAutomationBatchRequest:
        if self.proxy and self.proxies:
            raise ValueError("use either proxy or proxies, not both")
        return self


def _normalize_email(value: str) -> str:
    normalized = value.strip().casefold()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", normalized):
        raise ValueError("email must be a valid address")
    return normalized


class SignupMailboxInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=5, max_length=320)
    refresh_token: SecretStr = Field(min_length=8, max_length=8192)
    client_id: SecretStr = Field(min_length=8, max_length=512)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _normalize_email(value)


class AccountImportRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=5, max_length=320)
    email_password: SecretStr = Field(min_length=1, max_length=1024)
    refresh_token: SecretStr = Field(min_length=8, max_length=8192)
    client_id: SecretStr = Field(min_length=8, max_length=512)
    source_name: str = Field(default="", max_length=255)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _normalize_email(value)


class AccountImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[AccountImportRow] = Field(min_length=1, max_length=5000)


class AccountImportResponse(BaseModel):
    imported: int
    duplicates: int
    total: int


class AccountResponse(BaseModel):
    id: str
    email: str
    source_name: str
    has_email_password: bool
    has_refresh_token: bool
    has_client_id: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_domain(cls, account: ImportedAccount) -> AccountResponse:
        return cls(
            id=account.id,
            email=account.email,
            source_name=account.source_name,
            has_email_password=bool(account.email_password),
            has_refresh_token=bool(account.refresh_token),
            has_client_id=bool(account.client_id),
            created_at=account.created_at,
            updated_at=account.updated_at,
        )


class AccountListResponse(BaseModel):
    items: list[AccountResponse]
    total: int
    offset: int
    limit: int


class CreateSignupTestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_url: str = Field(
        default="https://www.tiktok.com/tiktokstudio/upload?lang=en",
        min_length=1,
        max_length=4096,
    )
    email: str = Field(min_length=5, max_length=320)
    account_password: SecretStr = Field(min_length=8, max_length=128)
    refresh_token: SecretStr = Field(min_length=8, max_length=8192)
    client_id: SecretStr = Field(min_length=8, max_length=512)
    username: str = Field(
        min_length=6,
        max_length=18,
        pattern=r"^[A-Za-z0-9._]+$",
    )
    birth_date: date
    proxy: ProxyInput | None = None
    fallback_mailboxes: list[SignupMailboxInput] = Field(
        default_factory=list,
        max_length=9,
    )

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return _normalize_email(value)

    @field_validator("username")
    @classmethod
    def validate_username(cls, value: str) -> str:
        if "_" not in value or not any(char.isalpha() for char in value):
            raise ValueError("username must contain letters and an underscore")
        if not any(char.isdigit() for char in value):
            raise ValueError("username must contain at least one digit")
        return value

    @model_validator(mode="after")
    def require_unique_mailboxes(self) -> CreateSignupTestRequest:
        emails = [self.email, *(item.email for item in self.fallback_mailboxes)]
        if len(set(emails)) != len(emails):
            raise ValueError("signup mailbox emails must be unique")
        return self

    @field_validator("birth_date")
    @classmethod
    def require_adult_test_identity(cls, value: date) -> date:
        today = date.today()
        age = today.year - value.year - ((today.month, today.day) < (value.month, value.day))
        if not 18 <= age <= 100:
            raise ValueError("birth_date must represent an age between 18 and 100")
        return value


class NavigateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl


class SessionResponse(BaseModel):
    id: str
    tenant_id: str
    display_name: str
    start_url: str
    initial_url: str
    current_url: str | None
    mode: BrowserMode
    status: SessionStatus
    locale: str
    timezone: str
    priority: int
    proxy_server: str | None
    error_code: str | None
    error_message: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime
    revision: int
    batch_id: str | None
    ephemeral: bool
    phase: SessionPhase
    auto_close_after_seconds: float | None
    rotation_attempts: int
    rotation_succeeded: bool | None
    extensions_enabled: bool
    humanize: bool

    @classmethod
    def from_domain(cls, session: BrowserSession) -> SessionResponse:
        return cls(
            id=session.id,
            tenant_id=session.tenant_id,
            display_name=session.display_name,
            start_url=session.start_url,
            initial_url=session.start_url,
            current_url=session.current_url,
            mode=session.mode,
            status=session.status,
            locale=session.locale,
            timezone=session.timezone,
            priority=session.priority,
            proxy_server=session.proxy.safe_server() if session.proxy else None,
            error_code=session.error_code,
            error_message=session.error_message,
            error=session.error_message,
            created_at=session.created_at,
            updated_at=session.updated_at,
            revision=session.revision,
            batch_id=session.batch_id,
            ephemeral=session.ephemeral,
            phase=session.phase,
            auto_close_after_seconds=session.auto_close_after_seconds,
            rotation_attempts=session.rotation_attempts,
            rotation_succeeded=session.rotation_succeeded,
            extensions_enabled=session.extensions_enabled,
            humanize=session.humanize,
        )


class SessionListResponse(BaseModel):
    items: list[SessionResponse]
    total: int
    offset: int
    limit: int


class UploadResponse(BaseModel):
    filename: str
    bytes_received: int


class AutomationBatchResponse(BaseModel):
    id: str
    tenant_id: str
    display_name: str
    start_url: str
    mode: BrowserMode
    total_jobs: int
    concurrency: int
    active_seconds: float
    proxy_server: str | None
    proxy_servers: list[str]
    proxy_auth_required: bool
    queue_status: QueueStatus
    rotation_enabled: bool
    status: BatchStatus
    session_ids: list[str]
    completed_jobs: int
    failed_jobs: int
    cancelled_jobs: int
    finished_jobs: int
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    revision: int

    @classmethod
    def from_domain(cls, batch: AutomationBatch) -> AutomationBatchResponse:
        return cls(
            id=batch.id,
            tenant_id=batch.tenant_id,
            display_name=batch.display_name,
            start_url=batch.start_url,
            mode=batch.mode,
            total_jobs=batch.total_jobs,
            concurrency=batch.concurrency,
            active_seconds=batch.active_seconds,
            proxy_server=batch.proxy_server,
            proxy_servers=list(batch.proxy_servers),
            proxy_auth_required=batch.proxy_auth_required,
            queue_status=batch.queue_status.value,
            rotation_enabled=batch.rotation_enabled,
            status=batch.status,
            session_ids=list(batch.session_ids),
            completed_jobs=batch.completed_jobs,
            failed_jobs=batch.failed_jobs,
            cancelled_jobs=batch.cancelled_jobs,
            finished_jobs=batch.finished_jobs,
            error_message=batch.error_message,
            created_at=batch.created_at,
            updated_at=batch.updated_at,
            started_at=batch.started_at,
            finished_at=batch.finished_at,
            revision=batch.revision,
        )


class AutomationBatchListResponse(BaseModel):
    items: list[AutomationBatchResponse]
    total: int
    offset: int
    limit: int


class AutomationBatchPolicyResponse(BaseModel):
    max_jobs: int
    max_concurrency: int


class SignupTestResponse(BaseModel):
    id: str
    session_id: str
    start_url: str
    email_masked: str
    requested_username: str
    status: SignupTestStatus
    phase: SignupTestPhase
    message: str
    error_code: str | None
    email_attempts: int
    total_email_candidates: int
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    revision: int

    @classmethod
    def from_domain(cls, test: SignupTest) -> SignupTestResponse:
        return cls(
            id=test.id,
            session_id=test.session_id,
            start_url=test.start_url,
            email_masked=test.email_masked,
            requested_username=test.requested_username,
            status=test.status,
            phase=test.phase,
            message=test.message,
            error_code=test.error_code,
            email_attempts=test.email_attempts,
            total_email_candidates=test.total_email_candidates,
            created_at=test.created_at,
            updated_at=test.updated_at,
            finished_at=test.finished_at,
            revision=test.revision,
        )


class HealthResponse(BaseModel):
    status: str
    service: str
    runtime: str | None = None


class ErrorResponse(BaseModel):
    code: str
    message: str


class EventMessage(BaseModel):
    type: str
    session_id: str | None
    tenant_id: str | None
    payload: dict[str, object]
    occurred_at: str

    @field_validator("payload")
    @classmethod
    def keep_payload_bounded(cls, value: dict[str, object]) -> dict[str, object]:
        if len(value) > 50:
            raise ValueError("event payload has too many fields")
        return value
