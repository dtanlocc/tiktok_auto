import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.domain.entities.account import TikTokAccount
from app.domain.entities.proxy import Proxy
from app.interfaces.api import accounts_router, proxies_router
from app.interfaces.dto.proxy_dto import ProxyCheckIn, ProxyCreateIn, ProxyImportTextIn, ProxyUpdateIn
from app.use_cases.orchestration.task_dispatcher import ConcurrentTaskDispatcher
from app.use_cases.proxies import proxy_health_check
from app.use_cases.proxies.proxy_health_check import cdn_script_urls, check_proxy


class ProxyRepo:
    def __init__(self, proxies=(), counts=None):
        self.proxies = {p.id: p for p in proxies}
        self.counts = dict(counts or {})
        self.detached = []

    def detach_accounts(self, proxy_id):
        moved = [f"acc{n}@example.com" for n in range(self.counts.get(str(proxy_id), 0))]
        self.counts[str(proxy_id)] = 0
        self.detached.extend(moved)
        return moved

    def get_all(self):
        return list(self.proxies.values())

    def get_by_id(self, proxy_id):
        return self.proxies.get(proxy_id)

    def save(self, proxy):
        self.proxies[proxy.id] = proxy
        return proxy

    def delete(self, proxy_id):
        return self.proxies.pop(proxy_id, None) is not None

    def account_counts(self):
        return dict(self.counts)


class AccountRepo:
    def __init__(self, accounts):
        self.accounts = {a.id: a for a in accounts}

    def get_by_id(self, account_id):
        return self.accounts.get(account_id)

    def get_all(self):
        return list(self.accounts.values())

    def save(self, account):
        self.accounts[account.id] = account
        return account


def _proxy(proxy_id, host, *, enabled=True, label="", username="u", password="p"):
    return Proxy(id=proxy_id, host=host, port=1080, protocol="socks5",
                 username=username, password=password, enabled=enabled, label=label)


def _account(n, proxy_id=None):
    email = f"acc{n}@example.com"
    return TikTokAccount(id=email, email=email, username=f"acc{n}", proxy_id=proxy_id)


def _run(coro):
    return asyncio.run(coro)


# --- import, create, edit, delete -------------------------------------------------

def test_import_skips_proxies_already_in_the_store_and_repeated_lines():
    repo = ProxyRepo([_proxy("old", "10.0.0.1")])
    text = "\n".join([
        "socks5://u:p@10.0.0.1:1080",        # already stored
        "socks5://u:p@10.0.0.2:1080",
        "SOCKS5://u:p@10.0.0.2:1080",        # repeated in the same paste
        "ftp://10.0.0.3:21",                 # unsupported protocol
        "not a proxy",
        "# comment",
    ])

    result = _run(proxies_router.import_proxies_from_text(ProxyImportTextIn(text=text), repo))

    assert (result["imported"], result["duplicates"], result["invalid"]) == (1, 2, 2)
    assert sorted(p.host for p in repo.get_all()) == ["10.0.0.1", "10.0.0.2"]
    assert "bỏ qua 2 proxy đã có" in result["message"]


def test_creating_a_proxy_that_already_exists_is_refused_with_its_name():
    repo = ProxyRepo([_proxy("old", "10.0.0.1", label="Viettel 1")])

    with pytest.raises(HTTPException) as err:
        _run(proxies_router.create_proxy(
            ProxyCreateIn(host="10.0.0.1", port=1080, protocol="socks5", username="u", password="x"),
            repo))

    assert err.value.status_code == 409
    assert "Viettel 1" in err.value.detail


@pytest.mark.parametrize("fields", [
    {"host": "10.0.0.9", "port": 0, "protocol": "socks5"},
    {"host": "10.0.0.9", "port": 1080, "protocol": "ftp"},
    {"host": "bad host", "port": 1080, "protocol": "http"},
])
def test_a_proxy_with_an_unusable_route_is_refused(fields):
    with pytest.raises(HTTPException) as err:
        _run(proxies_router.create_proxy(ProxyCreateIn(**fields), ProxyRepo()))
    assert err.value.status_code == 400


def test_editing_without_a_password_keeps_it_and_route_changes_clear_the_old_check():
    proxy = _proxy("p1", "10.0.0.1")
    proxy.check_status, proxy.exit_ip, proxy.cdn_ok = "OK", "10.0.0.1", True
    repo = ProxyRepo([proxy], counts={"p1": 4})

    out = _run(proxies_router.update_proxy("p1", ProxyUpdateIn(label="Nhóm A"), repo))
    assert out.label == "Nhóm A" and out.has_password and out.check_status == "OK"
    assert out.account_count == 4

    out = _run(proxies_router.update_proxy("p1", ProxyUpdateIn(port=2080), repo))
    assert repo.get_by_id("p1").password == "p"
    assert out.check_status == "UNCHECKED" and out.exit_ip == "" and out.cdn_ok is None

    _run(proxies_router.update_proxy("p1", ProxyUpdateIn(password=""), repo))
    assert repo.get_by_id("p1").password is None


def test_deleting_with_detach_puts_its_accounts_back_on_the_machine_network():
    repo = ProxyRepo([_proxy("busy", "10.0.0.1")], counts={"busy": 3})

    result = _run(proxies_router.delete_proxy("busy", detach_accounts=True, proxy_repo=repo))

    assert result["detached"] == 3
    assert len(repo.detached) == 3 and repo.get_all() == []
    assert "Mạng thật" in result["message"]


def test_a_proxy_still_assigned_to_accounts_cannot_be_deleted():
    repo = ProxyRepo([_proxy("busy", "10.0.0.1"), _proxy("free", "10.0.0.2")], counts={"busy": 3})

    with pytest.raises(HTTPException) as err:
        _run(proxies_router.delete_proxy("busy", proxy_repo=repo))
    assert err.value.status_code == 409 and "3 account" in err.value.detail

    _run(proxies_router.delete_proxy("free", proxy_repo=repo))
    assert [p.id for p in repo.get_all()] == ["busy"]


def test_the_list_never_returns_a_password():
    repo = ProxyRepo([_proxy("p1", "10.0.0.1")], counts={"p1": 2})

    out = _run(proxies_router.list_proxies(repo))[0]

    assert "password" not in out.model_dump()
    assert out.has_password is True and out.account_count == 2


# --- allocation ------------------------------------------------------------------

def test_allocation_spreads_accounts_only_over_the_ticked_proxies():
    proxies = [_proxy("a", "10.0.0.1"), _proxy("b", "10.0.0.2"), _proxy("c", "10.0.0.3")]
    accounts = [_account(n, proxy_id="a") for n in range(6)]
    account_repo = AccountRepo(accounts)

    result = _run(accounts_router.auto_allocate_proxies_endpoint(
        account_ids=[a.id for a in accounts], proxy_ids=["b", "c"],
        account_repo=account_repo, proxy_repo=ProxyRepo(proxies)))

    used = [account_repo.get_by_id(a.id).proxy_id for a in accounts]
    assert sorted(used) == ["b", "b", "b", "c", "c", "c"]
    assert result["distribution"] == {"b": 3, "c": 3}


def test_allocation_without_a_choice_uses_every_enabled_proxy_and_never_a_disabled_one():
    proxies = [_proxy("a", "10.0.0.1"), _proxy("off", "10.0.0.2", enabled=False)]
    accounts = [_account(n) for n in range(4)]
    account_repo = AccountRepo(accounts)

    _run(accounts_router.auto_allocate_proxies_endpoint(
        account_ids=[a.id for a in accounts], proxy_ids=None,
        account_repo=account_repo, proxy_repo=ProxyRepo(proxies)))

    assert {account_repo.get_by_id(a.id).proxy_id for a in accounts} == {"a"}


def test_ticking_a_disabled_proxy_is_refused_and_nothing_moves():
    proxies = [_proxy("a", "10.0.0.1"), _proxy("off", "10.0.0.2", enabled=False, label="Hỏng")]
    accounts = [_account(n, proxy_id="a") for n in range(2)]
    account_repo = AccountRepo(accounts)

    with pytest.raises(HTTPException) as err:
        _run(accounts_router.auto_allocate_proxies_endpoint(
            account_ids=[a.id for a in accounts], proxy_ids=["a", "off"],
            account_repo=account_repo, proxy_repo=ProxyRepo(proxies)))

    assert err.value.status_code == 400 and "Hỏng" in err.value.detail
    assert {a.proxy_id for a in account_repo.get_all()} == {"a"}


def test_an_account_cannot_be_bound_to_a_disabled_proxy_by_hand():
    account_repo = AccountRepo([_account(1, proxy_id="a")])
    proxy_repo = ProxyRepo([_proxy("a", "10.0.0.1"), _proxy("off", "10.0.0.2", enabled=False)])

    with pytest.raises(HTTPException) as err:
        _run(accounts_router.bind_proxy_to_account(
            "acc1@example.com", proxy_id="off", account_repo=account_repo, proxy_repo=proxy_repo))

    assert err.value.status_code == 400
    assert account_repo.get_by_id("acc1@example.com").proxy_id == "a"


def test_new_accounts_are_only_given_enabled_proxies():
    account_repo = AccountRepo([_account(n, proxy_id="busy") for n in range(3)])
    proxy_repo = ProxyRepo([_proxy("busy", "10.0.0.1"), _proxy("idle_off", "10.0.0.2", enabled=False)])

    assert accounts_router._get_least_used_proxy_id(account_repo, proxy_repo) == "busy"


# --- run time ----------------------------------------------------------------------

def test_a_run_on_a_disabled_proxy_stops_instead_of_taking_another_route(monkeypatch):
    dispatcher = ConcurrentTaskDispatcher(max_tabs=2)
    proxies = [SimpleNamespace(id="off", host="10.0.0.2", port=1080, enabled=False, label="Hỏng")]
    monkeypatch.setattr(dispatcher, "_load_all_proxies", lambda: proxies)

    with pytest.raises(RuntimeError, match="Proxy đang tắt"):
        _run(dispatcher._acquire_assigned_proxy("acc1@example.com", "off", object()))


# --- health check -----------------------------------------------------------------

def test_cdn_scripts_are_taken_one_per_host_from_the_page_itself():
    html = (
        '<script src="https://sf16-website-login.neutral.ttwstatic.com/obj/a/index.js"></script>'
        '<script src="https://sf16-website-login.neutral.ttwstatic.com/obj/b/other.js"></script>'
        '<script src="https://lf16-tiktok-web.tiktokcdn-us.com/obj/c/main.js"></script>'
        '<script src="https://example.com/x.js"></script>'
    )
    assert cdn_script_urls(html) == [
        "https://sf16-website-login.neutral.ttwstatic.com/obj/a/index.js",
        "https://lf16-tiktok-web.tiktokcdn-us.com/obj/c/main.js",
    ]


def _fake_client(routes):
    """routes: url prefix -> httpx.Response | Exception."""

    class Client:
        def __init__(self, **_kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_a):
            return None

        async def get(self, url):
            for prefix, answer in routes.items():
                if url.startswith(prefix):
                    if isinstance(answer, Exception):
                        raise answer
                    return answer
            raise httpx.ConnectError("no route")

    return Client


_PAGE = '<script src="https://lf16-tiktok-web.tiktokcdn-us.com/obj/app/main.js"></script>'


def test_a_proxy_that_reaches_tiktok_but_not_its_cdn_is_a_warning_naming_the_host():
    client = _fake_client({
        "https://ipinfo.io": httpx.Response(200, json={"ip": "1.2.3.4", "country": "id"}),
        "https://www.tiktok.com": httpx.Response(200, text=_PAGE),
        "https://lf16-tiktok-web.tiktokcdn-us.com": httpx.ConnectError("refused"),
    })

    result = _run(check_proxy(_proxy("p", "1.2.3.4"), client_factory=client))

    assert result.status == "WARN"
    assert (result.exit_ip, result.country, result.tiktok_ok, result.cdn_ok) == ("1.2.3.4", "ID", True, False)
    assert "lf16-tiktok-web.tiktokcdn-us.com" in result.error


def test_a_healthy_proxy_is_ok_even_when_the_first_ip_service_rate_limits_it():
    client = _fake_client({
        "https://ipinfo.io": httpx.Response(429),
        "http://ip-api.com": httpx.Response(200, json={"query": "5.6.7.8", "countryCode": "US"}),
        "https://www.tiktok.com": httpx.Response(200, text=_PAGE),
        "https://lf16-tiktok-web.tiktokcdn-us.com": httpx.Response(200, text="js"),
    })

    result = _run(check_proxy(_proxy("p", "1.2.3.4"), client_factory=client))

    assert result.status == "OK" and result.error == ""
    assert (result.exit_ip, result.country) == ("5.6.7.8", "US")


def test_an_unreachable_proxy_fails_without_trying_tiktok():
    asked = []

    class Client(_fake_client({})):
        async def get(self, url):
            asked.append(url)
            raise httpx.ProxyError("connection refused")

    result = _run(check_proxy(_proxy("p", "1.2.3.4"), client_factory=Client))

    assert result.status == "FAIL" and "Không kết nối được proxy" in result.error
    assert asked == ["https://ipinfo.io/json"]


def test_a_check_is_not_saved_over_a_proxy_edited_while_it_ran(monkeypatch):
    proxy = _proxy("p1", "10.0.0.1")
    repo = ProxyRepo([proxy])

    async def fake_check(proxies):
        # The operator changes the port while the check is running.
        repo.proxies["p1"] = _proxy("p1", "10.0.0.1")
        repo.proxies["p1"].port = 2080
        return {"p1": proxy_health_check.ProxyCheckResult("OK", "", "now", exit_ip="10.0.0.1")}

    monkeypatch.setattr(proxies_router, "check_proxies", fake_check)
    out = _run(proxies_router.check_proxy_health(ProxyCheckIn(proxy_ids=["p1"]), repo))

    assert out == []
    assert repo.get_by_id("p1").check_status == "UNCHECKED"
