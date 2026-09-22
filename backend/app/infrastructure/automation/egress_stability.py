"""Does a route leave the machine from one address, or a new one per connection?

⛔ A ROUTE THAT CHANGES ADDRESS PER CONNECTION LOGS THE ACCOUNT OUT ON POST.
Measured 2026-09-19 on a machine-wide VPN ("Mạng thật"): 12 requests in a row
left from 12 different 14.102.84.x addresses. Two accounts with working
cookies - one of them logged in by OTP on that very VPN minutes before -
published their video and TikTok ended the session 4-10s after "Post now"
("session expired, please sign in again"). The same accounts posted three
videos each the day before through a fixed-address proxy and kept their
sessions, as did all 55 accounts of the batch.

Each sample below opens a NEW connection, because the rotation happens per
connection: one kept-alive client would see one address and say "stable".
"""

from __future__ import annotations

import asyncio
import ipaddress
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urlsplit

import httpx

#: IPv4-only echo services, so a dual-stack machine never looks like it rotates.
_ECHO_URLS = ("https://api.ipify.org", "https://ipv4.icanhazip.com")
_TIMEOUT = httpx.Timeout(connect=10.0, read=10.0, write=10.0, pool=10.0)


def proxy_url_from_config(proxy_config: Optional[Dict[str, Any]]) -> Optional[str]:
    """The adapter's proxy dict as one URL httpx understands, or None for direct."""
    if not proxy_config or not proxy_config.get("server"):
        return None
    server = str(proxy_config["server"])
    if "://" not in server:
        server = "http://" + server
    parts = urlsplit(server)
    user = proxy_config.get("username")
    if not user:
        return server
    password = proxy_config.get("password") or ""
    auth = quote(str(user), safe="") + ":" + quote(str(password), safe="")
    return f"{parts.scheme}://{auth}@{parts.netloc}"


async def _one_sample(proxy_url: Optional[str], index: int) -> Optional[str]:
    for offset in range(len(_ECHO_URLS)):
        url = _ECHO_URLS[(index + offset) % len(_ECHO_URLS)]
        try:
            async with httpx.AsyncClient(proxy=proxy_url, timeout=_TIMEOUT) as client:
                text = (await client.get(url)).text.strip()
            ipaddress.IPv4Address(text)
            return text
        except Exception:
            continue
    return None


async def sample_egress_ips(proxy_url: Optional[str], samples: int = 5) -> List[str]:
    """The public IPv4 address of each of `samples` fresh connections.

    In parallel: one after another took 26s through the slow mobile proxy.
    """
    found = await asyncio.gather(
        *(_one_sample(proxy_url, index) for index in range(max(1, samples)))
    )
    return [ip for ip in found if ip]


def rotating_addresses(ips: List[str]) -> List[str]:
    """The distinct addresses when the route changed address, else []."""
    distinct = sorted(set(ips))
    return distinct if len(distinct) > 1 else []
