"""A debug session is the browser the operator most wants to watch.

stream_browser_frames ran in exactly one place, the dispatcher's worker, so a
debug-login session was never streamed. That was invisible while
force_visible put the window on the operator's own screen; from
invisible_playwright 0.24 the session builds its window on a private Win32
desktop, so "visible" is visible to nobody. Measured 24/09/2026: a debug
session open, its browser running, the live screen blank, and no streamer
ever started for it.
"""
import ast
import inspect
from pathlib import Path

from app.use_cases.debug import debug_login_service as service


def _source() -> str:
    return Path(inspect.getfile(service)).read_text(encoding="utf-8")


def test_the_debug_session_starts_a_streamer():
    assert "stream_browser_frames" in _source(), (
        "a debug session opens a browser nobody can see unless it is streamed"
    )


def test_the_streamer_is_told_where_the_window_lives():
    """The private desktop is what a capture thread has to attach to."""
    source = _source()
    for wiring in ("get_hwnd=", "recover_hwnd=", "get_desktop=", "capture_allowed="):
        assert wiring in source, f"the debug streamer is missing {wiring}"


def test_the_streamer_is_cancelled_with_the_session():
    """Left running, it would keep photographing a browser that is closing."""
    source = _source()
    assert "streamer_task.cancel()" in source
    tree = ast.parse(source)
    cancels_in_finally = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Try) and node.finalbody:
            body = ast.dump(ast.Module(body=node.finalbody, type_ignores=[]))
            if "streamer_task" in body and "cancel" in body:
                cancels_in_finally = True
    assert cancels_in_finally, "the streamer must stop even when the session raises"


def test_streaming_can_be_switched_off_with_the_same_setting():
    assert "settings.SCREEN_STREAM_ENABLED" in _source()
