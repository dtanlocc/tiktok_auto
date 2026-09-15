from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections.abc import Iterable

_HEADER_TIMESTAMP = b"x-tk-auto-timestamp"
_HEADER_NONCE = b"x-tk-auto-nonce"
_HEADER_CONTENT_HASH = b"x-tk-auto-content-sha256"
_HEADER_SIGNATURE = b"x-tk-auto-signature"
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{22,128}$")
_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class LocalAuthenticationError(RuntimeError):
    pass


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def decode_session_secret(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Local session secret must be unpadded base64url.")
    try:
        decoded = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (binascii.Error, ValueError) as exc:
        raise ValueError("Local session secret is invalid.") from exc
    if len(decoded) < 32:
        raise ValueError("Local session secret must contain at least 256 bits.")
    return decoded


def generate_session_secret() -> str:
    return _b64url(secrets.token_bytes(32))


def _request_target(scope: dict) -> bytes:
    path = scope.get("raw_path") or str(scope.get("path", "/")).encode("utf-8")
    query = scope.get("query_string") or b""
    return path + (b"?" + query if query else b"")


def _canonical_request(
    *, timestamp: int, nonce: str, method: str, target: bytes, content_hash: str
) -> bytes:
    return (
        f"TKAUTO-LOCAL-v1\n{timestamp}\n{nonce}\n{method.upper()}\n"
        f"{_b64url(target)}\n{content_hash}"
    ).encode("ascii")


class LocalRequestSigner:
    """Reference signer used by tests and the native bridge contract."""

    def __init__(self, session_secret: str | bytes) -> None:
        self._secret = (
            decode_session_secret(session_secret)
            if isinstance(session_secret, str)
            else session_secret
        )
        if len(self._secret) < 32:
            raise ValueError("Local session secret must contain at least 256 bits.")

    def headers(
        self,
        *,
        method: str,
        path: str,
        query: str = "",
        body: bytes = b"",
        timestamp: int | None = None,
        nonce: str | None = None,
    ) -> dict[str, str]:
        issued_at = int(time.time()) if timestamp is None else int(timestamp)
        request_nonce = nonce or _b64url(secrets.token_bytes(18))
        target = path.encode("utf-8") + (b"?" + query.encode("ascii") if query else b"")
        content_hash = hashlib.sha256(body).hexdigest()
        canonical = _canonical_request(
            timestamp=issued_at,
            nonce=request_nonce,
            method=method,
            target=target,
            content_hash=content_hash,
        )
        signature = _b64url(hmac.new(self._secret, canonical, hashlib.sha256).digest())
        return {
            "X-TK-Auto-Timestamp": str(issued_at),
            "X-TK-Auto-Nonce": request_nonce,
            "X-TK-Auto-Content-SHA256": content_hash,
            "X-TK-Auto-Signature": signature,
        }


class LocalRequestAuthenticator:
    def __init__(
        self,
        session_secret: str | bytes,
        *,
        max_skew_seconds: int = 30,
        replay_ttl_seconds: int = 120,
    ) -> None:
        self._secret = (
            decode_session_secret(session_secret)
            if isinstance(session_secret, str)
            else session_secret
        )
        if len(self._secret) < 32:
            raise ValueError("Local session secret must contain at least 256 bits.")
        self.max_skew_seconds = max(1, int(max_skew_seconds))
        self.replay_ttl_seconds = max(
            self.max_skew_seconds * 2, int(replay_ttl_seconds)
        )
        self._seen_nonces: dict[str, float] = {}
        self._lock = threading.Lock()

    def verify(self, scope: dict, body: bytes) -> None:
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        try:
            timestamp_text = headers[_HEADER_TIMESTAMP].decode("ascii")
            nonce = headers[_HEADER_NONCE].decode("ascii")
            content_hash = headers[_HEADER_CONTENT_HASH].decode("ascii")
            signature = headers[_HEADER_SIGNATURE].decode("ascii")
        except (KeyError, UnicodeDecodeError) as exc:
            raise LocalAuthenticationError(
                "Local request authentication is required."
            ) from exc
        if not timestamp_text.isdigit() or len(timestamp_text) > 12:
            raise LocalAuthenticationError("Local request timestamp is invalid.")
        timestamp = int(timestamp_text)
        now = int(time.time())
        if abs(now - timestamp) > self.max_skew_seconds:
            raise LocalAuthenticationError("Local request has expired.")
        if not _NONCE_RE.fullmatch(nonce):
            raise LocalAuthenticationError("Local request nonce is invalid.")
        actual_content_hash = hashlib.sha256(body).hexdigest()
        if not _HEX_RE.fullmatch(content_hash) or not hmac.compare_digest(
            content_hash, actual_content_hash
        ):
            raise LocalAuthenticationError("Local request body digest is invalid.")
        canonical = _canonical_request(
            timestamp=timestamp,
            nonce=nonce,
            method=(
                "WS" if scope.get("type") == "websocket" else scope.get("method", "")
            ),
            target=_request_target(scope),
            content_hash=content_hash,
        )
        expected = _b64url(hmac.new(self._secret, canonical, hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise LocalAuthenticationError("Local request signature is invalid.")
        monotonic_now = time.monotonic()
        with self._lock:
            expired = [
                key
                for key, deadline in self._seen_nonces.items()
                if deadline <= monotonic_now
            ]
            for key in expired:
                self._seen_nonces.pop(key, None)
            if nonce in self._seen_nonces:
                raise LocalAuthenticationError("Local request nonce was already used.")
            self._seen_nonces[nonce] = monotonic_now + self.replay_ttl_seconds


class SignedLocalAuthMiddleware:
    def __init__(
        self,
        app,
        *,
        security_mode: str,
        session_secret: str,
        max_skew_seconds: int = 30,
        replay_ttl_seconds: int = 120,
        max_body_bytes: int = 32 * 1024 * 1024,
    ) -> None:
        self.app = app
        self.enabled = security_mode.strip().lower() in {"production", "friends"}
        self.max_body_bytes = max(1, int(max_body_bytes))
        self.authenticator = None
        if self.enabled:
            self.authenticator = LocalRequestAuthenticator(
                session_secret,
                max_skew_seconds=max_skew_seconds,
                replay_ttl_seconds=replay_ttl_seconds,
            )

    async def __call__(self, scope, receive, send):
        if not self.enabled or scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        try:
            if scope["type"] == "websocket":
                self.authenticator.verify(scope, b"")
                await self.app(scope, receive, send)
                return
            body = bytearray()
            more_body = True
            while more_body:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                body.extend(chunk)
                if len(body) > self.max_body_bytes:
                    await self._http_error(send, 413, "REQUEST_TOO_LARGE")
                    return
                more_body = bool(message.get("more_body", False))
            frozen_body = bytes(body)
            self.authenticator.verify(scope, frozen_body)
            delivered = False

            async def replay_receive():
                nonlocal delivered
                if delivered:
                    return {"type": "http.request", "body": b"", "more_body": False}
                delivered = True
                return {"type": "http.request", "body": frozen_body, "more_body": False}

            await self.app(scope, replay_receive, send)
        except LocalAuthenticationError:
            if scope["type"] == "websocket":
                await send(
                    {
                        "type": "websocket.close",
                        "code": 4401,
                        "reason": "Authentication required",
                    }
                )
            else:
                await self._http_error(send, 401, "LOCAL_AUTH_REQUIRED")

    @staticmethod
    async def _http_error(send, status: int, code: str) -> None:
        payload = json.dumps(
            {"detail": "Local application authentication failed.", "code": code},
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": payload})
