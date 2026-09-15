from __future__ import annotations

import json
import time
import uuid

from sqlmodel import Field, SQLModel


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


class LicenseRecord(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("lic"), primary_key=True)
    key_digest: str = Field(index=True, unique=True)
    customer_reference: str = Field(default="", index=True)
    plan: str
    features_json: str
    max_accounts: int
    max_tabs: int
    max_devices: int
    channel: str = "stable"
    expires_at: int
    created_at: int = Field(default_factory=lambda: int(time.time()))
    revoked_at: int | None = None

    @property
    def features(self) -> tuple[str, ...]:
        value = json.loads(self.features_json)
        return tuple(value)


class DeviceRecord(SQLModel, table=True):
    id: str = Field(primary_key=True)
    license_id: str = Field(index=True)
    public_key_pem: str
    first_activated_at: int = Field(default_factory=lambda: int(time.time()))
    last_seen_at: int = Field(default_factory=lambda: int(time.time()))
    revoked_at: int | None = None


class UsedNonce(SQLModel, table=True):
    digest: str = Field(primary_key=True)
    expires_at: int = Field(index=True)


class UsedDownloadGrant(SQLModel, table=True):
    digest: str = Field(primary_key=True)
    expires_at: int = Field(index=True)


class ReleaseRecord(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("artifact"), primary_key=True)
    component: str = Field(index=True)
    version: str
    channel: str = Field(index=True)
    target: str = Field(index=True)
    storage_name: str
    download_url: str
    sha256: str
    size_bytes: int
    published_at: int = Field(default_factory=lambda: int(time.time()))
    minimum_launcher_version: str
    mandatory: bool = False
    signed_manifest: str
    active: bool = Field(default=True, index=True)


class AuditEvent(SQLModel, table=True):
    id: str = Field(default_factory=lambda: new_id("audit"), primary_key=True)
    event_type: str = Field(index=True)
    actor_id: str
    subject_id: str = Field(index=True)
    metadata_json: str = "{}"
    created_at: int = Field(default_factory=lambda: int(time.time()), index=True)
