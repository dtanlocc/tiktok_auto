from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from invisible_browser_studio.domain import (
    AutomationBatch,
    BrowserSession,
    ImportedAccount,
    SignupTestPhase,
)

from .dto import (
    ProxyRotationResult,
    RuntimeStartResult,
    SignupDriverResult,
    SignupFormInput,
)


class SessionRepository(Protocol):
    async def add(self, session: BrowserSession) -> None: ...

    async def get(self, session_id: str) -> BrowserSession | None: ...

    async def find_by_idempotency_key(
        self, tenant_id: str, idempotency_key: str
    ) -> BrowserSession | None: ...

    async def list(
        self, *, tenant_id: str | None, offset: int, limit: int
    ) -> tuple[list[BrowserSession], int]: ...

    async def update(self, session: BrowserSession) -> None: ...

    async def count_active(self) -> int: ...


class BatchRepository(Protocol):
    async def initialize(self) -> None: ...

    async def close(self) -> None: ...

    async def add(self, batch: AutomationBatch) -> None: ...

    async def get(self, batch_id: str) -> AutomationBatch | None: ...

    async def list(
        self, *, offset: int, limit: int
    ) -> tuple[list[AutomationBatch], int]: ...

    async def update(self, batch: AutomationBatch) -> None: ...


class AccountRepository(Protocol):
    async def initialize(self) -> None: ...

    async def close(self) -> None: ...

    async def add_many(
        self, accounts: tuple[ImportedAccount, ...]
    ) -> tuple[list[ImportedAccount], int]: ...

    async def list(
        self, *, offset: int, limit: int
    ) -> tuple[list[ImportedAccount], int]: ...


class ProxyRotator(Protocol):
    async def rotate(self, rotation_url: str) -> ProxyRotationResult: ...


class OtpReader(Protocol):
    async def fetch_tiktok_code(
        self,
        *,
        email: str,
        refresh_token: str,
        client_id: str,
        requested_at: datetime,
    ) -> str | None: ...


class SignupAutomationDriver(Protocol):
    async def begin_signup(
        self,
        session_id: str,
        form: SignupFormInput,
        progress: Callable[[SignupTestPhase, str], Awaitable[None]],
    ) -> SignupDriverResult: ...

    async def retry_signup_email(
        self,
        session_id: str,
        form: SignupFormInput,
        progress: Callable[[SignupTestPhase, str], Awaitable[None]],
    ) -> SignupDriverResult: ...

    async def resend_otp(
        self,
        session_id: str,
        progress: Callable[[SignupTestPhase, str], Awaitable[None]],
    ) -> SignupDriverResult: ...

    async def finish_signup(
        self,
        session_id: str,
        *,
        otp: str,
        username: str,
        progress: Callable[[SignupTestPhase, str], Awaitable[None]],
    ) -> SignupDriverResult: ...


class BrowserRuntime(Protocol):
    async def start(self, session: BrowserSession) -> RuntimeStartResult: ...

    async def close(self, session_id: str) -> None: ...

    async def navigate(self, session_id: str, url: str) -> str: ...

    async def upload(self, session_id: str, path: Path) -> None: ...

    async def shutdown(self) -> None: ...


@dataclass(frozen=True, slots=True)
class DomainEvent:
    type: str
    session_id: str | None
    tenant_id: str | None
    payload: dict[str, object]
    occurred_at: str


class EventPublisher(Protocol):
    async def publish(self, event: DomainEvent) -> None: ...


class EventSubscription(Protocol):
    async def __aenter__(self) -> AsyncIterator[DomainEvent]: ...

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None: ...


class FramePublisher(Protocol):
    async def publish_frame(self, session_id: str, frame: bytes) -> None: ...


JobHandler = Callable[[], Awaitable[None]]


class Scheduler(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def submit(
        self,
        *,
        job_id: str,
        tenant_id: str,
        priority: int,
        handler: JobHandler,
    ) -> bool: ...
