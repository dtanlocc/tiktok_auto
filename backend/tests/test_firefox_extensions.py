import json
import zipfile
from pathlib import Path

import pytest

from app.infrastructure.automation.firefox_extensions import (
    FirefoxExtensionInstallError,
    firefox_extension_id,
    install_firefox_extensions,
)
from app.infrastructure.automation.firefox_extensions import sync_engine_extensions


def _xpi(path: Path, addon_id: str, marker: str) -> None:
    manifest = {
        "manifest_version": 3,
        "name": "Test extension",
        "version": "1.0",
        "browser_specific_settings": {"gecko": {"id": addon_id}},
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("marker.txt", marker)


def test_installs_and_atomically_replaces_distribution_extension(tmp_path: Path) -> None:
    executable = tmp_path / "engine" / "firefox.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"binary")
    source = tmp_path / "solver.xpi"
    _xpi(source, "solver@example.test", "first")

    installed = install_firefox_extensions(executable, [source])
    destination = executable.parent / "distribution" / "extensions" / "solver@example.test.xpi"

    assert installed == (destination,)
    assert destination.read_bytes() == source.read_bytes()
    _xpi(source, "solver@example.test", "second")
    install_firefox_extensions(executable, [source])
    with zipfile.ZipFile(destination) as archive:
        assert archive.read("marker.txt") == b"second"
    assert not list(destination.parent.glob("*.tmp"))


def test_reads_id_and_rejects_invalid_archive(tmp_path: Path) -> None:
    source = tmp_path / "solver.xpi"
    _xpi(source, "solver@example.test", "ok")
    assert firefox_extension_id(source) == "solver@example.test"

    broken = tmp_path / "broken.xpi"
    broken.write_bytes(b"not a zip")
    with pytest.raises(FirefoxExtensionInstallError):
        firefox_extension_id(broken)


def test_explicit_removal_clears_an_extension_left_by_an_earlier_launch(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "engine" / "firefox.exe"
    executable.parent.mkdir()
    executable.write_bytes(b"binary")
    nord = tmp_path / "nord.xpi"
    omo = tmp_path / "omo.xpi"
    _xpi(nord, "nordvpnproxy@nordvpn.com", "nord")
    _xpi(omo, "omocaptcha@gmail.com", "omo")

    install_firefox_extensions(executable, [nord, omo])
    destination_dir = executable.parent / "distribution" / "extensions"
    assert (destination_dir / "nordvpnproxy@nordvpn.com.xpi").is_file()

    installed = install_firefox_extensions(
        executable,
        [omo],
        remove_addon_ids=["nordvpnproxy@nordvpn.com"],
    )

    assert installed == (destination_dir / "omocaptcha@gmail.com.xpi",)
    assert not (destination_dir / "nordvpnproxy@nordvpn.com.xpi").exists()
    assert (destination_dir / "omocaptcha@gmail.com.xpi").is_file()


def test_sync_installs_into_the_engine_upstream_resolves(tmp_path, monkeypatch):
    """⛔ THE SEAM THAT REPLACED A FORK. The patched launcher used to install
    these just before launching; losing that hook was the reason this project
    vendored the whole library. It only ever needed the engine's path, and
    upstream hands that out publicly, so the same work is done from here.

    ensure_binary is faked rather than called: the real one downloads 549 MB.
    """
    engine = tmp_path / "engine" / "firefox.exe"
    engine.parent.mkdir(parents=True)
    engine.write_bytes(b"not really firefox")
    source = tmp_path / "solver.xpi"
    _xpi(source, "solver@example.test", "ok")

    import invisible_playwright

    monkeypatch.setattr(invisible_playwright, "ensure_binary", lambda *a, **k: engine)

    installed = sync_engine_extensions([source], ["blocked@example.test"])

    target = engine.parent / "distribution" / "extensions" / "solver@example.test.xpi"
    assert target.is_file(), "XPI phai nam trong distribution/extensions cua engine"
    assert tuple(installed) == (target,)
