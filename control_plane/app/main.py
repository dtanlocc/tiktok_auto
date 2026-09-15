from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from collections import defaultdict, deque
from functools import cmp_to_key
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, delete, select
from starlette.middleware.trustedhost import TrustedHostMiddleware

from control_plane.app.config import ControlPlaneSettings
from backend.app.security.license import LeaseClaims, _compare_semver
from backend.app.security.release import ReleaseManifest
from control_plane.app.models import (
    AuditEvent,
    DeviceRecord,
    LicenseRecord,
    ReleaseRecord,
    UsedDownloadGrant,
)
from control_plane.app.schemas import (
    ActivateRequest,
    CreateLicenseRequest,
    CreateLicenseResponse,
    LeaseResponse,
    RegisterReleaseRequest,
    RegisterReleaseResponse,
    ReleaseCheckRequest,
    ReleaseCheckResponse,
    RenewLeaseRequest,
    RevokeRequest,
    UpdateLicenseRequest,
)
from control_plane.app.security import (
    canonical_device_proof,
    digest_license_key,
    enforce_device_id,
    enforce_fresh_proof,
    generate_license_key,
    issue_download_grant,
    issue_lease,
    load_device_public_key,
    load_lease_signer,
    load_release_signer,
    require_admin,
    verify_device_proof,
    verify_download_grant,
)


def create_app(settings: ControlPlaneSettings | None = None) -> FastAPI:
    settings = settings or ControlPlaneSettings()
    settings.validate_runtime()
    connect_args = (
        {"check_same_thread": False}
        if settings.database_url.startswith("sqlite")
        else {}
    )
    engine = create_engine(
        settings.database_url,
        connect_args=connect_args,
        pool_pre_ping=not settings.database_url.startswith("sqlite"),
    )
    SQLModel.metadata.create_all(engine)
    signer = load_lease_signer(
        settings.lease_signing_key_id, settings.lease_signing_private_key_path
    )
    release_signer = load_release_signer(
        settings.release_signing_key_id,
        settings.release_signing_private_key_path,
    )
    artifact_root = Path(settings.artifact_storage_root).expanduser().resolve()
    app = FastAPI(
        title="TikTok Auto License Control Plane",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    rate_buckets: dict[str, deque[float]] = defaultdict(deque)
    rate_lock = threading.Lock()

    def rate_limit_for_path(path: str) -> tuple[str, int] | None:
        if path == f"{settings.api_prefix}/activate":
            return "activate", settings.activation_rate_limit_per_minute
        if path.startswith(f"{settings.api_prefix}/admin/"):
            return "admin", settings.admin_rate_limit_per_minute
        if path.startswith(settings.api_prefix + "/"):
            return "device", settings.device_rate_limit_per_minute
        return None

    @app.middleware("http")
    async def security_boundary(request: Request, call_next):
        content_length = request.headers.get("content-length")
        if request.method in {"POST", "PUT", "PATCH"} and content_length:
            try:
                too_large = int(content_length) > settings.max_request_body_bytes
            except ValueError:
                too_large = True
            if too_large:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body is too large."},
                    headers={"Cache-Control": "no-store"},
                )
        limited = rate_limit_for_path(request.url.path)
        if limited:
            group, maximum = limited
            client_host = request.client.host if request.client else "unknown"
            bucket_key = f"{group}:{client_host}"
            now = time.monotonic()
            with rate_lock:
                bucket = rate_buckets[bucket_key]
                while bucket and now - bucket[0] >= 60:
                    bucket.popleft()
                if len(bucket) >= maximum:
                    return JSONResponse(
                        status_code=429,
                        content={"detail": "Too many requests."},
                        headers={"Retry-After": "60", "Cache-Control": "no-store"},
                    )
                bucket.append(now)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    def get_session():
        with Session(engine) as session:
            yield session

    admin_dependency = require_admin(settings.admin_token)

    def add_audit(
        session: Session,
        *,
        event_type: str,
        actor_id: str,
        subject_id: str,
        metadata: dict[str, object] | None = None,
    ) -> None:
        session.add(
            AuditEvent(
                event_type=event_type,
                actor_id=actor_id,
                subject_id=subject_id,
                metadata_json=json.dumps(
                    metadata or {}, sort_keys=True, separators=(",", ":")
                ),
            )
        )

    @app.get("/health")
    def health():
        return {"status": "ok"}

    def enforce_client_version(version: str) -> None:
        try:
            outdated = _compare_semver(version, settings.minimum_client_version) < 0
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="Client version is invalid."
            ) from exc
        if outdated:
            raise HTTPException(
                status_code=426,
                detail="Client version is below the required minimum.",
            )

    @app.post(
        f"{settings.api_prefix}/admin/licenses",
        response_model=CreateLicenseResponse,
        dependencies=[Depends(admin_dependency)],
    )
    def create_license(
        payload: CreateLicenseRequest, session: Session = Depends(get_session)
    ):
        now = int(time.time())
        if payload.expires_at <= now:
            raise HTTPException(
                status_code=400, detail="License expiry must be in the future."
            )
        # Reuse the strict client claims parser to validate feature identifiers.
        LeaseClaims(
            license_id="validation_license",
            device_id="validation_device",
            plan=payload.plan,
            features=tuple(dict.fromkeys(("app.start", *payload.features))),
            max_accounts=payload.max_accounts,
            max_tabs=payload.max_tabs,
            channel=payload.channel,
            minimum_version="0.1.0",
            minimum_backend_version="0.1.0",
            issued_at=now,
            not_before=now,
            expires_at=payload.expires_at,
            jti="validation_jti",
        )
        license_key = generate_license_key()
        record = LicenseRecord(
            key_digest=digest_license_key(license_key, settings.license_key_pepper),
            customer_reference=payload.customer_reference,
            plan=payload.plan,
            features_json=json.dumps(list(payload.features), separators=(",", ":")),
            max_accounts=payload.max_accounts,
            max_tabs=payload.max_tabs,
            max_devices=payload.max_devices,
            channel=payload.channel,
            expires_at=payload.expires_at,
        )
        session.add(record)
        add_audit(
            session,
            event_type="license.created",
            actor_id="admin",
            subject_id=record.id,
            metadata={
                "plan": record.plan,
                "channel": record.channel,
                "customer_reference": record.customer_reference,
            },
        )
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(
                status_code=409, detail="Generated license key collision."
            ) from exc
        return CreateLicenseResponse(license_id=record.id, license_key=license_key)

    @app.get(
        f"{settings.api_prefix}/admin/licenses",
        dependencies=[Depends(admin_dependency)],
    )
    def list_licenses(
        limit: int = 100,
        offset: int = 0,
        session: Session = Depends(get_session),
    ):
        if not 1 <= limit <= 500 or not 0 <= offset <= 10_000_000:
            raise HTTPException(status_code=422, detail="Pagination is invalid.")
        records = session.exec(
            select(LicenseRecord)
            .order_by(LicenseRecord.created_at.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        return {
            "items": [
                {
                    "license_id": record.id,
                    "customer_reference": record.customer_reference,
                    "plan": record.plan,
                    "features": record.features,
                    "max_accounts": record.max_accounts,
                    "max_tabs": record.max_tabs,
                    "max_devices": record.max_devices,
                    "channel": record.channel,
                    "expires_at": record.expires_at,
                    "created_at": record.created_at,
                    "revoked_at": record.revoked_at,
                }
                for record in records
            ],
            "limit": limit,
            "offset": offset,
        }

    @app.patch(
        f"{settings.api_prefix}/admin/licenses/{{license_id}}",
        dependencies=[Depends(admin_dependency)],
    )
    def update_license(
        license_id: str,
        payload: UpdateLicenseRequest,
        session: Session = Depends(get_session),
    ):
        record = session.get(LicenseRecord, license_id)
        if record is None:
            raise HTTPException(status_code=404, detail="License not found.")
        if record.revoked_at is not None:
            raise HTTPException(
                status_code=409, detail="Revoked licenses cannot be edited."
            )
        changes = payload.model_dump(exclude_unset=True)
        if "expires_at" in changes and int(changes["expires_at"]) <= int(time.time()):
            raise HTTPException(
                status_code=400, detail="License expiry must be in the future."
            )
        candidate_features = tuple(changes.get("features", record.features))
        candidate_plan = str(changes.get("plan", record.plan))
        candidate_channel = str(changes.get("channel", record.channel))
        candidate_accounts = int(changes.get("max_accounts", record.max_accounts))
        candidate_tabs = int(changes.get("max_tabs", record.max_tabs))
        now = int(time.time())
        LeaseClaims(
            license_id=record.id,
            device_id="validation_device",
            plan=candidate_plan,
            features=tuple(dict.fromkeys(("app.start", *candidate_features))),
            max_accounts=candidate_accounts,
            max_tabs=candidate_tabs,
            channel=candidate_channel,
            minimum_version=settings.minimum_client_version,
            minimum_backend_version=settings.minimum_backend_version,
            issued_at=now,
            not_before=now,
            expires_at=int(changes.get("expires_at", record.expires_at)),
            jti="validation_jti",
        )
        for name, value in changes.items():
            if name == "features":
                record.features_json = json.dumps(list(value), separators=(",", ":"))
            else:
                setattr(record, name, value)
        session.add(record)
        add_audit(
            session,
            event_type="license.updated",
            actor_id="admin",
            subject_id=record.id,
            metadata={"fields": sorted(changes)},
        )
        session.commit()
        return {"status": "updated", "license_id": record.id, "fields": sorted(changes)}

    @app.get(
        f"{settings.api_prefix}/admin/licenses/{{license_id}}/devices",
        dependencies=[Depends(admin_dependency)],
    )
    def list_license_devices(
        license_id: str,
        session: Session = Depends(get_session),
    ):
        if session.get(LicenseRecord, license_id) is None:
            raise HTTPException(status_code=404, detail="License not found.")
        devices = session.exec(
            select(DeviceRecord).where(DeviceRecord.license_id == license_id)
        ).all()
        return {
            "items": [
                {
                    "device_id": device.id,
                    "first_activated_at": device.first_activated_at,
                    "last_seen_at": device.last_seen_at,
                    "revoked_at": device.revoked_at,
                }
                for device in devices
            ]
        }

    @app.post(
        f"{settings.api_prefix}/admin/releases",
        response_model=RegisterReleaseResponse,
        dependencies=[Depends(admin_dependency)],
    )
    def register_release(
        payload: RegisterReleaseRequest,
        session: Session = Depends(get_session),
    ):
        source = (artifact_root / payload.artifact_filename).resolve()
        if source.parent != artifact_root or not source.is_file():
            raise HTTPException(
                status_code=400,
                detail="Release artifact is not present in private storage.",
            )
        with source.open("rb") as stream:
            pe_header = stream.read(2)
        if pe_header != b"MZ":
            raise HTTPException(
                status_code=400,
                detail="Release artifact is not a Windows PE executable.",
            )
        duplicate = session.exec(
            select(ReleaseRecord).where(
                ReleaseRecord.component == payload.component,
                ReleaseRecord.version == payload.version,
                ReleaseRecord.channel == payload.channel,
                ReleaseRecord.target == payload.target,
            )
        ).first()
        if duplicate is not None:
            raise HTTPException(
                status_code=409, detail="This release version is already registered."
            )
        sha256 = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                sha256.update(chunk)
        record = ReleaseRecord(
            component=payload.component,
            version=payload.version,
            channel=payload.channel,
            target=payload.target,
            storage_name=source.name,
            download_url="pending",
            sha256=sha256.hexdigest(),
            size_bytes=source.stat().st_size,
            minimum_launcher_version=payload.minimum_launcher_version,
            mandatory=payload.mandatory,
            signed_manifest="pending",
        )
        record.download_url = (
            f"{settings.public_base_url.rstrip('/')}{settings.api_prefix}"
            f"/artifacts/{record.id}/download"
        )
        try:
            manifest = ReleaseManifest(
                artifact_id=record.id,
                component=record.component,
                version=record.version,
                channel=record.channel,
                target=record.target,
                download_url=record.download_url,
                sha256=record.sha256,
                size_bytes=record.size_bytes,
                published_at=record.published_at,
                minimum_launcher_version=record.minimum_launcher_version,
                mandatory=record.mandatory,
            )
        except ValidationError as exc:
            raise HTTPException(
                status_code=422, detail="Release metadata is invalid."
            ) from exc
        record.signed_manifest = release_signer.sign(manifest)
        session.add(record)
        add_audit(
            session,
            event_type="release.registered",
            actor_id="admin",
            subject_id=record.id,
            metadata={"component": record.component, "version": record.version},
        )
        session.commit()
        return RegisterReleaseResponse(
            artifact_id=record.id,
            signed_manifest=record.signed_manifest,
        )

    @app.post(f"{settings.api_prefix}/activate", response_model=LeaseResponse)
    def activate(payload: ActivateRequest, session: Session = Depends(get_session)):
        enforce_client_version(payload.app_version)
        device_key = load_device_public_key(payload.device_public_key_pem)
        enforce_device_id(device_key, payload.device_id)
        verify_device_proof(
            device_key,
            canonical_device_proof(
                "ACTIVATE",
                payload.device_id,
                payload.issued_at,
                payload.nonce,
                payload.app_version,
            ),
            payload.signature,
        )
        enforce_fresh_proof(
            session,
            context=f"activate:{payload.device_id}",
            issued_at=payload.issued_at,
            nonce=payload.nonce,
            skew=settings.max_clock_skew_seconds,
        )
        key_digest = digest_license_key(
            payload.license_key, settings.license_key_pepper
        )
        record = session.exec(
            select(LicenseRecord)
            .where(LicenseRecord.key_digest == key_digest)
            .with_for_update()
        ).first()
        now = int(time.time())
        if record is None:
            raise HTTPException(status_code=401, detail="License key is invalid.")
        if record.revoked_at is not None or record.expires_at <= now:
            raise HTTPException(
                status_code=403, detail="License is revoked or expired."
            )
        device = session.get(DeviceRecord, payload.device_id)
        if device is not None and (
            device.license_id != record.id
            or device.public_key_pem != payload.device_public_key_pem
        ):
            raise HTTPException(
                status_code=409, detail="Device identity is already bound differently."
            )
        if device is None:
            active_devices = session.exec(
                select(DeviceRecord).where(
                    DeviceRecord.license_id == record.id,
                    DeviceRecord.revoked_at.is_(None),
                )
            ).all()
            if len(active_devices) >= record.max_devices:
                raise HTTPException(
                    status_code=403, detail="License device limit has been reached."
                )
            device = DeviceRecord(
                id=payload.device_id,
                license_id=record.id,
                public_key_pem=payload.device_public_key_pem,
            )
            session.add(device)
        device.last_seen_at = now
        token, claims = issue_lease(
            signer,
            record,
            device,
            ttl=settings.lease_ttl_seconds,
            minimum_version=settings.minimum_client_version,
            minimum_backend_version=settings.minimum_backend_version,
        )
        add_audit(
            session,
            event_type="device.activated",
            actor_id=device.id,
            subject_id=record.id,
        )
        session.commit()
        return LeaseResponse(
            lease=token,
            expires_at=claims.expires_at,
            renew_after=claims.issued_at + settings.lease_ttl_seconds // 2,
        )

    @app.post(f"{settings.api_prefix}/lease/renew", response_model=LeaseResponse)
    def renew(payload: RenewLeaseRequest, session: Session = Depends(get_session)):
        enforce_client_version(payload.app_version)
        device = session.get(DeviceRecord, payload.device_id)
        if (
            device is None
            or device.license_id != payload.license_id
            or device.revoked_at is not None
        ):
            raise HTTPException(
                status_code=403, detail="Device activation is not valid."
            )
        device_key = load_device_public_key(device.public_key_pem)
        verify_device_proof(
            device_key,
            canonical_device_proof(
                "RENEW",
                payload.device_id,
                payload.issued_at,
                payload.nonce,
                payload.app_version,
            ),
            payload.signature,
        )
        enforce_fresh_proof(
            session,
            context=f"renew:{payload.license_id}:{payload.device_id}",
            issued_at=payload.issued_at,
            nonce=payload.nonce,
            skew=settings.max_clock_skew_seconds,
        )
        record = session.get(LicenseRecord, payload.license_id)
        now = int(time.time())
        if record is None or record.revoked_at is not None or record.expires_at <= now:
            raise HTTPException(
                status_code=403, detail="License is revoked or expired."
            )
        device.last_seen_at = now
        token, claims = issue_lease(
            signer,
            record,
            device,
            ttl=settings.lease_ttl_seconds,
            minimum_version=settings.minimum_client_version,
            minimum_backend_version=settings.minimum_backend_version,
        )
        add_audit(
            session,
            event_type="lease.renewed",
            actor_id=device.id,
            subject_id=record.id,
        )
        session.commit()
        return LeaseResponse(
            lease=token,
            expires_at=claims.expires_at,
            renew_after=claims.issued_at + settings.lease_ttl_seconds // 2,
        )

    @app.post(
        f"{settings.api_prefix}/releases/check",
        response_model=ReleaseCheckResponse,
    )
    def check_release(
        payload: ReleaseCheckRequest,
        session: Session = Depends(get_session),
    ):
        device = session.get(DeviceRecord, payload.device_id)
        if (
            device is None
            or device.license_id != payload.license_id
            or device.revoked_at is not None
        ):
            raise HTTPException(
                status_code=403, detail="Device activation is not valid."
            )
        proof_action = (
            f"RELEASE:{payload.component}:{payload.current_version}:{payload.target}"
        )
        verify_device_proof(
            load_device_public_key(device.public_key_pem),
            canonical_device_proof(
                proof_action,
                payload.device_id,
                payload.issued_at,
                payload.nonce,
                payload.app_version,
            ),
            payload.signature,
        )
        enforce_fresh_proof(
            session,
            context=f"release:{payload.license_id}:{payload.device_id}:{payload.component}",
            issued_at=payload.issued_at,
            nonce=payload.nonce,
            skew=settings.max_clock_skew_seconds,
        )
        license_record = session.get(LicenseRecord, payload.license_id)
        now = int(time.time())
        if (
            license_record is None
            or license_record.revoked_at is not None
            or license_record.expires_at <= now
        ):
            raise HTTPException(
                status_code=403, detail="License is revoked or expired."
            )
        try:
            candidates = [
                item
                for item in session.exec(
                    select(ReleaseRecord).where(
                        ReleaseRecord.component == payload.component,
                        ReleaseRecord.channel == license_record.channel,
                        ReleaseRecord.target == payload.target,
                        ReleaseRecord.active.is_(True),
                    )
                ).all()
                if _compare_semver(item.version, payload.current_version) > 0
                and _compare_semver(payload.app_version, item.minimum_launcher_version)
                >= 0
            ]
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="Release version is invalid."
            ) from exc
        if not candidates:
            add_audit(
                session,
                event_type="release.checked",
                actor_id=device.id,
                subject_id=payload.component,
                metadata={"update_available": False},
            )
            session.commit()
            return ReleaseCheckResponse(update_available=False)
        selected = max(
            candidates,
            key=cmp_to_key(
                lambda left, right: _compare_semver(left.version, right.version)
            ),
        )
        grant, grant_expires_at = issue_download_grant(
            settings.download_grant_secret,
            artifact_id=selected.id,
            device_id=device.id,
            ttl_seconds=settings.download_grant_ttl_seconds,
        )
        add_audit(
            session,
            event_type="release.checked",
            actor_id=device.id,
            subject_id=selected.id,
            metadata={"update_available": True, "version": selected.version},
        )
        session.commit()
        return ReleaseCheckResponse(
            update_available=True,
            signed_manifest=selected.signed_manifest,
            download_grant=grant,
            grant_expires_at=grant_expires_at,
        )

    @app.get(f"{settings.api_prefix}/artifacts/{{artifact_id}}/download")
    def download_artifact(
        artifact_id: str,
        authorization: str = Header(default=""),
        session: Session = Depends(get_session),
    ):
        prefix = "Bearer "
        grant = authorization[len(prefix) :] if authorization.startswith(prefix) else ""
        claims = verify_download_grant(
            settings.download_grant_secret, grant, artifact_id
        )
        device = session.get(DeviceRecord, str(claims["device_id"]))
        record = session.get(ReleaseRecord, artifact_id)
        license_record = (
            session.get(LicenseRecord, device.license_id) if device else None
        )
        now = int(time.time())
        if (
            device is None
            or device.revoked_at is not None
            or license_record is None
            or license_record.revoked_at is not None
            or license_record.expires_at <= now
            or record is None
            or not record.active
        ):
            raise HTTPException(
                status_code=403, detail="Artifact download is not authorized."
            )
        artifact = (artifact_root / record.storage_name).resolve()
        if artifact.parent != artifact_root or not artifact.is_file():
            raise HTTPException(status_code=404, detail="Artifact is unavailable.")
        sha256 = hashlib.sha256()
        with artifact.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                sha256.update(chunk)
        if artifact.stat().st_size != record.size_bytes or not hmac.compare_digest(
            sha256.hexdigest(), record.sha256
        ):
            raise HTTPException(
                status_code=503, detail="Artifact integrity check failed."
            )
        grant_digest = hashlib.sha256(grant.encode("utf-8")).hexdigest()
        session.exec(
            delete(UsedDownloadGrant).where(UsedDownloadGrant.expires_at < now)
        )
        session.add(
            UsedDownloadGrant(
                digest=grant_digest,
                expires_at=int(claims["expires_at"]),
            )
        )
        add_audit(
            session,
            event_type="artifact.downloaded",
            actor_id=device.id,
            subject_id=record.id,
        )
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise HTTPException(
                status_code=409,
                detail="Download authorization was already used.",
            ) from exc
        return FileResponse(
            artifact,
            media_type="application/vnd.microsoft.portable-executable",
            filename=f"{record.component}-{record.version}.exe",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post(
        f"{settings.api_prefix}/admin/licenses/{{license_id}}/revoke",
        dependencies=[Depends(admin_dependency)],
    )
    def revoke_license(
        license_id: str, payload: RevokeRequest, session: Session = Depends(get_session)
    ):
        record = session.get(LicenseRecord, license_id)
        if record is None:
            raise HTTPException(status_code=404, detail="License not found.")
        if record.revoked_at is None:
            record.revoked_at = int(time.time())
            session.add(record)
            add_audit(
                session,
                event_type="license.revoked",
                actor_id="admin",
                subject_id=license_id,
                metadata={"reason": payload.reason},
            )
            session.commit()
        return {"status": "revoked", "license_id": license_id, "reason": payload.reason}

    @app.post(
        f"{settings.api_prefix}/admin/devices/{{device_id}}/revoke",
        dependencies=[Depends(admin_dependency)],
    )
    def revoke_device(
        device_id: str,
        payload: RevokeRequest,
        session: Session = Depends(get_session),
    ):
        device = session.get(DeviceRecord, device_id)
        if device is None:
            raise HTTPException(status_code=404, detail="Device not found.")
        if device.revoked_at is None:
            device.revoked_at = int(time.time())
            session.add(device)
            add_audit(
                session,
                event_type="device.revoked",
                actor_id="admin",
                subject_id=device.id,
                metadata={"license_id": device.license_id, "reason": payload.reason},
            )
            session.commit()
        return {"status": "revoked", "device_id": device.id, "reason": payload.reason}

    @app.post(
        f"{settings.api_prefix}/admin/releases/{{artifact_id}}/deactivate",
        dependencies=[Depends(admin_dependency)],
    )
    def deactivate_release(
        artifact_id: str,
        payload: RevokeRequest,
        session: Session = Depends(get_session),
    ):
        record = session.get(ReleaseRecord, artifact_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Release not found.")
        if record.active:
            record.active = False
            session.add(record)
            add_audit(
                session,
                event_type="release.deactivated",
                actor_id="admin",
                subject_id=record.id,
                metadata={"reason": payload.reason, "version": record.version},
            )
            session.commit()
        return {
            "status": "deactivated",
            "artifact_id": record.id,
            "reason": payload.reason,
        }

    app.state.engine = engine
    app.state.settings = settings
    app.state.lease_signer = signer
    app.state.release_signer = release_signer
    return app


def load_default_app() -> FastAPI:
    return create_app()
