from __future__ import annotations

import asyncio
import base64
from pathlib import Path

from invisible_browser_studio.application.dto import RuntimeStartResult
from invisible_browser_studio.application.ports import BrowserRuntime, FramePublisher
from invisible_browser_studio.domain import BrowserSession

# Valid 1x1 JPEG. The adapter remains deterministic and dependency-free.
_PLACEHOLDER_JPEG = base64.b64decode(
    b"/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////2wBDAf//////////////////////////////////////////////////////////////////////////////////////wAARCAABAAEDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAf/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/9oADAMBAAIQAxAAAAF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABBQJ//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwF//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPwF//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQAGPwJ//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPyF//9oADAMBAAIAAwAAABAf/8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPxB//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAgEBPxB//8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxB//9k="
)


class SimulatedBrowserRuntime(BrowserRuntime):
    """Safe local runtime used by default and by tests."""

    def __init__(
        self,
        frame_publisher: FramePublisher,
        *,
        start_delay_seconds: float = 0.01,
        frame_interval_seconds: float = 1.0,
    ) -> None:
        self._frame_publisher = frame_publisher
        self._start_delay = start_delay_seconds
        self._frame_interval = frame_interval_seconds
        self._urls: dict[str, str] = {}
        self._frame_tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def start(self, session: BrowserSession) -> RuntimeStartResult:
        await asyncio.sleep(self._start_delay)
        async with self._lock:
            if session.id in self._urls:
                return RuntimeStartResult(current_url=self._urls[session.id])
            self._urls[session.id] = session.start_url
            self._frame_tasks[session.id] = asyncio.create_task(
                self._emit_frames(session.id), name=f"simulated-frame-{session.id}"
            )
        return RuntimeStartResult(current_url=session.start_url)

    async def close(self, session_id: str) -> None:
        async with self._lock:
            self._urls.pop(session_id, None)
            task = self._frame_tasks.pop(session_id, None)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def navigate(self, session_id: str, url: str) -> str:
        async with self._lock:
            if session_id not in self._urls:
                raise RuntimeError("browser session is not running")
            self._urls[session_id] = url
        return url

    async def upload(self, session_id: str, path: Path) -> None:
        async with self._lock:
            if session_id not in self._urls:
                raise RuntimeError("browser session is not running")
        if not path.is_file():
            raise FileNotFoundError(path)

    async def shutdown(self) -> None:
        async with self._lock:
            tasks = tuple(self._frame_tasks.values())
            self._frame_tasks.clear()
            self._urls.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _emit_frames(self, session_id: str) -> None:
        try:
            while True:
                await self._frame_publisher.publish_frame(session_id, _PLACEHOLDER_JPEG)
                await asyncio.sleep(self._frame_interval)
        except asyncio.CancelledError:
            raise

