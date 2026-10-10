"""The close() fix must still reach invisible_core, and fail loudly if not.

⛔ THIS TEST IS THE EARLY WARNING FOR AN UPSTREAM BUMP. The fix used to live in
a fork of invisible_playwright; 0.30.0 deleted that file and the code moved to
invisible_core.juggler, where it is once again the version that closes both
descriptors in one loop. The patch re-applies it from the outside, and a patch
applied from the outside is exactly the kind that stops matching silently.
"""
import inspect
import threading

import pytest

from app.infrastructure.automation import juggler_close_patch


def test_the_patch_is_installed_on_the_class_upstream_now_owns():
    from invisible_core.juggler import connection

    # Importing the adapter applies it; assert the end state either way.
    juggler_close_patch.apply()
    assert getattr(connection.Connection.close, "_tkauto_patched", False)


def test_the_write_end_closes_first_and_the_read_end_waits():
    """⛔ THE ORDER IS THE WHOLE FIX. Closing the read end while the reader
    thread sits in os.read takes that descriptor's CRT lock on Windows and does
    not return - which is what froze the backend for fourteen minutes."""
    source = inspect.getsource(juggler_close_patch._patched_close)
    write_first = source.index("os.close(self._to_browser)")
    read_later = source.index("os.close(self._from_browser)")
    assert write_first < read_later
    assert "reader.join" in source
    assert "daemon=True" in source, (
        "a browser that refuses to die must cost a thread, not the caller"
    )


def test_two_callers_cannot_both_run_the_teardown():
    """Both the application's teardown and Juggler's own op_close reach close;
    that race is what the lock and the done-flag exist for."""
    class Fake:
        def __init__(self):
            self._tkauto_close_lock = threading.Lock()
            self._tkauto_close_done = False
            self._closed = False
            self._process = None
            self._reader = None
            self.closed_fds = []

    fake = Fake()
    opened = []

    import os as os_module

    real_close = os_module.close

    def record(fd):
        opened.append(fd)

    os_module.close = record
    try:
        fake._to_browser, fake._from_browser = 101, 102
        juggler_close_patch._patched_close(fake)
        juggler_close_patch._patched_close(fake)   # lan hai phai khong lam gi
    finally:
        os_module.close = real_close

    assert opened == [101, 102], f"moi descriptor dong dung mot lan: {opened}"


def test_a_changed_upstream_class_is_refused_not_papered_over(monkeypatch):
    from invisible_core.juggler import connection

    class Reshaped:
        def __init__(self):
            self._something_else = None

        def close(self, timeout: float = 5.0) -> None:
            pass

    monkeypatch.setattr(connection, "Connection", Reshaped)
    with pytest.raises(RuntimeError, match="khong con"):
        juggler_close_patch.apply()
