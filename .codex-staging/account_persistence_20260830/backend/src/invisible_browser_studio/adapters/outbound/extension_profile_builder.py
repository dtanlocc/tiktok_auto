"""Build a minimal Firefox profile containing external WebExtensions.

The patched Firefox used by invisible_playwright discovers profile extensions
before Playwright's runtime preferences are fully applied.  Therefore the
sideload preferences must be present in ``user.js`` before launch.  This module
keeps that Firefox-specific detail out of the TikTok adapter.

Supported sources:

* a Firefox ``.xpi`` file;
* a ``.zip`` file with ``manifest.json`` at its root;
* an unpacked extension directory containing ``manifest.json``;
* a directory containing any number of the sources above.

Unpacked/zip extensions without a Gecko ID receive a deterministic local ID.
They still need to be Firefox-compatible; loading a Chrome-only extension does
not make its APIs compatible with Firefox.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, MutableMapping, Optional


logger = logging.getLogger("invisible_browser_studio.extensions")

_ADDON_ID_RE = re.compile(r"^[A-Za-z0-9@._{}+-]+$")
_SUPPORTED_ARCHIVES = {".xpi", ".zip"}


@dataclass(frozen=True)
class InstalledExtension:
    source: Path
    addon_id: str
    version: str
    extension_uuid: str
    xpi_path: Path


@dataclass(frozen=True)
class _PreparedExtension:
    source: Path
    addon_id: str
    version: str
    entries: Mapping[str, bytes]
    preserve_original_archive: bool


def parse_extension_paths(raw: str) -> list[Path]:
    """Parse a Windows-friendly semicolon-separated extension source list."""
    return [Path(part.strip()).expanduser() for part in (raw or "").split(";") if part.strip()]


def parse_json_object(raw: str, setting_name: str) -> dict[str, Any]:
    if not (raw or "").strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{setting_name} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{setting_name} must be a JSON object")
    return value


def _deep_merge(target: MutableMapping[str, Any], patch: Mapping[str, Any]) -> None:
    for key, value in patch.items():
        current = target.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            _deep_merge(current, value)
        else:
            target[key] = value


def _safe_archive_name(name: str) -> str:
    normalized = PurePosixPath(name.replace("\\", "/"))
    if normalized.is_absolute() or ".." in normalized.parts:
        raise ValueError(f"Unsafe path in extension archive: {name}")
    return normalized.as_posix()


def _read_archive(path: Path) -> dict[str, bytes]:
    entries: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(path, "r") as archive:
            for info in archive.infolist():
                if info.is_dir():
                    continue
                name = _safe_archive_name(info.filename)
                if name:
                    entries[name] = archive.read(info)
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Extension archive is invalid: {path}") from exc
    return entries


def _read_directory(path: Path) -> dict[str, bytes]:
    entries: dict[str, bytes] = {}
    for item in sorted(path.rglob("*")):
        if item.is_symlink() or not item.is_file():
            continue
        name = _safe_archive_name(item.relative_to(path).as_posix())
        entries[name] = item.read_bytes()
    return entries


def _manifest(entries: Mapping[str, bytes], source: Path) -> dict[str, Any]:
    raw = entries.get("manifest.json")
    if raw is None:
        raise ValueError(f"Extension has no manifest.json at archive root: {source}")
    try:
        manifest = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Extension manifest is invalid: {source}") from exc
    if not isinstance(manifest, dict):
        raise ValueError(f"Extension manifest must be a JSON object: {source}")
    return manifest


def _manifest_addon_id(manifest: Mapping[str, Any]) -> Optional[str]:
    for top_key in ("browser_specific_settings", "applications"):
        top = manifest.get(top_key)
        if isinstance(top, Mapping):
            gecko = top.get("gecko")
            if isinstance(gecko, Mapping) and gecko.get("id"):
                return str(gecko["id"])
    return None


def _stable_local_addon_id(manifest: Mapping[str, Any], source: Path) -> str:
    identity = f"{manifest.get('name', source.stem)}|{source.resolve()}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"local-{digest}@invisible-browser-studio.local"


def _set_manifest_addon_id(manifest: MutableMapping[str, Any], addon_id: str) -> None:
    settings = manifest.setdefault("browser_specific_settings", {})
    if not isinstance(settings, dict):
        settings = {}
        manifest["browser_specific_settings"] = settings
    gecko = settings.setdefault("gecko", {})
    if not isinstance(gecko, dict):
        gecko = {}
        settings["gecko"] = gecko
    gecko["id"] = addon_id


def _natural_version_key(version: str) -> tuple[tuple[int, Any], ...]:
    parts: list[tuple[int, Any]] = []
    for part in re.split(r"(\d+)", version or "0"):
        if not part:
            continue
        parts.append((1, int(part)) if part.isdigit() else (0, part.lower()))
    return tuple(parts)


def _write_xpi(entries: Mapping[str, bytes], destination: Path) -> None:
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(entries):
            # Stable bytes avoid rewriting the engine-level distribution XPI
            # for every concurrent browser session when its content is equal.
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, entries[name])


def firefox_prefs_for_extensions(
    installed: Iterable[InstalledExtension],
) -> dict[str, Any]:
    """Prefs that must reach Firefox's pre-start ``user.js`` writer."""

    items = list(installed)
    uuid_map = {item.addon_id: item.extension_uuid for item in items}
    return {
        "extensions.autoDisableScopes": 0,
        "extensions.enabledScopes": 15,
        "extensions.startupScanScopes": 15,
        "xpinstall.signatures.required": False,
        "xpinstall.whitelist.required": False,
        # Keep storage.local on Firefox's supported JSONFile backend so a
        # fresh isolated profile can be seeded before the extension starts.
        "extensions.webextensions.ExtensionStorageIDB.enabled": False,
        "extensions.webextensions.uuids": json.dumps(
            uuid_map, ensure_ascii=False, separators=(",", ":")
        ),
    }


class ExtensionProfileBuilder:
    """Create a fresh profile and sideload compatible Firefox extensions."""

    def __init__(
        self,
        source_roots: Iterable[Path | str],
        *,
        json_resource_overrides: Optional[
            Mapping[str, Mapping[str, Mapping[str, Any]]]
        ] = None,
        uuid_overrides: Optional[Mapping[str, str]] = None,
        storage_local_seed_resources: Optional[Mapping[str, str]] = None,
        storage_local_overrides: Optional[Mapping[str, Mapping[str, Any]]] = None,
        fail_if_empty: bool = True,
    ) -> None:
        self.source_roots = [Path(path).expanduser() for path in source_roots]
        self.json_resource_overrides = dict(json_resource_overrides or {})
        self.uuid_overrides = dict(uuid_overrides or {})
        self.storage_local_seed_resources = dict(storage_local_seed_resources or {})
        self.storage_local_overrides = dict(storage_local_overrides or {})
        self.fail_if_empty = fail_if_empty

    def discover_sources(self) -> list[Path]:
        discovered: list[Path] = []
        for root in self.source_roots:
            root = root.resolve()
            if not root.exists():
                raise FileNotFoundError(f"Extension source does not exist: {root}")
            if root.is_file():
                if root.suffix.lower() not in _SUPPORTED_ARCHIVES:
                    raise ValueError(f"Unsupported extension file: {root}")
                discovered.append(root)
                continue
            if (root / "manifest.json").is_file():
                discovered.append(root)
                continue
            for child in sorted(root.iterdir()):
                if child.is_file() and child.suffix.lower() in _SUPPORTED_ARCHIVES:
                    discovered.append(child)
                elif child.is_dir() and (child / "manifest.json").is_file():
                    discovered.append(child)
        return discovered

    def _prepare(self, source: Path) -> _PreparedExtension:
        is_archive = source.is_file()
        entries = _read_archive(source) if is_archive else _read_directory(source)
        manifest = _manifest(entries, source)
        addon_id = _manifest_addon_id(manifest) or _stable_local_addon_id(manifest, source)
        if not _ADDON_ID_RE.fullmatch(addon_id):
            raise ValueError(f"Invalid Firefox Gecko extension ID {addon_id!r}: {source}")

        original_id = _manifest_addon_id(manifest)
        if original_id is None:
            _set_manifest_addon_id(manifest, addon_id)
            entries["manifest.json"] = json.dumps(
                manifest, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")

        overrides = self.json_resource_overrides.get(addon_id, {})
        for resource_name, resource_patch in overrides.items():
            safe_name = _safe_archive_name(resource_name)
            if safe_name not in entries:
                raise ValueError(
                    f"Configured JSON resource {safe_name!r} is missing in extension {addon_id}"
                )
            try:
                resource = json.loads(entries[safe_name].decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError(
                    f"Configured resource {safe_name!r} is not valid JSON in {addon_id}"
                ) from exc
            if not isinstance(resource, dict) or not isinstance(resource_patch, Mapping):
                raise ValueError(
                    f"JSON override for {addon_id}/{safe_name} must merge two objects"
                )
            _deep_merge(resource, resource_patch)
            entries[safe_name] = json.dumps(
                resource, ensure_ascii=False, indent=2
            ).encode("utf-8")

        # Any byte-level change invalidates Mozilla's original archive
        # signature.  Keeping a stale META-INF makes Firefox classify the XPI
        # as corrupt instead of as an unsigned extension (which the patched
        # invisible_playwright engine is explicitly configured to accept).
        if original_id is None or overrides:
            entries = {
                name: value
                for name, value in entries.items()
                if not name.casefold().startswith("meta-inf/")
            }

        return _PreparedExtension(
            source=source,
            addon_id=addon_id,
            version=str(manifest.get("version", "0")),
            entries=entries,
            preserve_original_archive=(
                is_archive
                and source.suffix.lower() == ".xpi"
                and original_id is not None
                and not overrides
            ),
        )

    def _deduplicate(self, prepared: Iterable[_PreparedExtension]) -> list[_PreparedExtension]:
        selected: dict[str, _PreparedExtension] = {}
        for item in prepared:
            current = selected.get(item.addon_id)
            if current is None:
                selected[item.addon_id] = item
                continue
            item_key = (_natural_version_key(item.version), item.preserve_original_archive)
            current_key = (
                _natural_version_key(current.version),
                current.preserve_original_archive,
            )
            if item_key > current_key:
                logger.info(
                    "Using newer/preferred extension %s %s from %s instead of %s from %s",
                    item.addon_id,
                    item.version,
                    item.source,
                    current.version,
                    current.source,
                )
                selected[item.addon_id] = item
            else:
                logger.info(
                    "Ignoring duplicate extension %s %s from %s",
                    item.addon_id,
                    item.version,
                    item.source,
                )
        return list(selected.values())

    def prepare_profile(self, profile_dir: Path | str) -> list[InstalledExtension]:
        profile = Path(profile_dir)
        extension_dir = profile / "extensions"
        extension_dir.mkdir(parents=True, exist_ok=True)

        sources = self.discover_sources()
        if not sources and self.fail_if_empty:
            roots = "; ".join(str(path) for path in self.source_roots) or "<none>"
            raise RuntimeError(f"No Firefox extensions found in: {roots}")

        prepared = self._deduplicate(self._prepare(source) for source in sources)
        installed: list[InstalledExtension] = []

        for item in prepared:
            extension_uuid = self.uuid_overrides.get(item.addon_id) or str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"invisible-browser-studio:{item.addon_id}",
                )
            )
            # Validate caller-provided UUID early; Firefox otherwise replaces it
            # silently and extension-specific URLs become unpredictable.
            extension_uuid = str(uuid.UUID(extension_uuid))
            destination = extension_dir / f"{item.addon_id}.xpi"
            if item.preserve_original_archive:
                shutil.copy2(item.source, destination)
            else:
                _write_xpi(item.entries, destination)
            installed.append(
                InstalledExtension(
                    source=item.source,
                    addon_id=item.addon_id,
                    version=item.version,
                    extension_uuid=extension_uuid,
                    xpi_path=destination,
                )
            )

            seed_resource = self.storage_local_seed_resources.get(item.addon_id)
            storage_patch = self.storage_local_overrides.get(item.addon_id, {})
            if seed_resource or storage_patch:
                storage_data: dict[str, Any] = {}
                if seed_resource:
                    safe_resource = _safe_archive_name(seed_resource)
                    raw_seed = item.entries.get(safe_resource)
                    if raw_seed is None:
                        raise ValueError(
                            f"Storage seed resource {safe_resource!r} is missing in {item.addon_id}"
                        )
                    try:
                        parsed_seed = json.loads(raw_seed.decode("utf-8-sig"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ValueError(
                            f"Storage seed resource {safe_resource!r} is invalid in {item.addon_id}"
                        ) from exc
                    if not isinstance(parsed_seed, dict):
                        raise ValueError(
                            f"Storage seed resource {safe_resource!r} must be an object"
                        )
                    storage_data.update(parsed_seed)
                if not isinstance(storage_patch, Mapping):
                    raise ValueError(
                        f"Storage override for {item.addon_id} must be an object"
                    )
                _deep_merge(storage_data, storage_patch)
                storage_dir = profile / "browser-extension-data" / item.addon_id
                storage_dir.mkdir(parents=True, exist_ok=True)
                (storage_dir / "storage.js").write_text(
                    json.dumps(storage_data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

        user_prefs = [
            f"user_pref({json.dumps(name)}, {json.dumps(value)});"
            for name, value in firefox_prefs_for_extensions(installed).items()
        ]
        (profile / "user.js").write_text("\n".join(user_prefs) + "\n", encoding="utf-8")
        return installed


__all__ = [
    "ExtensionProfileBuilder",
    "InstalledExtension",
    "firefox_prefs_for_extensions",
    "parse_extension_paths",
    "parse_json_object",
]
