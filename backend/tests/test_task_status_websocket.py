import asyncio
from types import SimpleNamespace

from app.domain.entities.account import TikTokAccount
from app.use_cases.orchestration import task_dispatcher as dispatcher_module
from app.use_cases.orchestration.task_dispatcher import (
    ConcurrentTaskDispatcher,
    _upload_route_concurrency_override,
)


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


def test_upload_route_override_serializes_same_proxy_without_lowering_global_limit(monkeypatch):
    async def scenario():
        dispatcher = ConcurrentTaskDispatcher(max_tabs=4)
        dispatcher.proxy_max_concurrent = 4
        proxy = SimpleNamespace(host="127.0.0.1", port=1080)
        monkeypatch.setattr(dispatcher, "_load_all_proxies", lambda: [proxy])

        async def ignore_status(*_args, **_kwargs):
            return None

        monkeypatch.setattr(dispatcher, "_update_account_status", ignore_status)
        first_proxy, first_key = await dispatcher._acquire_balanced_proxy(
            "first@example.com", object(), max_concurrent_override=1
        )
        assert first_proxy is proxy

        second = asyncio.create_task(dispatcher._acquire_balanced_proxy(
            "second@example.com", object(), max_concurrent_override=1
        ))
        await asyncio.sleep(0)
        assert second.done() is False

        await dispatcher._release_proxy(first_key)
        second_proxy, second_key = await asyncio.wait_for(second, timeout=1)
        assert second_proxy is proxy
        await dispatcher._release_proxy(second_key)

    asyncio.run(scenario())


def test_upload_route_override_does_not_serialize_direct_vpn_mode():
    assert _upload_route_concurrency_override("UPLOAD_MEDIA_BATCH", True) == 1
    assert _upload_route_concurrency_override("UPLOAD_MEDIA_BATCH", False) is None
    assert _upload_route_concurrency_override("UPDATE_PROFILE", True) is None


def test_direct_vpn_uploads_can_fill_the_user_selected_concurrency():
    async def scenario():
        dispatcher = ConcurrentTaskDispatcher(max_tabs=4)
        dispatcher.proxy_max_concurrent = 4
        override = _upload_route_concurrency_override(
            "UPLOAD_MEDIA_BATCH", use_proxy=False
        )

        for index in range(4):
            acquired = await dispatcher._acquire_direct_slot(
                f"account-{index}@example.com",
                object(),
                max_concurrent_override=override,
            )
            assert acquired is True
        assert dispatcher._direct_running == 4

        for _ in range(4):
            await dispatcher._release_direct_slot()

    asyncio.run(scenario())
