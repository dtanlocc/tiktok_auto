"""The browser extensions every automation browser runs with, set up one way.

The account sessions (login, upload) and the public analytics browser used to
diverge here. Only the account sessions built a profile through
``ExtensionProfileBuilder``, which is what writes OmoCaptcha's API key into its
``browser.storage.local``. The analytics browser launched bare - yet still got
OmoCaptcha, because the XPI lives in the engine's shared distribution folder -
so it ran the solver with no key: every captcha on that path showed
"[OMOcaptcha] Invalid or missing API KEY" and went unsolved, while the key sat
configured in ``backend/.env`` the whole time. Both paths now build from here.
"""

from __future__ import annotations

import json
import zipfile
from typing import Iterable

from app.core.config import settings
from app.core.extension_settings import NORDVPN_ADDON_ID, is_nordvpn_extension_enabled
from app.infrastructure.automation.extension_profile_builder import (
    ExtensionProfileBuilder,
    InstalledExtension,
    parse_extension_paths,
    parse_json_object,
)

OMOCAPTCHA_ADDON_ID = "omocaptcha@gmail.com"


def configured_extension_builder() -> tuple[ExtensionProfileBuilder, set[str]]:
    """The builder for a fresh profile, and the add-ons to exclude from the engine."""
    source_paths = parse_extension_paths(getattr(settings, "BROWSER_EXTENSION_PATHS", ""))
    if not source_paths:
        source_paths = [getattr(settings, "BROWSER_EXTENSIONS_DIR")]

    json_overrides = parse_json_object(
        getattr(settings, "BROWSER_EXTENSION_JSON_OVERRIDES", "{}"),
        "BROWSER_EXTENSION_JSON_OVERRIDES",
    )
    uuid_overrides = parse_json_object(
        getattr(settings, "BROWSER_EXTENSION_UUIDS_JSON", "{}"),
        "BROWSER_EXTENSION_UUIDS_JSON",
    )

    # OmoCaptcha must keep its Mozilla signature intact.  Its API key
    # is written to browser.storage.local after Firefox activates the
    # signed XPI; rewriting configs.json would invalidate the signature.
    omo_uuid = getattr(settings, "OMOCAPTCHA_EXTENSION_UUID", "")
    if omo_uuid:
        uuid_overrides.setdefault(OMOCAPTCHA_ADDON_ID, omo_uuid)

    excluded_addon_ids: set[str] = set()
    if not is_nordvpn_extension_enabled():
        excluded_addon_ids.add(NORDVPN_ADDON_ID)

    builder = ExtensionProfileBuilder(
        source_paths,
        json_resource_overrides=json_overrides,
        uuid_overrides=uuid_overrides,
        storage_local_seed_resources={OMOCAPTCHA_ADDON_ID: "configs.json"},
        storage_local_overrides={
            OMOCAPTCHA_ADDON_ID: {
                "api_key": getattr(settings, "OMOCAPTCHA_KEY", ""),
                "initialized": True,
            },
        },
        storage_local_seed_directory=getattr(settings, "BROWSER_EXTENSION_STORAGE_DIR", ""),
        excluded_addon_ids=excluded_addon_ids,
        fail_if_empty=getattr(settings, "BROWSER_EXTENSIONS_REQUIRED", True),
    )
    return builder, excluded_addon_ids


def validate_configured_extensions(installed_extensions: Iterable[InstalledExtension]) -> None:
    """Validate sensitive bundled config without changing signed XPIs."""

    expected_key = getattr(settings, "OMOCAPTCHA_KEY", "")
    for item in installed_extensions:
        if item.addon_id != OMOCAPTCHA_ADDON_ID:
            continue
        try:
            with zipfile.ZipFile(item.xpi_path) as archive:
                json.loads(archive.read("configs.json").decode("utf-8-sig"))
                has_signature = any(
                    name.casefold().startswith("meta-inf/")
                    for name in archive.namelist()
                )
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
            raise RuntimeError("OmoCaptcha 1.7.7 package/config is invalid") from exc
        if not has_signature:
            raise RuntimeError("OmoCaptcha XPI signature was not preserved")
        storage_path = (
            item.xpi_path.parents[1]
            / "browser-extension-data"
            / item.addon_id
            / "storage.js"
        )
        try:
            storage = json.loads(storage_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("OmoCaptcha storage seed is invalid") from exc
        if (
            expected_key
            and storage.get("api_key") != expected_key
            or storage.get("initialized") is not True
        ):
            raise RuntimeError(
                "OmoCaptcha storage does not contain the configured API key"
            )
