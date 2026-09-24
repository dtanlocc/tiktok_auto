"""The second press must carry the credentials the first one did.

Measured 24/09/2026 on the THAITEST batch, four accounts in a row: press one
answered "Internal server error. Please try again later.", TikTok rebuilt the
login form during the 20-35s pause and wiped both fields, and the retry
pressed an empty form. The form then closed, the feed came back signed out,
and each account waited the full 150s for a code nobody had asked for.
"""
import asyncio

import pytest

from app.use_cases.auth import login_strategies


class _Button:
    def __init__(self, page):
        self.page = page

    @property
    def first(self):
        return self

    async def click(self):
        self.page.clicks.append(dict(self.page.fields))

    async def count(self):
        return 1

    async def is_visible(self):
        return True

    async def is_enabled(self):
        return True


class _Page:
    """A login page that loses its fields between the two presses."""

    def __init__(self, *, wipes_after_first_press, inputs_present=True):
        self.fields = {"user": "someone@hotmail.com", "pass": "secret"}
        self.clicks = []
        self.wipes = wipes_after_first_press
        self.inputs_present = inputs_present

    async def evaluate(self, script, *_args):
        if script is login_strategies._LOGIN_INPUTS_GONE_JS:
            return not self.inputs_present
        if script is login_strategies._LOGIN_FIELDS_EMPTY_JS:
            return self.inputs_present and not (self.fields["user"] and self.fields["pass"])
        if script is login_strategies._NEXT_SCREEN_JS:
            return False
        if script is login_strategies._LOGIN_FORM_GONE_JS:
            return False
        return ""

    def wipe(self):
        if self.wipes:
            self.fields = {"user": "", "pass": ""}


class _Browser:
    def __init__(self, has_session=False):
        self.has_session = has_session

    async def extract_cookies(self):
        return (
            [{"name": "sessionid", "value": "x"}] if self.has_session else []
        )

    async def wait_captcha_cleared(self, timeout=0, step_logger=None):
        return None


def _run(page, browser, refill, answers):
    """Drive _submit_login with a scripted sequence of TikTok answers."""
    calls = {"n": 0}

    async def fake_response(_page, timeout_seconds=12.0, ignore_error=""):
        answer = answers[min(calls["n"], len(answers) - 1)]
        calls["n"] += 1
        page.wipe()                     # TikTok rebuilds the form afterwards
        return answer

    async def no_sleep(_seconds):
        return None

    async def enabled(_button, timeout_seconds=8.0):
        return True

    login_strategies._await_login_response_original = login_strategies._await_login_response
    login_strategies._await_login_response = fake_response
    login_strategies._wait_submit_enabled_original = login_strategies._wait_submit_enabled
    login_strategies._wait_submit_enabled = enabled
    sleep_original = login_strategies.asyncio.sleep
    login_strategies.asyncio.sleep = no_sleep
    try:
        return asyncio.run(
            login_strategies._submit_login(
                page, browser, _Button(page), None, transient_retries=1, refill=refill
            )
        )
    finally:
        login_strategies._await_login_response = login_strategies._await_login_response_original
        login_strategies._wait_submit_enabled = login_strategies._wait_submit_enabled_original
        login_strategies.asyncio.sleep = sleep_original


def test_a_wiped_form_is_refilled_before_the_second_press():
    page = _Page(wipes_after_first_press=True)
    filled = {"n": 0}

    async def refill():
        filled["n"] += 1
        page.fields = {"user": "someone@hotmail.com", "pass": "secret"}

    _run(page, _Browser(), refill, ["Internal server error. Please try again later.", ""])

    assert filled["n"] == 1, "the form was never refilled"
    assert len(page.clicks) == 2
    assert page.clicks[1] == {"user": "someone@hotmail.com", "pass": "secret"}, \
        "the second press submitted an empty form"


def test_a_form_that_kept_its_values_is_not_refilled():
    page = _Page(wipes_after_first_press=False)
    filled = {"n": 0}

    async def refill():
        filled["n"] += 1

    _run(page, _Browser(), refill, ["Internal server error. Please try again later.", ""])

    assert filled["n"] == 0
    assert len(page.clicks) == 2


def test_a_form_that_closed_is_reported_instead_of_pressed():
    page = _Page(wipes_after_first_press=True, inputs_present=False)

    async def refill():
        raise AssertionError("must not refill a form that is gone")

    verdict = _run(
        page, _Browser(has_session=False), refill,
        ["Internal server error. Please try again later.", ""],
    )

    assert "dong form dang nhap" in verdict
    assert len(page.clicks) == 1, "the second press went into the void"
