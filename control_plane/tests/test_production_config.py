from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from control_plane.app.config import ControlPlaneSettings


def write_key(path):
    path.write_bytes(
        Ed25519PrivateKey.generate().private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )


def test_production_config_loads_secrets_from_files(tmp_path):
    lease_key = tmp_path / "lease.pem"
    release_key = tmp_path / "release.pem"
    write_key(lease_key)
    write_key(release_key)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    secret_paths = {}
    values = {
        "database": "postgresql+psycopg://operator:secret@db/license",
        "admin": "a" * 48,
        "pepper": "b" * 48,
        "grant": "c" * 48,
    }
    for name, value in values.items():
        path = tmp_path / name
        path.write_text(value, encoding="utf-8")
        secret_paths[name] = path
    settings = ControlPlaneSettings(
        environment="production",
        database_url_file=str(secret_paths["database"]),
        admin_token_file=str(secret_paths["admin"]),
        license_key_pepper_file=str(secret_paths["pepper"]),
        download_grant_secret_file=str(secret_paths["grant"]),
        lease_signing_key_id="lease-test-001",
        lease_signing_private_key_path=str(lease_key),
        release_signing_key_id="release-test-001",
        release_signing_private_key_path=str(release_key),
        artifact_storage_root=str(artifacts),
        public_base_url="https://license.example.test",
        trusted_hosts="license.example.test",
    )
    settings.validate_runtime()
    assert settings.database_url == values["database"]
    assert settings.admin_token == values["admin"]
    assert settings.allowed_hosts == ["license.example.test"]


def test_config_rejects_invalid_minimum_versions(tmp_path):
    lease_key = tmp_path / "lease.pem"
    write_key(lease_key)
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    settings = ControlPlaneSettings(
        admin_token="a" * 48,
        license_key_pepper="b" * 48,
        download_grant_secret="c" * 48,
        lease_signing_key_id="lease-test-001",
        lease_signing_private_key_path=str(lease_key),
        release_signing_key_id="release-test-001",
        release_signing_private_key_path=str(lease_key),
        artifact_storage_root=str(artifacts),
        public_base_url="https://license.example.test",
        minimum_backend_version="latest",
    )
    with pytest.raises(RuntimeError, match="minimum_backend_version"):
        settings.validate_runtime()
