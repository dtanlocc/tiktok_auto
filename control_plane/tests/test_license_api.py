import base64
import hashlib
import secrets
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from backend.app.security.license import LeaseVerifier
from backend.app.security.release import ReleaseManifestVerifier
from control_plane.app.config import ControlPlaneSettings
from control_plane.app.main import create_app
from control_plane.app.models import AuditEvent
from control_plane.app.security import canonical_device_proof


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def make_client(tmp_path):
    signing_key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "lease-signing.pem"
    key_path.write_bytes(
        signing_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    artifact_root = tmp_path / "artifacts"
    artifact_root.mkdir()
    settings = ControlPlaneSettings(
        database_url=f"sqlite:///{tmp_path / 'control.db'}",
        admin_token="admin-token-" + "a" * 40,
        license_key_pepper="pepper-" + "b" * 40,
        lease_signing_key_id="lease-key-test-001",
        lease_signing_private_key_path=str(key_path),
        release_signing_key_id="release-key-test-001",
        release_signing_private_key_path=str(key_path),
        artifact_storage_root=str(artifact_root),
        public_base_url="https://licenses.example.test",
        download_grant_secret="download-secret-" + "c" * 40,
        lease_ttl_seconds=3600,
        max_clock_skew_seconds=300,
    )
    return TestClient(create_app(settings)), settings, signing_key


def create_license(client, settings, *, max_devices=1):
    response = client.post(
        "/v1/admin/licenses",
        headers={"Authorization": f"Bearer {settings.admin_token}"},
        json={
            "plan": "pro",
            "features": ["upload.video", "accounts.login"],
            "max_accounts": 100,
            "max_tabs": 4,
            "max_devices": max_devices,
            "channel": "stable",
            "expires_at": int(time.time()) + 86_400,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def device_request(action, device_key, device_id, app_version="1.0.0"):
    issued_at = int(time.time())
    nonce = secrets.token_urlsafe(24)
    signature = device_key.sign(
        canonical_device_proof(action, device_id, issued_at, nonce, app_version)
    )
    return {
        "device_id": device_id,
        "issued_at": issued_at,
        "nonce": nonce,
        "signature": b64url(signature),
        "app_version": app_version,
    }


def device_id_for(key):
    raw = key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return "device_" + hashlib.sha256(raw).hexdigest()[:40]


def public_pem(key):
    return (
        key.public_key()
        .public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )


def test_activate_and_renew_device_bound_lease(tmp_path):
    client, settings, signing_key = make_client(tmp_path)
    license_data = create_license(client, settings)
    device_key = Ed25519PrivateKey.generate()
    device_id = device_id_for(device_key)
    activation = {
        **device_request("ACTIVATE", device_key, device_id),
        "license_key": license_data["license_key"],
        "device_public_key_pem": public_pem(device_key),
    }
    response = client.post("/v1/activate", json=activation)
    assert response.status_code == 200, response.text
    claims = (
        LeaseVerifier({"lease-key-test-001": signing_key.public_key()})
        .verify(response.json()["lease"])
        .claims
    )
    assert claims.device_id == device_id
    assert claims.license_id == license_data["license_id"]
    assert "upload.video" in claims.features
    assert claims.minimum_version == settings.minimum_client_version
    assert claims.minimum_backend_version == settings.minimum_backend_version

    renewal = {
        **device_request("RENEW", device_key, device_id),
        "license_id": license_data["license_id"],
    }
    renewed = client.post("/v1/lease/renew", json=renewal)
    assert renewed.status_code == 200, renewed.text


def test_activation_rejects_bad_device_proof_and_second_device(tmp_path):
    client, settings, _ = make_client(tmp_path)
    license_data = create_license(client, settings, max_devices=1)
    first_key = Ed25519PrivateKey.generate()
    first_device_id = device_id_for(first_key)
    first = {
        **device_request("ACTIVATE", first_key, first_device_id),
        "license_key": license_data["license_key"],
        "device_public_key_pem": public_pem(first_key),
    }
    assert client.post("/v1/activate", json=first).status_code == 200

    second_key = Ed25519PrivateKey.generate()
    second_device_id = device_id_for(second_key)
    second = {
        **device_request("ACTIVATE", second_key, second_device_id),
        "license_key": license_data["license_key"],
        "device_public_key_pem": public_pem(second_key),
    }
    assert client.post("/v1/activate", json=second).status_code == 403

    bad = {
        **device_request("ACTIVATE", second_key, first_device_id),
        "license_key": license_data["license_key"],
        "device_public_key_pem": public_pem(first_key),
    }
    assert client.post("/v1/activate", json=bad).status_code == 401


def test_activation_rejects_device_id_not_derived_from_public_key(tmp_path):
    client, settings, _ = make_client(tmp_path)
    license_data = create_license(client, settings)
    device_key = Ed25519PrivateKey.generate()
    activation = {
        **device_request("ACTIVATE", device_key, "device_not_the_key_hash"),
        "license_key": license_data["license_key"],
        "device_public_key_pem": public_pem(device_key),
    }
    assert client.post("/v1/activate", json=activation).status_code == 400


def test_activation_has_application_rate_limit(tmp_path):
    client, _, _ = make_client(tmp_path)
    for _ in range(10):
        assert client.post("/v1/activate", json={}).status_code == 422
    limited = client.post("/v1/activate", json={})
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "60"


def test_nonce_replay_and_revocation_fail_closed(tmp_path):
    client, settings, _ = make_client(tmp_path)
    license_data = create_license(client, settings)
    key = Ed25519PrivateKey.generate()
    device_id = device_id_for(key)
    activation = {
        **device_request("ACTIVATE", key, device_id),
        "license_key": license_data["license_key"],
        "device_public_key_pem": public_pem(key),
    }
    assert client.post("/v1/activate", json=activation).status_code == 200
    assert client.post("/v1/activate", json=activation).status_code == 409

    revoke = client.post(
        f"/v1/admin/licenses/{license_data['license_id']}/revoke",
        headers={"Authorization": f"Bearer {settings.admin_token}"},
        json={"reason": "test revocation"},
    )
    assert revoke.status_code == 200
    renewal = {
        **device_request("RENEW", key, device_id),
        "license_id": license_data["license_id"],
    }
    assert client.post("/v1/lease/renew", json=renewal).status_code == 403


def test_admin_endpoints_require_bearer_token(tmp_path):
    client, _, _ = make_client(tmp_path)
    response = client.post("/v1/admin/licenses", json={})
    assert response.status_code == 401


def test_admin_can_list_update_and_transfer_devices_without_exposing_key_digest(
    tmp_path,
):
    client, settings, _ = make_client(tmp_path)
    license_data = create_license(client, settings, max_devices=2)
    headers = {"Authorization": f"Bearer {settings.admin_token}"}
    updated = client.patch(
        f"/v1/admin/licenses/{license_data['license_id']}",
        headers=headers,
        json={"customer_reference": "customer-001", "max_tabs": 8},
    )
    assert updated.status_code == 200, updated.text
    listed = client.get("/v1/admin/licenses", headers=headers)
    assert listed.status_code == 200, listed.text
    serialized = listed.text
    assert "customer-001" in serialized
    assert "key_digest" not in serialized

    key = Ed25519PrivateKey.generate()
    device_id = device_id_for(key)
    activation = {
        **device_request("ACTIVATE", key, device_id),
        "license_key": license_data["license_key"],
        "device_public_key_pem": public_pem(key),
    }
    assert client.post("/v1/activate", json=activation).status_code == 200
    devices = client.get(
        f"/v1/admin/licenses/{license_data['license_id']}/devices",
        headers=headers,
    )
    assert devices.status_code == 200
    assert devices.json()["items"][0]["device_id"] == device_id
    assert "public_key_pem" not in devices.text


def test_release_is_signed_checked_and_downloaded_with_short_lived_grant(tmp_path):
    client, settings, signing_key = make_client(tmp_path)
    artifact_bytes = b"MZ" + secrets.token_bytes(256)
    (tmp_path / "artifacts" / "backend-1.1.0.exe").write_bytes(artifact_bytes)
    registered = client.post(
        "/v1/admin/releases",
        headers={"Authorization": f"Bearer {settings.admin_token}"},
        json={
            "component": "backend",
            "version": "1.1.0",
            "channel": "stable",
            "target": "windows-x86_64",
            "artifact_filename": "backend-1.1.0.exe",
            "minimum_launcher_version": "1.0.0",
            "mandatory": True,
        },
    )
    assert registered.status_code == 200, registered.text
    manifest = (
        ReleaseManifestVerifier({"release-key-test-001": signing_key.public_key()})
        .verify(registered.json()["signed_manifest"])
        .manifest
    )
    assert manifest.sha256
    assert manifest.size_bytes == len(artifact_bytes)

    license_data = create_license(client, settings)
    device_key = Ed25519PrivateKey.generate()
    device_id = device_id_for(device_key)
    activation = {
        **device_request("ACTIVATE", device_key, device_id),
        "license_key": license_data["license_key"],
        "device_public_key_pem": public_pem(device_key),
    }
    assert client.post("/v1/activate", json=activation).status_code == 200
    release_action = "RELEASE:backend:1.0.0:windows-x86_64"
    check = {
        **device_request(release_action, device_key, device_id),
        "license_id": license_data["license_id"],
        "component": "backend",
        "current_version": "1.0.0",
        "target": "windows-x86_64",
    }
    checked = client.post("/v1/releases/check", json=check)
    assert checked.status_code == 200, checked.text
    assert checked.json()["update_available"] is True
    grant = checked.json()["download_grant"]
    assert (
        client.get(
            manifest.download_url.replace("https://licenses.example.test", "")
        ).status_code
        == 401
    )
    download = client.get(
        manifest.download_url.replace("https://licenses.example.test", ""),
        headers={"Authorization": f"Bearer {grant}"},
    )
    assert download.status_code == 200, download.text
    assert download.content == artifact_bytes
    replay = client.get(
        manifest.download_url.replace("https://licenses.example.test", ""),
        headers={"Authorization": f"Bearer {grant}"},
    )
    assert replay.status_code == 409
    second_check = {
        **device_request(release_action, device_key, device_id),
        "license_id": license_data["license_id"],
        "component": "backend",
        "current_version": "1.0.0",
        "target": "windows-x86_64",
    }
    second_grant = client.post("/v1/releases/check", json=second_check).json()[
        "download_grant"
    ]
    deactivated = client.post(
        f"/v1/admin/releases/{manifest.artifact_id}/deactivate",
        headers={"Authorization": f"Bearer {settings.admin_token}"},
        json={"reason": "rollback test"},
    )
    assert deactivated.status_code == 200
    assert (
        client.get(
            manifest.download_url.replace("https://licenses.example.test", ""),
            headers={"Authorization": f"Bearer {second_grant}"},
        ).status_code
        == 403
    )
    revoked = client.post(
        f"/v1/admin/licenses/{license_data['license_id']}/revoke",
        headers={"Authorization": f"Bearer {settings.admin_token}"},
        json={"reason": "download revocation test"},
    )
    assert revoked.status_code == 200
    assert (
        client.get(
            manifest.download_url.replace("https://licenses.example.test", ""),
            headers={"Authorization": f"Bearer {grant}"},
        ).status_code
        == 403
    )


def test_release_registration_rejects_missing_artifact(tmp_path):
    client, settings, _ = make_client(tmp_path)
    response = client.post(
        "/v1/admin/releases",
        headers={"Authorization": f"Bearer {settings.admin_token}"},
        json={
            "component": "backend",
            "version": "1.1.0",
            "artifact_filename": "missing.exe",
            "minimum_launcher_version": "1.0.0",
        },
    )
    assert response.status_code == 400


def test_admin_device_revocation_blocks_renewal_and_audit_omits_raw_key(tmp_path):
    client, settings, _ = make_client(tmp_path)
    license_data = create_license(client, settings)
    key = Ed25519PrivateKey.generate()
    device_id = device_id_for(key)
    activation = {
        **device_request("ACTIVATE", key, device_id),
        "license_key": license_data["license_key"],
        "device_public_key_pem": public_pem(key),
    }
    assert client.post("/v1/activate", json=activation).status_code == 200
    revoked = client.post(
        f"/v1/admin/devices/{device_id}/revoke",
        headers={"Authorization": f"Bearer {settings.admin_token}"},
        json={"reason": "customer device transfer"},
    )
    assert revoked.status_code == 200
    renewal = {
        **device_request("RENEW", key, device_id),
        "license_id": license_data["license_id"],
    }
    assert client.post("/v1/lease/renew", json=renewal).status_code == 403
    with Session(client.app.state.engine) as session:
        events = session.exec(select(AuditEvent)).all()
    assert {event.event_type for event in events} >= {
        "license.created",
        "device.activated",
        "device.revoked",
    }
    serialized = "\n".join(event.metadata_json for event in events)
    assert license_data["license_key"] not in serialized
