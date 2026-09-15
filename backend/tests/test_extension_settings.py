import asyncio
import json

from app.core import extension_settings
from app.interfaces.api import tasks_router


def _configure_paths(monkeypatch, tmp_path) -> None:
    extension_dir = tmp_path / "extensions"
    extension_dir.mkdir()
    (extension_dir / f"{extension_settings.NORDVPN_ADDON_ID}.xpi").write_bytes(b"xpi")
    storage_dir = tmp_path / "extension-storage"
    addon_storage = storage_dir / extension_settings.NORDVPN_ADDON_ID
    addon_storage.mkdir(parents=True)
    persisted = {
        "extension.auth": json.dumps({"authorizedAt": "present"}),
    }
    (addon_storage / "storage.js").write_text(
        json.dumps(
            {"persist:@nordvpn:extension-firefox": json.dumps(persisted)}
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        extension_settings.settings,
        "BROWSER_EXTENSION_SETTINGS_PATH",
        str(tmp_path / "browser-extension-settings.json"),
    )
    monkeypatch.setattr(
        extension_settings.settings, "BROWSER_EXTENSIONS_DIR", str(extension_dir)
    )
    monkeypatch.setattr(extension_settings.settings, "BROWSER_EXTENSION_PATHS", "")
    monkeypatch.setattr(
        extension_settings.settings, "BROWSER_EXTENSION_STORAGE_DIR", str(storage_dir)
    )
    monkeypatch.setattr(extension_settings.settings, "NORDVPN_EXTENSION_ENABLED", True)


def test_nordvpn_toggle_is_persisted_and_reports_private_artifacts(
    monkeypatch, tmp_path
) -> None:
    _configure_paths(monkeypatch, tmp_path)

    assert extension_settings.is_nordvpn_extension_enabled() is True
    assert extension_settings.nordvpn_package_available() is True
    assert extension_settings.nordvpn_authenticated_state_available() is True

    assert extension_settings.set_nordvpn_extension_enabled(False) is False
    monkeypatch.setattr(extension_settings.settings, "NORDVPN_EXTENSION_ENABLED", True)
    assert extension_settings.is_nordvpn_extension_enabled() is False


def test_nordvpn_runtime_endpoint_changes_future_browser_mode(
    monkeypatch, tmp_path
) -> None:
    _configure_paths(monkeypatch, tmp_path)

    result = asyncio.run(
        tasks_router.set_nordvpn_extension_status(
            tasks_router.NordVpnExtensionRequest(enabled=False)
        )
    )

    assert result["status"] == "SUCCESS"
    assert result["enabled"] is False
    assert result["package_available"] is True
    assert result["authenticated_state_available"] is True
