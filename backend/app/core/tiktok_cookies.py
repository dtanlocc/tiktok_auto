"""Validation helpers for persisted TikTok authentication cookies."""

from typing import Any, Iterable


TIKTOK_AUTH_COOKIE_NAMES = frozenset({"sessionid", "sessionid_ss"})


def has_tiktok_auth_cookies(cookies: Iterable[dict[str, Any]] | None) -> bool:
    """Return whether a browser snapshot contains a non-empty auth session."""
    return any(
        isinstance(cookie, dict)
        and str(cookie.get("name") or "") in TIKTOK_AUTH_COOKIE_NAMES
        and bool(cookie.get("value"))
        for cookie in cookies or []
    )
