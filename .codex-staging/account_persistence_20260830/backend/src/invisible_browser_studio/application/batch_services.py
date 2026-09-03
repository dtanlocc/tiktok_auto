from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime

from invisible_browser_studio.domain import (
    AutomationBatch,
    BatchStatus,
    ProxyConfig,
    SessionPhase,
    SessionStatus,
)

from .dto import CreateAutomationBatchCommand, CreateSessionCommand
from .errors import ApplicationError, BatchNotFound
from .ports import BatchRepository, DomainEvent, EventPublisher, ProxyRotator
from .services import BrowserSessionService

_TERMINAL_BATCH_STATUSES = {
    BatchStatus.COMPLETED,
    BatchStatus.COMPLETED_WITH_ERRORS,
    BatchStatus.CANCELLED,
    BatchStatus.FAILED,
}


class AutomationBatchService:
    """Runs bounded parallel ephemeral browser jobs without persisting profiles."""

    def __init__(
        self,
        *,
        repository: BatchRepository,
        sessions: BrowserSessionService,
        rotator: ProxyRotator,
        events: EventPublisher,
        max_jobs: int,
        max_concurrency: int,
    ) -> None:
        self._repository = repository
        self._sessions = sessions
        self._rotator = rotator
        self._events = events
        self._max_jobs = max(1, max_jobs)
        self._max_concurrency = max(1, max_concurrency)
        self._global_gate = asyncio.Semaphore(self._max_concurrency)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._task_lock = asyncio.Lock()
        self._progress_lock = asyncio.Lock()
        self._commands: dict[str, CreateAutomationBatchCommand] = {}

    @property
    def max_jobs(self) -> int:
        return self._max_jobs

    @property
    def max_concurrency(self) -> int:
        return self._max_concurrency

    async def initialize(self) -> None:
        await self._repository.initialize()

    async def create(self, command: CreateAutomationBatchCommand) -> AutomationBatch:
        if not 1 <= command.total_jobs <= self._max_jobs:
            raise ApplicationError(f"total_jobs must be between 1 and {self._max_jobs}")
        if not 1 <= command.concurrency <= self._max_concurrency:
            raise ApplicationError(f"concurrency must be between 1 and {self._max_concurrency}")
        if command.concurrency > command.total_jobs:
            raise ApplicationError("concurrency cannot exceed total_jobs")
        if command.proxy and command.proxies:
            raise ApplicationError("use either proxy or proxies, not both")

        proxies = command.proxies or ((command.proxy,) if command.proxy else ())
        if len(proxies) > self._max_jobs:
            raise ApplicationError(f"proxy count cannot exceed {self._max_jobs}")

        batch_id = str(uuid.uuid4())
        batch = AutomationBatch(
            id=batch_id,
            tenant_id=command.tenant_id,
            display_name=command.display_name,
            start_url=command.start_url,
            mode=command.mode,
            total_jobs=command.total_jobs,
            concurrency=command.concurrency,
            active_seconds=command.active_seconds,
            proxy_server=proxies[0].safe_server() if proxies else None,
            proxy_servers=[proxy.safe_server() for proxy in proxies],
            proxy_auth_required=any(proxy.username is not None for proxy in proxies),
            rotation_enabled=bool(command.rotation_url),
        )
        await self._repository.add(batch)

        label = command.display_name.strip()[:104]
        session_commands = [
            CreateSessionCommand(
                tenant_id=command.tenant_id,
                start_url=command.start_url,
                display_name=(f"{label} · {index + 1:02d}/{command.total_jobs:02d}"),
                mode=command.mode,
                locale=command.locale,
                timezone=command.timezone,
                proxy=(proxies[index % len(proxies)] if proxies else None),
                priority=command.priority,
                batch_id=batch.id,
                ephemeral=True,
                auto_close_after_seconds=command.active_seconds,
            )
            for index in range(command.total_jobs)
        ]
        try:
            sessions = await self._sessions.reserve_ephemeral_batch(session_commands)
        except Exception as exc:
            batch.mark_failed(str(exc))
            await self._repository.update(batch)
            await self._publish("batch.failed", batch, message=str(exc), severity="error")
            raise

        batch.attach_sessions([session.id for session in sessions])
        await self._repository.update(batch)
        self._commands[batch.id] = command
        await self._publish(
            "batch.created",
            batch,
            message=f"Queued {batch.total_jobs} ephemeral browser jobs",
        )
        return batch

    async def start(self, batch_id: str) -> AutomationBatch:
        async with self._task_lock:
            existing_task = self._tasks.get(batch_id)
            if existing_task and not existing_task.done():
                return await self.get(batch_id)
            batch = await self.get(batch_id)
            if batch.status is not BatchStatus.QUEUED:
                raise ApplicationError(f"batch cannot start from {batch.status.value}")
            command = self._commands.get(batch_id)
            if command is None:
                raise ApplicationError("batch launch details are unavailable after backend restart")
            batch.mark_running()
            await self._repository.update(batch)
            task = asyncio.create_task(
                self._run_batch(batch.id, command.rotation_url),
                name=f"automation-batch-{batch.id}",
            )
            self._tasks[batch.id] = task
        await self._publish(
            "batch.running",
            batch,
            message=(
                f"Running up to {batch.concurrency} jobs in parallel "
                f"(global cap {self._max_concurrency})"
            ),
            severity="success",
        )
        return batch

    async def retry(self, batch_id: str) -> AutomationBatch:
        batch = await self.get(batch_id)
        if batch.status not in _TERMINAL_BATCH_STATUSES:
            raise ApplicationError("only a finished or stopped batch can be retried")
        command = self._commands.get(batch_id)
        if command is None:
            if batch.proxy_auth_required or batch.rotation_enabled:
                raise ApplicationError(
                    "recreate this queue to re-enter proxy credentials or rotation details"
                )
            command = CreateAutomationBatchCommand(
                tenant_id=batch.tenant_id,
                display_name=batch.display_name,
                start_url=batch.start_url,
                mode=batch.mode,
                total_jobs=batch.total_jobs,
                concurrency=batch.concurrency,
                active_seconds=batch.active_seconds,
                proxies=tuple(ProxyConfig(server=item) for item in batch.proxy_servers),
            )
        retried = await self.create(
            replace(command, display_name=f"{batch.display_name[:118]} retry")
        )
        return await self.start(retried.id)

    async def get(self, batch_id: str) -> AutomationBatch:
        batch = await self._repository.get(batch_id)
        if batch is None:
            raise BatchNotFound(f"Automation batch {batch_id} was not found")
        return batch

    async def list(self, *, offset: int, limit: int) -> tuple[list[AutomationBatch], int]:
        return await self._repository.list(offset=offset, limit=limit)

    async def cancel(self, batch_id: str) -> AutomationBatch:
        batch = await self.get(batch_id)
        if batch.status in _TERMINAL_BATCH_STATUSES:
            return batch
        batch.mark_cancelling()
        await self._repository.update(batch)
        await self._publish(
            "batch.cancelling",
            batch,
            message="Cancelling queued jobs and closing active browsers",
            severity="warning",
        )
        async with self._task_lock:
            task = self._tasks.get(batch_id)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        else:
            await self._cancel_remaining(batch)
        return await self.get(batch_id)

    async def shutdown(self) -> None:
        async with self._task_lock:
            tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_batch(self, batch_id: str, rotation_url: str | None) -> None:
        workers: list[asyncio.Task[None]] = []
        try:
            batch = await self.get(batch_id)
            if batch.status is BatchStatus.CANCELLING:
                await self._cancel_remaining(batch)
                return
            if batch.status is not BatchStatus.RUNNING:
                raise ApplicationError(f"batch cannot run from {batch.status.value}")

            queue: asyncio.Queue[str] = asyncio.Queue()
            for session_id in batch.session_ids:
                queue.put_nowait(session_id)
            workers = [
                asyncio.create_task(
                    self._worker(batch.id, queue, rotation_url),
                    name=f"batch-{batch.id}-worker-{index}",
                )
                for index in range(batch.concurrency)
            ]
            await queue.join()
            await self._stop_workers(workers)
            workers.clear()

            batch = await self._refresh_progress(batch.id)
            batch.mark_finished()
            await self._repository.update(batch)
            await self._publish(
                "batch.completed",
                batch,
                message=(
                    f"Batch finished: {batch.completed_jobs} completed, {batch.failed_jobs} failed"
                ),
                severity="success" if not batch.failed_jobs else "warning",
            )
        except asyncio.CancelledError:
            await self._stop_workers(workers)
            batch = await self.get(batch_id)
            await self._cancel_remaining(batch)
        except Exception as exc:
            await self._stop_workers(workers)
            batch = await self.get(batch_id)
            batch.mark_failed(str(exc))
            await self._repository.update(batch)
            await self._publish("batch.failed", batch, message=str(exc), severity="error")
        finally:
            async with self._task_lock:
                self._tasks.pop(batch_id, None)

    async def _worker(
        self,
        batch_id: str,
        queue: asyncio.Queue[str],
        rotation_url: str | None,
    ) -> None:
        while True:
            session_id = await queue.get()
            try:
                async with self._global_gate:
                    if rotation_url:
                        await self._sessions.set_phase(
                            session_id,
                            SessionPhase.ROTATING_PROXY,
                            message="Calling the configured proxy rotation endpoint",
                        )
                        try:
                            result = await self._rotator.rotate(rotation_url)
                        except Exception as exc:
                            await self._sessions.record_rotation(
                                session_id, succeeded=False, attempts=0
                            )
                            await self._sessions.fail_ephemeral(
                                session_id,
                                code="proxy_rotation_failed",
                                message=str(exc),
                            )
                            continue
                        await self._sessions.record_rotation(
                            session_id,
                            succeeded=True,
                            attempts=result.attempts,
                            elapsed_seconds=result.elapsed_seconds,
                        )
                    await self._sessions.run_ephemeral(session_id)
            finally:
                queue.task_done()
                await self._refresh_progress(batch_id)

    async def _refresh_progress(self, batch_id: str) -> AutomationBatch:
        # Several workers can finish within the same event-loop tick. Serializing
        # reconciliation prevents an older snapshot from overwriting newer counters.
        async with self._progress_lock:
            batch = await self.get(batch_id)
            sessions = [await self._sessions.get(item) for item in batch.session_ids]
            completed = sum(session.phase is SessionPhase.COMPLETED for session in sessions)
            failed = sum(session.status is SessionStatus.FAILED for session in sessions)
            cancelled = sum(session.phase is SessionPhase.CANCELLED for session in sessions)
            batch.reconcile(completed=completed, failed=failed, cancelled=cancelled)
            await self._repository.update(batch)
            return batch

    async def _cancel_remaining(self, batch: AutomationBatch) -> None:
        await asyncio.gather(
            *(self._cancel_if_active(session_id) for session_id in batch.session_ids),
            return_exceptions=True,
        )
        batch = await self._refresh_progress(batch.id)
        batch.mark_cancelled()
        await self._repository.update(batch)
        await self._publish(
            "batch.cancelled",
            batch,
            message="Batch cancelled; active browsers and profiles were cleaned up",
            severity="warning",
        )

    async def _cancel_if_active(self, session_id: str) -> None:
        session = await self._sessions.get(session_id)
        if session.status not in {SessionStatus.CLOSED, SessionStatus.FAILED}:
            await self._sessions.cancel_ephemeral(session_id)

    @staticmethod
    async def _stop_workers(workers: list[asyncio.Task[None]]) -> None:
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)

    async def _publish(self, event_type: str, batch: AutomationBatch, **payload: object) -> None:
        await self._events.publish(
            DomainEvent(
                type=event_type,
                session_id=None,
                tenant_id=batch.tenant_id,
                payload={
                    "batch_id": batch.id,
                    "status": batch.status.value,
                    "finished_jobs": batch.finished_jobs,
                    "total_jobs": batch.total_jobs,
                    **payload,
                },
                occurred_at=datetime.now(UTC).isoformat(),
            )
        )
