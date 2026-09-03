import time
from types import SimpleNamespace

import pytest

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

