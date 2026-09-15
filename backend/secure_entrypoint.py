"""Binary-only production entry point.

The native launcher writes one bounded JSON line to stdin. Secrets never appear
in process arguments. Environment variables are removed immediately after the
application settings singleton has consumed them.
"""

from __future__ import annotations

import json
import os
import sys

_BOOTSTRAP_LIMIT = 64 * 1024
_BOOTSTRAP_ENV = {
    "device_id": "LICENSE_DEVICE_ID",
    "lease_path": "LICENSE_LEASE_PATH",
    "license_public_keys_json": "LICENSE_PUBLIC_KEYS_JSON",
    "local_session_secret": "LOCAL_SESSION_SECRET",
    "app_version": "APP_VERSION",
    "database_url": "DATABASE_URL",
    "omocaptcha_key": "OMOCAPTCHA_KEY",
}


def _read_bootstrap() -> tuple[dict[str, str], int]:
    raw = sys.stdin.buffer.readline(_BOOTSTRAP_LIMIT + 1)
    if not raw or len(raw) > _BOOTSTRAP_LIMIT or not raw.endswith(b"\n"):
        raise RuntimeError("Secure launcher bootstrap is missing or too large.")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Secure launcher bootstrap is invalid.") from exc
    if not isinstance(value, dict) or set(value) != {*_BOOTSTRAP_ENV, "port"}:
        raise RuntimeError("Secure launcher bootstrap fields are invalid.")
    fields: dict[str, str] = {}
    for name in _BOOTSTRAP_ENV:
        item = value.get(name)
        if not isinstance(item, str) or not item or len(item) > 32_768:
            raise RuntimeError("Secure launcher bootstrap value is invalid.")
        fields[name] = item
    port = value.get("port")
    if not isinstance(port, int) or not 1024 <= port <= 65535:
        raise RuntimeError("Secure launcher port is invalid.")
    return fields, port


def main() -> None:
    fields, port = _read_bootstrap()
    os.environ["SECURITY_MODE"] = "production"
    os.environ["DEBUG"] = "false"
    for field, env_name in _BOOTSTRAP_ENV.items():
        os.environ[env_name] = fields[field]
    try:
        from app.core.config import settings
    finally:
        os.environ.pop("LOCAL_SESSION_SECRET", None)
        os.environ.pop("LICENSE_PUBLIC_KEYS_JSON", None)
        os.environ.pop("OMOCAPTCHA_KEY", None)
    if settings.SECURITY_MODE != "production" or settings.DEBUG:
        raise RuntimeError(
            "Production bootstrap did not establish fail-closed settings."
        )
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
