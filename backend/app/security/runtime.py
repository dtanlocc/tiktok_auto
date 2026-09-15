from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.security.license import EntitlementError, LeaseClaims, LeaseVerifier


class EntitlementGate(Protocol):
    def require(
        self,
        feature: str,
        *,
        account_count: int | None = None,
        tab_count: int | None = None,
    ) -> None: ...

    def status(self) -> dict[str, object]: ...


@dataclass(frozen=True)
class DevelopmentEntitlementGate:
    """Explicit source-development bypass; production never selects this."""

    mode: str = "development"

    def require(
        self,
        feature: str,
        *,
        account_count: int | None = None,
        tab_count: int | None = None,
    ) -> None:
        return None

    def status(self) -> dict[str, object]:
        return {"mode": self.mode, "licensed": True}


@dataclass(frozen=True)
class SignedLeaseEntitlementGate:
    claims: LeaseClaims
    device_id: str
    app_version: str
    clock_skew_seconds: int = 30

    def require(
        self,
        feature: str,
        *,
        account_count: int | None = None,
        tab_count: int | None = None,
    ) -> None:
        self.claims.assert_runtime_valid(
            device_id=self.device_id,
            app_version=self.app_version,
            component="backend",
            clock_skew_seconds=self.clock_skew_seconds,
        )
        self.claims.require(feature, account_count=account_count, tab_count=tab_count)

    def status(self) -> dict[str, object]:
        return {
            "mode": "production",
            "licensed": True,
            "license_id": self.claims.license_id,
            "plan": self.claims.plan,
            "channel": self.claims.channel,
            "expires_at": self.claims.expires_at,
            "minimum_version": self.claims.minimum_version,
            "minimum_backend_version": self.claims.minimum_backend_version,
            "max_accounts": self.claims.max_accounts,
            "max_tabs": self.claims.max_tabs,
        }


_TASK_FEATURES = {
    "UPDATE_PROFILE": "profiles.update",
    "INTERACT_VIDEOS": "interaction.run",
    "SYNC_ANALYTICS": "analytics.sync",
    "UPLOAD_VIDEO": "upload.video",
    "UPLOAD_MEDIA": "upload.video",
    "UPLOAD_MEDIA_BATCH": "upload.video",
}


def feature_for_task(task_type: str) -> str:
    if task_type.startswith("LOGIN_"):
        return "accounts.login"
    try:
        return _TASK_FEATURES[task_type]
    except KeyError as exc:
        raise EntitlementError(
            f"Task type has no entitlement mapping: {task_type}"
        ) from exc


def build_entitlement_gate(settings) -> EntitlementGate:
    mode = str(getattr(settings, "SECURITY_MODE", "development")).strip().lower()
    if mode in {"development", "friends"}:
        return DevelopmentEntitlementGate(mode=mode)
    if mode != "production":
        raise RuntimeError(
            "SECURITY_MODE must be 'development', 'friends', or 'production'."
        )

    app_version = str(getattr(settings, "APP_VERSION", "")).strip()
    device_id = str(getattr(settings, "LICENSE_DEVICE_ID", "")).strip()
    lease_path_value = str(getattr(settings, "LICENSE_LEASE_PATH", "")).strip()
    raw_keys = str(getattr(settings, "LICENSE_PUBLIC_KEYS_JSON", "")).strip()
    if not app_version or not device_id or not lease_path_value or not raw_keys:
        raise RuntimeError(
            "Production security requires APP_VERSION, LICENSE_DEVICE_ID, "
            "LICENSE_LEASE_PATH, and LICENSE_PUBLIC_KEYS_JSON."
        )

    lease_path = Path(lease_path_value).expanduser().resolve()
    if not lease_path.is_file():
        raise RuntimeError("The production lease file does not exist.")
    token = lease_path.read_text(encoding="utf-8").strip()
    try:
        public_keys = json.loads(raw_keys)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LICENSE_PUBLIC_KEYS_JSON is not valid JSON.") from exc
    if not isinstance(public_keys, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in public_keys.items()
    ):
        raise RuntimeError(
            "LICENSE_PUBLIC_KEYS_JSON must map key IDs to PEM public keys."
        )

    claims = LeaseVerifier.from_pem_mapping(public_keys).verify(token).claims
    gate = SignedLeaseEntitlementGate(
        claims=claims,
        device_id=device_id,
        app_version=app_version,
        clock_skew_seconds=max(
            0, int(getattr(settings, "LICENSE_CLOCK_SKEW_SECONDS", 30))
        ),
    )
    gate.require("app.start")
    return gate
