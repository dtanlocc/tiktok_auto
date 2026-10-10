"""Trusted Windows file uploads for the patched engine's B178 path regression.

firefox-21 delivers Playwright's file-chooser event, but the repository's B178
tests still document that ``Page.setFileInputFiles`` rejects real paths. This
module keeps the browser on its normal headed-cloaked renderer and completes the
real Windows chooser without exposing it on the desktop.

The operating system, not page JavaScript, changes the input.  Consequently the
page receives browser-generated ``input`` and ``change`` events with
``isTrusted == true``, and large videos are never copied into a 50 MB-limited
protocol payload.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import functools
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence


_CHOOSER_LOCK = threading.Lock()
_DIALOG_CLASS = "#32770"
_DWMWA_CLOAK = 13
_FILE_NAME_CONTROL_ID = 1148
_CLICK_COMPLETION_TIMEOUT_SECONDS = 5.0
_CHOOSER_LOCK_WAIT_TIMEOUT_SECONDS = 90.0
_CONTROL_READY_TIMEOUT_SECONDS = 5.0
_DIALOG_ACCEPT_TIMEOUT_SECONDS = 12.0
_WM_GETTEXT = 0x000D
_WM_GETTEXTLENGTH = 0x000E
_WM_COMMAND = 0x0111
_WM_NEXTDLGCTL = 0x0028
_IDOK = 1
_SMTO_BLOCK = 0x0001
_SMTO_ABORTIFHUNG = 0x0002


# ⛔ FROM 0.24 THE BROWSER - AND ITS FILE CHOOSER - LIVE ON A PRIVATE DESKTOP.
# `headless=True` builds the session on a desktop of its own (`CreateDesktopW`),
# and a thread attached to the ordinary desktop enumerates NONE of its windows:
# the chooser would never be found, the click would look like it opened nothing.
# The name is set by `set_input_files_native` while it holds the chooser lock,
# so a second session cannot see the wrong desktop, and every window-facing
# helper below runs attached to it. Empty (the pre-0.24 shape) changes nothing.
_ACTIVE_DESKTOP: str | None = None
_DESKTOP_ALL_ACCESS = 0x000F01FF


@contextlib.contextmanager
def _thread_on_desktop(name: str | None):
    """Attach THIS thread to `name` for the duration of the block."""
    if os.name != "nt" or not name:
        yield True
        return
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenDesktopW.restype = wintypes.HANDLE
    user32.OpenDesktopW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    user32.GetThreadDesktop.argtypes = (wintypes.DWORD,)
    user32.SetThreadDesktop.restype = wintypes.BOOL
    user32.SetThreadDesktop.argtypes = (wintypes.HANDLE,)
    user32.CloseDesktop.restype = wintypes.BOOL
    user32.CloseDesktop.argtypes = (wintypes.HANDLE,)

    previous = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    handle = user32.OpenDesktopW(name, 0, False, _DESKTOP_ALL_ACCESS)
    if not handle:
        raise NativeUploadError(
            "Could not open the browser desktop %s (WinError %d)"
            % (name, ctypes.get_last_error())
        )
    attached = bool(user32.SetThreadDesktop(handle))
    try:
        if not attached:
            raise NativeUploadError(
                "Could not attach to the browser desktop %s (WinError %d)"
                % (name, ctypes.get_last_error())
            )
        yield attached
    finally:
        if attached and previous:
            user32.SetThreadDesktop(previous)
        user32.CloseDesktop(handle)


def _on_browser_desktop(function):
    """Run a window-facing helper where the browser's windows actually are."""

    @functools.wraps(function)
    def wrapper(*args, **kwargs):
        with _thread_on_desktop(_ACTIVE_DESKTOP):
            return function(*args, **kwargs)

    return wrapper


async def _acquire_chooser_lock(
    timeout_seconds: float = _CHOOSER_LOCK_WAIT_TIMEOUT_SECONDS,
) -> None:
    """Acquire the process-wide chooser lock without leaking it on cancel.

    ``asyncio.to_thread(lock.acquire)`` cannot cancel the underlying blocking
    worker.  If its coroutine is cancelled while waiting, that orphaned worker
    can acquire the lock later and never release it, permanently blocking every
    following upload.  Polling a non-blocking acquire keeps ownership in the
    coroutine which will also run the matching ``finally`` block.
    """

    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    while True:
        if _CHOOSER_LOCK.acquire(blocking=False):
            return
        if time.monotonic() >= deadline:
            raise NativeUploadError(
                "Windows file chooser remained busy for "
                f"{float(timeout_seconds):g}s."
            )
        await asyncio.sleep(0.05)


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


@_on_browser_desktop
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

    # EnumWindows can fail transiently when a shell dialog is destroyed while
    # Windows is enumerating its out-of-process controls. Handles collected
    # before that race are still useful; a later polling pass sees the rest.
    try:
        win32gui.EnumWindows(collect, None)
    except Exception:
        pass
    return dialogs


def _is_firefox_dialog(
    hwnd: int,
    owner_process_ids: frozenset[int] | None = None,
    owner_session_token: Any | None = None,
) -> bool:
    import psutil
    import win32process

    try:
        _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
        process = psutil.Process(process_id)
        if owner_session_token is not None:
            if not owner_session_token.matches(process):
                return False
        elif owner_process_ids is not None and process_id not in owner_process_ids:
            return False
        return Path(process.exe()).name.casefold() == "firefox.exe"
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


def _activate_hidden_dialog_offscreen(hwnd: int) -> None:
    """Give a pre-show common dialog an operable UI thread without flashing."""
    import win32con
    import win32gui

    # Position it before setting WS_VISIBLE so even compositors that apply DWM
    # cloak one frame late never draw it on the user's desktop.
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
    win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
    _cloak_and_park(hwnd)
    time.sleep(0.15)


@_on_browser_desktop
def _watch_new_dialog(
    before: set[int],
    found: threading.Event,
    stop: threading.Event,
    result: dict[str, Any],
    timeout_seconds: float,
    owner_process_ids: frozenset[int] | None = None,
    owner_session_token: Any | None = None,
) -> None:
    import psutil
    import win32gui
    import win32process

    deadline = time.monotonic() + timeout_seconds
    observed: dict[int, dict[str, Any]] = {}
    while not stop.is_set() and time.monotonic() < deadline:
        for hwnd in _snapshot_dialogs() - before:
            try:
                visible = bool(win32gui.IsWindowVisible(hwnd))
                if hwnd not in observed:
                    _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
                    try:
                        process_name = Path(psutil.Process(process_id).exe()).name
                    except Exception:
                        process_name = "<unreadable>"
                    observed[hwnd] = {
                        "hwnd": int(hwnd),
                        "pid": int(process_id),
                        "process": process_name,
                        "visible": visible,
                        "title": win32gui.GetWindowText(hwnd)[:120],
                    }
                    result["observed"] = list(observed.values())
                # A chooser created by a DWM-cloaked Firefox session can have
                # WS_VISIBLE cleared from its first frame. It is still a real,
                # fully operable #32770 dialog. Process ownership and creation
                # after the click are the authoritative identity checks.
                if not _is_firefox_dialog(
                    hwnd, owner_process_ids, owner_session_token
                ):
                    continue
                # A pre-show chooser must be shown once offscreen before its
                # filename control accepts focus/edit notifications. A chooser
                # Windows already marked visible can be cloaked immediately.
                if visible:
                    _cloak_and_park(hwnd)
                else:
                    _activate_hidden_dialog_offscreen(hwnd)
                result["hwnd"] = hwnd
                found.set()
                return
            except Exception as exc:
                result["error"] = exc
        time.sleep(0.003)
    found.set()


def _read_control_text(hwnd: int, timeout_ms: int = 1_000) -> tuple[str, bool]:
    """Read another process' Edit text with bounded Win32 messages.

    GetWindowText intentionally does not return control text owned by another
    process. WM_GETTEXT is a system message, so Windows marshals it across the
    boundary. The boolean says whether this authoritative read was available.
    """
    import win32gui

    user32 = ctypes.windll.user32
    send_timeout = getattr(user32, "SendMessageTimeoutW", None)
    if not callable(send_timeout):
        try:
            return str(win32gui.GetWindowText(hwnd) or ""), False
        except Exception:
            return "", False

    try:
        from ctypes import wintypes

        send_timeout.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_size_t),
        ]
        send_timeout.restype = wintypes.LPARAM
        flags = _SMTO_BLOCK | _SMTO_ABORTIFHUNG
        length_result = ctypes.c_size_t(0)
        ok = send_timeout(
            ctypes.c_void_p(hwnd),
            _WM_GETTEXTLENGTH,
            0,
            0,
            flags,
            max(50, int(timeout_ms)),
            ctypes.byref(length_result),
        )
        if not ok:
            return "", False
        buffer = ctypes.create_unicode_buffer(max(1, int(length_result.value) + 1))
        copied_result = ctypes.c_size_t(0)
        ok = send_timeout(
            ctypes.c_void_p(hwnd),
            _WM_GETTEXT,
            len(buffer),
            ctypes.cast(buffer, ctypes.c_void_p).value or 0,
            flags,
            max(50, int(timeout_ms)),
            ctypes.byref(copied_result),
        )
        if not ok:
            return "", False
        return buffer.value, True
    except Exception:
        try:
            return str(win32gui.GetWindowText(hwnd) or ""), False
        except Exception:
            return "", False


def _focus_dialog_control(hwnd: int, control: int) -> None:
    """Attach briefly to the dialog thread so its edit really receives focus."""
    import win32gui

    user32 = ctypes.windll.user32
    kernel32 = getattr(ctypes.windll, "kernel32", None)
    attached = False
    current_thread = 0
    dialog_thread = 0
    try:
        if kernel32 is not None:
            current_thread = int(kernel32.GetCurrentThreadId())
        get_thread = getattr(user32, "GetWindowThreadProcessId", None)
        if callable(get_thread):
            dialog_thread = int(get_thread(ctypes.c_void_p(hwnd), None))
        attach = getattr(user32, "AttachThreadInput", None)
        if (
            callable(attach)
            and current_thread
            and dialog_thread
            and current_thread != dialog_thread
        ):
            attached = bool(attach(current_thread, dialog_thread, True))
        for method_name in (
            "BringWindowToTop",
            "SetForegroundWindow",
            "SetActiveWindow",
        ):
            method = getattr(user32, method_name, None)
            if callable(method):
                try:
                    method(ctypes.c_void_p(hwnd))
                except Exception:
                    pass
        win32gui.SendMessage(hwnd, _WM_NEXTDLGCTL, control, 1)
        set_focus = getattr(user32, "SetFocus", None)
        if callable(set_focus):
            set_focus(ctypes.c_void_p(control))
        else:
            win32gui.SetFocus(control)
    except Exception:
        try:
            win32gui.SendMessage(hwnd, _WM_NEXTDLGCTL, control, 1)
            win32gui.SetFocus(control)
        except Exception:
            pass
    finally:
        if attached:
            try:
                user32.AttachThreadInput(current_thread, dialog_thread, False)
            except Exception:
                pass


def _notify_filename_changed(hwnd: int, filename: int, control_id: int) -> None:
    """Synchronise the Explorer-style dialog model with its filename Edit."""
    user32 = ctypes.windll.user32
    for notification in (0x0400, 0x0300):  # EN_UPDATE, EN_CHANGE
        user32.SendMessageW(
            ctypes.c_void_p(hwnd),
            _WM_COMMAND,
            (control_id & 0xFFFF) | (notification << 16),
            filename,
        )


def _post_dialog_accept(hwnd: int, open_button: int) -> None:
    """Queue IDOK on the dialog itself so activation cannot swallow BM_CLICK."""
    import win32gui

    # BM_CLICK can be ignored when a dialog is inactive. The chooser is
    # intentionally offscreen/DWM-cloaked, so send the equivalent command to
    # its dialog manager. PostMessage stays non-blocking if validation opens a
    # child alert for a genuinely invalid filename.
    win32gui.PostMessage(hwnd, _WM_COMMAND, _IDOK, open_button)


@_on_browser_desktop
def _fill_and_accept(hwnd: int, files: Sequence[str]) -> dict[str, Any]:
    import win32con
    import win32gui

    # Keep this path independent from pywinauto. Its backend registry uses
    # dynamic imports and can behave differently after one-file compilation.
    # The common file chooser exposes ordinary Win32 Edit/Button children, so
    # direct messages are sufficient and survive packaging unchanged.
    edits: list[int] = []

    def collect_edit(child: int, _extra: Any) -> bool:
        try:
            if (
                win32gui.GetClassName(child) == "Edit"
                and win32gui.IsWindowEnabled(child)
            ):
                edits.append(int(child))
        except Exception:
            pass
        return True

    value = files[0] if len(files) == 1 else " ".join(f'"{path}"' for path in files)

    def control_id(edit: int) -> int:
        try:
            return int(win32gui.GetDlgCtrlID(edit))
        except Exception:
            return -1

    # EnumWindows can reveal #32770 before Explorer has finished creating and
    # enabling ID 1148/IDOK. Waiting here removes the intermittent race where
    # the Edit exists but cannot accept automation yet.
    filename = 0
    open_button = 0
    ready_deadline = time.monotonic() + _CONTROL_READY_TIMEOUT_SECONDS
    while time.monotonic() < ready_deadline:
        edits.clear()
        try:
            win32gui.EnumChildWindows(hwnd, collect_edit, None)
        except Exception:
            edits.clear()
        filename = next(
            (edit for edit in edits if control_id(edit) == _FILE_NAME_CONTROL_ID),
            next(
                (
                    edit
                    for edit in edits
                    if bool(win32gui.IsWindowVisible(edit))
                ),
                edits[0] if edits else 0,
            ),
        )
        open_button = int(win32gui.GetDlgItem(hwnd, _IDOK) or 0)
        if filename and open_button:
            try:
                if (
                    win32gui.IsWindowEnabled(hwnd)
                    and win32gui.IsWindowEnabled(filename)
                    and win32gui.IsWindowEnabled(open_button)
                ):
                    break
            except Exception:
                break
        time.sleep(0.05)
    if not filename:
        raise NativeUploadError("Windows file chooser has no File name control.")
    if not open_button:
        raise NativeUploadError("Windows file chooser has no Open button.")

    diagnostics: dict[str, Any] = {
        "dialog_visible": bool(win32gui.IsWindowVisible(hwnd)),
        "edit_control_ids": [control_id(edit) for edit in edits],
        "selected_control_id": control_id(filename),
        "controls_ready": True,
    }
    _focus_dialog_control(hwnd, filename)
    # WM_SETTEXT is a system message, so Windows safely marshals its string
    # across the process boundary. On current Windows 11 common dialogs it is
    # also the only path that survives a headed-but-DWM-cloaked Firefox window.
    # GetWindowText is not a valid cross-process readback for an Edit control;
    # _read_control_text uses bounded WM_GETTEXT instead so a successful setter
    # can be distinguished from a chooser that silently discarded the value.
    wm_settext_sent = False
    try:
        diagnostics["wm_settext_result"] = win32gui.SendMessage(
            filename, win32con.WM_SETTEXT, 0, value
        )
        wm_settext_sent = True
    except Exception as exc:
        diagnostics["wm_settext_error"] = f"{type(exc).__name__}: {exc}"
    diagnostics["wm_settext_sent"] = wm_settext_sent

    user32 = ctypes.windll.user32
    try:
        from ctypes import wintypes

        user32.SendMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.SendMessageW.restype = ctypes.c_ssize_t
    except (AttributeError, TypeError):
        # Unit doubles need not implement ctypes function metadata.
        pass
    control = control_id(filename) & 0xFFFF
    _notify_filename_changed(hwnd, filename, control)
    time.sleep(0.12)
    actual_text, reliable_readback = _read_control_text(filename)
    diagnostics["wm_gettext_reliable"] = reliable_readback
    diagnostics["wm_settext_readback_matches"] = actual_text == value
    diagnostics["win32_text_matches"] = actual_text == value
    pywinauto_sent = False
    has_supplementary_unicode = any(ord(character) > 0xFFFF for character in value)
    diagnostics["has_supplementary_unicode"] = has_supplementary_unicode
    if has_supplementary_unicode:
        # EditWrapper's EM_REPLACESEL path has corrupted UTF-16 surrogate pairs
        # in real Firefox choosers (for example U+1F35A RICE BALL). WM_SETTEXT
        # is Unicode-safe and the explicit EN_UPDATE/EN_CHANGE notifications
        # below update the common-dialog filename model without rewriting it.
        diagnostics["pywinauto_replace"] = "skipped-supplementary-unicode"
    elif not reliable_readback or actual_text != value:
        try:
            # EditWrapper uses EM_SETSEL + EM_REPLACESEL. Run it for BMP-only
            # paths even when WM_SETTEXT succeeded: some Explorer-style chooser
            # instances need the replace sequence to update their filename model.
            from pywinauto.controls.win32_controls import EditWrapper

            EditWrapper(filename).set_edit_text(value)
            pywinauto_sent = True
            diagnostics["pywinauto_replace"] = "completed"
            _notify_filename_changed(hwnd, filename, control)
            time.sleep(0.12)
            actual_text, pywinauto_readback_reliable = _read_control_text(filename)
            diagnostics["pywinauto_readback_reliable"] = (
                pywinauto_readback_reliable
            )
            diagnostics["pywinauto_text_matches"] = actual_text == value
        except Exception as exc:
            diagnostics["pywinauto_replace"] = f"{type(exc).__name__}: {exc}"
    else:
        diagnostics["pywinauto_replace"] = "not-needed-wm-gettext-confirmed"

    fallback_text, fallback_readback_reliable = _read_control_text(filename)
    diagnostics["fallback_text_matches"] = fallback_text == value
    diagnostics["fallback_readback_reliable"] = fallback_readback_reliable
    # WM_SETTEXT returning non-zero only means the message was processed. If
    # authoritative WM_GETTEXT still disagrees, type into the Edit so its own
    # window procedure emits the same state changes as real keyboard input.
    if (
        (fallback_readback_reliable and fallback_text != value)
        or (
            not fallback_readback_reliable
            and not wm_settext_sent
            and not pywinauto_sent
        )
    ):
        _focus_dialog_control(hwnd, filename)
        user32.SendMessageW(
            ctypes.c_void_p(filename),
            win32con.EM_SETSEL,
            0,
            -1,
        )
        user32.SendMessageW(ctypes.c_void_p(filename), 0x0303, 0, 0)  # WM_CLEAR
        # WM_CHAR transports UTF-16 code units, not Unicode scalar values.
        # Split supplementary characters into their surrogate pair so this
        # final fallback also preserves emoji in Windows paths.
        utf16_units = (
            int.from_bytes(value.encode("utf-16-le")[index:index + 2], "little")
            for index in range(0, len(value.encode("utf-16-le")), 2)
        )
        for code_unit in utf16_units:
            user32.SendMessageW(
                ctypes.c_void_p(filename),
                0x0102,  # WM_CHAR
                code_unit,
                0,
            )
        _notify_filename_changed(hwnd, filename, control)
        time.sleep(0.12)
        fallback_text, wm_char_readback_reliable = _read_control_text(filename)
        diagnostics["wm_char_readback_reliable"] = wm_char_readback_reliable
        diagnostics["wm_char_text_matches"] = fallback_text == value

    # Tell the common-dialog parent that control 1148 changed. Merely setting
    # the Edit text is insufficient on some Windows 10/11 dialog builds: the
    # visible text changes, but IDOK still reads an older empty value.
    _notify_filename_changed(hwnd, filename, control)

    final_text, final_readback_reliable = _read_control_text(filename)
    diagnostics["final_readback_reliable"] = final_readback_reliable
    diagnostics["final_text_matches"] = final_text == value
    if final_readback_reliable and final_text != value:
        raise NativeUploadError(
            "Windows file chooser did not retain the filename. "
            f"Diagnostics: {diagnostics}"
        )

    time.sleep(0.35)

    # IDOK is language-independent, unlike the localized Open caption. Keep the
    # command asynchronous: a genuinely invalid filename can open a modal alert
    # on the chooser thread, and synchronous dispatch would then hold the global
    # chooser lock until someone dismissed that hidden alert.
    _focus_dialog_control(hwnd, filename)
    _post_dialog_accept(hwnd, open_button)
    diagnostics["open_clicked"] = True
    diagnostics["accept_method"] = "WM_COMMAND_IDOK"
    is_window = getattr(win32gui, "IsWindow", None)
    if callable(is_window):
        close_deadline = time.monotonic() + _DIALOG_ACCEPT_TIMEOUT_SECONDS
        while is_window(hwnd) and time.monotonic() < close_deadline:
            time.sleep(0.05)
        diagnostics["dialog_closed_after_open"] = not bool(is_window(hwnd))
        if is_window(hwnd):
            try:
                diagnostics["dialog_enabled_after_open"] = bool(
                    win32gui.IsWindowEnabled(hwnd)
                )
            except Exception:
                pass
    return diagnostics


@_on_browser_desktop
def _cancel_dialog(hwnd: int | None) -> None:
    if not hwnd:
        return
    try:
        import win32con
        import win32gui

        cancel = win32gui.GetDlgItem(hwnd, 2)
        if cancel:
            win32gui.PostMessage(cancel, win32con.BM_CLICK, 0, 0)
        else:
            # The common-dialog validation alert observed on Windows 11 owns
            # one visible ``OK`` button whose control id is 0. GetDlgItem
            # cannot identify it reliably, so enumerate direct children and
            # click the first enabled button. This re-enables the parent
            # chooser, which the next cleanup pass can cancel normally.
            buttons: list[int] = []

            def collect_button(child: int, _extra: Any) -> bool:
                try:
                    if (
                        win32gui.GetClassName(child) == "Button"
                        and win32gui.IsWindowEnabled(child)
                    ):
                        buttons.append(int(child))
                except Exception:
                    pass
                return True

            win32gui.EnumChildWindows(hwnd, collect_button, None)
            if buttons:
                win32gui.PostMessage(buttons[0], win32con.BM_CLICK, 0, 0)
            else:
                win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    except Exception:
        pass


@_on_browser_desktop
def _close_owned_dialogs(
    *,
    owner_process_ids: frozenset[int] | None,
    owner_session_token: Any | None,
    exclude: set[int] | None = None,
    timeout_seconds: float = 1.5,
) -> list[int]:
    """Dismiss chooser/error dialogs belonging to one browser session."""
    import win32gui

    protected = exclude or set()
    deadline = time.monotonic() + max(0.1, timeout_seconds)
    remaining: list[int] = []
    while time.monotonic() < deadline:
        remaining = [
            hwnd
            for hwnd in _snapshot_dialogs()
            if hwnd not in protected
            and _is_firefox_dialog(
                hwnd, owner_process_ids, owner_session_token
            )
        ]
        if not remaining:
            return []
        # Close an enabled validation alert before its disabled parent chooser.
        remaining.sort(
            key=lambda hwnd: bool(win32gui.IsWindowEnabled(hwnd)),
            reverse=True,
        )
        for hwnd in remaining:
            _cancel_dialog(hwnd)
        time.sleep(0.05)
    return remaining


async def set_input_files_native(
    locator: Any,
    paths: Iterable[os.PathLike[str] | str],
    *,
    trigger: Any | None = None,
    allow_input_replacement: bool = False,
    owner_process_ids: Iterable[int] | None = None,
    owner_session_token: Any | None = None,
    on_dialog_active: Callable[[bool], None] | None = None,
    trigger_dwell_ms: int | None = None,
    trigger_click_delay_ms: int | None = None,
    desktop: str | None = None,
    timeout_ms: int = 15_000,
) -> None:
    """Attach files through a real, DWM-cloaked Windows chooser.

    ``locator`` is a Playwright Locator for one ``<input type=file>``.  Pass the
    visible label/button that normally opens it as ``trigger`` when the input is
    hidden. Set ``allow_input_replacement`` for reactive pages which remove the
    input immediately after accepting a file; the caller must then verify the
    page's editor/progress state. The function briefly focuses an offscreen,
    DWM-cloaked dialog and never uses the clipboard. A process-wide lock serialises only the sub-second native
    selection stage so concurrent browser sessions cannot consume one another's
    chooser. ``trigger_dwell_ms`` pauses after a pointer hover, while
    ``trigger_click_delay_ms`` keeps the pointer down briefly on the visible
    upload button. Both happen only after this session owns the chooser lock,
    so concurrent accounts cannot interleave native-selection gestures.
    """

    if os.name != "nt":
        raise NativeUploadError("Native trusted upload is currently Windows-only.")
    files = _normalise_files(paths)
    owner_ids = (
        frozenset(int(process_id) for process_id in owner_process_ids)
        if owner_process_ids is not None
        else None
    )
    if owner_ids is not None and not owner_ids:
        raise NativeUploadError("The Firefox session has no owned processes.")
    timeout_seconds = max(1.0, timeout_ms / 1000.0)
    await _acquire_chooser_lock()
    global _ACTIVE_DESKTOP
    _ACTIVE_DESKTOP = desktop
    dialog_hwnd: int | None = None
    fill_diagnostics: dict[str, Any] | None = None
    click_task: asyncio.Task[Any] | None = None
    watcher: threading.Thread | None = None
    stop = threading.Event()
    before: set[int] = set()
    activity_notified = False
    try:
        if on_dialog_active is not None:
            on_dialog_active(True)
            activity_notified = True

        # With the global lock held, a chooser owned by this exact browser
        # session can only be residue from an earlier timed-out attempt.
        if owner_ids is not None or owner_session_token is not None:
            stale = await asyncio.to_thread(
                _close_owned_dialogs,
                owner_process_ids=owner_ids,
                owner_session_token=owner_session_token,
            )
            if stale:
                raise NativeUploadError(
                    f"Could not close stale Windows file dialogs: {stale}"
                )

        multiple = await locator.get_attribute("multiple")
        if len(files) > 1 and multiple is None:
            raise NativeUploadError("The target file input does not allow multiple files.")

        before = await asyncio.to_thread(_snapshot_dialogs)
        found = threading.Event()
        watcher_result: dict[str, Any] = {}
        watcher = threading.Thread(
            target=_watch_new_dialog,
            args=(
                before,
                found,
                stop,
                watcher_result,
                timeout_seconds,
                owner_ids,
                owner_session_token,
            ),
            name="invpw-native-file-chooser",
            daemon=True,
        )
        watcher.start()
        click_target = trigger if trigger is not None else locator
        if trigger_dwell_ms is not None:
            try:
                await click_target.hover(timeout=min(timeout_ms, 5_000))
                await asyncio.sleep(
                    max(40, min(1_200, int(trigger_dwell_ms))) / 1_000
                )
            except Exception:
                pass
        click_options: dict[str, Any] = {
            "no_wait_after": True,
            "timeout": timeout_ms,
        }
        if trigger_click_delay_ms is not None:
            click_options["delay"] = max(25, min(350, int(trigger_click_delay_ms)))
        click_task = asyncio.create_task(click_target.click(**click_options))

        ready = await asyncio.wait_for(
            asyncio.to_thread(found.wait, timeout_seconds),
            timeout=timeout_seconds + 1,
        )
        if not ready or "hwnd" not in watcher_result:
            error = watcher_result.get("error")
            details: list[str] = []
            if error:
                details.append(f"watcher error={type(error).__name__}: {error}")
            observed = watcher_result.get("observed")
            if observed:
                details.append(f"observed dialogs={observed}")
            if click_task.done():
                try:
                    click_task.result()
                except Exception as click_error:
                    details.append(
                        "click error="
                        f"{type(click_error).__name__}: {click_error}"
                    )
                else:
                    details.append("click completed without opening a dialog")
            else:
                details.append("click command remained pending")
            detail = f" ({'; '.join(details)})" if details else ""
            raise NativeUploadError(f"Windows file chooser did not appear{detail}")
        dialog_hwnd = int(watcher_result["hwnd"])
        fill_diagnostics = await asyncio.to_thread(
            _fill_and_accept, dialog_hwnd, files
        )
        if (
            fill_diagnostics
            and fill_diagnostics.get("dialog_closed_after_open") is False
        ):
            raise NativeUploadError(
                "Windows chooser rejected the filename instead of closing. "
                f"Diagnostics: {fill_diagnostics}"
            )
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
                "File chooser closed but the input contains "
                f"{actual}/{expected} file(s). Diagnostics: {fill_diagnostics}"
            )
    finally:
        stop.set()
        if click_task is not None:
            if not click_task.done():
                click_task.cancel()
            await asyncio.gather(click_task, return_exceptions=True)
        if watcher is not None:
            await asyncio.to_thread(watcher.join, 1.0)
        try:
            await asyncio.to_thread(
                _close_owned_dialogs,
                owner_process_ids=owner_ids,
                owner_session_token=owner_session_token,
                exclude=before,
            )
        except Exception:
            _cancel_dialog(dialog_hwnd)
        if activity_notified and on_dialog_active is not None:
            try:
                on_dialog_active(False)
            except Exception:
                pass
        # The desktop belongs to the session that held the lock; the next
        # caller sets its own.
        _ACTIVE_DESKTOP = None
        _CHOOSER_LOCK.release()
