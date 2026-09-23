import asyncio

from app.infrastructure.automation.playwright_adapter import (
    _browser_pipe_dead,
    _launch_invisible_context,
)


def test_browser_startups_are_serialized_but_both_complete():
    async def scenario():
        active = 0
        max_active = 0

        class FakeInvisiblePlaywright:
            _session_token = None

            async def __aenter__(self):
                nonlocal active, max_active
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.02)
                active -= 1
                return self

            async def __aexit__(self, *_args):
                return None

        first, second = await asyncio.gather(
            _launch_invisible_context(FakeInvisiblePlaywright(), 1),
            _launch_invisible_context(FakeInvisiblePlaywright(), 1),
        )

        assert first is not second
        assert max_active == 1

    asyncio.run(scenario())


def test_failed_startup_finishes_cleanup_before_next_start():
    async def scenario():
        events = []

        class FailingInvisiblePlaywright:
            _session_token = None

            async def __aenter__(self):
                events.append("failing-start")
                raise RuntimeError("boom")

            async def __aexit__(self, *_args):
                await asyncio.sleep(0.02)
                events.append("failing-cleanup")

        class WorkingInvisiblePlaywright:
            _session_token = None

            async def __aenter__(self):
                events.append("working-start")
                return self

            async def __aexit__(self, *_args):
                return None

        failing = asyncio.create_task(
            _launch_invisible_context(FailingInvisiblePlaywright(), 1)
        )
        await asyncio.sleep(0)
        working = asyncio.create_task(
            _launch_invisible_context(WorkingInvisiblePlaywright(), 1)
        )

        try:
            await failing
        except RuntimeError as exc:
            assert str(exc) == "boom"
        else:
            raise AssertionError("expected the first launch to fail")
        await working

        assert events == ["failing-start", "failing-cleanup", "working-start"]

    asyncio.run(scenario())


def test_a_dead_pipe_is_read_as_a_dead_engine():
    """The wording Playwright uses when the firefox process is already gone."""
    class TargetClosedError(Exception):
        pass

    assert _browser_pipe_dead(
        TargetClosedError("BrowserContext.new_page: the pipe is closed")
    )
    assert _browser_pipe_dead(
        Exception("Target page, context or browser has been closed")
    )


def test_an_ordinary_page_error_is_not_a_dead_engine():
    """A relaunch must not be triggered by a plain timeout or a bad locale."""
    assert not _browser_pipe_dead(TimeoutError("Timeout 30000ms exceeded"))
    assert not _browser_pipe_dead(
        RuntimeError("TikTok browser locale was not applied: {'language': 'vi'}")
    )
