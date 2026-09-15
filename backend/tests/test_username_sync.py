import asyncio

from app.infrastructure.automation import playwright_adapter as adapter_module
from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter


class _FakeLocator:
    def __init__(self, input_value=""):
        self._input_value = input_value

    @property
    def first(self):
        return self

    def nth(self, _index):
        return self

    async def count(self):
        return 1

    async def is_visible(self):
        return True

    async def is_enabled(self):
        return True

    async def wait_for(self, **_kwargs):
        return None

    async def click(self, **_kwargs):
        return None

    async def dispatch_event(self, *_args, **_kwargs):
        return None

    async def input_value(self):
        return self._input_value


class _FakePage:
    def __init__(self, web_username):
        self._generic = _FakeLocator()
        self._username = _FakeLocator(web_username)

    def locator(self, selector):
        if 'input[placeholder="Username"' in selector:
            return self._username
        return self._generic


def test_hidden_nav_profile_href_is_used_for_direct_profile_navigation(monkeypatch):
    class Candidate:
        def __init__(self, visible):
            self.visible = visible

        async def is_visible(self):
            return self.visible

        async def click(self, **_kwargs):
            return None

    class Group:
        def __init__(self, candidates):
            self.candidates = candidates

        async def count(self):
            return len(self.candidates)

        def nth(self, index):
            return self.candidates[index]

    class Page:
        def __init__(self):
            self.on_profile = False
            self.url = "https://www.tiktok.com/foryou?lang=en"

        def locator(self, selector):
            if "edit-profile-entrance" in selector:
                return Group([Candidate(self.on_profile)])
            if "nav-profile" in selector:
                return Group([Candidate(False)])
            return Group([])

        async def evaluate(self, _script):
            return "/@merced3_mint49"

    clock = [0.0]

    def monotonic():
        clock[0] += 0.3
        return clock[0]

    async def no_sleep(_seconds):
        return None

    page = Page()
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = page
    navigated = []

    async def navigate(url):
        navigated.append(url)
        page.url = url
        page.on_profile = True

    monkeypatch.setattr(adapter_module.time, "monotonic", monotonic)
    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "navigate_to", navigate)

    edit_button = asyncio.run(
        adapter._open_own_profile_page("stale_database_username")
    )

    assert edit_button is not None
    assert navigated == ["https://www.tiktok.com/@merced3_mint49"]


def test_real_web_username_different_from_db_is_synced_back_to_db(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _FakePage("sta_rew7ma2")
    messages = []

    async def step_logger(message):
        messages.append(message)

    success, username_for_db = asyncio.run(
        adapter.update_profile(
            avatar_path=None,
            bio=None,
            step_logger=step_logger,
            db_username="vues12ze77_ra4s",
        )
    )

    assert success is True
    assert username_for_db == "sta_rew7ma2"
    assert any("se cap nhat DB theo username web" in message for message in messages)
