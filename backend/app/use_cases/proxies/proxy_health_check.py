"""Check a proxy the way a TikTok browser session will use it.

Reaching www.tiktok.com is not enough: in 2026-09 a proxy served tiktok.com
normally while refusing every request to TikTok's CDN, so pages loaded with no
scripts and rendered blank. The check therefore also fetches the script assets
the TikTok page served THROUGH THIS PROXY links to - their host differs by exit
region (ttwstatic.com from Indonesia, tiktokcdn-us.com from the US), so no
fixed host is tested.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import httpx

from app.domain.entities.proxy import Proxy
from app.use_cases.health_check.quick_check_use_case import _HTTP_HEADERS

TIKTOK_URL = "https://www.tiktok.com/explore?lang=en"
#: Tried in order: ipinfo.io rate-limits some exit IPs (429 measured 2026-09-17).
IP_LOOKUPS = (
    ("https://ipinfo.io/json", "ip", "country"),
    ("http://ip-api.com/json/?fields=status,countryCode,query", "query", "countryCode"),
    ("https://api.ipify.org?format=json", "ip", None),
)
_CDN_SCRIPT = re.compile(
    r'(?:src|href)="(https://[a-z0-9.-]+\.(?:tiktokcdn(?:-[a-z]+)?\.com|ttwstatic\.com)/[^"]+?\.js)"',
    re.IGNORECASE,
)
_MAX_CDN_HOSTS = 3
_TIMEOUT = httpx.Timeout(connect=10.0, read=15.0, write=10.0, pool=10.0)

STATUS_OK = "OK"
STATUS_WARN = "WARN"
STATUS_FAIL = "FAIL"


@dataclass
class ProxyCheckResult:
    status: str
    error: str
    checked_at: str
    exit_ip: str = ""
    country: str = ""
    latency_ms: Optional[int] = None
    tiktok_ok: Optional[bool] = None
    cdn_ok: Optional[bool] = None

    def apply_to(self, proxy: Proxy) -> Proxy:
        proxy.check_status = self.status
        proxy.check_error = self.error
        proxy.checked_at = self.checked_at
        proxy.exit_ip = self.exit_ip
        proxy.country = self.country
        proxy.latency_ms = self.latency_ms
        proxy.tiktok_ok = self.tiktok_ok
        proxy.cdn_ok = self.cdn_ok
        return proxy


def cdn_script_urls(html: str, limit: int = _MAX_CDN_HOSTS) -> list[str]:
    """One script URL per CDN host the page loads from, in page order."""
    urls: list[str] = []
    hosts: set[str] = set()
    for url in _CDN_SCRIPT.findall(html or ""):
        host = urlparse(url).hostname or ""
        if host and host not in hosts:
            hosts.add(host)
            urls.append(url)
            if len(urls) >= limit:
                break
    return urls


def _describe(exc: BaseException) -> str:
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text[:120]}" if text else type(exc).__name__


async def check_proxy(
    proxy: Proxy,
    *,
    client_factory: Callable[..., Any] = httpx.AsyncClient,
) -> ProxyCheckResult:
    checked_at = datetime.now().isoformat(timespec="seconds")
    problems: list[str] = []
    exit_ip = country = ""
    latency_ms: Optional[int] = None
    tiktok_ok: Optional[bool] = None
    cdn_ok: Optional[bool] = None
    try:
        async with client_factory(
            proxy=proxy.url_with_auth,
            headers=_HTTP_HEADERS,
            timeout=_TIMEOUT,
            follow_redirects=True,
            trust_env=False,
        ) as client:
            connected = False
            last_error: Optional[BaseException] = None
            for url, ip_key, country_key in IP_LOOKUPS:
                try:
                    response = await client.get(url)
                except (httpx.ProxyError, httpx.ConnectError, httpx.ConnectTimeout) as exc:
                    last_error = exc
                    break   # the proxy itself is unreachable; another lookup won't help
                except httpx.HTTPError as exc:
                    last_error = exc
                    connected = True
                    continue
                connected = True
                if response.status_code != 200:
                    continue
                try:
                    data = response.json()
                except ValueError:
                    continue
                exit_ip = str(data.get(ip_key) or "")
                country = str(data.get(country_key) or "").upper() if country_key else ""
                if exit_ip:
                    break
            if not connected:
                return ProxyCheckResult(
                    STATUS_FAIL,
                    "Không kết nối được proxy"
                    + (f" ({_describe(last_error)})" if last_error else ""),
                    checked_at,
                )
            if not exit_ip:
                problems.append("không xác định được IP ra")

            html = ""
            started = time.monotonic()
            try:
                response = await client.get(TIKTOK_URL)
                latency_ms = int((time.monotonic() - started) * 1000)
                tiktok_ok = response.status_code < 400
                html = response.text if tiktok_ok else ""
                if not tiktok_ok:
                    problems.append(f"TikTok trả HTTP {response.status_code}")
            except httpx.HTTPError as exc:
                tiktok_ok = False
                problems.append(f"không vào được TikTok ({_describe(exc)})")

            if tiktok_ok:
                scripts = cdn_script_urls(html)
                if not scripts:
                    problems.append("TikTok không trả danh sách CDN để thử")
                else:
                    blocked = []
                    for url in scripts:
                        host = urlparse(url).hostname
                        try:
                            response = await client.get(url)
                            if response.status_code >= 400:
                                blocked.append(f"{host} (HTTP {response.status_code})")
                        except httpx.HTTPError as exc:
                            blocked.append(f"{host} ({type(exc).__name__})")
                    cdn_ok = not blocked
                    if blocked:
                        problems.append("CDN TikTok bị chặn: " + ", ".join(blocked))
    except Exception as exc:
        return ProxyCheckResult(STATUS_FAIL, f"Lỗi kiểm tra ({_describe(exc)})", checked_at)

    if tiktok_ok and cdn_ok and exit_ip:
        status = STATUS_OK
    else:
        status = STATUS_WARN
    return ProxyCheckResult(
        status,
        "; ".join(problems),
        checked_at,
        exit_ip=exit_ip,
        country=country,
        latency_ms=latency_ms,
        tiktok_ok=tiktok_ok,
        cdn_ok=cdn_ok,
    )


async def check_proxies(proxies: list[Proxy], concurrency: int = 4) -> dict[str, ProxyCheckResult]:
    gate = asyncio.Semaphore(max(1, concurrency))

    async def run(proxy: Proxy) -> tuple[str, ProxyCheckResult]:
        async with gate:
            return str(proxy.id), await check_proxy(proxy)

    return dict(await asyncio.gather(*(run(proxy) for proxy in proxies)))
