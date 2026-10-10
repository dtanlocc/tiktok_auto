import contextlib
import asyncio
import ctypes
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from app.infrastructure.automation import native_upload
from app.infrastructure.automation.native_upload import _normalise_files


def test_cancelled_chooser_waiter_cannot_leak_lock(monkeypatch):
    chooser_lock = threading.Lock()
    monkeypatch.setattr(native_upload, "_CHOOSER_LOCK", chooser_lock)

    async def exercise() -> None:
        chooser_lock.acquire()
        waiter = asyncio.create_task(
            native_upload._acquire_chooser_lock(timeout_seconds=1.0)
        )
        await asyncio.sleep(0.02)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter

        chooser_lock.release()
        await asyncio.wait_for(
            native_upload._acquire_chooser_lock(timeout_seconds=0.2),
            timeout=0.5,
        )
        chooser_lock.release()

    asyncio.run(exercise())


def test_normalise_files_resolves_and_validates(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"media")

    assert _normalise_files([media]) == [str(media.resolve())]


def test_normalise_files_rejects_empty_and_missing(tmp_path):
    with pytest.raises(ValueError, match="At least one file"):
        _normalise_files([])
    with pytest.raises(FileNotFoundError):
        _normalise_files([Path(tmp_path) / "missing.mp4"])


def test_native_upload_allows_pending_click_after_windows_accepts(monkeypatch):
    click_options = {"hover_calls": 0}

    class Locator:
        async def get_attribute(self, _name):
            return None

        async def evaluate(self, _expression, timeout):
            return 1

    class Trigger:
        async def hover(self, **_kwargs):
            click_options["hover_calls"] += 1

        async def click(self, **kwargs):
            click_options.update(kwargs)
            await asyncio.Event().wait()

    def find_dialog(
        _before,
        found,
        _stop,
        result,
        _timeout_seconds,
        _owner_process_ids,
        _owner_session_token,
    ):
        result["hwnd"] = 123
        found.set()

    monkeypatch.setattr(native_upload, "_snapshot_dialogs", lambda: set())
    monkeypatch.setattr(native_upload, "_watch_new_dialog", find_dialog)
    monkeypatch.setattr(native_upload, "_fill_and_accept", lambda _hwnd, _files: None)
    monkeypatch.setattr(native_upload, "_cancel_dialog", lambda _hwnd: None)
    monkeypatch.setattr(native_upload, "_CLICK_COMPLETION_TIMEOUT_SECONDS", 0.01)

    asyncio.run(
        native_upload.set_input_files_native(
            Locator(),
            [__file__],
            trigger=Trigger(),
            allow_input_replacement=True,
            trigger_dwell_ms=1,
            trigger_click_delay_ms=112,
            timeout_ms=1_000,
        )
    )
    assert click_options["hover_calls"] == 1
    assert click_options["delay"] == 112


def test_fill_and_accept_uses_direct_win32_controls(monkeypatch):
    state = {"text": "", "clicked": False}

    def enum_children(_hwnd, callback, extra):
        callback(201, extra)

    def send_message(hwnd, message, _wparam, value):
        if hwnd == 201 and message == 12:
            state["text"] = value
        if hwnd == 100 and message == 0x0111 and _wparam == 1 and value == 202:
            state["clicked"] = True

    class FakeUser32:
        def SetFocus(self, _hwnd):
            return 201

        def SendMessageW(self, _hwnd, message, _wparam, value):
            if message == 194:
                state["text"] = ctypes.wstring_at(value)
            return 1

    fake_gui = SimpleNamespace(
        EnumChildWindows=enum_children,
        GetClassName=lambda hwnd: "Edit" if hwnd == 201 else "Button",
        IsWindowVisible=lambda _hwnd: True,
        IsWindowEnabled=lambda _hwnd: True,
        SendMessage=send_message,
        PostMessage=send_message,
        GetWindowText=lambda _hwnd: state["text"],
        GetDlgItem=lambda _hwnd, control_id: 202 if control_id == 1 else 0,
    )
    fake_con = SimpleNamespace(
        WM_SETTEXT=12, EM_SETSEL=177, EM_REPLACESEL=194, BM_CLICK=245
    )
    monkeypatch.setitem(sys.modules, "win32gui", fake_gui)
    monkeypatch.setitem(sys.modules, "win32con", fake_con)
    monkeypatch.setattr(
        native_upload.ctypes,
        "windll",
        SimpleNamespace(user32=FakeUser32()),
    )
    monkeypatch.setattr(
        native_upload,
        "_read_control_text",
        lambda _hwnd: (state["text"], True),
    )

    diagnostics = native_upload._fill_and_accept(100, [r"C:\media\clip.mp4"])

    assert state == {"text": r"C:\media\clip.mp4", "clicked": True}
    assert diagnostics["pywinauto_replace"] == "not-needed-wm-gettext-confirmed"


def test_fill_and_accept_waits_until_filename_controls_are_ready(monkeypatch):
    state = {"enum_calls": 0, "text": "", "clicked": False}

    def enum_children(_hwnd, callback, extra):
        state["enum_calls"] += 1
        if state["enum_calls"] >= 3:
            callback(201, extra)

    def send_message(hwnd, message, wparam, value):
        if hwnd == 201 and message == 12:
            state["text"] = value
        if hwnd == 100 and message == 0x0111 and wparam == 1 and value == 202:
            state["clicked"] = True
        return 1

    class FakeUser32:
        def SetFocus(self, _hwnd):
            return 201

        def SendMessageW(self, _hwnd, _message, _wparam, _value):
            return 1

    fake_gui = SimpleNamespace(
        EnumChildWindows=enum_children,
        GetClassName=lambda _hwnd: "Edit",
        IsWindowVisible=lambda _hwnd: True,
        IsWindowEnabled=lambda _hwnd: True,
        SendMessage=send_message,
        PostMessage=send_message,
        GetDlgCtrlID=lambda hwnd: 1148 if hwnd == 201 else 1,
        GetDlgItem=lambda _hwnd, control_id: (
            202 if control_id == 1 and state["enum_calls"] >= 3 else 0
        ),
    )
    fake_con = SimpleNamespace(
        WM_SETTEXT=12, EM_SETSEL=177, EM_REPLACESEL=194, BM_CLICK=245
    )
    monkeypatch.setitem(sys.modules, "win32gui", fake_gui)
    monkeypatch.setitem(sys.modules, "win32con", fake_con)
    monkeypatch.setattr(
        native_upload.ctypes,
        "windll",
        SimpleNamespace(user32=FakeUser32()),
    )
    monkeypatch.setattr(
        native_upload,
        "_read_control_text",
        lambda _hwnd: (state["text"], True),
    )
    monkeypatch.setattr(native_upload.time, "sleep", lambda _seconds: None)

    diagnostics = native_upload._fill_and_accept(100, [r"C:\media\clip.mp4"])

    assert state == {
        "enum_calls": 3,
        "text": r"C:\media\clip.mp4",
        "clicked": True,
    }
    assert diagnostics["controls_ready"] is True


def test_fill_and_accept_allows_unreadable_cross_process_edit(monkeypatch):
    state = {"clicked": False, "wm_settext_value": "", "wrapper_calls": 0}

    class FakeUser32:
        def SetFocus(self, _hwnd):
            return 201

        def SendMessageW(self, _hwnd, _message, _wparam, _value):
            return 1

    class FakeEditWrapper:
        def __init__(self, _hwnd):
            pass

        def set_edit_text(self, value):
            state["wrapper_calls"] += 1

    fake_gui = SimpleNamespace(
        EnumChildWindows=lambda _hwnd, callback, extra: callback(201, extra),
        GetClassName=lambda _hwnd: "Edit",
        IsWindowVisible=lambda _hwnd: True,
        IsWindowEnabled=lambda _hwnd: True,
        GetWindowText=lambda _hwnd: "",
        GetDlgItem=lambda _hwnd, control_id: 202 if control_id == 1 else 0,
        SendMessage=lambda hwnd, message, _wparam, value: (
            state.update(wm_settext_value=value)
            if hwnd == 201 and message == 12
            else None
        ),
        PostMessage=lambda hwnd, message, wparam, lparam: state.update(
            clicked=bool(hwnd == 100 and message == 0x0111 and wparam == 1 and lparam == 202)
        ),
    )
    fake_con = SimpleNamespace(
        WM_SETTEXT=12, EM_SETSEL=177, EM_REPLACESEL=194, BM_CLICK=245
    )
    monkeypatch.setitem(sys.modules, "win32gui", fake_gui)
    monkeypatch.setitem(sys.modules, "win32con", fake_con)
    monkeypatch.setitem(
        sys.modules,
        "pywinauto.controls.win32_controls",
        SimpleNamespace(EditWrapper=FakeEditWrapper),
    )
    monkeypatch.setattr(
        native_upload.ctypes,
        "windll",
        SimpleNamespace(user32=FakeUser32()),
    )

    native_upload._fill_and_accept(100, [r"C:\media\clip.mp4"])

    assert state == {
        "clicked": True,
        "wm_settext_value": r"C:\media\clip.mp4",
        "wrapper_calls": 1,
    }


def test_fill_and_accept_preserves_emoji_without_pywinauto_rewrite(monkeypatch):
    value = "D:\\media\\clip 🍚 #fyp.mp4"
    state = {"clicked": False, "wm_settext_value": "", "wrapper_calls": 0}

    class FakeUser32:
        def SetFocus(self, _hwnd):
            return 201

        def SendMessageW(self, _hwnd, _message, _wparam, _value):
            return 1

    class FakeEditWrapper:
        def __init__(self, _hwnd):
            pass

        def set_edit_text(self, _value):
            state["wrapper_calls"] += 1

    fake_gui = SimpleNamespace(
        EnumChildWindows=lambda _hwnd, callback, extra: callback(201, extra),
        GetClassName=lambda _hwnd: "Edit",
        IsWindowVisible=lambda _hwnd: True,
        IsWindowEnabled=lambda _hwnd: True,
        GetWindowText=lambda _hwnd: state["wm_settext_value"],
        GetDlgItem=lambda _hwnd, control_id: 202 if control_id == 1 else 0,
        SendMessage=lambda hwnd, message, _wparam, message_value: (
            state.update(wm_settext_value=message_value)
            if hwnd == 201 and message == 12
            else None
        ),
        PostMessage=lambda hwnd, message, wparam, lparam: state.update(
            clicked=bool(hwnd == 100 and message == 0x0111 and wparam == 1 and lparam == 202)
        ),
    )
    fake_con = SimpleNamespace(
        WM_SETTEXT=12, EM_SETSEL=177, EM_REPLACESEL=194, BM_CLICK=245
    )
    monkeypatch.setitem(sys.modules, "win32gui", fake_gui)
    monkeypatch.setitem(sys.modules, "win32con", fake_con)
    monkeypatch.setitem(
        sys.modules,
        "pywinauto.controls.win32_controls",
        SimpleNamespace(EditWrapper=FakeEditWrapper),
    )
    monkeypatch.setattr(
        native_upload.ctypes,
        "windll",
        SimpleNamespace(user32=FakeUser32()),
    )

    diagnostics = native_upload._fill_and_accept(100, [value])

    assert state == {
        "clicked": True,
        "wm_settext_value": value,
        "wrapper_calls": 0,
    }
    assert diagnostics["pywinauto_replace"] == "skipped-supplementary-unicode"


def test_fill_and_accept_types_with_wm_char_when_primary_setters_raise(monkeypatch):
    state = {"clicked": False, "text": ""}

    class FakeUser32:
        def SetFocus(self, _hwnd):
            return 201

        def SendMessageW(self, _hwnd, message, wparam, _value):
            if message == 0x0303:  # WM_CLEAR
                state["text"] = ""
            elif message == 0x0102:  # WM_CHAR
                state["text"] += chr(wparam)
            return 1

    class IgnoredEditWrapper:
        def __init__(self, _hwnd):
            pass

        def set_edit_text(self, _value):
            raise OSError("EM_REPLACESEL unavailable")

    fake_gui = SimpleNamespace(
        EnumChildWindows=lambda _hwnd, callback, extra: callback(201, extra),
        GetClassName=lambda _hwnd: "Edit",
        IsWindowVisible=lambda _hwnd: True,
        IsWindowEnabled=lambda _hwnd: True,
        GetWindowText=lambda _hwnd: state["text"],
        GetDlgItem=lambda _hwnd, control_id: 202 if control_id == 1 else 0,
        SendMessage=lambda hwnd, message, _wparam, _value: (
            (_ for _ in ()).throw(OSError("WM_SETTEXT unavailable"))
            if hwnd == 201 and message == 12
            else None
        ),
        PostMessage=lambda hwnd, message, wparam, lparam: state.update(
            clicked=bool(hwnd == 100 and message == 0x0111 and wparam == 1 and lparam == 202)
        ),
    )
    fake_con = SimpleNamespace(
        WM_SETTEXT=12, EM_SETSEL=177, EM_REPLACESEL=194, BM_CLICK=245
    )
    monkeypatch.setitem(sys.modules, "win32gui", fake_gui)
    monkeypatch.setitem(sys.modules, "win32con", fake_con)
    monkeypatch.setitem(
        sys.modules,
        "pywinauto.controls.win32_controls",
        SimpleNamespace(EditWrapper=IgnoredEditWrapper),
    )
    monkeypatch.setattr(
        native_upload.ctypes,
        "windll",
        SimpleNamespace(user32=FakeUser32()),
    )

    diagnostics = native_upload._fill_and_accept(
        100, [r"C:\media\clip.mp4"]
    )

    assert state == {"clicked": True, "text": r"C:\media\clip.mp4"}
    assert diagnostics["wm_char_text_matches"] is True


def test_firefox_dialog_must_belong_to_requested_session(monkeypatch):
    fake_process = SimpleNamespace(exe=lambda: r"C:\browser\firefox.exe")
    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(Process=lambda _pid: fake_process),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32process",
        SimpleNamespace(GetWindowThreadProcessId=lambda _hwnd: (10, 321)),
    )

    assert native_upload._is_firefox_dialog(100, frozenset({321})) is True
    assert native_upload._is_firefox_dialog(100, frozenset({999})) is False


def test_snapshot_keeps_handles_when_windows_enumeration_races(monkeypatch):
    def enum_windows(callback, extra):
        callback(100, extra)
        raise OSError("dialog module disappeared")

    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(
            EnumWindows=enum_windows,
            GetClassName=lambda _hwnd: "#32770",
        ),
    )

    assert native_upload._snapshot_dialogs() == {100}


def test_owned_dialog_cleanup_closes_enabled_alert_before_parent(monkeypatch):
    dialogs = {100, 200}
    enabled = {100: False, 200: True}
    closed = []

    def cancel(hwnd):
        closed.append(hwnd)
        dialogs.discard(hwnd)
        if hwnd == 200:
            enabled[100] = True

    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(IsWindowEnabled=lambda hwnd: enabled[hwnd]),
    )
    monkeypatch.setattr(native_upload, "_snapshot_dialogs", lambda: set(dialogs))
    monkeypatch.setattr(native_upload, "_is_firefox_dialog", lambda *_args: True)
    monkeypatch.setattr(native_upload, "_cancel_dialog", cancel)
    monkeypatch.setattr(native_upload.time, "sleep", lambda _seconds: None)

    assert native_upload._close_owned_dialogs(
        owner_process_ids=frozenset({321}),
        owner_session_token=None,
    ) == []
    assert closed == [200, 100]


def test_cancel_dialog_clicks_idless_validation_button(monkeypatch):
    posted = []

    def enum_children(_hwnd, callback, extra):
        callback(301, extra)

    monkeypatch.setitem(
        sys.modules,
        "win32gui",
        SimpleNamespace(
            GetDlgItem=lambda _hwnd, _control_id: 0,
            EnumChildWindows=enum_children,
            GetClassName=lambda _hwnd: "Button",
            IsWindowEnabled=lambda _hwnd: True,
            PostMessage=lambda hwnd, message, wparam, lparam: posted.append(
                (hwnd, message, wparam, lparam)
            ),
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "win32con",
        SimpleNamespace(BM_CLICK=245, WM_CLOSE=16),
    )

    native_upload._cancel_dialog(200)

    assert posted == [(301, 245, 0, 0)]


def test_waiting_for_global_chooser_lock_keeps_stream_active(monkeypatch):
    chooser_lock = threading.Lock()
    chooser_lock.acquire()
    monkeypatch.setattr(native_upload, "_CHOOSER_LOCK", chooser_lock)

    class Locator:
        async def get_attribute(self, _name):
            return None

        async def evaluate(self, _expression, timeout):
            return 1

    class Trigger:
        async def click(self, **_kwargs):
            return None

    def find_dialog(
        _before,
        found,
        _stop,
        result,
        _timeout_seconds,
        _owner_process_ids,
        _owner_session_token,
    ):
        result["hwnd"] = 123
        found.set()

    monkeypatch.setattr(native_upload, "_snapshot_dialogs", lambda: set())
    monkeypatch.setattr(native_upload, "_watch_new_dialog", find_dialog)
    monkeypatch.setattr(
        native_upload,
        "_fill_and_accept",
        lambda _hwnd, _files: {"dialog_closed_after_open": True},
    )

    activity = []

    async def exercise():
        task = asyncio.create_task(
            native_upload.set_input_files_native(
                Locator(),
                [__file__],
                trigger=Trigger(),
                allow_input_replacement=True,
                on_dialog_active=activity.append,
                timeout_ms=1_000,
            )
        )
        await asyncio.sleep(0.02)
        assert activity == []
        chooser_lock.release()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(exercise())
    assert activity == [True, False]


def test_a_window_helper_runs_on_the_session_desktop(monkeypatch):
    """From 0.24 the chooser is on the browser's own desktop, not this one."""
    attached = []

    @contextlib.contextmanager
    def fake_attach(name):
        attached.append(name)
        yield True

    monkeypatch.setattr(native_upload, "_thread_on_desktop", fake_attach)
    monkeypatch.setattr(native_upload, "_ACTIVE_DESKTOP", "invpw_abc123")

    @native_upload._on_browser_desktop
    def find_windows():
        return "found"

    assert find_windows() == "found"
    assert attached == ["invpw_abc123"]


def test_the_helpers_that_touch_windows_are_all_bound():
    for name in ("_snapshot_dialogs", "_watch_new_dialog", "_fill_and_accept",
                 "_close_owned_dialogs", "_cancel_dialog"):
        function = getattr(native_upload, name)
        assert getattr(function, "__wrapped__", None) is not None, name


def test_no_desktop_means_the_thread_is_left_alone():
    with native_upload._thread_on_desktop(None) as attached:
        assert attached is True
