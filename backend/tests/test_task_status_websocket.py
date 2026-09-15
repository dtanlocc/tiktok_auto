import asyncio
from types import SimpleNamespace

from app.domain.entities.account import TikTokAccount
from app.use_cases.orchestration import task_dispatcher as dispatcher_module
from app.use_cases.orchestration.task_dispatcher import ConcurrentTaskDispatcher


def test_default_dispatcher_keeps_proxy_mode_and_multiple_machine_slots_enabled():
    dispatcher = ConcurrentTaskDispatcher()

    assert dispatcher_module.settings.USE_PROXY is True
    assert dispatcher.max_tabs >= 3


def test_status_event_contains_latest_username_for_immediate_ui_refresh(monkeypatch):
    account = TikTokAccount(
        id="account@example.com",
        email="account@example.com",
        username="sta_rew7ma2",
    )
    broadcasts = []

    class FakeRepository:
        def __init__(self, _session):
            pass

        def update_status(self, _account_id, status):
            account.status = status

        def get_by_id(self, _account_id):
            return account

        def save(self, saved_account):
            return saved_account

    async def capture_broadcast(message):
        broadcasts.append(message)

    monkeypatch.setattr(dispatcher_module, "SQLiteAccountRepository", FakeRepository)
    monkeypatch.setattr(dispatcher_module.ws_manager, "broadcast", capture_broadcast)

    async def scenario():
        dispatcher = ConcurrentTaskDispatcher(max_tabs=1)
        await dispatcher._update_account_status(
            account.id,
            "SUCCESS",
            step_desc="Thanh cong",
            session=object(),
        )

    asyncio.run(scenario())

    assert broadcasts[-1]["event"] == "ACCOUNT_STATUS_CHANGED"
    assert broadcasts[-1]["data"]["username"] == "sta_rew7ma2"


def test_account_waits_for_its_assigned_proxy_even_when_another_proxy_is_free(monkeypatch):
    async def scenario():
        dispatcher = ConcurrentTaskDispatcher(max_tabs=8)
        proxies = [
            SimpleNamespace(id="proxy-1", host="127.0.0.1", port=1080),
            SimpleNamespace(id="proxy-2", host="127.0.0.2", port=1080),
        ]
        monkeypatch.setattr(dispatcher, "_load_all_proxies", lambda: proxies)

        async def ignore_status(*_args, **_kwargs):
            return None

        monkeypatch.setattr(dispatcher, "_update_account_status", ignore_status)
        first_proxy, first_key = await dispatcher._acquire_assigned_proxy(
            "first@example.com", "proxy-1", object()
        )
        assert first_proxy is proxies[0]

        second = asyncio.create_task(
            dispatcher._acquire_assigned_proxy(
                "second@example.com", "proxy-1", object()
            )
        )
        await asyncio.sleep(0)
        assert second.done() is False

        free_proxy, free_key = await dispatcher._acquire_assigned_proxy(
            "third@example.com", "proxy-2", object()
        )
        assert free_proxy is proxies[1]

        await dispatcher._release_proxy(first_key)
        second_proxy, second_key = await asyncio.wait_for(second, timeout=1)
        assert second_proxy is proxies[0]
        await dispatcher._release_proxy(second_key)
        await dispatcher._release_proxy(free_key)

    asyncio.run(scenario())


def test_three_proxies_allow_three_unique_concurrent_routes(monkeypatch):
    async def scenario():
        dispatcher = ConcurrentTaskDispatcher(max_tabs=4)
        dispatcher.proxy_max_concurrent = 1
        proxies = [
            SimpleNamespace(id=f"proxy-{index}", host=f"192.168.1.{index}", port=30000 + index)
            for index in range(1, 4)
        ]
        monkeypatch.setattr(dispatcher, "_load_all_proxies", lambda: proxies)

        async def ignore_status(*_args, **_kwargs):
            return None

        monkeypatch.setattr(dispatcher, "_update_account_status", ignore_status)

        acquired = [
            await dispatcher._acquire_assigned_proxy(
                f"account-{index}@example.com",
                f"proxy-{index + 1}",
                object(),
            )
            for index in range(3)
        ]
        assert {proxy.id for proxy, _key in acquired} == {
            "proxy-1",
            "proxy-2",
            "proxy-3",
        }

        fourth = asyncio.create_task(
            dispatcher._acquire_assigned_proxy(
                "account-4@example.com",
                "proxy-1",
                object(),
            )
        )
        await asyncio.sleep(0)
        assert fourth.done() is False

        await dispatcher._release_proxy(acquired[0][1])
        fourth_proxy, fourth_key = await asyncio.wait_for(fourth, timeout=1)
        assert fourth_proxy.id == acquired[0][0].id

        for _proxy, proxy_key in acquired[1:]:
            await dispatcher._release_proxy(proxy_key)
        await dispatcher._release_proxy(fourth_key)

    asyncio.run(scenario())


def test_frontend_concurrency_three_admits_exactly_three_tasks():
    async def scenario():
        dispatcher = ConcurrentTaskDispatcher(max_tabs=8)
        dispatcher.set_proxy_concurrency_limit(3)

        for _ in range(3):
            await dispatcher.semaphore.acquire()
        assert dispatcher.semaphore._value == 0

        fourth = asyncio.create_task(dispatcher.semaphore.acquire())
        await asyncio.sleep(0)
        assert fourth.done() is False

        dispatcher.semaphore.release()
        await asyncio.wait_for(fourth, timeout=1)

        for _ in range(3):
            dispatcher.semaphore.release()

    asyncio.run(scenario())


def test_direct_vpn_mode_uses_exactly_the_user_selected_concurrency(monkeypatch):
    async def scenario():
        dispatcher = ConcurrentTaskDispatcher(max_tabs=8)
        dispatcher.set_proxy_concurrency_limit(3)

        async def ignore_status(*_args, **_kwargs):
            return None

        monkeypatch.setattr(dispatcher, "_update_account_status", ignore_status)

        for index in range(3):
            acquired = await dispatcher._acquire_direct_slot(
                f"account-{index}@example.com",
                object(),
            )
            assert acquired is True
        assert dispatcher._direct_running == 3

        fourth = asyncio.create_task(
            dispatcher._acquire_direct_slot("account-4@example.com", object())
        )
        await asyncio.sleep(0)
        assert fourth.done() is False

        await dispatcher._release_direct_slot()
        assert await asyncio.wait_for(fourth, timeout=1) is True

        for _ in range(3):
            await dispatcher._release_direct_slot()

    asyncio.run(scenario())
