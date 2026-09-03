"""Trusted Windows file uploads for the firefox-20 file-input regression.

firefox-20 has two independent defects documented by this repository's B178
tests: ``Page.setFileInputFiles`` rejects real paths, while the native chooser
cannot be consumed through Playwright's file-chooser event.  This module keeps
the browser on its normal headed-cloaked renderer and completes the real Windows
chooser without exposing it on the desktop.

The operating system, not page JavaScript, changes the input.  Consequently the
page receives browser-generated ``input`` and ``change`` events with
``isTrusted == true``, and large videos are never copied into a 50 MB-limited
protocol payload.
"""

from __future__ import annotations

import asyncio
import ctypes
import os
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Sequence


_CHOOSER_LOCK = threading.Lock()
_DIALOG_CLASS = "#32770"
_DWMWA_CLOAK = 13
_CLICK_COMPLETION_TIMEOUT_SECONDS = 5.0


class NativeUploadError(RuntimeError):
    """The trusted native chooser could not attach the requested files."""


def _normalise_files(paths: Iterable[os.PathLike[str] | str]) -> list[str]:
    files = [str(Path(value).expanduser().resolve()) for value in paths]
    if not files:
        raise ValueError("At least one file is required.")
    missing = [value for value in files if not Path(value).is_file()]
    if missing:
        raise FileNotFoundError(missing[0])
    return files


def _snapshot_dialogs() -> set[int]:
    import win32gui

    dialogs: set[int] = set()

    def collect(hwnd: int, _extra: Any) -> bool:
        try:
            if win32gui.GetClassName(hwnd) == _DIALOG_CLASS:
                dialogs.add(hwnd)
        except Exception:
            pass
        return True

    win32gui.EnumWindows(collect, None)
    return dialogs


def _is_firefox_dialog(hwnd: int) -> bool:
    import psutil
    import win32process

    try:
        _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
        return Path(psutil.Process(process_id).exe()).name.casefold() == "firefox.exe"
    except Exception:
        return False


def _cloak_and_park(hwnd: int) -> None:
    import win32con
    import win32gui

    value = ctypes.c_int(1)
    cloak_result = ctypes.windll.dwmapi.DwmSetWindowAttribute(
        ctypes.c_void_p(hwnd),
        _DWMWA_CLOAK,
        ctypes.byref(value),
        ctypes.sizeof(value),
    )
    # Windows may deny DWM attributes across integrity levels. Parking remains
    # reliable in that case and is also a second guard for RDP/compositors that
    # do not honour DWMWA_CLOAK.
    parked = False
    try:
        win32gui.SetWindowPos(
            hwnd,
            None,
            -6400,
            -6400,
            0,
            0,
            win32con.SWP_NOSIZE
            | win32con.SWP_NOACTIVATE
            | win32con.SWP_NOZORDER,
        )
        # pywin32 returns None on success; absence of an exception is the
        # success signal.
        parked = True
    except Exception:
        pass
    if not parked and cloak_result != 0:
        raise OSError(
            "Could not cloak or park the Windows chooser "
            f"(DWM HRESULT={cloak_result})."
        )


def _watch_new_dialog(
    before: set[int],
    found: threading.Event,
    stop: threading.Event,
    result: dict[str, Any],
    timeout_seconds: float,
) -> None:
    import win32gui

    deadline = time.monotonic() + timeout_seconds
    while not stop.is_set() and time.monotonic() < deadline:
        for hwnd in _snapshot_dialogs() - before:
            try:
                if not win32gui.IsWindowVisible(hwnd) or not _is_firefox_dialog(hwnd):
                    continue
                _cloak_and_park(hwnd)
                result["hwnd"] = hwnd
                found.set()
                return
            except Exception as exc:
                result["error"] = exc
        time.sleep(0.003)
    found.set()


def _fill_and_accept(hwnd: int, files: Sequence[str]) -> None:
    import win32gui
    from pywinauto import Desktop

    dialog = Desktop(backend="win32").window(handle=hwnd)
    edits = dialog.descendants(class_name="Edit")
    filename = next(
        (item for item in edits if win32gui.IsWindowVisible(item.handle)),
        edits[0] if edits else None,
    )
    if filename is None:
        raise NativeUploadError("Windows file chooser has no File name control.")

    value = files[0] if len(files) == 1 else " ".join(f'"{path}"' for path in files)
    filename.set_edit_text(value)
    if filename.window_text() != value:
        raise NativeUploadError("Windows file chooser did not accept the file path.")

    open_button = dialog.child_window(
        class_name="Button",
        title_re=r"(?i).*(open|mở).*$",
    ).wrapper_object()
    open_button.click()


def _cancel_dialog(hwnd: int | None) -> None:
    if not hwnd:
        return
    try:
        import win32con
        import win32gui

        cancel = win32gui.GetDlgItem(hwnd, 2)
        if cancel:
            win32gui.SendMessage(cancel, win32con.BM_CLICK, 0, 0)
    except Exception:
        pass


async def set_input_files_native(
    locator: Any,
    paths: Iterable[os.PathLike[str] | str],
    *,
    trigger: Any | None = None,
    allow_input_replacement: bool = False,
    timeout_ms: int = 15_000,
) -> None:
    """Attach files through a real, DWM-cloaked Windows chooser.

    ``locator`` is a Playwright Locator for one ``<input type=file>``.  Pass the
    visible label/button that normally opens it as ``trigger`` when the input is
    hidden. Set ``allow_input_replacement`` for reactive pages which remove the
    input immediately after accepting a file; the caller must then verify the
    page's editor/progress state. The function does not focus the dialog or use
    the clipboard. A process-wide lock serialises only the sub-second native
    selection stage so concurrent browser sessions cannot consume one another's
    chooser.
    """

    if os.name != "nt":
        raise NativeUploadError("Native trusted upload is currently Windows-only.")
    files = _normalise_files(paths)
    timeout_seconds = max(1.0, timeout_ms / 1000.0)
    await asyncio.to_thread(_CHOOSER_LOCK.acquire)
    dialog_hwnd: int | None = None
    click_task: asyncio.Task[Any] | None = None
    watcher: threading.Thread | None = None
    stop = threading.Event()
    try:
        multiple = await locator.get_attribute("multiple")
        if len(files) > 1 and multiple is None:
            raise NativeUploadError("The target file input does not allow multiple files.")

        before = await asyncio.to_thread(_snapshot_dialogs)
        found = threading.Event()
        watcher_result: dict[str, Any] = {}
        watcher = threading.Thread(
            target=_watch_new_dialog,
            args=(before, found, stop, watcher_result, timeout_seconds),
            name="invpw-native-file-chooser",
            daemon=True,
        )
        watcher.start()
        click_target = trigger if trigger is not None else locator
        click_task = asyncio.create_task(
            click_target.click(no_wait_after=True, timeout=timeout_ms)
        )

        ready = await asyncio.wait_for(
            asyncio.to_thread(found.wait, timeout_seconds),
            timeout=timeout_seconds + 1,
        )
        if not ready or "hwnd" not in watcher_result:
            error = watcher_result.get("error")
            detail = f": {error}" if error else ""
            raise NativeUploadError(f"Windows file chooser did not appear{detail}")
        dialog_hwnd = int(watcher_result["hwnd"])
        await asyncio.to_thread(_fill_and_accept, dialog_hwnd, files)
        try:
            # Firefox can keep Playwright's click command pending even after
            # the native chooser has accepted the file and closed. The OS
            # chooser is the trusted source of the selection; for reactive
            # inputs the caller explicitly verifies the fresh editor/progress
            # state. Do not turn that harmless protocol lag into a false
            # native-upload failure.
            await asyncio.wait_for(
                asyncio.shield(click_task),
                timeout=_CLICK_COMPLETION_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError as exc:
            if not allow_input_replacement:
                raise NativeUploadError(
                    "Windows chooser accepted the file but the browser click "
                    "command did not settle."
                ) from exc

        expected = len(files)
        actual = 0
        # The native dialog closes before Firefox finishes updating the DOM
        # file list. Wait for that browser-side handoff instead of sampling the
        # input in the same event-loop tick as the Open button.
        dom_deadline = time.monotonic() + 5.0
        while time.monotonic() < dom_deadline:
            try:
                actual = await locator.evaluate(
                    "element => element.files.length",
                    timeout=1_000,
                )
            except Exception:
                if allow_input_replacement:
                    return
                raise
            if int(actual) == expected:
                break
            await asyncio.sleep(0.05)
        if int(actual) != expected:
            raise NativeUploadError(
                f"File chooser closed but the input contains {actual}/{expected} file(s)."
            )
    finally:
        stop.set()
        if click_task is not None:
            if not click_task.done():
                click_task.cancel()
            await asyncio.gather(click_task, return_exceptions=True)
        if watcher is not None:
            await asyncio.to_thread(watcher.join, 1.0)
        _cancel_dialog(dialog_hwnd)
        _CHOOSER_LOCK.release()
