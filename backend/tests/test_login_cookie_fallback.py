import asyncio
from types import SimpleNamespace

import pytest

from app.core.exceptions import AuthenticationPageNotReady
from app.use_cases.auth import login_strategies
from app.use_cases.auth.login_strategies import _open_email_login_form
from app.use_cases.auth.tiktok_login import LoginStrategyFactory, TikTokLoginUseCase


def test_invalid_cookie_session_is_cleared_before_otp(monkeypatch):
    events = []
    account = SimpleNamespace(cookies=[{"name": "sessionid", "value": "expired"}])

    class Browser:
        async def navigate_to(self, _url):
            events.append("navigate")

        async def inject_cookies(self, _cookies):
            events.append("inject")

        async def check_login_status(self):
            events.append("cookie-invalid")
            return False

        async def clear_auth_session(self):
            events.append("clear")

    async def fake_otp_login(self, browser, account, **_kwargs):
        events.append("otp")
        return True

    monkeypatch.setattr(
        login_strategies.CredentialEmailOtpLoginStrategy,
        "login",
        fake_otp_login,
    )

    strategy = login_strategies.CookieThenCredentialLoginStrategy()
    result = asyncio.run(strategy.login(Browser(), account))

    assert result is True
    assert events[-2:] == ["clear", "otp"]
    assert strategy.last_login_method == "CREDENTIAL"


def test_successful_cookie_login_records_cookie_method():
    account = SimpleNamespace(cookies=[{"name": "sessionid", "value": "valid"}])

    class Browser:
        async def navigate_to(self, _url):
            return None

        async def inject_cookies(self, _cookies):
            return None

        async def check_login_status(self):
            return True

    strategy = login_strategies.CookieThenCredentialLoginStrategy()

    assert asyncio.run(strategy.login(Browser(), account)) is True
    assert strategy.last_login_method == "COOKIE"


def test_cookie_identity_mismatch_falls_back_to_credential(monkeypatch):
    events = []
    account = SimpleNamespace(
        username="expected_user",
        cookies=[{"name": "sessionid", "value": "other-account"}],
    )

    class Browser:
        async def navigate_to(self, _url):
            return None

        async def inject_cookies(self, _cookies):
            return None

        async def check_login_status(self):
            return True

        async def validate_authenticated_identity(self, expected_username):
            events.append(("identity", expected_username))
            return False

        async def clear_auth_session(self):
            events.append(("clear", None))

    async def fake_otp_login(self, browser, account, **_kwargs):
        events.append(("otp", account.username))
        return True

    monkeypatch.setattr(
        login_strategies.CredentialEmailOtpLoginStrategy,
        "login",
        fake_otp_login,
    )

    strategy = login_strategies.CookieThenCredentialLoginStrategy()

    assert asyncio.run(strategy.login(Browser(), account)) is True
    assert events == [
        ("identity", "expected_user"),
        ("clear", None),
        ("otp", "expected_user"),
    ]
    assert strategy.last_login_method == "CREDENTIAL"


def test_unsettled_cookie_page_keeps_cookie_and_does_not_fallback_to_otp(monkeypatch):
    events = []
    account = SimpleNamespace(
        username="expected_user",
        cookies=[{"name": "sessionid", "value": "keep-me"}],
    )

    class Browser:
        async def navigate_to(self, _url):
            events.append("navigate")

        async def inject_cookies(self, _cookies):
            events.append("inject")

        async def check_login_status(self):
            events.append("page-not-ready")
            raise AuthenticationPageNotReady("network still loading")

        async def clear_auth_session(self):
            events.append("clear")

    async def fake_otp_login(self, browser, account, **_kwargs):
        events.append("otp")
        return True

    monkeypatch.setattr(
        login_strategies.CredentialEmailOtpLoginStrategy,
        "login",
        fake_otp_login,
    )
    strategy = login_strategies.CookieThenCredentialLoginStrategy()

    with pytest.raises(AuthenticationPageNotReady):
        asyncio.run(strategy.login(Browser(), account))

    assert events.count("page-not-ready") == 2
    assert "clear" not in events
    assert "otp" not in events
    assert strategy.last_login_method is None


def test_email_login_falls_back_to_direct_url_when_home_button_is_missing(monkeypatch):
    class Locator:
        def __init__(self, visible=False):
            self.visible = visible

        @property
        def first(self):
            return self

        async def wait_for(self, **_kwargs):
            if not self.visible:
                raise RuntimeError("selector missing")

        async def click(self):
            return None

        def filter(self, **_kwargs):
            return self

    class Page:
        def __init__(self, direct=False):
            self.direct = direct

        def locator(self, selector):
            is_email = "input" in selector and (
                "username" in selector or "Email" in selector
            )
            return Locator(visible=self.direct and is_email)

    class Browser:
        def __init__(self):
            self._page = Page()
            self.urls = []

        async def navigate_to(self, url):
            self.urls.append(url)
            self._page = Page(direct=True)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(login_strategies.asyncio, "sleep", no_sleep)
    browser = Browser()

    page, email_input = asyncio.run(_open_email_login_form(browser))

    assert browser.urls == [
        "https://www.tiktok.com/login/phone-or-email/email?lang=en&enter_method=direct"
    ]
    assert page is browser._page
    assert email_input.first.visible is True


def test_successful_login_replaces_saved_cookies(monkeypatch):
    old_cookies = [{"name": "sessionid", "value": "old"}]
    fresh_cookies = [{"name": "sessionid", "value": "fresh"}]
    account = SimpleNamespace(
        id="account-1",
        username="user",
        cookies=old_cookies,
        status="IDLE",
        health_status="UNKNOWN",
        current_step="",
    )

    class Repo:
        def __init__(self):
            self.saved = None

        def get_by_id(self, _account_id):
            return account

        def save(self, saved_account):
            self.saved = saved_account

    class SuccessfulStrategy:
        async def login(self, *_args, **_kwargs):
            return True

    class Browser:
        async def extract_cookies(self):
            return fresh_cookies

        async def update_profile(self, **_kwargs):
            return True, None

    monkeypatch.setattr(
        LoginStrategyFactory,
        "get_strategy",
        staticmethod(lambda _method: SuccessfulStrategy()),
    )
    repo = Repo()

    result = asyncio.run(TikTokLoginUseCase(repo, Browser()).execute("account-1", "COOKIE"))

    assert result is True
    assert repo.saved is account
    assert account.cookies == fresh_cookies
    assert account.cookies is not old_cookies


def test_successful_login_does_not_replace_auth_with_partial_snapshot(monkeypatch):
    old_cookies = [{"name": "sessionid", "value": "keep-me"}]
    account = SimpleNamespace(
        id="account-1",
        username="user",
        cookies=old_cookies,
        status="IDLE",
        health_status="UNKNOWN",
        current_step="",
    )

    class Repo:
        def get_by_id(self, _account_id):
            return account

        def save(self, _saved_account):
            return None

    class SuccessfulStrategy:
        async def login(self, *_args, **_kwargs):
            return True

    class Browser:
        async def extract_cookies(self):
            return [{"name": "tt_csrf_token", "value": "guest-only"}]

        async def update_profile(self, **_kwargs):
            return True, None

    monkeypatch.setattr(
        LoginStrategyFactory,
        "get_strategy",
        staticmethod(lambda _method: SuccessfulStrategy()),
    )

    assert asyncio.run(TikTokLoginUseCase(Repo(), Browser()).execute("account-1", "COOKIE")) is True
    assert account.cookies is old_cookies
