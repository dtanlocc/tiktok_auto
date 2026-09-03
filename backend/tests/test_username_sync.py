import asyncio

from app.infrastructure.automation import playwright_adapter as adapter_module
from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter


class _FakeLocator:
    def __init__(self, input_value=""):
        self._input_value = input_value

    @property
    def first(self):
        return self

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
