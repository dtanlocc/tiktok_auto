import time

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.security.release import (
    ReleaseManifest,
    ReleaseManifestError,
    ReleaseManifestSigner,
    ReleaseManifestVerifier,
)


def make_manifest() -> ReleaseManifest:
    return ReleaseManifest(
        artifact_id="artifact_backend_001",
        component="backend",
        version="1.2.3",
        channel="stable",
        download_url="https://downloads.example.test/backend-1.2.3.exe",
        sha256="a" * 64,
        size_bytes=123_456,
        published_at=int(time.time()),
        minimum_launcher_version="1.0.0",
        mandatory=True,
    )


def test_signed_release_manifest_round_trip():
    key = Ed25519PrivateKey.generate()
    token = ReleaseManifestSigner("release-key-001", key).sign(make_manifest())
    envelope = ReleaseManifestVerifier({"release-key-001": key.public_key()}).verify(
        token
    )
    assert envelope.manifest.version == "1.2.3"
    assert envelope.manifest.component == "backend"


def test_release_manifest_rejects_tampering_and_unknown_key():
    key = Ed25519PrivateKey.generate()
    token = ReleaseManifestSigner("release-key-001", key).sign(make_manifest())
    head, body, signature = token.split(".")
    with pytest.raises(ReleaseManifestError, match="signature"):
        ReleaseManifestVerifier({"release-key-001": key.public_key()}).verify(
            f"{head}.{body[:-1]}A.{signature}"
        )
    with pytest.raises(ReleaseManifestError, match="unknown key"):
        ReleaseManifestVerifier({"release-key-002": key.public_key()}).verify(token)


@pytest.mark.parametrize(
    "field,value",
    [
        ("download_url", "http://downloads.example.test/backend.exe"),
        ("sha256", "not-a-hash"),
        ("version", "1.2"),
    ],
)
def test_release_manifest_rejects_unsafe_metadata(field, value):
    payload = make_manifest().model_dump()
    payload[field] = value
    with pytest.raises(ValueError):
        ReleaseManifest.model_validate(payload)
