from __future__ import annotations

import base64
import binascii
import json
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_LEASE_TYPE = "TKAUTO-LEASE"
_LEASE_ALGORITHM = "EdDSA"
_MAX_TOKEN_BYTES = 64 * 1024
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_FEATURE_RE = re.compile(r"^[a-z][a-z0-9._-]{1,63}$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


class LeaseError(RuntimeError):
    """Raised when a lease is malformed, untrusted, or unusable."""


class EntitlementError(LeaseError):
    """Raised when a trusted lease does not authorize an operation."""


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    if (
        not value
        or len(value) > _MAX_TOKEN_BYTES
        or not re.fullmatch(r"[A-Za-z0-9_-]+", value)
    ):
        raise LeaseError("Lease contains invalid base64url data.")
    try:
        return base64.b64decode(
            value + "=" * (-len(value) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (binascii.Error, ValueError) as exc:
        raise LeaseError("Lease contains invalid base64url data.") from exc


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _strict_json_object(raw: bytes, label: str) -> dict[str, Any]:
    if len(raw) > _MAX_TOKEN_BYTES:
        raise LeaseError(f"Lease {label} is too large.")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LeaseError(f"Lease {label} is not valid JSON.") from exc
    if not isinstance(value, dict):
        raise LeaseError(f"Lease {label} must be an object.")
    return value


def _validate_identifier(value: str, label: str) -> str:
    if not _IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{label} has an invalid format")
    return value


class LeaseClaims(BaseModel):
    """Strict, signed authorization state issued by the vendor control plane."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal[1] = 1
    license_id: str = Field(min_length=3, max_length=128)
    device_id: str = Field(min_length=3, max_length=128)
    plan: str = Field(min_length=2, max_length=64)
    features: tuple[str, ...] = ()
    max_accounts: int = Field(ge=1, le=1_000_000)
    max_tabs: int = Field(ge=1, le=1_000)
    channel: Literal["internal", "beta", "stable"] = "stable"
    # minimum_version is the desktop/launcher floor retained for protocol-v1
    # compatibility; backend has an independent revocation/update floor.
    minimum_version: str
    minimum_backend_version: str
    issued_at: int = Field(ge=0)
    not_before: int = Field(ge=0)
    expires_at: int = Field(ge=0)
    jti: str = Field(min_length=3, max_length=128)

    @field_validator("license_id", "device_id", "plan", "jti")
    @classmethod
    def validate_identifiers(cls, value: str, info) -> str:
        return _validate_identifier(value, info.field_name)

    @field_validator("minimum_version", "minimum_backend_version")
    @classmethod
    def validate_minimum_version(cls, value: str) -> str:
        _parse_semver(value)
        return value

    @field_validator("features")
    @classmethod
    def validate_features(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(dict.fromkeys(values))
        if normalized != values:
            raise ValueError("features must be unique and retain server order")
        if any(not _FEATURE_RE.fullmatch(value) for value in values):
            raise ValueError("features contain an invalid identifier")
        return values

    @model_validator(mode="after")
    def validate_time_window(self) -> "LeaseClaims":
        if self.not_before < self.issued_at - 300:
            raise ValueError("not_before cannot substantially precede issued_at")
        if self.expires_at <= self.not_before:
            raise ValueError("expires_at must be later than not_before")
        return self

    def assert_runtime_valid(
        self,
        *,
        device_id: str,
        app_version: str,
        component: Literal["desktop", "backend"] = "backend",
        now: int | None = None,
        clock_skew_seconds: int = 30,
    ) -> None:
        current_time = int(time.time()) if now is None else int(now)
        if self.device_id != device_id:
            raise EntitlementError("Lease belongs to a different device.")
        if current_time + clock_skew_seconds < self.not_before:
            raise EntitlementError("Lease is not active yet.")
        if current_time - clock_skew_seconds >= self.expires_at:
            raise EntitlementError("Lease has expired.")
        minimum_version = (
            self.minimum_version
            if component == "desktop"
            else self.minimum_backend_version
        )
        if _compare_semver(app_version, minimum_version) < 0:
            raise EntitlementError("Application version is below the required minimum.")

    def require(
        self,
        feature: str,
        *,
        account_count: int | None = None,
        tab_count: int | None = None,
    ) -> None:
        if feature not in self.features:
            raise EntitlementError(f"Feature is not licensed: {feature}")
        if account_count is not None and account_count > self.max_accounts:
            raise EntitlementError("Requested account count exceeds the license limit.")
        if tab_count is not None and tab_count > self.max_tabs:
            raise EntitlementError(
                "Requested browser concurrency exceeds the license limit."
            )


@dataclass(frozen=True)
class LeaseEnvelope:
    key_id: str
    claims: LeaseClaims


class LeaseSigner:
    """Control-plane helper. Never import a private key in customer builds."""

    def __init__(self, key_id: str, private_key: Ed25519PrivateKey) -> None:
        self.key_id = _validate_identifier(key_id, "key_id")
        self._private_key = private_key

    @classmethod
    def from_pem(
        cls, key_id: str, pem: str | bytes, password: bytes | None = None
    ) -> "LeaseSigner":
        key = serialization.load_pem_private_key(
            pem.encode("utf-8") if isinstance(pem, str) else pem,
            password=password,
        )
        if not isinstance(key, Ed25519PrivateKey):
            raise TypeError("Lease signing key must be Ed25519.")
        return cls(key_id, key)

    def sign(self, claims: LeaseClaims) -> str:
        header = {
            "alg": _LEASE_ALGORITHM,
            "kid": self.key_id,
            "typ": _LEASE_TYPE,
            "v": 1,
        }
        encoded_header = _b64url_encode(_canonical_json(header))
        encoded_claims = _b64url_encode(_canonical_json(claims.model_dump(mode="json")))
        signing_input = f"{encoded_header}.{encoded_claims}".encode("ascii")
        signature = self._private_key.sign(signing_input)
        return f"{encoded_header}.{encoded_claims}.{_b64url_encode(signature)}"


class LeaseVerifier:
    def __init__(self, public_keys: Mapping[str, Ed25519PublicKey]) -> None:
        if not public_keys:
            raise ValueError("At least one license verification key is required.")
        self._public_keys = {
            _validate_identifier(key_id, "key_id"): key
            for key_id, key in public_keys.items()
        }
        if any(
            not isinstance(key, Ed25519PublicKey) for key in self._public_keys.values()
        ):
            raise TypeError("All license verification keys must be Ed25519.")

    @classmethod
    def from_pem_mapping(
        cls, public_keys: Mapping[str, str | bytes]
    ) -> "LeaseVerifier":
        loaded: dict[str, Ed25519PublicKey] = {}
        for key_id, pem in public_keys.items():
            key = serialization.load_pem_public_key(
                pem.encode("utf-8") if isinstance(pem, str) else pem
            )
            if not isinstance(key, Ed25519PublicKey):
                raise TypeError("License verification key must be Ed25519.")
            loaded[key_id] = key
        return cls(loaded)

    def verify(self, token: str) -> LeaseEnvelope:
        if not token or len(token.encode("utf-8")) > _MAX_TOKEN_BYTES:
            raise LeaseError("Lease is empty or too large.")
        parts = token.split(".")
        if len(parts) != 3:
            raise LeaseError("Lease must contain three signed segments.")
        header_part, claims_part, signature_part = parts
        header = _strict_json_object(_b64url_decode(header_part), "header")
        if set(header) != {"alg", "kid", "typ", "v"}:
            raise LeaseError("Lease header fields are not allowed.")
        if (
            header.get("alg") != _LEASE_ALGORITHM
            or header.get("typ") != _LEASE_TYPE
            or header.get("v") != 1
        ):
            raise LeaseError("Lease header algorithm or type is not supported.")
        key_id = header.get("kid")
        if not isinstance(key_id, str) or key_id not in self._public_keys:
            raise LeaseError("Lease was signed by an unknown key.")
        signature = _b64url_decode(signature_part)
        if len(signature) != 64:
            raise LeaseError("Lease signature length is invalid.")
        try:
            self._public_keys[key_id].verify(
                signature,
                f"{header_part}.{claims_part}".encode("ascii"),
            )
        except InvalidSignature as exc:
            raise LeaseError("Lease signature is invalid.") from exc
        raw_claims = _strict_json_object(_b64url_decode(claims_part), "claims")
        try:
            claims = LeaseClaims.model_validate(raw_claims)
        except ValueError as exc:
            raise LeaseError("Lease claims are invalid.") from exc
        return LeaseEnvelope(key_id=key_id, claims=claims)


def _parse_semver(
    value: str,
) -> tuple[int, int, int, tuple[tuple[int, int | str], ...] | None]:
    match = _SEMVER_RE.fullmatch(value)
    if not match:
        raise ValueError("Version must be strict semantic versioning (x.y.z).")
    prerelease = match.group(4)
    parsed_prerelease: tuple[tuple[int, int | str], ...] | None = None
    if prerelease is not None:
        items: list[tuple[int, int | str]] = []
        for item in prerelease.split("."):
            if item.isdigit():
                if len(item) > 1 and item.startswith("0"):
                    raise ValueError(
                        "Numeric prerelease identifiers cannot have leading zeroes."
                    )
                items.append((0, int(item)))
            else:
                items.append((1, item))
        parsed_prerelease = tuple(items)
    return (
        int(match.group(1)),
        int(match.group(2)),
        int(match.group(3)),
        parsed_prerelease,
    )


def _compare_semver(left: str, right: str) -> int:
    left_value = _parse_semver(left)
    right_value = _parse_semver(right)
    if left_value[:3] != right_value[:3]:
        return -1 if left_value[:3] < right_value[:3] else 1
    left_pre = left_value[3]
    right_pre = right_value[3]
    if left_pre is None and right_pre is None:
        return 0
    if left_pre is None:
        return 1
    if right_pre is None:
        return -1
    for left_item, right_item in zip(left_pre, right_pre):
        if left_item == right_item:
            continue
        return -1 if left_item < right_item else 1
    if len(left_pre) == len(right_pre):
        return 0
    return -1 if len(left_pre) < len(right_pre) else 1
