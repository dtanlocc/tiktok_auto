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


# =============================================================================
# Lease duoc gia han NGAY DUOI CHAN backend dang chay
# =============================================================================
def _signed(private_key, claims) -> str:
    return LeaseSigner("lease-key-001", private_key).sign(claims)


def _public_pem(private_key) -> str:
    return (
        private_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )


def _gate_on_a_lease_file(tmp_path, private_key, claims):
    lease_file = tmp_path / "runtime.lease"
    lease_file.write_text(_signed(private_key, claims), encoding="utf-8")
    settings = Settings()
    settings.LICENSE_LEASE_PATH = str(lease_file)
    settings.LICENSE_PUBLIC_KEYS_JSON = json.dumps(
        {"lease-key-001": _public_pem(private_key)}
    )
    return build_entitlement_gate(settings), lease_file


def _rewrite(lease_file, text: str) -> None:
    """Ghi de VA day mtime len. Launcher that ghi cach nhau ~30 phut, nen mtime
    luon khac; trong test hai lan ghi co the roi vao cung mot nhip dong ho."""
    import os

    lease_file.write_text(text, encoding="utf-8")
    stat = lease_file.stat()
    os.utime(lease_file, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))


def test_a_renewed_lease_is_picked_up_without_restarting_the_backend(
    tmp_path, monkeypatch
):
    """⛔ DAY LA LOI DA DEN TAY NGUOI DUNG, 16/10/2026: "Lease has expired."

    `build_entitlement_gate` chi chay MOT LAN luc backend khoi dong, va truoc
    ban sua nay no giu nguyen claims doc duoc luc do - vinh vien. Lease song 1
    gio. Launcher van gia han dung han va ghi lease moi ra dia, nhung backend
    khong he doc lai.

    Nen ai de app mo qua mot tieng la moi chuc nang chet, cho toi khi khoi dong
    lai app. Khong mot phep thu ngan nao thay duoc.
    """
    now = 1_800_000_000
    monkeypatch.setattr("app.security.license.time.time", lambda: now)
    private_key = Ed25519PrivateKey.generate()
    gate, lease_file = _gate_on_a_lease_file(tmp_path, private_key, make_claims(now))
    gate.require("upload.video")

    # Mot tieng mot phut sau: lease cu da het han.
    later = now + 3_660
    monkeypatch.setattr("app.security.license.time.time", lambda: later)
    with pytest.raises(EntitlementError, match="expired"):
        gate.require("upload.video")

    # Launcher gia han va ghi lease moi - KHONG khoi dong lai backend.
    _rewrite(lease_file, _signed(private_key, make_claims(later)))

    gate.require("upload.video")
    assert gate.status()["expires_at"] == later + 3600


def test_a_renewed_lease_also_brings_new_features(tmp_path, monkeypatch):
    """Quyen duoc cap them phai an ngay, khong doi khoi dong lai.

    Dung canh da xay ra that: key phat thieu `accounts.login`, sua tren may chu,
    va may khach phai nhan duoc quyen do o lan gia han ke tiep.
    """
    now = 1_800_000_000
    monkeypatch.setattr("app.security.license.time.time", lambda: now)
    private_key = Ed25519PrivateKey.generate()
    hep = make_claims(now).model_copy(update={"features": ("app.start", "upload.video")})
    gate, lease_file = _gate_on_a_lease_file(tmp_path, private_key, hep)

    with pytest.raises(EntitlementError, match="accounts.login"):
        gate.require("accounts.login")

    _rewrite(lease_file, _signed(private_key, make_claims(now)))

    gate.require("accounts.login")


def test_a_forged_lease_cannot_extend_access(tmp_path, monkeypatch):
    """⛔ Doc lai tu dia khong duoc tro thanh mot duong keo dai quyen.

    Neu tep bi thay bang mot lease ky bang khoa khac, no phai bi bo qua, va ban
    cu van het han dung han cua no.
    """
    now = 1_800_000_000
    monkeypatch.setattr("app.security.license.time.time", lambda: now)
    private_key = Ed25519PrivateKey.generate()
    gate, lease_file = _gate_on_a_lease_file(tmp_path, private_key, make_claims(now))

    later = now + 3_660
    monkeypatch.setattr("app.security.license.time.time", lambda: later)
    _rewrite(lease_file, _signed(Ed25519PrivateKey.generate(), make_claims(later)))

    with pytest.raises(EntitlementError, match="expired"):
        gate.require("upload.video")


def test_a_deleted_lease_file_does_not_grant_anything(tmp_path, monkeypatch):
    now = 1_800_000_000
    monkeypatch.setattr("app.security.license.time.time", lambda: now)
    private_key = Ed25519PrivateKey.generate()
    gate, lease_file = _gate_on_a_lease_file(tmp_path, private_key, make_claims(now))

    lease_file.unlink()
    gate.require("upload.video")  # ban cu con han -> van chay

    monkeypatch.setattr("app.security.license.time.time", lambda: now + 3_660)
    with pytest.raises(EntitlementError, match="expired"):
        gate.require("upload.video")
