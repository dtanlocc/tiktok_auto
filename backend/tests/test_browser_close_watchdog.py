"""Closing a browser must never be able to freeze the backend.

23/09/2026 it did: the driver's shutdown stopped inside os.close() on the
browser pipe, on the event loop, and the API stopped answering - /docs
included - while two browsers stayed alive. asyncio.wait_for cannot fire
while the loop it lives on is the thing that is stuck, so the guard has to be
an ordinary thread.
"""
import asyncio
import time

from app.infrastructure.automation import playwright_adapter as adapter_module
from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter


class _StuckDriver:
    """A driver whose teardown blocks the thread it is awaited on."""

    def __init__(self, released, block_seconds=30.0):
        self._session_token = "token-of-this-session"
        self._released = released
        self._block_seconds = block_seconds
        self.exited = False

    async def __aexit__(self, *_args):
        deadline = time.monotonic() + self._block_seconds
        # Blocking sleep, exactly like os.close() waiting on the reader: the
        # loop cannot run anything else, including its own timeout.
        while time.monotonic() < deadline and not self._released.is_set():
            time.sleep(0.02)
        self.exited = True
        return False


def test_a_frozen_teardown_is_reaped_by_the_watchdog(monkeypatch, tmp_path):
    reaped = []
    released = __import__("threading").Event()

    def fake_reap(token):
        reaped.append(token)
        released.set()          # killing the tree is what ends the blocked call
        return 3

    monkeypatch.setattr(adapter_module, "_reap_session_tree", fake_reap)
    monkeypatch.setattr(adapter_module.settings, "BROWSER_CLOSE_TIMEOUT", 0.2)

    adapter = InvisiblePlaywrightAdapter()
    driver = _StuckDriver(released)
    adapter._invisible_pw = driver
    adapter._temp_profile_path = None

    started = time.monotonic()
    asyncio.run(adapter.close())
    waited = time.monotonic() - started

    assert reaped == ["token-of-this-session"], "the watchdog never reaped the session"
    assert driver.exited, "the teardown never returned"
    # Bounded by the watchdog, not by the 30s the driver wanted to block for.
    assert waited < 10, f"close held the loop for {waited:.1f}s"
    assert adapter._invisible_pw is None


def test_a_clean_teardown_does_not_reap_anything(monkeypatch):
    reaped = []
    monkeypatch.setattr(
        adapter_module, "_reap_session_tree", lambda token: reaped.append(token)
    )
    monkeypatch.setattr(adapter_module.settings, "BROWSER_CLOSE_TIMEOUT", 0.3)

    class _CleanDriver:
        _session_token = "healthy-session"

        async def __aexit__(self, *_args):
            return False

    adapter = InvisiblePlaywrightAdapter()
    adapter._invisible_pw = _CleanDriver()
    adapter._temp_profile_path = None
    asyncio.run(adapter.close())

    # Give a mis-cancelled timer time to misfire.
    time.sleep(0.8)
    assert reaped == [], "a healthy session was killed by the watchdog"
