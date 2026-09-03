from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from invisible_browser_studio.application.ports import DomainEvent


class InMemoryEventBroker:
    def __init__(self, *, subscriber_buffer: int = 256) -> None:
        self._subscriber_buffer = subscriber_buffer
        self._subscribers: set[asyncio.Queue[DomainEvent]] = set()
        self._lock = asyncio.Lock()

    async def publish(self, event: DomainEvent) -> None:
        async with self._lock:
            subscribers = tuple(self._subscribers)
        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[DomainEvent]]:
        queue: asyncio.Queue[DomainEvent] = asyncio.Queue(self._subscriber_buffer)
        async with self._lock:
            self._subscribers.add(queue)
        try:
            yield queue
        finally:
            async with self._lock:
                self._subscribers.discard(queue)


class LatestFrameBroker:
    """Fan-out broker with one pending frame per viewer.

    A slow client loses old frames instead of increasing memory usage or latency.
    """

    def __init__(self, *, max_frame_bytes: int = 8 * 1024 * 1024) -> None:
        self._max_frame_bytes = max_frame_bytes
        self._latest: dict[str, bytes] = {}
        self._subscribers: dict[str, set[asyncio.Queue[bytes]]] = {}
        self._lock = asyncio.Lock()

    async def publish_frame(self, session_id: str, frame: bytes) -> None:
        if not frame:
            return
        if len(frame) > self._max_frame_bytes:
            raise ValueError("frame exceeds configured byte limit")
        immutable_frame = bytes(frame)
        async with self._lock:
            self._latest[session_id] = immutable_frame
            subscribers = tuple(self._subscribers.get(session_id, set()))
        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(immutable_frame)

    @asynccontextmanager
    async def subscribe(self, session_id: str) -> AsyncIterator[asyncio.Queue[bytes]]:
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=1)
        async with self._lock:
            self._subscribers.setdefault(session_id, set()).add(queue)
            latest = self._latest.get(session_id)
        if latest:
            queue.put_nowait(latest)
        try:
            yield queue
        finally:
            async with self._lock:
                subscribers = self._subscribers.get(session_id)
                if subscribers:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._subscribers.pop(session_id, None)

    async def discard(self, session_id: str) -> None:
        async with self._lock:
            self._latest.pop(session_id, None)

