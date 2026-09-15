import json
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.security.license import EntitlementError, LeaseClaims, LeaseSigner
from app.security.runtime import (
    DevelopmentEntitlementGate,
    SignedLeaseEntitlementGate,
    build_entitlement_gate,
    feature_for_task,
)
from app.interfaces.api.deps import require_runtime_entitlement


class Settings:
    SECURITY_MODE = "production"
    APP_VERSION = "1.5.0"
    LICENSE_DEVICE_ID = "device_001"
    LICENSE_LEASE_PATH = ""
    LICENSE_PUBLIC_KEYS_JSON = "{}"
    LICENSE_CLOCK_SKEW_SECONDS = 0


def make_claims(now: int) -> LeaseClaims:
    return LeaseClaims(
        license_id="license_001",
        device_id="device_001",
        plan="pro",
        features=("app.start", "upload.video", "accounts.login"),
        max_accounts=20,
        max_tabs=4,
        channel="stable",
        minimum_version="1.4.0",
        minimum_backend_version="1.4.0",
        issued_at=now - 10,
        not_before=now - 10,
        expires_at=now + 3600,
        jti="lease_runtime_001",
    )


def test_build_production_gate_from_signed_file(tmp_path, monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr("app.security.license.time.time", lambda: now)
    private_key = Ed25519PrivateKey.generate()
    lease_file = tmp_path / "runtime.lease"
    lease_file.write_text(
        LeaseSigner("lease-key-001", private_key).sign(make_claims(now)),
        encoding="utf-8",
    )
    public_pem = (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )
    settings = Settings()
    settings.LICENSE_LEASE_PATH = str(lease_file)
    settings.LICENSE_PUBLIC_KEYS_JSON = json.dumps({"lease-key-001": public_pem})
    gate = build_entitlement_gate(settings)
    gate.require("upload.video", tab_count=4)
    assert gate.status()["license_id"] == "license_001"


def test_production_gate_fails_closed_without_configuration():
    with pytest.raises(RuntimeError, match="Production security requires"):
        build_entitlement_gate(Settings())


def test_development_bypass_is_explicit():
    class DevelopmentSettings:
        SECURITY_MODE = "development"

    gate = build_entitlement_gate(DevelopmentSettings())
    assert isinstance(gate, DevelopmentEntitlementGate)
    gate.require("anything.local")


def test_friends_distribution_has_no_license_but_reports_its_mode():
    class FriendsSettings:
        SECURITY_MODE = "friends"

    gate = build_entitlement_gate(FriendsSettings())
    assert isinstance(gate, DevelopmentEntitlementGate)
    assert gate.status() == {"mode": "friends", "licensed": True}


def test_signed_gate_checks_each_call(monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr("app.security.license.time.time", lambda: now)
    gate = SignedLeaseEntitlementGate(make_claims(now), "device_001", "1.5.0", 0)
    gate.require("upload.video")
    monkeypatch.setattr("app.security.license.time.time", lambda: now + 4000)
    with pytest.raises(EntitlementError, match="expired"):
        gate.require("upload.video")


@pytest.mark.parametrize(
    ("task_type", "feature"),
    [
        ("LOGIN_COOKIE", "accounts.login"),
        ("UPDATE_PROFILE", "profiles.update"),
        ("INTERACT_VIDEOS", "interaction.run"),
        ("SYNC_ANALYTICS", "analytics.sync"),
        ("UPLOAD_MEDIA_BATCH", "upload.video"),
    ],
)
def test_task_feature_mapping_is_explicit(task_type, feature):
    assert feature_for_task(task_type) == feature


def test_unknown_task_type_fails_closed():
    with pytest.raises(EntitlementError, match="no entitlement mapping"):
        feature_for_task("NEW_UNMAPPED_TASK")


def test_api_entitlement_dependency_checks_gate_on_each_request():
    checked = []

    class RecordingGate:
        def require(self, feature):
            checked.append(feature)

    dependency = require_runtime_entitlement("app.start")
    dependency(
        SimpleNamespace(
            app=SimpleNamespace(state=SimpleNamespace(entitlement_gate=RecordingGate()))
        )
    )
    assert checked == ["app.start"]
