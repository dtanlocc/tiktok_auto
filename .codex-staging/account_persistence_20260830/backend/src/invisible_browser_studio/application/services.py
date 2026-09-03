from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from pathlib import Path

from invisible_browser_studio.domain import BrowserSession, SessionPhase, SessionStatus
from invisible_browser_studio.domain.errors import InvalidStateTransition

from .dto import CreateSessionCommand, UploadResult
from .errors import CapacityExceeded, RuntimeOperationFailed, SessionNotFound
from .ports import BrowserRuntime, DomainEvent, EventPublisher, Scheduler, SessionRepository


class BrowserSessionService:
    def __init__(
        self,
        *,
        repository: SessionRepository,
        runtime: BrowserRuntime,
        scheduler: Scheduler,
        events: EventPublisher,
        max_sessions: int,
    ) -> None:
        self._repository = repository
        self._runtime = runtime
        self._scheduler = scheduler
        self._events = events
        self._max_sessions = max_sessions
        self._creation_lock = asyncio.Lock()
        self._session_locks: dict[str, asyncio.Lock] = {}

    async def create(self, command: CreateSessionCommand) -> BrowserSession:
        normalized_key = command.idempotency_key.strip() if command.idempotency_key else None
        if normalized_key:
            existing = await self._repository.find_by_idempotency_key(
                command.tenant_id, normalized_key
            )
            if existing:
                return existing

        async with self._creation_lock:
            # The legacy TD-white feature is a singleton. Repeated clicks must
            # reuse its queued/starting/running session instead of opening two
            # visible Firefox windows while the first launch is still pending.
            if command.tenant_id.strip() == "blank-browser":
                blank_sessions, _ = await self._repository.list(
                    tenant_id="blank-browser",
                    offset=0,
                    limit=max(self._max_sessions, 1),
                )
                active_blank = next(
                    (
                        item
                        for item in blank_sessions
                        if item.status not in {SessionStatus.CLOSED, SessionStatus.FAILED}
                    ),
                    None,
                )
                if active_blank:
                    return active_blank
            if normalized_key:
                existing = await self._repository.find_by_idempotency_key(
                    command.tenant_id, normalized_key
                )
                if existing:
                    return existing
            if await self._repository.count_active() >= self._max_sessions:
                raise CapacityExceeded("maximum number of active sessions reached")

            session = BrowserSession(
                id=str(uuid.uuid4()),
                tenant_id=command.tenant_id.strip(),
                start_url=command.start_url,
                mode=command.mode,
                display_name=command.display_name,
                locale=command.locale,
                timezone=command.timezone,
                proxy=command.proxy,
                priority=command.priority,
                idempotency_key=normalized_key,
                batch_id=command.batch_id,
                ephemeral=command.ephemeral,
                auto_close_after_seconds=command.auto_close_after_seconds,
                extensions_enabled=command.extensions_enabled,
                humanize=command.humanize,
            )
            await self._repository.add(session)

        await self._publish("session.created", session)
        try:
            accepted = await self._scheduler.submit(
                job_id=f"start:{session.id}",
                tenant_id=session.tenant_id,
                priority=session.priority,
                handler=lambda: self._start_session(session.id),
            )
            if not accepted:
                return await self.get(session.id)
        except Exception as exc:
            stored = await self.get(session.id)
            stored.mark_failed("scheduler_rejected", str(exc))
            await self._repository.update(stored)
            await self._publish("session.failed", stored, error_code=stored.error_code)
            if isinstance(exc, CapacityExceeded):
                raise
            raise RuntimeOperationFailed(str(exc)) from exc
        return session

    async def reserve_ephemeral_batch(
        self, commands: list[CreateSessionCommand]
    ) -> list[BrowserSession]:
        """Atomically reserve all in-memory job records without starting browsers."""
        if not commands:
            raise ValueError("At least one ephemeral session is required")
        if any(not command.ephemeral or not command.batch_id for command in commands):
            raise ValueError("Every reserved session must be ephemeral and belong to a batch")

        async with self._creation_lock:
            active = await self._repository.count_active()
            if active + len(commands) > self._max_sessions:
                raise CapacityExceeded("batch would exceed the maximum number of active sessions")
            sessions = [
                BrowserSession(
                    id=str(uuid.uuid4()),
                    tenant_id=command.tenant_id.strip(),
                    start_url=command.start_url,
                    mode=command.mode,
                    display_name=command.display_name,
                    locale=command.locale,
                    timezone=command.timezone,
                    proxy=command.proxy,
                    priority=command.priority,
                    batch_id=command.batch_id,
                    ephemeral=True,
                    auto_close_after_seconds=command.auto_close_after_seconds,
                    extensions_enabled=command.extensions_enabled,
                    humanize=command.humanize,
                )
                for command in commands
            ]
            for session in sessions:
                await self._repository.add(session)

        for session in sessions:
            await self._publish(
                "session.created",
                session,
                message="Ephemeral browser job queued",
            )
        return sessions

    async def set_phase(
        self, session_id: str, phase: SessionPhase, *, message: str
    ) -> BrowserSession:
        async with self._lock_for(session_id):
            session = await self.get(session_id)
            session.set_phase(phase)
            await self._repository.update(session)
        await self._publish(
            "session.phase_changed",
            session,
            phase=phase.value,
            message=message,
        )
        return session

    async def record_rotation(
        self,
        session_id: str,
        *,
        succeeded: bool,
        attempts: int,
        elapsed_seconds: float | None = None,
    ) -> BrowserSession:
        async with self._lock_for(session_id):
            session = await self.get(session_id)
            session.record_rotation(succeeded=succeeded, attempts=attempts)
            await self._repository.update(session)
        await self._publish(
            "proxy.rotation_completed" if succeeded else "proxy.rotation_failed",
            session,
            attempts=attempts,
            elapsed_seconds=round(elapsed_seconds or 0.0, 3),
            message=(
                "Proxy rotation endpoint accepted the request"
                if succeeded
                else "Proxy rotation failed"
            ),
            severity="success" if succeeded else "error",
        )
        return session

    async def fail_ephemeral(self, session_id: str, *, code: str, message: str) -> BrowserSession:
        async with self._lock_for(session_id):
            session = await self.get(session_id)
            if session.status is not SessionStatus.CLOSED:
                session.mark_failed(code, message)
                await self._repository.update(session)
        await self._publish(
            "session.failed",
            session,
            error_code=code,
            message=message[:500],
            severity="error",
        )
        return session

    async def run_ephemeral(self, session_id: str) -> BrowserSession:
        """Launch one isolated browser, hold it briefly, then always tear it down."""
        started = False
        try:
            async with self._lock_for(session_id):
                session = await self.get(session_id)
                if not session.ephemeral:
                    raise InvalidStateTransition("Session is not an ephemeral job")
                if session.status is not SessionStatus.QUEUED:
                    return session
                session.transition(SessionStatus.STARTING)
                session.set_phase(SessionPhase.LAUNCHING)
                await self._repository.update(session)
                await self._publish(
                    "session.starting",
                    session,
                    message="Launching a fresh browser and temporary profile",
                )
                result = await self._runtime.start(session)
                started = True
                session.mark_running(result.current_url)
                session.set_phase(SessionPhase.ACTIVE)
                await self._repository.update(session)
            await self._publish(
                "session.running",
                session,
                url=session.current_url,
                message="Ephemeral browser job is active",
                severity="success",
            )

            await asyncio.sleep(session.auto_close_after_seconds or 0.1)
            return await self.close(session_id, final_phase=SessionPhase.COMPLETED)
        except asyncio.CancelledError:
            await asyncio.shield(self.cancel_ephemeral(session_id))
            raise
        except Exception as exc:
            if started:
                await asyncio.gather(self._runtime.close(session_id), return_exceptions=True)
            return await self.fail_ephemeral(
                session_id,
                code="ephemeral_runtime_failed",
                message=str(exc),
            )

    async def cancel_ephemeral(self, session_id: str) -> BrowserSession:
        session = await self.get(session_id)
        if session.status is SessionStatus.FAILED:
            return session
        return await self.close(session_id, final_phase=SessionPhase.CANCELLED)

    async def get(self, session_id: str) -> BrowserSession:
        session = await self._repository.get(session_id)
        if session is None:
            raise SessionNotFound(f"Browser session {session_id} was not found")
        return session

    async def list(
        self, *, tenant_id: str | None, offset: int, limit: int
    ) -> tuple[list[BrowserSession], int]:
        return await self._repository.list(tenant_id=tenant_id, offset=offset, limit=limit)

    async def navigate(self, session_id: str, url: str) -> BrowserSession:
        async with self._lock_for(session_id):
            session = await self.get(session_id)
            if session.status is not SessionStatus.RUNNING:
                raise InvalidStateTransition("Session must be running before navigation")
            session.record_navigation(url)
            try:
                actual_url = await self._runtime.navigate(session.id, url)
            except Exception as exc:
                await self._publish("session.navigation_failed", session, error=str(exc)[:500])
                raise RuntimeOperationFailed(str(exc)) from exc
            session.current_url = actual_url
            await self._repository.update(session)
        await self._publish("session.navigated", session, url=actual_url)
        return session

    async def upload(
        self, session_id: str, path: Path, *, original_filename: str, size: int
    ) -> UploadResult:
        async with self._lock_for(session_id):
            session = await self.get(session_id)
            if session.status is not SessionStatus.RUNNING:
                raise InvalidStateTransition("Session must be running before upload")
            await self._publish("upload.started", session, filename=original_filename, bytes=size)
            try:
                await self._runtime.upload(session.id, path)
            except Exception as exc:
                await self._publish(
                    "upload.failed",
                    session,
                    filename=original_filename,
                    error=str(exc)[:500],
                )
                raise RuntimeOperationFailed(str(exc)) from exc
        await self._publish("upload.completed", session, filename=original_filename, bytes=size)
        return UploadResult(filename=original_filename, bytes_received=size)

    async def close(
        self,
        session_id: str,
        *,
        final_phase: SessionPhase | None = None,
    ) -> BrowserSession:
        async with self._lock_for(session_id):
            session = await self.get(session_id)
            if session.status is SessionStatus.CLOSED:
                return session
            if session.status is not SessionStatus.CLOSING:
                session.transition(SessionStatus.CLOSING)
                if session.ephemeral:
                    session.set_phase(SessionPhase.CLEANUP)
                await self._repository.update(session)
                await self._publish(
                    "session.closing",
                    session,
                    message="Closing browser and deleting its temporary profile",
                )
            try:
                await self._runtime.close(session.id)
                session.transition(SessionStatus.CLOSED)
                session.error_code = None
                session.error_message = None
                if session.ephemeral:
                    session.set_phase(final_phase or SessionPhase.CANCELLED)
            except Exception as exc:
                session.mark_failed("runtime_close_failed", str(exc))
                await self._repository.update(session)
                await self._publish("session.failed", session, error_code=session.error_code)
                raise RuntimeOperationFailed(str(exc)) from exc
            await self._repository.update(session)
        await self._publish(
            "session.closed",
            session,
            phase=session.phase.value,
            message=(
                "Ephemeral browser closed and temporary profile deleted"
                if session.ephemeral
                else "Browser session closed"
            ),
            severity="success",
        )
        self._session_locks.pop(session_id, None)
        return session

    async def _start_session(self, session_id: str) -> None:
        async with self._lock_for(session_id):
            session = await self.get(session_id)
            if session.status is not SessionStatus.QUEUED:
                return
            session.transition(SessionStatus.STARTING)
            session.set_phase(SessionPhase.LAUNCHING)
            await self._repository.update(session)
            await self._publish("session.starting", session)
            try:
                result = await self._runtime.start(session)
                session.mark_running(result.current_url)
                session.set_phase(SessionPhase.ACTIVE)
                await self._repository.update(session)
            except Exception as exc:
                session.mark_failed("runtime_start_failed", str(exc))
                await self._repository.update(session)
                await self._publish("session.failed", session, error_code=session.error_code)
                return
        await self._publish("session.running", session, url=session.current_url)

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        return self._session_locks.setdefault(session_id, asyncio.Lock())

    async def _publish(self, event_type: str, session: BrowserSession, **payload: object) -> None:
        await self._events.publish(
            DomainEvent(
                type=event_type,
                session_id=session.id,
                tenant_id=session.tenant_id,
                payload={
                    "status": session.status.value,
                    "phase": session.phase.value,
                    **payload,
                },
                occurred_at=datetime.now(UTC).isoformat(),
            )
        )
