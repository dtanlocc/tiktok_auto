"""Work that needs a desktop must not ride a reused pool thread.

Measured 24/09/2026 on a backend that had already run several accounts: an
outside process attached to the live session's desktop and saw its Firefox
window immediately, while the backend's own lookup found nothing and the live
screen stayed blank for the whole batch. SetThreadDesktop refuses a thread
that still holds windows or hooks, and the threads behind asyncio.to_thread
are reused across sessions whose desktops have since been destroyed - and the
refusal was only logged at debug level, so every caller carried on
enumerating the wrong desktop in silence.
"""
import sys
import threading

import pytest

from app.infrastructure.streaming import win_capture


def test_without_a_desktop_the_work_runs_where_it_is():
    """The old behaviour for sessions whose window is on the main desktop."""
    here = threading.current_thread().name
    ran_on = win_capture.run_on_desktop(None, lambda: threading.current_thread().name)
    assert ran_on == here


@pytest.mark.skipif(sys.platform != "win32", reason="desktops are a Windows notion")
def test_a_desktop_that_cannot_be_opened_returns_the_default():
    """"Could not look" must be distinguishable from "not there"."""
    called = []

    def work():
        called.append(True)
        return "should not happen"

    outcome = win_capture.run_on_desktop(
        "desktop_khong_ton_tai_9c1f", work, default="KHONG NHIN DUOC"
    )

    assert outcome == "KHONG NHIN DUOC"
    assert called == [], "work ran on the wrong desktop"


@pytest.mark.skipif(sys.platform != "win32", reason="desktops are a Windows notion")
def test_the_work_gets_a_thread_of_its_own(monkeypatch):
    """A fresh thread per call is what keeps a stale attach from poisoning it."""
    seen = []

    @win_capture.contextlib.contextmanager
    def always_attached(_desktop):
        yield True

    monkeypatch.setattr(win_capture, "thread_on_desktop", always_attached)
    here = threading.current_thread().name

    ran_on = win_capture.run_on_desktop(
        "any_desktop", lambda: threading.current_thread().name
    )
    seen.append(ran_on)

    assert ran_on != here
    assert ran_on.startswith("desktop-work")
