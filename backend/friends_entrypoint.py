"""Binary-only entry point for the unlicensed Friends distribution.

The native desktop sends one bounded bootstrap record through stdin. This build
does not contain commercial licensing, but it retains per-launch HMAC
authentication and never exposes the session key to the webview.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_BOOTSTRAP_LIMIT = 64 * 1024
_BOOTSTRAP_ENV = {
    "local_session_secret": "LOCAL_SESSION_SECRET",
    "app_version": "APP_VERSION",
    "database_url": "DATABASE_URL",
    "omocaptcha_key": "OMOCAPTCHA_KEY",
}
_NATIVE_UPLOAD_SMOKE_ENV = (
    "TKAUTO_NATIVE_UPLOAD_SMOKE_URL",
    "TKAUTO_NATIVE_UPLOAD_SMOKE_FILE",
    "TKAUTO_NATIVE_UPLOAD_SMOKE_RESULT",
)


def _read_bootstrap() -> tuple[dict[str, str], int]:
    raw = sys.stdin.buffer.readline(_BOOTSTRAP_LIMIT + 1)
    if not raw or len(raw) > _BOOTSTRAP_LIMIT or not raw.endswith(b"\n"):
        raise RuntimeError("Friends launcher bootstrap is missing or too large.")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("Friends launcher bootstrap is invalid.") from exc
    if not isinstance(value, dict) or set(value) != {*_BOOTSTRAP_ENV, "port"}:
        raise RuntimeError("Friends launcher bootstrap fields are invalid.")
    fields: dict[str, str] = {}
    for name in _BOOTSTRAP_ENV:
        item = value.get(name)
        if not isinstance(item, str) or not item or len(item) > 32_768:
            raise RuntimeError("Friends launcher bootstrap value is invalid.")
        fields[name] = item
    port = value.get("port")
    if not isinstance(port, int) or not 1024 <= port <= 65535:
        raise RuntimeError("Friends launcher port is invalid.")
    return fields, port


async def _run_native_upload_smoke(url: str, media_path: str) -> None:
    """Exercise the real Windows chooser from inside the compiled runtime."""

    from app.infrastructure.automation.playwright_adapter import (
        InvisiblePlaywrightAdapter,
    )
    from invisible_playwright import set_input_files_native

    browser = InvisiblePlaywrightAdapter()
    try:
        await browser.initialize(seed=731_905, force_visible=False)
        await browser.navigate_to(url)
        target = browser._page.locator('input[type="file"]').first
        trigger = browser._page.locator("#choose").first
        await target.wait_for(state="attached", timeout=15_000)
        await trigger.wait_for(state="visible", timeout=15_000)
        owner_process_ids = await asyncio.to_thread(
            browser._native_upload_process_ids
        )
        owner_session_token = getattr(
            browser._invisible_pw, "_session_token", None
        )
        await set_input_files_native(
            target,
            [media_path],
            trigger=trigger,
            owner_process_ids=owner_process_ids or None,
            owner_session_token=owner_session_token,
            timeout_ms=15_000,
        )
        actual = await target.evaluate(
            "element => element.files.length ? element.files[0].name : ''",
            timeout=2_000,
        )
        if actual != Path(media_path).name:
            raise RuntimeError(
                f"Native upload smoke attached an unexpected file: {actual!r}."
            )
    finally:
        await browser.close()


def main() -> None:
    smoke_values = {
        name: os.environ.pop(name, "") for name in _NATIVE_UPLOAD_SMOKE_ENV
    }
    fields, port = _read_bootstrap()
    os.environ["SECURITY_MODE"] = "friends"
    os.environ["DEBUG"] = "false"
    for field, env_name in _BOOTSTRAP_ENV.items():
        os.environ[env_name] = fields[field]
    try:
        from app.core.config import settings
    finally:
        os.environ.pop("LOCAL_SESSION_SECRET", None)
        os.environ.pop("OMOCAPTCHA_KEY", None)
    if settings.SECURITY_MODE != "friends" or settings.DEBUG:
        raise RuntimeError("Friends bootstrap did not establish hardened settings.")
    if any(smoke_values.values()):
        if not all(smoke_values.values()):
            raise RuntimeError("Native upload smoke configuration is incomplete.")
        result_path = Path(smoke_values["TKAUTO_NATIVE_UPLOAD_SMOKE_RESULT"])
        try:
            asyncio.run(
                _run_native_upload_smoke(
                    smoke_values["TKAUTO_NATIVE_UPLOAD_SMOKE_URL"],
                    smoke_values["TKAUTO_NATIVE_UPLOAD_SMOKE_FILE"],
                )
            )
        except Exception as exc:
            result_path.write_text(
                json.dumps(
                    {"ok": False, "error": f"{type(exc).__name__}: {exc}"},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            raise
        result_path.write_text(
            json.dumps({"ok": True}, ensure_ascii=False), encoding="utf-8"
        )
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
