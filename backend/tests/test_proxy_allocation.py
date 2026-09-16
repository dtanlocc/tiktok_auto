import pytest

from types import SimpleNamespace

from app.core.proxy_allocation import (
    interleave_accounts_by_proxy,
    plan_balanced_proxy_assignments,
    plan_round_robin_proxy_assignments,
)


def _proxy(proxy_id, host):
    return SimpleNamespace(
        id=proxy_id,
        protocol="socks5",
        host=host,
        port=50101,
    )


def _account(index, proxy_id):
    return SimpleNamespace(
        id=f"account-{index}@example.com",
        email=f"account-{index}@example.com",
        proxy_id=proxy_id,
    )


def test_balance_repairs_dangling_assignments_without_moving_valid_sessions():
    proxies = [_proxy("proxy-1", "10.0.0.1"), _proxy("proxy-2", "10.0.0.2")]
    accounts = (
        [_account(index, "proxy-1") for index in range(291)]
        + [_account(index + 291, "proxy-2") for index in range(157)]
        + [_account(index + 448, "deleted-proxy") for index in range(324)]
    )

    plan = plan_balanced_proxy_assignments(accounts, proxies)

    assert list(plan.values()).count("proxy-1") == 386
    assert list(plan.values()).count("proxy-2") == 386
    assert all(plan[account.id] == account.proxy_id for account in accounts[:448])


def test_balance_selected_accounts_is_even_and_moves_only_surplus():
    proxies = [_proxy("proxy-1", "10.0.0.1"), _proxy("proxy-2", "10.0.0.2")]
    accounts = [_account(index, "proxy-1") for index in range(5)]

    plan = plan_balanced_proxy_assignments(accounts, proxies)

    assert sorted(list(plan.values()).count(proxy.id) for proxy in proxies) == [2, 3]
    assert sum(plan[account.id] != account.proxy_id for account in accounts) == 2


def test_queue_order_round_robins_proxy_lanes():
    accounts = [
        _account(1, "proxy-1"),
        _account(2, "proxy-1"),
        _account(3, "proxy-2"),
        _account(4, "proxy-2"),
        _account(5, "proxy-3"),
    ]

    ordered = interleave_accounts_by_proxy(accounts)

    assert [account.proxy_id for account in ordered] == [
        "proxy-1",
        "proxy-2",
        "proxy-3",
        "proxy-1",
        "proxy-2",
    ]


def test_explicit_selected_redistribution_ignores_every_old_proxy_assignment():
    proxies = [
        _proxy("proxy-1", "10.0.0.1"),
        _proxy("proxy-2", "10.0.0.2"),
        _proxy("proxy-3", "10.0.0.3"),
    ]
    accounts = [_account(index, "proxy-3") for index in range(7)]

    plan = plan_round_robin_proxy_assignments(accounts, proxies)

    assert [plan[account.id] for account in accounts] == [
        "proxy-1",
        "proxy-2",
        "proxy-3",
        "proxy-1",
        "proxy-2",
        "proxy-3",
        "proxy-1",
    ]
    assert sorted(list(plan.values()).count(proxy.id) for proxy in proxies) == [2, 2, 3]


def test_explicit_selected_redistribution_does_not_include_unselected_accounts():
    proxies = [_proxy("proxy-1", "10.0.0.1"), _proxy("proxy-2", "10.0.0.2")]
    selected = [_account(1, "old-proxy"), _account(2, "old-proxy")]
    unselected = _account(3, "old-proxy")

    plan = plan_round_robin_proxy_assignments(selected, proxies)

    assert plan == {
        selected[0].id: "proxy-1",
        selected[1].id: "proxy-2",
    }
    assert unselected.id not in plan


def test_an_empty_proxy_store_is_refused_not_run_direct():
    """Returning nothing here launched the browser with no proxy at all.

    The caller only builds proxy_config when an entity comes back, so an empty
    store meant a direct connection while USE_PROXY was True, silently. The
    session then publishes from this machine's own egress and TikTok stamps
    that country on the video permanently - measured 2026-09-16 as seven
    videos marked VN on accounts whose every earlier upload was ID.
    """
    import asyncio

    from app.use_cases.orchestration.task_dispatcher import ConcurrentTaskDispatcher

    dispatcher = ConcurrentTaskDispatcher.__new__(ConcurrentTaskDispatcher)
    dispatcher._load_all_proxies = lambda: []

    with pytest.raises(RuntimeError, match="Kho Proxy"):
        asyncio.run(
            dispatcher._acquire_assigned_proxy("account", "some-proxy-id", None)
        )


def test_an_unassigned_account_is_still_refused():
    """The pre-existing guard must survive the new one."""
    import asyncio

    from app.use_cases.orchestration.task_dispatcher import ConcurrentTaskDispatcher

    dispatcher = ConcurrentTaskDispatcher.__new__(ConcurrentTaskDispatcher)
    dispatcher._load_all_proxies = lambda: [object()]

    with pytest.raises(RuntimeError, match="chua duoc gan proxy"):
        asyncio.run(dispatcher._acquire_assigned_proxy("account", None, None))
