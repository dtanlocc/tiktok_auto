import asyncio
import time
from types import SimpleNamespace

import pytest

from app.infrastructure.automation import playwright_adapter as adapter_module
from app.infrastructure.automation.playwright_adapter import (
    InvisiblePlaywrightAdapter,
    _foryou_state_ready,
)


def _ready_state(**overrides):
    state = {
        "ready": "complete",
        "loggedIn": True,
        "login": False,
        "feedItems": 2,
        "mediaReady": 1,
        "pendingImages": 0,
        "busy": 0,
        "fontsLoaded": True,
    }
    state.update(overrides)
    return state


def test_foryou_requires_complete_network_and_decoded_media():
    assert _foryou_state_ready(_ready_state(), network_idle=True) is True
    assert _foryou_state_ready(_ready_state(ready="interactive"), True) is False
    assert _foryou_state_ready(_ready_state(feedItems=0), True) is False
    assert _foryou_state_ready(_ready_state(mediaReady=0), True) is False
    assert _foryou_state_ready(_ready_state(pendingImages=1), True) is False
    assert _foryou_state_ready(_ready_state(busy=1), True) is False
    assert _foryou_state_ready(_ready_state(), network_idle=False) is False


def test_upload_ticket_is_valid_once_and_only_while_still_on_foryou():
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = SimpleNamespace(url="https://www.tiktok.com/foryou?lang=en")
    adapter._foryou_ready_at = time.monotonic()

    adapter._consume_foryou_upload_ticket()

    with pytest.raises(RuntimeError, match="For You"):
        adapter._consume_foryou_upload_ticket()


def test_upload_ticket_rejects_navigation_away_from_foryou():
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = SimpleNamespace(url="https://www.tiktok.com/tiktokstudio/upload")
    adapter._foryou_ready_at = time.monotonic()

    with pytest.raises(RuntimeError, match="For You"):
        adapter._consume_foryou_upload_ticket()


def test_foryou_gate_accepts_sustained_readiness_when_feed_content_rotates(monkeypatch):
    class DynamicFeedPage:
        def __init__(self):
            self.url = "https://www.tiktok.com/foryou?lang=en"
            self.observations = 0

        async def wait_for_load_state(self, *_args, **_kwargs):
            return None

        def on(self, *_args, **_kwargs):
            return None

        def remove_listener(self, *_args, **_kwargs):
            return None

        async def evaluate(self, _script):
            self.observations += 1
            return _ready_state(
                fingerprint=f"/foryou|rotating-feed-item-{self.observations}"
            )

    clock = [100.0]
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = DynamicFeedPage()

    async def navigate(_url):
        return None

    async def no_gate():
        return None

    async def no_captcha():
        return False

    async def advance_clock(seconds):
        clock[0] += seconds

    monkeypatch.setattr(adapter_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(adapter_module.asyncio, "sleep", advance_clock)
    monkeypatch.setattr(adapter, "navigate_to", navigate)
    monkeypatch.setattr(adapter, "_wait_automation_gate", no_gate)
    monkeypatch.setattr(adapter, "is_captcha_present", no_captcha)

    ready = asyncio.run(adapter.prepare_foryou_home())

    assert ready is True
    assert adapter._page.observations < 90


def test_guest_upload_link_is_not_accepted_as_authenticated(monkeypatch):
    class Locator:
        def __init__(self, count=0, visible=False):
            self._count = count
            self._visible = visible

        @property
        def first(self):
            return self

        async def count(self):
            return self._count

        async def is_visible(self):
            return self._visible

    class GuestPage:
        def locator(self, selector):
            # Guest pages expose Upload, but no profile/messages marker.
            if "nav-upload" in selector or "/tiktokstudio/upload" in selector:
                return Locator(count=1, visible=True)
            if "nav-login-button" in selector:
                return Locator(count=1, visible=True)
            return Locator()

        async def evaluate(self, _script):
            return False

    adapter = InvisiblePlaywrightAdapter()
    adapter._page = GuestPage()

    async def no_sleep(_seconds):
        return None

    async def no_captcha():
        return False

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "is_captcha_present", no_captcha)

    assert asyncio.run(adapter.check_login_status()) is False
