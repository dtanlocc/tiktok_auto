import base64
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.security.license import (
    EntitlementError,
    LeaseClaims,
    LeaseError,
    LeaseSigner,
    LeaseVerifier,
)


def claims(**overrides) -> LeaseClaims:
    values = {
        "license_id": "lic_customer_001",
        "device_id": "device_windows_001",
        "plan": "commercial",
        "features": ("accounts.manage", "upload.video"),
        "max_accounts": 100,
        "max_tabs": 4,
        "channel": "stable",
        "minimum_version": "1.2.0",
        "minimum_backend_version": "1.2.0",
        "issued_at": 1_700_000_000,
        "not_before": 1_700_000_000,
        "expires_at": 1_700_086_400,
        "jti": "lease_001_unique",
    }
    values.update(overrides)
    return LeaseClaims(**values)


def signer_and_verifier():
    private_key = Ed25519PrivateKey.generate()
    return (
        LeaseSigner("license-key-2026-01", private_key),
        LeaseVerifier({"license-key-2026-01": private_key.public_key()}),
    )


def test_signed_lease_round_trip_and_entitlement_limits():
    signer, verifier = signer_and_verifier()
    verified = verifier.verify(signer.sign(claims())).claims

    verified.assert_runtime_valid(
        device_id="device_windows_001",
        app_version="1.2.1",
        now=1_700_000_100,
    )
    verified.require("upload.video", account_count=100, tab_count=4)

    with pytest.raises(EntitlementError, match="account count"):
        verified.require("upload.video", account_count=101)
    with pytest.raises(EntitlementError, match="browser concurrency"):
        verified.require("upload.video", tab_count=5)
    with pytest.raises(EntitlementError, match="not licensed"):
        verified.require("interaction.run")


def test_lease_rejects_tampering_unknown_keys_and_algorithm_confusion():
    signer, verifier = signer_and_verifier()
    token = signer.sign(claims())
    header, payload, signature = token.split(".")

    raw_payload = json.loads(
        base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
    )
    raw_payload["max_tabs"] = 999
    tampered_payload = (
        base64.urlsafe_b64encode(
            json.dumps(raw_payload, separators=(",", ":"), sort_keys=True).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    with pytest.raises(LeaseError, match="signature"):
        verifier.verify(f"{header}.{tampered_payload}.{signature}")

    other = Ed25519PrivateKey.generate()
    unknown_token = LeaseSigner("unknown-key-2026", other).sign(claims())
    with pytest.raises(LeaseError, match="unknown key"):
        verifier.verify(unknown_token)

    raw_header = json.loads(base64.urlsafe_b64decode(header + "=" * (-len(header) % 4)))
    raw_header["alg"] = "HS256"
    confused_header = (
        base64.urlsafe_b64encode(
            json.dumps(raw_header, separators=(",", ":"), sort_keys=True).encode()
        )
        .rstrip(b"=")
        .decode()
    )
    with pytest.raises(LeaseError, match="algorithm"):
        verifier.verify(f"{confused_header}.{payload}.{signature}")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"device_id": "another-device", "now": 1_700_000_100}, "different device"),
        ({"device_id": "device_windows_001", "now": 1_700_086_500}, "expired"),
        ({"device_id": "device_windows_001", "now": 1_699_999_000}, "not active"),
    ],
)
def test_runtime_rejects_wrong_device_and_invalid_time(kwargs, message):
    with pytest.raises(EntitlementError, match=message):
        claims().assert_runtime_valid(app_version="1.2.0", **kwargs)


def test_runtime_rejects_downgrade_and_semver_prerelease():
    lease = claims(minimum_version="2.0.0")
    with pytest.raises(EntitlementError, match="required minimum"):
        lease.assert_runtime_valid(
            device_id="device_windows_001",
            app_version="1.99.99",
            component="desktop",
            now=1_700_000_100,
        )
    with pytest.raises(EntitlementError, match="required minimum"):
        lease.assert_runtime_valid(
            device_id="device_windows_001",
            app_version="2.0.0-rc.1",
            component="desktop",
            now=1_700_000_100,
        )


def test_desktop_and_backend_minimum_versions_are_enforced_independently():
    lease = claims(minimum_version="1.2.0", minimum_backend_version="2.4.0")
    lease.assert_runtime_valid(
        device_id="device_windows_001",
        app_version="1.2.0",
        component="desktop",
        now=1_700_000_100,
    )
    with pytest.raises(EntitlementError, match="required minimum"):
        lease.assert_runtime_valid(
            device_id="device_windows_001",
            app_version="2.3.9",
            component="backend",
            now=1_700_000_100,
        )
    lease.assert_runtime_valid(
        device_id="device_windows_001",
        app_version="2.4.0",
        component="backend",
        now=1_700_000_100,
    )


def test_claims_are_strict_and_feature_names_are_constrained():
    with pytest.raises(ValueError):
        claims(features=("upload.video", "upload.video"))
    with pytest.raises(ValueError):
        claims(features=("UPLOAD VIDEO",))
    with pytest.raises(ValueError):
        LeaseClaims(**{**claims().model_dump(), "unexpected": "field"})
