from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import secrets
import time
import json
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Header, HTTPException
from sqlmodel import Session, delete, select

from backend.app.security.license import LeaseClaims, LeaseSigner
from backend.app.security.release import ReleaseManifestSigner
from control_plane.app.models import DeviceRecord, LicenseRecord, UsedNonce


def b64url_decode(value: str) -> bytes:
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid device signature encoding."
        ) from exc


def generate_license_key() -> str:
    encoded = base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")
    return "TKAUTO-" + "-".join(
        encoded[index : index + 4] for index in range(0, len(encoded), 4)
    )


def digest_license_key(license_key: str, pepper: str) -> str:
    normalized = "".join(license_key.upper().split())
    return hmac.new(
        pepper.encode("utf-8"), normalized.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def issue_download_grant(
    secret: str,
    *,
    artifact_id: str,
    device_id: str,
    ttl_seconds: int,
) -> tuple[str, int]:
    expires_at = int(time.time()) + ttl_seconds
    claims = {
        "artifact_id": artifact_id,
        "device_id": device_id,
        "expires_at": expires_at,
        "nonce": secrets.token_urlsafe(18),
        "v": 1,
    }
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    signature = hmac.new(
        secret.encode(), payload.encode("ascii"), hashlib.sha256
    ).digest()
    token = (
        f"{payload}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"
    )
    return token, expires_at


def verify_download_grant(
    secret: str, token: str, artifact_id: str
) -> dict[str, object]:
    if not token or len(token) > 4096:
        raise HTTPException(
            status_code=401, detail="Download authorization is invalid."
        )
    parts = token.split(".")
    if len(parts) != 2:
        raise HTTPException(
            status_code=401, detail="Download authorization is invalid."
        )
    payload, encoded_signature = parts
    supplied_signature = b64url_decode(encoded_signature)
    expected_signature = hmac.new(
        secret.encode(), payload.encode("ascii"), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise HTTPException(
            status_code=401, detail="Download authorization is invalid."
        )
    try:
        claims = json.loads(b64url_decode(payload).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=401, detail="Download authorization is invalid."
        ) from exc
    if not isinstance(claims, dict) or set(claims) != {
        "artifact_id",
        "device_id",
        "expires_at",
        "nonce",
        "v",
    }:
        raise HTTPException(
            status_code=401, detail="Download authorization is invalid."
        )
    if (
        claims.get("v") != 1
        or claims.get("artifact_id") != artifact_id
        or not isinstance(claims.get("device_id"), str)
        or not isinstance(claims.get("expires_at"), int)
        or int(time.time()) >= claims["expires_at"]
    ):
        raise HTTPException(
            status_code=401, detail="Download authorization is invalid or expired."
        )
    return claims


def canonical_device_proof(
    action: str, device_id: str, issued_at: int, nonce: str, app_version: str
) -> bytes:
    return (
        f"TKAUTO-{action}-v1\n{device_id}\n{issued_at}\n{nonce}\n{app_version}".encode(
            "utf-8"
        )
    )


def load_device_public_key(pem: str) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(pem.encode("utf-8"))
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail="Invalid device public key."
        ) from exc
    if not isinstance(key, Ed25519PublicKey):
        raise HTTPException(status_code=400, detail="Device key must be Ed25519.")
    return key


def expected_device_id(key: Ed25519PublicKey) -> str:
    raw = key.public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    return f"device_{hashlib.sha256(raw).hexdigest()[:40]}"


def enforce_device_id(key: Ed25519PublicKey, supplied_device_id: str) -> None:
    if not hmac.compare_digest(expected_device_id(key), supplied_device_id):
        raise HTTPException(
            status_code=400,
            detail="Device ID does not match the supplied public key.",
        )


def verify_device_proof(
    key: Ed25519PublicKey, canonical: bytes, signature: str
) -> None:
    raw_signature = b64url_decode(signature)
    if len(raw_signature) != 64:
        raise HTTPException(status_code=400, detail="Invalid device signature length.")
    try:
        key.verify(raw_signature, canonical)
    except InvalidSignature as exc:
        raise HTTPException(status_code=401, detail="Device proof is invalid.") from exc


def enforce_fresh_proof(
    session: Session, *, context: str, issued_at: int, nonce: str, skew: int
) -> None:
    now = int(time.time())
    if abs(now - issued_at) > skew:
        raise HTTPException(
            status_code=401, detail="Device proof is outside the allowed time window."
        )
    session.exec(delete(UsedNonce).where(UsedNonce.expires_at < now))
    digest = hashlib.sha256(
        f"{context}\n{issued_at}\n{nonce}".encode("utf-8")
    ).hexdigest()
    if session.get(UsedNonce, digest) is not None:
        raise HTTPException(
            status_code=409, detail="Device proof nonce was already used."
        )
    session.add(UsedNonce(digest=digest, expires_at=now + skew * 2))


def load_lease_signer(key_id: str, private_key_path: str) -> LeaseSigner:
    pem = Path(private_key_path).expanduser().resolve().read_bytes()
    return LeaseSigner.from_pem(key_id, pem)


def load_release_signer(key_id: str, private_key_path: str) -> ReleaseManifestSigner:
    pem = Path(private_key_path).expanduser().resolve().read_bytes()
    return ReleaseManifestSigner.from_pem(key_id, pem)


def issue_lease(
    signer: LeaseSigner,
    license_record: LicenseRecord,
    device: DeviceRecord,
    *,
    ttl: int,
    minimum_version: str,
    minimum_backend_version: str,
) -> tuple[str, LeaseClaims]:
    now = int(time.time())
    expires_at = min(license_record.expires_at, now + ttl)
    if expires_at <= now:
        raise HTTPException(status_code=403, detail="License has expired.")
    features = tuple(dict.fromkeys(("app.start", *license_record.features)))
    claims = LeaseClaims(
        license_id=license_record.id,
        device_id=device.id,
        plan=license_record.plan,
        features=features,
        max_accounts=license_record.max_accounts,
        max_tabs=license_record.max_tabs,
        channel=license_record.channel,
        minimum_version=minimum_version,
        minimum_backend_version=minimum_backend_version,
        issued_at=now,
        not_before=now - 5,
        expires_at=expires_at,
        jti=f"lease_{secrets.token_hex(16)}",
    )
    return signer.sign(claims), claims


def require_admin(expected_token: str):
    async def dependency(authorization: str = Header(default="")) -> None:
        prefix = "Bearer "
        supplied = (
            authorization[len(prefix) :] if authorization.startswith(prefix) else ""
        )
        if not hmac.compare_digest(supplied, expected_token):
            raise HTTPException(
                status_code=401, detail="Administrator authentication required."
            )

    return dependency
