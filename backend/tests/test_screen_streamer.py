import asyncio
import base64
import sys
from types import SimpleNamespace

from app.infrastructure.streaming import screen_streamer
from app.infrastructure.streaming import win_capture


def test_hwnd_lookup_uses_positive_session_token_ownership(monkeypatch):
    process_ids = {101: 9001, 202: 9002}

    class Token:
        def __bool__(self):
            return True

        def matches(self, process):
            return process.pid == 9002

    def enum_windows(callback, extra):
        callback(101, extra)
        callback(202, extra)

    fake_win32gui = SimpleNamespace(
        EnumWindows=enum_windows,
        GetClassName=lambda _hwnd: "MozillaWindowClass",
        GetWindowRect=lambda hwnd: (0, 0, 800 if hwnd == 101 else 1280, 720),
    )
    fake_win32process = SimpleNamespace(
        GetWindowThreadProcessId=lambda hwnd: (1, process_ids[hwnd]),
    )
    fake_psutil = SimpleNamespace(
        Process=lambda process_id: SimpleNamespace(pid=process_id),
    )

    monkeypatch.setattr(win_capture.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "win32gui", fake_win32gui)
    monkeypatch.setitem(sys.modules, "win32process", fake_win32process)
    monkeypatch.setitem(sys.modules, "psutil", fake_psutil)

    assert win_capture.find_session_moz_hwnd(Token()) == 202


def test_windows_hwnd_stream_does_not_use_playwright_screenshot(monkeypatch):
    messages = []
    capture_options = {}

    class Page:
        async def screenshot(self, **_kwargs):
            raise AssertionError("Playwright screenshot must not run when HWND is available")

    async def no_sleep(_seconds):
        return None

    async def direct_to_thread(function, *args):
        return function(*args)

    async def capture_message(message):
        messages.append(message)
        if message["event"] == "BROWSER_FRAME":
            raise asyncio.CancelledError

    monkeypatch.setattr(screen_streamer.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(screen_streamer.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(screen_streamer.sys, "platform", "win32")
    monkeypatch.setattr(screen_streamer, "screens_are_watched", lambda: True)
    def capture_hwnd(_hwnd, max_width, quality):
        capture_options.update(max_width=max_width, quality=quality)
        return b"jpeg-from-print-window"

    monkeypatch.setattr(screen_streamer, "capture_hwnd_jpeg", capture_hwnd)
    monkeypatch.setattr(screen_streamer.screen_ws_manager, "broadcast", capture_message)

    asyncio.run(
        screen_streamer.stream_browser_frames(
            lambda: Page(),
            "account-id",
            "username",
            get_hwnd=lambda: 1234,
        )
    )

    frame = next(message for message in messages if message["event"] == "BROWSER_FRAME")
    assert base64.b64decode(frame["data"]["jpeg_b64"]) == b"jpeg-from-print-window"
    assert capture_options == {"max_width": 1280, "quality": 92}


def test_stream_recovers_after_more_than_fifteen_capture_failures(monkeypatch):
    attempts = 0
    messages = []

    class Page:
        async def screenshot(self, **_kwargs):
            nonlocal attempts
            attempts += 1
            if attempts <= 16:
                raise RuntimeError("page is navigating")
            return b"recovered-frame"

    async def no_sleep(_seconds):
        return None

    async def direct_to_thread(function, *args):
        return function(*args)

    async def capture_message(message):
        messages.append(message)
        if message["event"] == "BROWSER_FRAME":
            raise asyncio.CancelledError

    monkeypatch.setattr(screen_streamer.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(screen_streamer.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(screen_streamer.sys, "platform", "linux")
    monkeypatch.setattr(screen_streamer, "screens_are_watched", lambda: True)
    monkeypatch.setattr(screen_streamer, "downscale_jpeg", lambda raw, *_args: raw)
    monkeypatch.setattr(screen_streamer.screen_ws_manager, "broadcast", capture_message)

    asyncio.run(
        screen_streamer.stream_browser_frames(
            lambda: Page(),
            "account-id",
            "username",
        )
    )

    assert attempts == 17
    frame = next(message for message in messages if message["event"] == "BROWSER_FRAME")
    assert base64.b64decode(frame["data"]["jpeg_b64"]) == b"recovered-frame"


def test_windows_stream_recovers_hwnd_without_playwright_screenshot(monkeypatch):
    messages = []
    recovered = []

    class Page:
        async def screenshot(self, **_kwargs):
            raise AssertionError("Windows HWND stream must not use Playwright screenshot")

    async def no_sleep(_seconds):
        return None

    async def direct_to_thread(function, *args):
        return function(*args)

    async def recover():
        recovered.append(True)
        return 4321

    async def capture_message(message):
        messages.append(message)
        if message["event"] == "BROWSER_FRAME":
            raise asyncio.CancelledError

    monkeypatch.setattr(screen_streamer.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(screen_streamer.asyncio, "to_thread", direct_to_thread)
    monkeypatch.setattr(screen_streamer.sys, "platform", "win32")
    monkeypatch.setattr(screen_streamer, "screens_are_watched", lambda: True)
    monkeypatch.setattr(
        screen_streamer,
        "capture_hwnd_jpeg",
        lambda hwnd, *_args: b"token-owned-frame" if hwnd == 4321 else None,
    )
    monkeypatch.setattr(screen_streamer.screen_ws_manager, "broadcast", capture_message)

    asyncio.run(
        screen_streamer.stream_browser_frames(
            lambda: Page(),
            "account-id",
            "username",
            get_hwnd=lambda: None,
            recover_hwnd=recover,
        )
    )

    assert recovered == [True]
    frame = next(message for message in messages if message["event"] == "BROWSER_FRAME")
    assert base64.b64decode(frame["data"]["jpeg_b64"]) == b"token-owned-frame"
