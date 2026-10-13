"""Re-apply this project's `close()` fix to the Juggler connection.

⛔ WHY THIS EXISTS AT ALL. The fix used to live in a fork of
invisible_playwright, in `_juggler/connection.py`. Upstream 0.30.0 deleted that
file: the Juggler client moved into `invisible_core.juggler`, shared with
invisible-selenium and invisible-puppeteer. The code came across unchanged, so
`invisible_core.juggler.connection.Connection.close` is once again the version
that closes BOTH descriptors in one loop - verified by reading
invisible_core 38.34.0 off PyPI on 10/10/2026.

⛔ WHAT THAT BUG DID, measured 23/09/2026 and not theoretical. Both file
descriptors were closed in one loop, the READ end included, while `_read_loop`
was sitting inside `os.read` on it. On Windows that call takes the descriptor's
CRT lock and does not return until the read does. Two callers reached close at
once - the application's teardown and Juggler's own `op_close` - both stopped
on the read end, the browser stayed alive, and because the teardown was awaited
on an asyncio loop the whole server stopped answering with it. Fourteen minutes
and counting when it was found.

The order below is the fix: closing the write end IS the exit command, so the
read end can wait for the process to be gone. If it never goes, one leaked
descriptor on a daemon thread is cheaper than a frozen caller.

⛔ A PATCH THAT STOPS APPLYING MUST BREAK LOUDLY, not quietly. The patched
`__init__` refuses when the instance no longer carries the attributes this
rewrite reads, instead of installing a `close` that would raise on the first
teardown - and `tests/test_juggler_close_patch.py` fails on the next upstream
bump that moves them. Reported upstream as well; if it is taken, this file goes.

⛔ AND THAT CHECK MUST NOT READ SOURCE CODE. The first version of `apply()`
used `inspect.getsource`, which has no source to read inside a Nuitka binary:
it raised `OSError: could not get source code` at import time, so the compiled
backend died at startup while the uncompiled one was fine. A check that only
works in development is not a check.
"""
from __future__ import annotations

import os
import threading
import time

#: Những thuộc tính bản vá này đọc. Thiếu một cái là upstream đã đổi hình dạng
#: lớp, và lúc đó dừng lại ồn ào tốt hơn là chạy tiếp rồi vỡ lúc đóng phiên.
_REQUIRED = ("_to_browser", "_from_browser", "_process", "_closed", "_reader")


def _close_when_reader_is_out(reader: threading.Thread, fd: int) -> None:
    reader.join()
    try:
        os.close(fd)
    except OSError:
        pass


def _patched_close(self, timeout: float = 5.0) -> None:
    """Close the pipe in an order the reader thread can survive."""
    with self._tkauto_close_lock:
        if self._tkauto_close_done:
            return
        self._tkauto_close_done = True
        self._closed = True

        # 1) The exit command: Juggler shuts the browser down when its read
        #    returns zero, which closing the write end causes.
        try:
            os.close(self._to_browser)
        except OSError:
            pass

        process = self._process
        if process is not None:
            # 2) Give the browser its own exit, then insist.
            deadline = time.monotonic() + timeout
            while process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
                grace = time.monotonic() + 2.0
                while process.poll() is None and time.monotonic() < grace:
                    time.sleep(0.05)

        # 3) The read returns as soon as the writer at the other end is gone.
        #    Only then is the descriptor safe to close.
        reader = getattr(self, "_reader", None)
        if reader is not None and reader.is_alive():
            reader.join(timeout=2.0)
            if reader.is_alive():
                # The browser is still holding its end. A thread can wait for
                # that as long as it takes; the caller cannot.
                threading.Thread(
                    target=_close_when_reader_is_out,
                    args=(reader, self._from_browser),
                    daemon=True,
                ).start()
                return
        try:
            os.close(self._from_browser)
        except OSError:
            pass


_patched_close._tkauto_patched = True


def apply() -> bool:
    """Install the fix. True if installed now, False if it already was."""
    from invisible_core.juggler import connection

    target = connection.Connection
    if getattr(target.close, "_tkauto_patched", False):
        return False

    original_init = target.__init__

    def _init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        # ⛔ KIỂM TRÊN INSTANCE, KHÔNG ĐỌC MÃ NGUỒN, và đây là một lỗi đã xảy
        # ra thật chứ không phải một sở thích. Bản đầu của `apply()` dùng
        # `inspect.getsource(target.__init__)` để soi tên thuộc tính. Trong bản
        # Nuitka biên dịch thì KHÔNG CÓ mã nguồn: `inspect.getsource` nổ
        # `OSError: could not get source code` ngay lúc import module, backend
        # chết, launcher kill nó rồi báo một lỗi không nói gì về nguyên nhân -
        # và chạy dev thì không bao giờ thấy, vì dev có source. Tức bản vá chỉ
        # hỏng ở đúng chỗ không ai soi được: một artifact đã phát hành.
        #
        # Kiểm ở đây đúng hơn về bản chất: thuộc tính chỉ TỒN TẠI sau khi
        # `__init__` chạy, nên hỏi chính instance là hỏi đúng thứ cần biết.
        # Giá phải trả là lỗi hiện ra lúc mở trình duyệt thay vì lúc import -
        # vẫn trước mọi lần đóng, nên `close()` không bao giờ chạy trên một lớp
        # mà nó không hiểu.
        missing = [name for name in _REQUIRED if not hasattr(self, name)]
        if missing:
            raise RuntimeError(
                "Khong va duoc close() cho invisible_core.juggler: "
                f"Connection khong con {', '.join(missing)}. "
                "Upstream da doi hinh dang lop - doc lai juggler_close_patch.py "
                "truoc khi go canh bao nay."
            )
        # ⛔ KHOÁ ĐƯỢC TẠO Ở ĐÂY, không tạo lười trong close(): hai luồng cùng
        # vào close sẽ cùng thấy chưa có khoá và cùng tạo một cái riêng, tức
        # không khoá gì cả - mà "hai luồng cùng vào close" chính là tình huống
        # đã treo server.
        self._tkauto_close_lock = threading.Lock()
        self._tkauto_close_done = False

    _init._tkauto_patched = True
    target.__init__ = _init
    target.close = _patched_close
    return True
