from __future__ import annotations

import asyncio
import socket
import time
from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx

from invisible_browser_studio.application.dto import ProxyRotationResult
from invisible_browser_studio.application.ports import ProxyRotator


class HttpProxyRotator(ProxyRotator):
    """Calls a provider rotation endpoint without logging or retaining its secret URL."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        max_attempts: int = 3,
        settle_seconds: float = 2.0,
    ) -> None:
        self._timeout = max(1.0, timeout_seconds)
        self._max_attempts = max(1, max_attempts)
        self._settle_seconds = max(0.0, settle_seconds)

    async def rotate(self, rotation_url: str) -> ProxyRotationResult:
        await self._validate_public_url(rotation_url)
        started = time.monotonic()
        last_error: Exception | None = None
        async with httpx.AsyncClient(
            # Redirects are deliberately rejected so a validated public endpoint
            # cannot bounce the backend into localhost or a private network.
            follow_redirects=False,
            timeout=self._timeout,
            trust_env=False,
            headers={"User-Agent": "InvisibleBrowserStudio/0.1"},
        ) as client:
            for attempt in range(1, self._max_attempts + 1):
                try:
                    response = await client.get(rotation_url)
                    response.raise_for_status()
                    if self._settle_seconds:
                        await asyncio.sleep(self._settle_seconds)
                    return ProxyRotationResult(
                        attempts=attempt,
                        elapsed_seconds=time.monotonic() - started,
                        status_code=response.status_code,
                    )
                except (httpx.HTTPError, TimeoutError) as exc:
                    last_error = exc
                    if attempt < self._max_attempts:
                        await asyncio.sleep(min(0.5 * attempt, 2.0))
        raise RuntimeError(
            f"Proxy rotation endpoint failed after {self._max_attempts} attempts"
        ) from last_error

    @staticmethod
    async def _validate_public_url(value: str) -> None:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Rotation URL must be an absolute http(s) URL")
        if parsed.username or parsed.password:
            raise ValueError("Rotation URL cannot contain URL userinfo")
        hostname = parsed.hostname.rstrip(".").casefold()
        if hostname == "localhost" or hostname.endswith(".localhost"):
            raise ValueError("Rotation URL cannot target localhost")

        try:
            literal = ip_address(hostname)
        except ValueError:
            literal = None
        if literal is not None:
            if not literal.is_global:
                raise ValueError("Rotation URL must target a public address")
            return

        try:
            records = await asyncio.to_thread(
                socket.getaddrinfo,
                hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
                type=socket.SOCK_STREAM,
            )
        except OSError as exc:
            raise ValueError("Rotation URL hostname could not be resolved") from exc
        addresses = {record[4][0] for record in records}
        if not addresses or any(not ip_address(address).is_global for address in addresses):
            raise ValueError("Rotation URL resolved to a non-public address")
