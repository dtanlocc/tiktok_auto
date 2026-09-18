import asyncio

import pytest

from app.infrastructure.automation import playwright_adapter as adapter_module
from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter


class _Handle:
    def __init__(self, error=None):
        self.error = error
        self.files = None

    async def set_input_files(self, files, timeout=None):
        if self.error:
            raise self.error
        self.files = files


class _Input:
    def __init__(self, handle):
        self.handle = handle

    async def element_handle(self, timeout=None):
        return self.handle


class _Apply:
    def __init__(self, visible):
        self.visible = visible

    @property
    def first(self):
        return self

    async def count(self):
        return 1 if self.visible else 0

    async def is_visible(self):
        return self.visible


class _Located:
    def __init__(self, selector, seen):
        self.selector = selector
        seen.append(selector)

    @property
    def first(self):
        return self

    async def wait_for(self, **_kw):
        return None


class _Page:
    def __init__(self, crop_dialog_appears):
        self.crop_dialog_appears = crop_dialog_appears
        self.located = []

    def get_by_role(self, role, name=None):
        return _Apply(self.crop_dialog_appears)

    def locator(self, selector):
        return _Located(selector, self.located)


def _adapter(monkeypatch, page, native_calls):
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = page

    async def native(target, paths, **kwargs):
        native_calls.append((target, paths, kwargs))

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(adapter_module, "set_input_files_native", native)
    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "_native_upload_process_ids", lambda: [])
    return adapter


def test_the_avatar_goes_straight_into_its_input_and_no_native_dialog_opens(monkeypatch):
    native_calls = []
    page = _Page(crop_dialog_appears=True)
    adapter = _adapter(monkeypatch, page, native_calls)
    handle = _Handle()

    asyncio.run(adapter._attach_avatar_file(_Input(handle), r"C:\a\avatar.png"))

    assert handle.files == [r"C:\a\avatar.png"]
    assert native_calls == []


def test_the_native_fallback_is_opened_by_the_avatars_own_edit_icon(monkeypatch):
    native_calls = []
    page = _Page(crop_dialog_appears=False)
    adapter = _adapter(monkeypatch, page, native_calls)
    avatar_input = _Input(_Handle(error=RuntimeError("no dispatcher")))

    asyncio.run(adapter._attach_avatar_file(avatar_input, r"C:\a\avatar.png"))

    assert len(native_calls) == 1
    target, paths, kwargs = native_calls[0]
    assert target is avatar_input and paths == [r"C:\a\avatar.png"]
    assert kwargs["trigger"].selector == InvisiblePlaywrightAdapter._AVATAR_EDIT_ICON
    assert "edit-profile-avatar-edit-icon" in kwargs["trigger"].selector


class _DialogParts:
    """Edit-profile dialog that closes after `closes_after_clicks` Save clicks."""

    def __init__(self, closes_after_clicks):
        self.closes_after_clicks = closes_after_clicks
        self.clicks = 0

    @property
    def first(self):
        return self

    async def count(self):
        return 0 if self.clicks >= self.closes_after_clicks else 1

    async def is_visible(self):
        return True

    async def click(self, **_kw):
        self.clicks += 1


class _EditProfilePage:
    def __init__(self, dialog):
        self.dialog = dialog

    def locator(self, _selector):
        return self.dialog


def _saving_adapter(monkeypatch, closes_after_clicks):
    adapter = InvisiblePlaywrightAdapter()
    dialog = _DialogParts(closes_after_clicks)
    adapter._page = _EditProfilePage(dialog)

    class Clock:
        now = 0.0

        def time(self):
            return self.now

    clock = Clock()

    async def fake_sleep(seconds):
        clock.now += seconds

    monkeypatch.setattr(adapter_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(adapter_module.asyncio, "get_running_loop", lambda: clock)
    return adapter, dialog


def test_a_save_that_closes_the_dialog_is_accepted_without_clicking_again(monkeypatch):
    adapter, dialog = _saving_adapter(monkeypatch, closes_after_clicks=1)
    dialog.clicks = 1          # the flow's own Save click already happened

    asyncio.run(adapter._confirm_profile_saved(dialog, retry_save=True))

    assert dialog.clicks == 1


def test_a_dialog_left_open_gets_save_pressed_again(monkeypatch):
    adapter, dialog = _saving_adapter(monkeypatch, closes_after_clicks=2)
    dialog.clicks = 1

    asyncio.run(adapter._confirm_profile_saved(dialog, retry_save=True))

    assert dialog.clicks == 2


def test_a_save_tiktok_keeps_refusing_is_a_failure_not_a_success(monkeypatch):
    adapter, dialog = _saving_adapter(monkeypatch, closes_after_clicks=99)
    dialog.clicks = 1

    with pytest.raises(RuntimeError, match="TikTok chua luu ho so"):
        asyncio.run(adapter._confirm_profile_saved(dialog, retry_save=True))
    assert dialog.clicks == 3      # the first Save plus two retries, then it gives up


def test_a_pending_username_confirmation_is_never_answered_with_another_save(monkeypatch):
    adapter, dialog = _saving_adapter(monkeypatch, closes_after_clicks=99)
    dialog.clicks = 1

    with pytest.raises(RuntimeError):
        asyncio.run(adapter._confirm_profile_saved(dialog, retry_save=False))
    assert dialog.clicks == 1


class _Candidate:
    def __init__(self, name, covered):
        self.name = name
        self.covered = covered

    async def count(self):
        return 1

    async def is_visible(self):
        return True

    async def is_enabled(self):
        return True

    async def evaluate(self, _script):
        return not self.covered

    def nth(self, _index):
        return self

    def filter(self, **_kw):
        return self


class _Missing:
    @property
    def first(self):
        return self

    async def count(self):
        return 0


class _NavPage:
    """Only match: the sidebar's "Upload" link, covered by a dialog."""

    def get_by_role(self, *_a, **_kw):
        return _Missing()

    def get_by_text(self, *_a, **_kw):
        return _Missing()

    def locator(self, _selector):
        return _Candidate("sidebar Upload", covered=True)


def test_a_button_covered_by_a_dialog_is_never_chosen_as_the_upload_trigger():
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _NavPage()

    class Target:
        async def element_handle(self, timeout=None):
            return None

    with pytest.raises(RuntimeError, match="Khong tim thay nut chon photo"):
        asyncio.run(adapter._resolve_native_upload_trigger(Target(), "photo"))
