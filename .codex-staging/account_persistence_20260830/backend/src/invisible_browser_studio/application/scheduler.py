from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
from collections import deque
from dataclasses import dataclass, field

from .errors import CapacityExceeded
from .ports import JobHandler

logger = logging.getLogger(__name__)


@dataclass(order=True, slots=True)
class _HeapEntry:
    sort_priority: int
    sequence: int
    job: "ScheduledJob" = field(compare=False)


@dataclass(frozen=True, slots=True)
class ScheduledJob:
    job_id: str
    tenant_id: str
    priority: int
    handler: JobHandler


class FairPriorityScheduler:
    """Bounded, work-conserving scheduler with tenant-level round robin.

    Jobs are stored in a priority heap per tenant (O(log n) enqueue/dequeue).
    Workers rotate between active tenants, preventing a large tenant from
    starving smaller tenants while preserving priority inside each tenant.
    """

    def __init__(self, *, workers: int = 4, max_queued: int = 256) -> None:
        if workers < 1:
            raise ValueError("workers must be positive")
        if max_queued < workers:
            raise ValueError("max_queued must be at least workers")
        self._worker_count = workers
        self._max_queued = max_queued
        self._tenant_heaps: dict[str, list[_HeapEntry]] = {}
        self._active_tenants: deque[str] = deque()
        self._active_set: set[str] = set()
        self._known_jobs: set[str] = set()
        self._sequence = itertools.count()
        self._queued = 0
        self._condition = asyncio.Condition()
        self._workers: list[asyncio.Task[None]] = []
        self._accepting = False

    @property
    def queued_count(self) -> int:
        return self._queued

    async def start(self) -> None:
        async with self._condition:
            if self._accepting:
                return
            self._accepting = True
            self._workers = [
                asyncio.create_task(self._worker_loop(index), name=f"scheduler-{index}")
                for index in range(self._worker_count)
            ]

    async def stop(self) -> None:
        async with self._condition:
            self._accepting = False
            self._condition.notify_all()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def submit(
        self,
        *,
        job_id: str,
        tenant_id: str,
        priority: int,
        handler: JobHandler,
    ) -> bool:
        if not 0 <= priority <= 100:
            raise ValueError("priority must be between 0 and 100")
        async with self._condition:
            if not self._accepting:
                raise RuntimeError("scheduler is not running")
            if job_id in self._known_jobs:
                return False
            if self._queued >= self._max_queued:
                raise CapacityExceeded("scheduler queue is full")

            job = ScheduledJob(job_id, tenant_id, priority, handler)
            heap = self._tenant_heaps.setdefault(tenant_id, [])
            heapq.heappush(heap, _HeapEntry(-priority, next(self._sequence), job))
            self._known_jobs.add(job_id)
            self._queued += 1
            if tenant_id not in self._active_set:
                self._active_tenants.append(tenant_id)
                self._active_set.add(tenant_id)
            self._condition.notify(1)
            return True

    async def _take(self) -> ScheduledJob | None:
        async with self._condition:
            await self._condition.wait_for(
                lambda: self._queued > 0 or not self._accepting
            )
            if self._queued == 0:
                return None

            tenant_id = self._active_tenants.popleft()
            self._active_set.remove(tenant_id)
            heap = self._tenant_heaps[tenant_id]
            entry = heapq.heappop(heap)
            self._queued -= 1
            if heap:
                self._active_tenants.append(tenant_id)
                self._active_set.add(tenant_id)
            else:
                del self._tenant_heaps[tenant_id]
            return entry.job

    async def _worker_loop(self, index: int) -> None:
        while True:
            job = await self._take()
            if job is None:
                return
            try:
                await job.handler()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduled job failed", extra={"job_id": job.job_id})
            finally:
                async with self._condition:
                    self._known_jobs.discard(job.job_id)

