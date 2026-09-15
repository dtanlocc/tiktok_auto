import asyncio

from app.interfaces.api import proxies_router


class FakeUpload:
    def __init__(self, text: str):
        self._content = text.encode("utf-8")

    async def read(self) -> bytes:
        return self._content


class FakeProxyRepository:
    def __init__(self):
        self.saved = []

    def save(self, proxy):
        self.saved.append(proxy)
        return proxy


def test_import_accepts_host_port_username_password_format():
    proxy_repo = FakeProxyRepository()

    result = asyncio.run(
        proxies_router.import_proxies_from_files(
            files=[
                FakeUpload("socks5://192.168.1.8:30001:proxyuser30001:proxypass30001")
            ],
            proxy_repo=proxy_repo,
        )
    )

    assert len(proxy_repo.saved) == 1
    proxy = proxy_repo.saved[0]
    assert proxy.protocol == "socks5"
    assert proxy.host == "192.168.1.8"
    assert proxy.port == 30001
    assert proxy.username == "proxyuser30001"
    assert proxy.password == "proxypass30001"
    assert "1 Proxy" in result["message"]


def test_parser_keeps_existing_formats_supported():
    assert proxies_router._parse_proxy_line("http://127.0.0.1:8080") == (
        "127.0.0.1",
        8080,
        "http",
        None,
        None,
    )
    assert proxies_router._parse_proxy_line("socks5://user:pass@proxy.test:1080") == (
        "proxy.test",
        1080,
        "socks5",
        "user",
        "pass",
    )
    assert proxies_router._parse_proxy_line(
        "proxy.test|3128|http|legacy-user|legacy-pass"
    ) == ("proxy.test", 3128, "http", "legacy-user", "legacy-pass")
