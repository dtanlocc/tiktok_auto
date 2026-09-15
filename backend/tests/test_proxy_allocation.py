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
