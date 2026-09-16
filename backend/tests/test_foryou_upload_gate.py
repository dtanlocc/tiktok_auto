import asyncio
import time
from types import SimpleNamespace

import pytest

from app.core.exceptions import AuthenticationPageNotReady
from app.infrastructure.automation import playwright_adapter as adapter_module
from app.infrastructure.automation.playwright_adapter import (
    InvisiblePlaywrightAdapter,
    _auth_shell_state_ready,
    _foryou_state_ready,
    _sanitize_browser_cookies,
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


def _settled_auth_state(**overrides):
    state = {
        "ready": "complete",
        "rootReady": True,
        "fontsLoaded": True,
        "busy": 0,
        # A settled page has rendered text. A blank body that merely reached
        # readyState=complete is the shape a broken proxy produces.
        "textLen": 1200,
        "href": "https://www.tiktok.com/foryou?lang=en",
    }
    state.update(overrides)
    return state


def test_foryou_requires_complete_network_and_decoded_media():
    assert _foryou_state_ready(_ready_state(), network_idle=True) is True
    assert _foryou_state_ready(_ready_state(ready="interactive"), True) is False
    assert _foryou_state_ready(_ready_state(feedItems=0), True) is False
    assert _foryou_state_ready(_ready_state(mediaReady=0), True) is False
    assert _foryou_state_ready(_ready_state(pendingImages=1), True) is False
    assert _foryou_state_ready(_ready_state(busy=2), True) is True
    assert _foryou_state_ready(_ready_state(busy=3), True) is False
    assert _foryou_state_ready(_ready_state(), network_idle=False) is False


def test_auth_shell_requires_complete_document_and_finished_rendering():
    assert _auth_shell_state_ready(_settled_auth_state()) is True
    assert _auth_shell_state_ready(_settled_auth_state(ready="interactive")) is False
    assert _auth_shell_state_ready(_settled_auth_state(rootReady=False)) is False
    assert _auth_shell_state_ready(_settled_auth_state(fontsLoaded=False)) is False
    assert _auth_shell_state_ready(_settled_auth_state(busy=3)) is False
    # A body with no text has not finished rendering, whatever readyState says.
    assert _auth_shell_state_ready(_settled_auth_state(textLen=0)) is False


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
        url = "https://www.tiktok.com/foryou?lang=en"

        async def wait_for_load_state(self, *_args, **_kwargs):
            return None

        def locator(self, selector):
            # Guest pages expose Upload, but no profile/messages marker.
            if "nav-upload" in selector or "/tiktokstudio/upload" in selector:
                return Locator(count=1, visible=True)
            if "nav-login-button" in selector:
                return Locator(count=1, visible=True)
            return Locator()

        async def evaluate(self, _script):
            return _settled_auth_state()

    adapter = InvisiblePlaywrightAdapter()
    adapter._page = GuestPage()

    async def no_sleep(_seconds):
        return None

    async def no_captcha():
        return False

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "is_captcha_present", no_captcha)

    assert asyncio.run(adapter.check_login_status()) is False


def test_hidden_profile_template_and_hydration_flag_do_not_fake_cookie_login(monkeypatch):
    class Locator:
        def __init__(self, matches):
            self.matches = list(matches)

        @property
        def first(self):
            return self

        def nth(self, index):
            return Locator([self.matches[index]])

        async def count(self):
            return len(self.matches)

        async def is_visible(self):
            return bool(self.matches and self.matches[0])

    class GuestPage:
        url = "https://www.tiktok.com/foryou?lang=en"

        async def wait_for_load_state(self, *_args, **_kwargs):
            return None

        def locator(self, selector):
            if "profile-icon" in selector or "nav-profile" in selector:
                # TikTok mounts this template for guests, but keeps it hidden.
                return Locator([False])
            if "nav-login-button" in selector:
                return Locator([True])
            return Locator([])

        async def evaluate(self, _script):
            return _settled_auth_state()

    adapter = InvisiblePlaywrightAdapter()
    adapter._page = GuestPage()

    async def no_sleep(_seconds):
        return None

    async def no_captcha():
        return False

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "is_captcha_present", no_captcha)

    assert asyncio.run(adapter.check_login_status()) is False


def test_visible_guest_profile_marker_does_not_override_visible_login(monkeypatch):
    class Locator:
        def __init__(self, visible=False):
            self.visible = visible

        @property
        def first(self):
            return self

        async def count(self):
            return 1 if self.visible else 0

        async def is_visible(self):
            return self.visible

    class GuestPage:
        url = "https://www.tiktok.com/foryou?lang=en"

        async def wait_for_load_state(self, *_args, **_kwargs):
            return None

        def locator(self, selector):
            # TikTok's guest shell can show both of these at the same time.
            if "profile-icon" in selector or "nav-profile" in selector:
                return Locator(True)
            if "nav-login-button" in selector:
                return Locator(True)
            return Locator(False)

        async def evaluate(self, _script):
            return _settled_auth_state()

    adapter = InvisiblePlaywrightAdapter()
    adapter._page = GuestPage()

    async def no_sleep(_seconds):
        return None

    async def no_captcha():
        return False

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "is_captcha_present", no_captcha)

    assert asyncio.run(adapter.check_login_status()) is False


def test_visible_profile_marker_confirms_cookie_login(monkeypatch):
    class Locator:
        def __init__(self, visible=False):
            self.visible = visible

        @property
        def first(self):
            return self

        async def count(self):
            return 1 if self.visible else 0

        async def is_visible(self):
            return self.visible

    class SignedInPage:
        url = "https://www.tiktok.com/foryou?lang=en"

        async def wait_for_load_state(self, *_args, **_kwargs):
            return None

        def locator(self, selector):
            return Locator("profile-icon" in selector)

        async def evaluate(self, _script):
            return _settled_auth_state()

    adapter = InvisiblePlaywrightAdapter()
    adapter._page = SignedInPage()

    async def no_captcha():
        return False

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "is_captcha_present", no_captcha)

    assert asyncio.run(adapter.check_login_status()) is True


def test_transient_guest_nav_before_complete_load_does_not_invalidate_cookie(monkeypatch):
    class Locator:
        def __init__(self, page, kind):
            self.page = page
            self.kind = kind

        @property
        def first(self):
            return self

        async def count(self):
            return 1 if self.kind in {"login", "profile"} else 0

        async def is_visible(self):
            if self.kind == "login":
                return self.page.observation <= 7
            if self.kind == "profile":
                return self.page.observation > 7
            return False

    class HydratingPage:
        url = "https://www.tiktok.com/foryou?lang=en"

        def __init__(self):
            self.observation = 0

        async def wait_for_load_state(self, *_args, **_kwargs):
            return None

        def locator(self, selector):
            if "tux-dialog" in selector:
                return Locator(self, "none")
            if "profile-icon" in selector or "nav-profile" in selector:
                return Locator(self, "profile")
            if "nav-login-button" in selector:
                return Locator(self, "login")
            return Locator(self, "none")

        async def evaluate(self, _script):
            self.observation += 1
            if self.observation <= 7:
                return _settled_auth_state(ready="interactive")
            return _settled_auth_state()

    adapter = InvisiblePlaywrightAdapter()
    adapter._page = HydratingPage()

    async def no_sleep(_seconds):
        return None

    async def no_captcha():
        return False

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "is_captcha_present", no_captcha)

    assert asyncio.run(adapter.check_login_status()) is True
    assert adapter._page.observation >= 10


def test_cookie_import_drops_export_only_fields_and_keeps_latest_duplicate():
    cookies = [
        {
            "name": "sessionid",
            "value": "old",
            "domain": ".tiktok.com",
            "path": "/",
            "size": 41,
            "session": False,
            "hostOnly": False,
            "sameSite": "Lax",
        },
        {
            "name": "sessionid",
            "value": "fresh",
            "domain": ".tiktok.com",
            "path": "/",
            "size": 43,
            "session": False,
            "sameSite": "Lax",
        },
    ]

    assert _sanitize_browser_cookies(cookies) == [{
        "name": "sessionid",
        "value": "fresh",
        "domain": ".tiktok.com",
        "path": "/",
        "sameSite": "Lax",
        "secure": True,
    }]


def test_cookie_import_repairs_insecure_tiktok_same_site_none_cookie():
    cookies = [{
        "name": "sessionid",
        "value": "session-value",
        "domain": ".tiktok.com",
        "path": "/",
        "sameSite": "None",
        "secure": False,
    }]

    assert _sanitize_browser_cookies(cookies) == [{
        "name": "sessionid",
        "value": "session-value",
        "domain": ".tiktok.com",
        "path": "/",
        "sameSite": "None",
        "secure": True,
    }]


def test_cookie_import_omits_exported_negative_expiry_for_session_cookie():
    cookies = [{
        "name": "sessionid",
        "value": "session-value",
        "domain": ".tiktok.com",
        "path": "/",
        "expires": -1,
        "sameSite": "None",
        "secure": False,
    }]

    assert _sanitize_browser_cookies(cookies) == [{
        "name": "sessionid",
        "value": "session-value",
        "domain": ".tiktok.com",
        "path": "/",
        "sameSite": "None",
        "secure": True,
    }]


def test_cookie_import_does_not_rewrite_non_tiktok_cookie_security():
    cookies = [{
        "name": "example",
        "value": "value",
        "domain": ".example.com",
        "path": "/",
        "sameSite": "None",
        "secure": False,
    }]

    assert _sanitize_browser_cookies(cookies)[0]["secure"] is False


def test_authenticated_identity_requires_expected_nav_username():
    class Page:
        async def evaluate(self, _script):
            return "leonie2_bright73"

    adapter = InvisiblePlaywrightAdapter()
    adapter._page = Page()

    assert asyncio.run(
        adapter.validate_authenticated_identity("leonie2_bright73")
    ) is True
    assert asyncio.run(
        adapter.validate_authenticated_identity("another_account")
    ) is False


class _NoMatch:
    @property
    def first(self):
        return self

    async def count(self):
        return 0

    async def is_visible(self):
        return False


class _UnpaintedPage:
    """readyState=complete, markup present, not one character of text.

    Measured 2026-09-16: the proxy served `www.tiktok.com` and refused every
    request to `lf16-tiktok-web.tiktokcdn-us.com`, so the document arrived
    (317KB, 69 script tags) and no script that paints it ever did. The page
    stayed like this through three reloads.
    """

    url = "https://www.tiktok.com/foryou?lang=en"

    def __init__(self, refusals):
        self._refusals = refusals
        self._handlers = []

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None

    def locator(self, _selector):
        return _NoMatch()

    def on(self, event, handler):
        if event == "requestfailed":
            self._handlers.append(handler)

    async def evaluate(self, _script):
        # Deliver the refusals the way the browser would: as they happen,
        # while the caller is observing the DOM.
        for url in self._refusals:
            for handler in self._handlers:
                handler(SimpleNamespace(url=url))
        self._refusals = []
        return _settled_auth_state(textLen=0, htmlLen=317506)


def _run_login_check(monkeypatch, page):
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = page
    slept = []

    async def no_sleep(seconds):
        slept.append(seconds)

    async def no_captcha():
        return False

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "is_captcha_present", no_captcha)

    with pytest.raises(AuthenticationPageNotReady) as excinfo:
        asyncio.run(adapter.check_login_status())
    return str(excinfo.value), slept


def test_refused_cdn_requests_are_named_instead_of_blamed_on_cookies(monkeypatch):
    """The refusals are the evidence; the DOM never says why it is empty."""
    refusals = [
        "https://lf16-tiktok-web.tiktokcdn-us.com/obj/tiktok-web-tx/a.js"
    ] * 8
    message, slept = _run_login_check(monkeypatch, _UnpaintedPage(refusals))

    assert "CDN" in message
    assert "lf16-tiktok-web.tiktokcdn-us.com" in message
    assert "Cookies không liên quan" in message
    # Certain within seconds, not after the full 45s budget.
    assert len(slept) < 10


def test_an_unpainted_page_with_no_refusals_is_not_blamed_on_the_proxy(monkeypatch):
    """TikTok is an SPA: complete-but-unpainted is the ordinary first seconds
    of a healthy load, and calling that a dead proxy stopped accounts 8s in."""
    message, slept = _run_login_check(monkeypatch, _UnpaintedPage([]))

    assert "CDN" not in message
    # It waits out the budget rather than convicting a working proxy.
    assert len(slept) >= 40
