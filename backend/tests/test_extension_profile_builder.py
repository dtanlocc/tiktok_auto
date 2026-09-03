import json
import zipfile
from pathlib import Path

from app.infrastructure.automation.extension_profile_builder import (
    ExtensionProfileBuilder,
    firefox_prefs_for_extensions,
    parse_extension_paths,
    parse_json_object,
)


def _write_unpacked(path: Path, *, addon_id: str | None, version: str) -> None:
    path.mkdir(parents=True)
    manifest = {
        "manifest_version": 3,
        "name": "Test Solver",
        "version": version,
        "background": {"scripts": ["background.js"]},
    }
    if addon_id:
        manifest["browser_specific_settings"] = {"gecko": {"id": addon_id}}
    (path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (path / "background.js").write_text("void 0;", encoding="utf-8")
    (path / "config.json").write_text(
        json.dumps({"api": {"key": "old"}, "enabled": False}), encoding="utf-8"
    )


def _write_xpi(path: Path, *, addon_id: str, version: str) -> bytes:
    manifest = {
        "manifest_version": 3,
        "name": "Packaged Solver",
        "version": version,
        "browser_specific_settings": {"gecko": {"id": addon_id}},
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("background.js", "void 0;")
    return path.read_bytes()


def test_builds_unpacked_extension_and_assigns_stable_identity(tmp_path: Path) -> None:
    source = tmp_path / "solver"
    _write_unpacked(source, addon_id=None, version="1.2.3")

    first_profile = tmp_path / "profile-one"
    second_profile = tmp_path / "profile-two"
    first = ExtensionProfileBuilder([source]).prepare_profile(first_profile)
    second = ExtensionProfileBuilder([source]).prepare_profile(second_profile)

    assert len(first) == 1
    assert first[0].addon_id.startswith("local-")
    assert first[0].addon_id == second[0].addon_id
    assert first[0].extension_uuid == second[0].extension_uuid
    assert first[0].xpi_path.exists()

    with zipfile.ZipFile(first[0].xpi_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["browser_specific_settings"]["gecko"]["id"] == first[0].addon_id

    user_js = (first_profile / "user.js").read_text(encoding="utf-8")
    assert 'extensions.autoDisableScopes", 0' in user_js
    assert first[0].addon_id in user_js
    assert first[0].extension_uuid in user_js


def test_json_override_is_generic_and_recursive(tmp_path: Path) -> None:
    addon_id = "solver@example.test"
    source = tmp_path / "solver"
    _write_unpacked(source, addon_id=addon_id, version="2.0")
    signature_dir = source / "META-INF"
    signature_dir.mkdir()
    (signature_dir / "manifest.mf").write_text("stale-signature", encoding="utf-8")
    profile = tmp_path / "profile"

    installed = ExtensionProfileBuilder(
        [source],
        json_resource_overrides={
            addon_id: {"config.json": {"api": {"key": "new"}, "enabled": True}}
        },
    ).prepare_profile(profile)

    with zipfile.ZipFile(installed[0].xpi_path) as archive:
        config = json.loads(archive.read("config.json"))
        assert not any(name.casefold().startswith("meta-inf/") for name in archive.namelist())
    assert config == {"api": {"key": "new"}, "enabled": True}


def test_repacked_extension_is_deterministic_and_prefs_include_uuid(tmp_path: Path) -> None:
    addon_id = "solver@example.test"
    source = tmp_path / "solver"
    _write_unpacked(source, addon_id=addon_id, version="2.0")

    first = ExtensionProfileBuilder(
        [source], json_resource_overrides={addon_id: {"config.json": {"enabled": True}}}
    ).prepare_profile(tmp_path / "profile-one")
    second = ExtensionProfileBuilder(
        [source], json_resource_overrides={addon_id: {"config.json": {"enabled": True}}}
    ).prepare_profile(tmp_path / "profile-two")

    assert first[0].xpi_path.read_bytes() == second[0].xpi_path.read_bytes()
    prefs = firefox_prefs_for_extensions(first)
    uuid_map = json.loads(prefs["extensions.webextensions.uuids"])
    assert uuid_map == {addon_id: first[0].extension_uuid}


def test_preserves_unmodified_xpi_bytes_and_uses_explicit_uuid(tmp_path: Path) -> None:
    addon_id = "signed@example.test"
    source = tmp_path / "signed.xpi"
    original = _write_xpi(source, addon_id=addon_id, version="4.5.6")
    profile = tmp_path / "profile"
    expected_uuid = "c44cb1e0-8f66-4e74-9756-e06abc3e6284"

    installed = ExtensionProfileBuilder(
        [source], uuid_overrides={addon_id: expected_uuid}
    ).prepare_profile(profile)

    assert installed[0].xpi_path.read_bytes() == original
    assert installed[0].extension_uuid == expected_uuid


def test_seeds_storage_local_without_rewriting_signed_xpi(tmp_path: Path) -> None:
    addon_id = "signed@example.test"
    source = tmp_path / "signed.xpi"
    original = _write_xpi(source, addon_id=addon_id, version="4.5.6")
    with zipfile.ZipFile(source, "a", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("config.json", json.dumps({"api_key": "bundled", "on": True}))
    original = source.read_bytes()
    profile = tmp_path / "profile"

    installed = ExtensionProfileBuilder(
        [source],
        storage_local_seed_resources={addon_id: "config.json"},
        storage_local_overrides={
            addon_id: {"api_key": "configured", "initialized": True}
        },
    ).prepare_profile(profile)

    assert installed[0].xpi_path.read_bytes() == original
    storage = json.loads(
        (profile / "browser-extension-data" / addon_id / "storage.js").read_text(
            encoding="utf-8"
        )
    )
    assert storage == {"api_key": "configured", "on": True, "initialized": True}
    user_js = (profile / "user.js").read_text(encoding="utf-8")
    assert 'extensions.webextensions.ExtensionStorageIDB.enabled", false' in user_js


def test_container_discovers_multiple_extensions_and_keeps_newest_duplicate(
    tmp_path: Path,
) -> None:
    container = tmp_path / "extensions"
    container.mkdir()
    _write_unpacked(container / "solver-old", addon_id="same@example.test", version="1.9")
    _write_unpacked(container / "solver-new", addon_id="same@example.test", version="1.10")
    _write_xpi(container / "other.xpi", addon_id="other@example.test", version="1.0")

    installed = ExtensionProfileBuilder([container]).prepare_profile(tmp_path / "profile")
    by_id = {item.addon_id: item for item in installed}

    assert set(by_id) == {"same@example.test", "other@example.test"}
    assert by_id["same@example.test"].version == "1.10"


def test_setting_parsers() -> None:
    assert parse_extension_paths(r"C:\one.xpi; D:\two ;") == [
        Path(r"C:\one.xpi"),
        Path(r"D:\two"),
    ]
    assert parse_json_object('{"a": 1}', "TEST") == {"a": 1}
