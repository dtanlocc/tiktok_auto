"""A login form that closes without a word is a failure, not a wait.

Measured 24/09/2026 on @spou70_we10shan: after Log in, TikTok closed the form
and left the For You feed showing, signed out, with the address still on
/login. The wait for a code screen only gave up when its 150s ran out, so
every such account spent two and a half minutes proving nothing, and the
operator was told the Email choice "never appeared".
"""
import asyncio

import pytest

from app.use_cases.auth import login_strategies


class _Locator:
    def __init__(self, visible=False):
        self._visible = visible

    @property
    def first(self):
        return self

    async def count(self):
        return 1 if self._visible else 0

    async def is_visible(self):
        return self._visible


class _Page:
    """A page that answers the three scripts the wait asks about."""

    def __init__(self, *, inputs_on_screen, path="/login/phone-or-email/email"):
        self.inputs_on_screen = inputs_on_screen
        self.path = path
        self.body_text = "TikTok Search For You Explore Log in © 2026 TikTok"

    async def evaluate(self, script, *_args):
        if script is login_strategies._LOGIN_INPUTS_GONE_JS:
            return not self.inputs_on_screen
        if script is login_strategies._LOGIN_FORM_GONE_JS:
            return not self.inputs_on_screen and "/login" not in self.path
        if script is login_strategies._NEXT_SCREEN_JS:
            return False
        return self.body_text


class _Browser:
    def __init__(self, has_session=False):
        self.has_session = has_session

    async def extract_cookies(self):
        return (
            [{"name": "sessionid", "value": "x", "domain": ".tiktok.com"}]
            if self.has_session
            else []
        )


def _wait(page, browser, timeout=5.0):
    return asyncio.run(
        login_strategies._wait_verification_screen(
            page,
            browser,
            _Locator(visible=False),
            _Locator(visible=False),
            None,
            timeout_seconds=timeout,
        )
    )


def test_a_form_that_closed_signed_out_is_reported_at_once(monkeypatch):
    monkeypatch.setattr(login_strategies, "_login_form_error", _never_an_error)
    page = _Page(inputs_on_screen=False)          # URL still says /login
    verdict = _wait(page, _Browser(has_session=False))
    assert verdict.startswith("error:")
    assert "chua dang nhap" in verdict


def test_a_form_still_on_screen_is_still_waited_for(monkeypatch):
    monkeypatch.setattr(login_strategies, "_login_form_error", _never_an_error)
    page = _Page(inputs_on_screen=True)
    verdict = _wait(page, _Browser(has_session=False), timeout=2.0)
    assert verdict == "none"                       # ran out of time, as before


def test_a_closed_form_with_a_live_session_is_not_called_a_failure(monkeypatch):
    """Signed straight in: no code was asked for and the session is there."""
    monkeypatch.setattr(login_strategies, "_login_form_error", _never_an_error)
    page = _Page(inputs_on_screen=False, path="/foryou")
    verdict = _wait(page, _Browser(has_session=True))
    assert verdict == "none"


async def _never_an_error(_page):
    return ""
