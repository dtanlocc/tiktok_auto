from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateLicenseRequest(StrictModel):
    customer_reference: str = Field(default="", max_length=128)
    plan: str = Field(min_length=2, max_length=64)
    features: tuple[str, ...]
    max_accounts: int = Field(ge=1, le=1_000_000)
    max_tabs: int = Field(ge=1, le=1_000)
    max_devices: int = Field(ge=1, le=1_000)
    channel: Literal["internal", "beta", "stable"] = "stable"
    expires_at: int = Field(ge=0)


class DeviceProofRequest(StrictModel):
    device_id: str = Field(
        min_length=3, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]+$"
    )
    issued_at: int = Field(ge=0)
    nonce: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    signature: str = Field(min_length=40, max_length=256, pattern=r"^[A-Za-z0-9_-]+$")
    app_version: str = Field(min_length=5, max_length=64)


class ActivateRequest(DeviceProofRequest):
    license_key: str = Field(min_length=20, max_length=128)
    device_public_key_pem: str = Field(min_length=80, max_length=2_000)


class RenewLeaseRequest(DeviceProofRequest):
    license_id: str = Field(min_length=3, max_length=128)


class LeaseResponse(StrictModel):
    lease: str
    expires_at: int
    renew_after: int


class CreateLicenseResponse(StrictModel):
    license_id: str
    license_key: str


class RevokeRequest(StrictModel):
    reason: str = Field(min_length=3, max_length=500)


class UpdateLicenseRequest(StrictModel):
    customer_reference: str | None = Field(default=None, max_length=128)
    plan: str | None = Field(default=None, min_length=2, max_length=64)
    features: tuple[str, ...] | None = None
    max_accounts: int | None = Field(default=None, ge=1, le=1_000_000)
    max_tabs: int | None = Field(default=None, ge=1, le=1_000)
    max_devices: int | None = Field(default=None, ge=1, le=1_000)
    channel: Literal["internal", "beta", "stable"] | None = None
    expires_at: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def require_change(self):
        if not self.model_fields_set:
            raise ValueError("At least one license field must be updated.")
        return self


class RegisterReleaseRequest(StrictModel):
    component: Literal["backend", "desktop"]
    version: str = Field(min_length=5, max_length=64)
    channel: Literal["internal", "beta", "stable"] = "stable"
    target: Literal["windows-x86_64"] = "windows-x86_64"
    artifact_filename: str = Field(
        min_length=5,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*\.exe$",
    )
    minimum_launcher_version: str = Field(min_length=5, max_length=64)
    mandatory: bool = False


class RegisterReleaseResponse(StrictModel):
    artifact_id: str
    signed_manifest: str


class ReleaseCheckRequest(DeviceProofRequest):
    license_id: str = Field(min_length=3, max_length=128)
    component: Literal["backend", "desktop"]
    current_version: str = Field(min_length=5, max_length=64)
    target: Literal["windows-x86_64"] = "windows-x86_64"


class ReleaseCheckResponse(StrictModel):
    update_available: bool
    signed_manifest: str | None = None
    download_grant: str | None = None
    grant_expires_at: int | None = None
