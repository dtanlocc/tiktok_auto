from __future__ import annotations

import asyncio
from copy import deepcopy

from invisible_browser_studio.application.ports import BatchRepository
from invisible_browser_studio.domain import AutomationBatch


class InMemoryBatchRepository(BatchRepository):
    """Process-local batch metadata; browser profiles are never persisted here."""

    def __init__(self) -> None:
        self._batches: dict[str, AutomationBatch] = {}
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def add(self, batch: AutomationBatch) -> None:
        async with self._lock:
            if batch.id in self._batches:
                raise ValueError(f"Batch {batch.id} already exists")
            self._batches[batch.id] = deepcopy(batch)

    async def get(self, batch_id: str) -> AutomationBatch | None:
        async with self._lock:
            batch = self._batches.get(batch_id)
            return deepcopy(batch) if batch else None

    async def list(
        self, *, offset: int, limit: int
    ) -> tuple[list[AutomationBatch], int]:
        async with self._lock:
            batches = sorted(
                self._batches.values(),
                key=lambda item: item.created_at,
                reverse=True,
            )
            return deepcopy(batches[offset : offset + limit]), len(batches)

    async def update(self, batch: AutomationBatch) -> None:
        async with self._lock:
            if batch.id not in self._batches:
                raise KeyError(batch.id)
            current = self._batches[batch.id]
            if batch.revision < current.revision:
                raise RuntimeError("Stale batch update")
            self._batches[batch.id] = deepcopy(batch)
