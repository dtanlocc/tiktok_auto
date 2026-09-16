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


def test_network_mode_survives_a_backend_restart(monkeypatch, tmp_path):
    """The switch used to live in memory only.

    Restarting the backend put it back on proxy silently, while a page left
    open still said "Mạng thật" - so sessions ran through the proxy the
    operator believed was off.
    """
    monkeypatch.setattr(
        extension_settings.settings,
        "BROWSER_EXTENSION_SETTINGS_PATH",
        str(tmp_path / "browser-extension-settings.json"),
    )
    monkeypatch.setattr(extension_settings.settings, "USE_PROXY", True)

    result = asyncio.run(
        tasks_router.set_proxy_mode(tasks_router.ProxyModeRequest(use_proxy=False))
    )
    assert result["use_proxy"] is False
    assert extension_settings.settings.USE_PROXY is False

    # A restart rebuilds settings from their defaults...
    monkeypatch.setattr(extension_settings.settings, "USE_PROXY", True)
    # ...and startup restores what the operator chose.
    assert extension_settings.load_proxy_mode() is False


def test_network_mode_defaults_to_the_configured_value(monkeypatch, tmp_path):
    monkeypatch.setattr(
        extension_settings.settings,
        "BROWSER_EXTENSION_SETTINGS_PATH",
        str(tmp_path / "never-written.json"),
    )
    monkeypatch.setattr(extension_settings.settings, "USE_PROXY", True)
    assert extension_settings.load_proxy_mode() is True


def test_saving_the_network_mode_keeps_the_nordvpn_choice(monkeypatch, tmp_path):
    """Both switches share one document; writing one must not erase the other."""
    monkeypatch.setattr(
        extension_settings.settings,
        "BROWSER_EXTENSION_SETTINGS_PATH",
        str(tmp_path / "browser-extension-settings.json"),
    )
    # Both setters also write the process-wide settings object; register them
    # with monkeypatch so the values are restored for the tests that follow.
    monkeypatch.setattr(extension_settings.settings, "USE_PROXY", True)
    monkeypatch.setattr(
        extension_settings.settings, "NORDVPN_EXTENSION_ENABLED", False
    )
    extension_settings.set_nordvpn_extension_enabled(True)
    extension_settings.set_proxy_mode(False)

    document = json.loads(
        (tmp_path / "browser-extension-settings.json").read_text(encoding="utf-8")
    )
    assert document == {"nordvpn_extension_enabled": True, "use_proxy": False}
