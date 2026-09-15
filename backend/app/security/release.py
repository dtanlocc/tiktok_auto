from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .license import _parse_semver

_TYPE = "TKAUTO-RELEASE"
_ALGORITHM = "EdDSA"
_MAX_TOKEN_BYTES = 64 * 1024
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


class ReleaseManifestError(RuntimeError):
    """Raised when release metadata is malformed or cannot be trusted."""


def _identifier(value: str, label: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{label} has an invalid format")
    return value


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    if not value or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ReleaseManifestError("Release token contains invalid base64url data.")
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
    except (binascii.Error, ValueError) as exc:
        raise ReleaseManifestError(
            "Release token contains invalid base64url data."
        ) from exc


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


class ReleaseManifest(BaseModel):
    """Immutable artifact metadata verified by the native launcher before use."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal[1] = 1
    artifact_id: str = Field(min_length=3, max_length=128)
    component: Literal["backend", "desktop"]
    version: str
    channel: Literal["internal", "beta", "stable"]
    target: Literal["windows-x86_64"] = "windows-x86_64"
    download_url: str = Field(min_length=12, max_length=2048)
    sha256: str
    size_bytes: int = Field(gt=0, le=10 * 1024 * 1024 * 1024)
    published_at: int = Field(ge=0)
    minimum_launcher_version: str
    mandatory: bool = False

    @field_validator("artifact_id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        return _identifier(value, "artifact_id")

    @field_validator("version", "minimum_launcher_version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        _parse_semver(value)
        return value

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = value.lower()
        if not _SHA256_RE.fullmatch(normalized):
            raise ValueError("sha256 must contain exactly 64 lowercase hex characters")
        return normalized

    @field_validator("download_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise ValueError("download_url must be an HTTPS URL without user info")
        if parsed.fragment:
            raise ValueError("download_url cannot contain a fragment")
        return value


@dataclass(frozen=True)
class ReleaseEnvelope:
    key_id: str
    manifest: ReleaseManifest


class ReleaseManifestSigner:
    """Vendor-only release metadata signer; never ship this private key."""

    def __init__(self, key_id: str, private_key: Ed25519PrivateKey) -> None:
        self.key_id = _identifier(key_id, "key_id")
        self._private_key = private_key

    @classmethod
    def from_pem(cls, key_id: str, pem: str | bytes) -> "ReleaseManifestSigner":
        key = serialization.load_pem_private_key(
            pem.encode() if isinstance(pem, str) else pem, password=None
        )
        if not isinstance(key, Ed25519PrivateKey):
            raise TypeError("Release signing key must be Ed25519.")
        return cls(key_id, key)

    def sign(self, manifest: ReleaseManifest) -> str:
        header = {"alg": _ALGORITHM, "kid": self.key_id, "typ": _TYPE, "v": 1}
        head = _encode(_canonical(header))
        body = _encode(_canonical(manifest.model_dump(mode="json")))
        signature = self._private_key.sign(f"{head}.{body}".encode("ascii"))
        return f"{head}.{body}.{_encode(signature)}"


class ReleaseManifestVerifier:
    def __init__(self, public_keys: Mapping[str, Ed25519PublicKey]) -> None:
        if not public_keys:
            raise ValueError("At least one release verification key is required.")
        self._public_keys = {
            _identifier(key_id, "key_id"): key for key_id, key in public_keys.items()
        }
        if any(
            not isinstance(key, Ed25519PublicKey) for key in self._public_keys.values()
        ):
            raise TypeError("All release verification keys must be Ed25519.")

    def verify(self, token: str) -> ReleaseEnvelope:
        if not token or len(token.encode()) > _MAX_TOKEN_BYTES:
            raise ReleaseManifestError("Release token is empty or too large.")
        parts = token.split(".")
        if len(parts) != 3:
            raise ReleaseManifestError(
                "Release token must contain three signed segments."
            )
        head, body, signature_part = parts
        try:
            header = json.loads(_decode(head).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReleaseManifestError("Release header is invalid.") from exc
        if not isinstance(header, dict) or set(header) != {"alg", "kid", "typ", "v"}:
            raise ReleaseManifestError("Release header fields are not allowed.")
        if (
            header.get("alg") != _ALGORITHM
            or header.get("typ") != _TYPE
            or header.get("v") != 1
        ):
            raise ReleaseManifestError(
                "Release signing algorithm or type is unsupported."
            )
        key_id = header.get("kid")
        if not isinstance(key_id, str) or key_id not in self._public_keys:
            raise ReleaseManifestError("Release token was signed by an unknown key.")
        signature = _decode(signature_part)
        if len(signature) != 64:
            raise ReleaseManifestError("Release signature length is invalid.")
        try:
            self._public_keys[key_id].verify(
                signature, f"{head}.{body}".encode("ascii")
            )
        except InvalidSignature as exc:
            raise ReleaseManifestError("Release signature is invalid.") from exc
        try:
            raw_manifest = json.loads(_decode(body).decode("utf-8"))
            manifest = ReleaseManifest.model_validate(raw_manifest)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise ReleaseManifestError("Release manifest is invalid.") from exc
        return ReleaseEnvelope(key_id=key_id, manifest=manifest)
