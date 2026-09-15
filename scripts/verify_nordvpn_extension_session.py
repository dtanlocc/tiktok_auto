from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.infrastructure.automation.playwright_adapter import (  # noqa: E402
    InvisiblePlaywrightAdapter,
)


async def verify(storage_directory: Path) -> bool:
    addon_id = "nordvpnproxy@nordvpn.com"
    metadata_path = storage_directory / addon_id / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    extension_uuid = str(metadata["extension_uuid"])

    adapter = InvisiblePlaywrightAdapter()
    try:
        await adapter.initialize(force_visible=False)
        await asyncio.sleep(6)
        profile = Path(adapter._temp_profile_path or "")
        registry = json.loads((profile / "extensions.json").read_text(encoding="utf-8"))
        addon = next(
            (item for item in registry.get("addons", []) if item.get("id") == addon_id),
            None,
        )
        active_and_signed = bool(
            addon and addon.get("active") and int(addon.get("signedState") or 0) > 0
        )

        storage = json.loads(
            (
                profile
                / "browser-extension-data"
                / addon_id
                / "storage.js"
            ).read_text(encoding="utf-8-sig")
        )
        persisted = json.loads(storage["persist:@nordvpn:extension-firefox"])
        auth = json.loads(persisted["extension.auth"])
        services = json.loads(persisted["extension.services"])
        connection = services.get("connection") or {}
        authenticated = bool(auth.get("authorizedAt"))
        reusable_connection = bool(
            connection.get("server")
            and connection.get("sessionId")
            and not connection.get("error")
        )

        prefs = (profile / "user.js").read_text(encoding="utf-8")
        uuid_preserved = extension_uuid in prefs
        print(
            "NordVPN extension active and Mozilla-signed: "
            f"{'yes' if active_and_signed else 'no'}"
        )
        print(f"NordVPN source UUID preserved: {'yes' if uuid_preserved else 'no'}")
        print(f"NordVPN authenticated state present: {'yes' if authenticated else 'no'}")
        print(
            "NordVPN reusable connection state present: "
            f"{'yes' if reusable_connection else 'no'}"
        )
        return active_and_signed and uuid_preserved and authenticated
    finally:
        await adapter.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify a private NordVPN extension-state snapshot in a fresh profile."
    )
    parser.add_argument(
        "--storage-directory",
        type=Path,
        default=REPO_ROOT / ".runtime" / "extension-storage",
    )
    args = parser.parse_args()
    if not asyncio.run(verify(args.storage_directory.resolve())):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
