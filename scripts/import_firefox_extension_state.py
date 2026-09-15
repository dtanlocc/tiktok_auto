from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any


ADDON_ID_RE = re.compile(r"^[A-Za-z0-9@._{}+-]+$")
UUID_PREF_RE = re.compile(
    r'^user_pref\("extensions\.webextensions.uuids",\s*(.+)\);\s*$'
)


def _manifest_addon_id(manifest: dict[str, Any]) -> str:
    for top_key in ("browser_specific_settings", "applications"):
        gecko = (manifest.get(top_key) or {}).get("gecko") or {}
        if gecko.get("id"):
            return str(gecko["id"])
    raise ValueError("The extension manifest has no Firefox Gecko ID")


def _profile_extension_uuid(profile: Path, addon_id: str) -> str:
    prefs_path = profile / "prefs.js"
    for line in prefs_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = UUID_PREF_RE.match(line)
        if not match:
            continue
        encoded_mapping = json.loads(match.group(1))
        mapping = json.loads(encoded_mapping)
        return str(uuid.UUID(str(mapping[addon_id])))
    raise ValueError(f"No internal Firefox UUID found for {addon_id}")


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_bytes(data: bytes, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(data: dict[str, Any], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def import_extension_state(
    profile: Path,
    addon_id: str,
    extension_directory: Path,
    storage_directory: Path,
) -> tuple[Path, Path, str, int]:
    profile = profile.resolve()
    if not profile.is_dir():
        raise FileNotFoundError(f"Firefox profile does not exist: {profile}")
    if not ADDON_ID_RE.fullmatch(addon_id):
        raise ValueError(f"Invalid Firefox add-on ID: {addon_id!r}")

    source_xpi = profile / "extensions" / f"{addon_id}.xpi"
    source_storage = profile / "browser-extension-data" / addon_id / "storage.js"
    if not source_xpi.is_file():
        raise FileNotFoundError(f"Extension XPI does not exist: {source_xpi}")
    if not source_storage.is_file():
        raise FileNotFoundError(f"Extension storage does not exist: {source_storage}")

    with zipfile.ZipFile(source_xpi, "r") as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8-sig"))
        if _manifest_addon_id(manifest) != addon_id:
            raise ValueError("The XPI manifest ID does not match the requested add-on ID")
        if not any(name.casefold().startswith("meta-inf/") for name in archive.namelist()):
            raise ValueError("The source XPI does not contain a Mozilla signature")

    storage_raw = source_storage.read_bytes()
    storage_data = json.loads(storage_raw.decode("utf-8-sig"))
    if not isinstance(storage_data, dict):
        raise ValueError("Firefox extension storage.js must contain a JSON object")
    extension_uuid = _profile_extension_uuid(profile, addon_id)

    destination_xpi = extension_directory.resolve() / f"{addon_id}.xpi"
    destination_storage_dir = storage_directory.resolve() / addon_id
    destination_storage = destination_storage_dir / "storage.js"
    _atomic_copy(source_xpi, destination_xpi)
    _atomic_bytes(storage_raw, destination_storage)
    _atomic_json(
        {
            "addon_id": addon_id,
            "extension_uuid": extension_uuid,
            "version": str(manifest.get("version", "0")),
            "xpi_sha256": hashlib.sha256(source_xpi.read_bytes()).hexdigest(),
        },
        destination_storage_dir / "metadata.json",
    )
    return destination_xpi, destination_storage, str(manifest.get("version", "0")), len(
        storage_data
    )


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description=(
            "Import a signed Firefox XPI and its private storage.local state "
            "without printing credential values."
        )
    )
    parser.add_argument("profile", type=Path)
    parser.add_argument("addon_id")
    parser.add_argument(
        "--extension-directory", type=Path, default=repo_root / "backend" / "extensions"
    )
    parser.add_argument(
        "--storage-directory",
        type=Path,
        default=repo_root / ".runtime" / "extension-storage",
    )
    args = parser.parse_args()
    xpi, storage, version, key_count = import_extension_state(
        args.profile,
        args.addon_id,
        args.extension_directory,
        args.storage_directory,
    )
    print(f"Imported {args.addon_id} version {version}")
    print(f"Signed XPI: {xpi}")
    print(f"Private storage snapshot: {storage} ({key_count} top-level keys; values hidden)")


if __name__ == "__main__":
    main()
