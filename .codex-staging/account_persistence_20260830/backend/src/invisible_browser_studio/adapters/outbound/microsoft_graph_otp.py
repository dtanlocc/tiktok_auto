from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from invisible_browser_studio.application.ports import OtpReader

_OTP = re.compile(r"(?<!\d)(\d{6})(?!\d)")
_TOKEN_URLS = (
    "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",
    "https://login.microsoftonline.com/common/oauth2/v2.0/token",
)
_SCOPES = (
    "https://graph.microsoft.com/Mail.Read offline_access",
    "https://graph.microsoft.com/.default offline_access",
    "Mail.Read offline_access",
)
_MAIL_URLS = (
    "https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages",
    "https://graph.microsoft.com/v1.0/me/mailFolders/junkemail/messages",
)
_TIKTOK_HINTS = ("tiktok", "verification", "verify", "security code")


class MicrosoftGraphOtpReader(OtpReader):
    """Reads one fresh TikTok code from an explicitly supplied Microsoft mailbox."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 25,
        max_attempts: int = 12,
        delay_seconds: float = 4,
    ) -> None:
        self._timeout = max(5.0, timeout_seconds)
        self._max_attempts = max(1, max_attempts)
        self._delay_seconds = max(0.25, delay_seconds)

    async def fetch_tiktok_code(
        self,
        *,
        email: str,
        refresh_token: str,
        client_id: str,
        requested_at: datetime,
    ) -> str | None:
        del email  # The Graph token identifies the mailbox; never log the address here.
        async with httpx.AsyncClient(timeout=self._timeout, trust_env=False) as client:
            access_token = await self._access_token(client, refresh_token, client_id)
            if not access_token:
                return None
            threshold = requested_at
            if threshold.tzinfo is None:
                threshold = threshold.astimezone().astimezone(UTC)
            else:
                threshold = threshold.astimezone(UTC)
            # A small clock-skew allowance avoids reusing a code from an earlier
            # retry while still tolerating Microsoft/local timestamp differences.
            threshold -= timedelta(seconds=5)

            for attempt in range(self._max_attempts):
                messages = await self._messages(client, access_token)
                candidates: list[tuple[datetime, str]] = []
                for message in messages:
                    received = _received_at(message)
                    if received is None or received < threshold or not _looks_like_tiktok(message):
                        continue
                    code = _extract_code(message)
                    if code:
                        candidates.append((received, code))
                if candidates:
                    return max(candidates, key=lambda item: item[0])[1]
                if attempt + 1 < self._max_attempts:
                    await asyncio.sleep(self._delay_seconds)
        return None
    async def _access_token(
        self,
        client: httpx.AsyncClient,
        refresh_token: str,
        client_id: str,
    ) -> str | None:
        for token_url in _TOKEN_URLS:
            for scope in _SCOPES:
                try:
                    response = await client.post(
                        token_url,
                        data={
                            "client_id": client_id,
                            "refresh_token": refresh_token,
                            "grant_type": "refresh_token",
                            "scope": scope,
                        },
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                except httpx.HTTPError:
                    continue
                if response.status_code == 200:
                    access_token = response.json().get("access_token")
                    if isinstance(access_token, str) and access_token:
                        return access_token
        return None

    @staticmethod
    async def _messages(
        client: httpx.AsyncClient, access_token: str
    ) -> list[dict[str, Any]]:
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "$top": "15",
            "$select": "subject,bodyPreview,receivedDateTime,from",
            "$orderby": "receivedDateTime desc",
        }
        messages: list[dict[str, Any]] = []
        for url in _MAIL_URLS:
            try:
                response = await client.get(url, headers=headers, params=params)
            except httpx.HTTPError:
                continue
            if response.status_code == 200:
                values = response.json().get("value", [])
                if isinstance(values, list):
                    messages.extend(value for value in values if isinstance(value, dict))
        return messages


class SimulatedOtpReader(OtpReader):
    async def fetch_tiktok_code(
        self,
        *,
        email: str,
        refresh_token: str,
        client_id: str,
        requested_at: datetime,
    ) -> str | None:
        del email, refresh_token, client_id, requested_at
        return "123456"


def _looks_like_tiktok(message: dict[str, Any]) -> bool:
    sender = message.get("from")
    blob = " ".join(
        (
            str(message.get("subject") or ""),
            str(message.get("bodyPreview") or ""),
            str(sender or ""),
        )
    ).casefold()
    return any(hint in blob for hint in _TIKTOK_HINTS)


def _extract_code(message: dict[str, Any]) -> str | None:
    for field in ("subject", "bodyPreview"):
        match = _OTP.search(str(message.get(field) or ""))
        if match:
            return match.group(1)
    return None


def _received_at(message: dict[str, Any]) -> datetime | None:
    raw = message.get("receivedDateTime")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
