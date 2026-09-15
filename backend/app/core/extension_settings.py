from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from pathlib import Path
from typing import Any

from app.core.config import settings


logger = logging.getLogger("ExtensionSettings")

NORDVPN_ADDON_ID = "nordvpnproxy@nordvpn.com"
_NORDVPN_ENABLED_KEY = "nordvpn_extension_enabled"
_SETTINGS_LOCK = threading.Lock()


def _settings_path() -> Path:
    return Path(settings.BROWSER_EXTENSION_SETTINGS_PATH).expanduser()


def _read_document(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("Ignoring invalid browser-extension settings at %s: %s", path, exc)
        return {}
    return value if isinstance(value, dict) else {}


def is_nordvpn_extension_enabled() -> bool:
    with _SETTINGS_LOCK:
        value = _read_document(_settings_path()).get(_NORDVPN_ENABLED_KEY)
    if isinstance(value, bool):
        return value
    return bool(getattr(settings, "NORDVPN_EXTENSION_ENABLED", False))


def set_nordvpn_extension_enabled(enabled: bool) -> bool:
    value = bool(enabled)
    path = _settings_path()
    with _SETTINGS_LOCK:
        document = _read_document(path)
        document[_NORDVPN_ENABLED_KEY] = value
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(document, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    settings.NORDVPN_EXTENSION_ENABLED = value
    return value


def nordvpn_package_available() -> bool:
    configured = str(getattr(settings, "BROWSER_EXTENSION_PATHS", "") or "")
    roots = [
        Path(value.strip()).expanduser()
        for value in configured.split(";")
        if value.strip()
    ] or [Path(settings.BROWSER_EXTENSIONS_DIR).expanduser()]
    expected_name = f"{NORDVPN_ADDON_ID}.xpi"
    return any(
        (root.is_file() and root.name == expected_name)
        or (root.is_dir() and (root / expected_name).is_file())
        for root in roots
    )


def nordvpn_authenticated_state_available() -> bool:
    storage_path = (
        Path(settings.BROWSER_EXTENSION_STORAGE_DIR).expanduser()
        / NORDVPN_ADDON_ID
        / "storage.js"
    )
    try:
        storage = json.loads(storage_path.read_text(encoding="utf-8-sig"))
        persisted = storage.get("persist:@nordvpn:extension-firefox")
        persisted = json.loads(persisted) if isinstance(persisted, str) else persisted
        auth = persisted.get("extension.auth") if isinstance(persisted, dict) else None
        auth = json.loads(auth) if isinstance(auth, str) else auth
        return bool(auth.get("authorizedAt")) if isinstance(auth, dict) else False
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return False


__all__ = [
    "NORDVPN_ADDON_ID",
    "is_nordvpn_extension_enabled",
    "nordvpn_authenticated_state_available",
    "nordvpn_package_available",
    "set_nordvpn_extension_enabled",
]
