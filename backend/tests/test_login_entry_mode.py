"""Which door the login walks through is an operator setting.

"foryou" clicks Log in on the home page and uses the modal - the path a
person walks. "login_page" opens TikTok's own login page. Neither is
obviously better; the point of the switch is that a run can measure it, and
that whichever is chosen still has the other one as a fallback.
"""
import asyncio

import pytest

from app.use_cases.auth import login_strategies


class _Input:
    def __init__(self, visible):
        self._visible = visible

    @property
    def first(self):
        return self

    async def wait_for(self, state="visible", timeout=0):
        if not self._visible:
            raise TimeoutError("not visible")
        return None


class _Page:
    def __init__(self, input_visible):
        self.url = "https://www.tiktok.com/foryou"
        self.input_visible = input_visible
        self.locator_calls = []

    def locator(self, selector):
        self.locator_calls.append(selector)
        return _Input(self.input_visible)


class _Browser:
    def __init__(self, input_visible_after_goto):
        self._page = _Page(input_visible=False)
        self.visited = []
        self.input_visible_after_goto = input_visible_after_goto

    async def navigate_to(self, url):
        self.visited.append(url)
        self._page = _Page(input_visible=self.input_visible_after_goto)


def test_login_page_mode_goes_straight_to_the_login_page(monkeypatch):
    monkeypatch.setattr(login_strategies.settings, "LOGIN_ENTRY_MODE", "login_page")
    browser = _Browser(input_visible_after_goto=True)

    page, _field = asyncio.run(login_strategies._open_email_login_form(browser))

    assert browser.visited, "the login page was never opened"
    assert "/login/phone-or-email/email" in browser.visited[0]
    assert page is browser._page


def test_login_page_mode_falls_back_when_the_page_does_not_render(monkeypatch):
    monkeypatch.setattr(login_strategies.settings, "LOGIN_ENTRY_MODE", "login_page")
    browser = _Browser(input_visible_after_goto=False)
    said = []

    async def log(message):
        said.append(message)

    with pytest.raises(Exception):
        # The For You path is exercised next and has no home page to click in
        # this stand-in; what matters is that the fallback was announced.
        asyncio.run(login_strategies._open_email_login_form(browser, step_logger=log))

    assert any("quay ve loi vao tu For You" in message for message in said)


def test_foryou_mode_tries_the_modal_before_the_login_page(monkeypatch):
    """Both modes can end on the login page; the order is what differs."""
    monkeypatch.setattr(login_strategies.settings, "LOGIN_ENTRY_MODE", "foryou")
    browser = _Browser(input_visible_after_goto=True)
    said = []

    async def log(message):
        said.append(message)

    asyncio.run(login_strategies._open_email_login_form(browser, step_logger=log))

    assert said, "nothing was reported"
    assert "Log in ngoai trang chu" in said[0], said
    assert not any("Mo thang trang dang nhap" in message for message in said)


def test_login_page_mode_says_so_before_anything_else(monkeypatch):
    monkeypatch.setattr(login_strategies.settings, "LOGIN_ENTRY_MODE", "login_page")
    browser = _Browser(input_visible_after_goto=True)
    said = []

    async def log(message):
        said.append(message)

    asyncio.run(login_strategies._open_email_login_form(browser, step_logger=log))

    assert "Mo thang trang dang nhap" in said[0], said


def test_the_mode_reader_is_forgiving_about_spelling(monkeypatch):
    monkeypatch.setattr(login_strategies.settings, "LOGIN_ENTRY_MODE", "  LOGIN_PAGE ")
    assert login_strategies._login_entry_mode() == "login_page"
    monkeypatch.setattr(login_strategies.settings, "LOGIN_ENTRY_MODE", "")
    assert login_strategies._login_entry_mode() == "foryou"
