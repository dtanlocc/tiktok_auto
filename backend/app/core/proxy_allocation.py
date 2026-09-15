"""Deterministic proxy balancing and fair queue ordering helpers."""

from collections import OrderedDict, deque
from typing import Any, Iterable


def _account_id(account: Any) -> str:
    return str(
        getattr(account, "id", None) or getattr(account, "email", None) or ""
    ).strip().casefold()


def _proxy_id(proxy: Any) -> str:
    return str(getattr(proxy, "id", None) or "")


def _proxy_sort_key(proxy: Any) -> tuple:
    return (
        str(getattr(proxy, "protocol", "") or "").casefold(),
        str(getattr(proxy, "host", "") or "").casefold(),
        int(getattr(proxy, "port", 0) or 0),
        _proxy_id(proxy),
    )


def plan_balanced_proxy_assignments(
    accounts: Iterable[Any],
    proxies: Iterable[Any],
) -> dict[str, str]:
    """Balance selected accounts while changing as few routes as possible."""
    proxy_list = sorted(
        [proxy for proxy in proxies if _proxy_id(proxy)],
        key=_proxy_sort_key,
    )
    if not proxy_list:
        return {}

    unique_accounts: list[Any] = []
    seen_accounts: set[str] = set()
    for account in accounts:
        key = _account_id(account)
        if not key or key in seen_accounts:
            continue
        seen_accounts.add(key)
        unique_accounts.append(account)
    if not unique_accounts:
        return {}

    proxy_ids = [_proxy_id(proxy) for proxy in proxy_list]
    groups: dict[str, list[Any]] = {proxy_id: [] for proxy_id in proxy_ids}
    pending: list[Any] = []
    for account in unique_accounts:
        current_proxy_id = str(getattr(account, "proxy_id", None) or "")
        if current_proxy_id in groups:
            groups[current_proxy_id].append(account)
        else:
            pending.append(account)

    base, extra = divmod(len(unique_accounts), len(proxy_ids))
    target_priority = sorted(
        proxy_ids,
        key=lambda proxy_id: (-len(groups[proxy_id]), proxy_ids.index(proxy_id)),
    )
    extra_proxy_ids = set(target_priority[:extra])
    targets = {
        proxy_id: base + int(proxy_id in extra_proxy_ids)
        for proxy_id in proxy_ids
    }

    assignments: dict[str, str] = {}
    kept_counts: dict[str, int] = {}
    for proxy_id in proxy_ids:
        keep = groups[proxy_id][: targets[proxy_id]]
        overflow = groups[proxy_id][targets[proxy_id] :]
        kept_counts[proxy_id] = len(keep)
        pending.extend(overflow)
        for account in keep:
            assignments[_account_id(account)] = proxy_id

    deficits: list[str] = []
    for proxy_id in proxy_ids:
        deficits.extend([proxy_id] * (targets[proxy_id] - kept_counts[proxy_id]))
    for account, proxy_id in zip(pending, deficits):
        assignments[_account_id(account)] = proxy_id

    return assignments


def plan_round_robin_proxy_assignments(
    accounts: Iterable[Any],
    proxies: Iterable[Any],
) -> dict[str, str]:
    """Remap only the selected accounts from scratch in strict round-robin.

    Existing ``account.proxy_id`` values are deliberately ignored. This is the
    behaviour of the explicit "auto allocate selected accounts" action: the
    selected range is a new distribution batch and accounts outside that range
    are not part of its balancing calculation.
    """
    proxy_ids = [
        _proxy_id(proxy)
        for proxy in sorted(
            [proxy for proxy in proxies if _proxy_id(proxy)],
            key=_proxy_sort_key,
        )
    ]
    if not proxy_ids:
        return {}

    assignments: dict[str, str] = {}
    seen_accounts: set[str] = set()
    position = 0
    for account in accounts:
        key = _account_id(account)
        if not key or key in seen_accounts:
            continue
        seen_accounts.add(key)
        assignments[key] = proxy_ids[position % len(proxy_ids)]
        position += 1
    return assignments


def interleave_accounts_by_proxy(accounts: Iterable[Any]) -> list[Any]:
    """Round-robin lanes so initial worker slots use distinct proxies."""
    buckets: "OrderedDict[str, deque[Any]]" = OrderedDict()
    for account in accounts:
        lane = str(getattr(account, "proxy_id", None) or "__unassigned__")
        buckets.setdefault(lane, deque()).append(account)

    ordered: list[Any] = []
    while buckets:
        empty: list[str] = []
        for lane, bucket in buckets.items():
            if bucket:
                ordered.append(bucket.popleft())
            if not bucket:
                empty.append(lane)
        for lane in empty:
            buckets.pop(lane, None)
    return ordered
