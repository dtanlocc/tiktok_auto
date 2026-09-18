from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote

@dataclass
class Proxy:
    id: Optional[str]
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    protocol: str = "http"  # http, https, socks5
    label: str = ""
    note: str = ""
    enabled: bool = True
    created_at: str = ""
    check_status: str = "UNCHECKED"  # OK, WARN, FAIL, UNCHECKED
    check_error: str = ""
    checked_at: str = ""
    exit_ip: str = ""
    country: str = ""
    latency_ms: Optional[int] = None
    tiktok_ok: Optional[bool] = None
    cdn_ok: Optional[bool] = None

    @property
    def connection_string(self) -> str:
        """Trả về chuỗi kết nối chuẩn cho Playwright/Curl_cffi"""
        return f"{self.protocol}://{self.host}:{self.port}"

    @property
    def url_with_auth(self) -> str:
        """URL for httpx, credentials percent-encoded."""
        auth = ""
        if self.username:
            auth = f"{quote(str(self.username), safe='')}:{quote(str(self.password or ''), safe='')}@"
        return f"{self.protocol}://{auth}{self.host}:{self.port}"

    @property
    def endpoint_key(self) -> tuple:
        """Two proxies with the same key are the same route."""
        return (
            (self.protocol or "").strip().casefold(),
            (self.host or "").strip().casefold(),
            int(self.port or 0),
            (self.username or "").strip(),
        )
