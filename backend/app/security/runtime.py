from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from app.security.license import EntitlementError, LeaseClaims, LeaseVerifier


class EntitlementGate(Protocol):
    def require(
        self,
        feature: str,
        *,
        account_count: int | None = None,
        tab_count: int | None = None,
    ) -> None: ...

    def status(self) -> dict[str, object]: ...


@dataclass(frozen=True)
class DevelopmentEntitlementGate:
    """Explicit source-development bypass; production never selects this."""

    mode: str = "development"

    def require(
        self,
        feature: str,
        *,
        account_count: int | None = None,
        tab_count: int | None = None,
    ) -> None:
        return None

    def status(self) -> dict[str, object]:
        return {"mode": self.mode, "licensed": True}


@dataclass
class _LeaseOnDisk:
    """Lease hien hanh, DOC LAI khi tep tren dia doi.

    ⛔ MOT BAN CHUP LUC KHOI DONG LA MOT QUA BOM HEN GIO. `build_entitlement_gate`
    chi chay MOT LAN (`main.py`), nen truoc 16/10/2026 backend giu nguyen claims
    doc duoc luc do - vinh vien. Lease song 1 GIO. Launcher van gia han dung
    han va ghi lease moi ra dia, nhung backend khong he doc lai, nen:

        ai de app mo qua mot tieng la MOI chuc nang chet voi
        "Lease has expired.", cho toi khi khoi dong lai app.

    Mot nguoi dung binh thuong de app chay ca buoi - tuc loi nay cham vao tat
    ca moi nguoi, va no khong lo ra trong bat ky phep thu ngan nao.

    Doc lai theo `st_mtime_ns`: khong doi thi khong cham vao dia. Khi launcher
    gia han, no ghi tep moi -> mtime doi -> claims moi duoc nap, ke ca quyen
    vua duoc cap them.
    """

    path: Path
    verifier: LeaseVerifier
    claims: LeaseClaims
    mtime_ns: int

    def current(self) -> LeaseClaims:
        try:
            mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            # ⛔ GIU BAN CU, KHONG TU CHOI NGAY. Tren Windows mot lan doc co the
            # va phai sharing violation nhat thoi. Van fail-closed: ban cu co
            # han cua no, va `assert_runtime_valid` se tu choi khi het han.
            return self.claims
        if mtime_ns == self.mtime_ns:
            return self.claims
        try:
            claims = self.verifier.verify(
                self.path.read_text(encoding="utf-8").strip()
            ).claims
        except Exception:
            # Lease moi hong hoac sai chu ky -> khong nhan. Ban cu van het han
            # dung han cua no, nen khong co duong nao keo dai quyen bang mot tep
            # rac.
            return self.claims
        self.claims = claims
        self.mtime_ns = mtime_ns
        return claims


@dataclass(frozen=True)
class SignedLeaseEntitlementGate:
    #: Claims co dinh (test, lease bat bien) hoac mot `_LeaseOnDisk` tu doc lai.
    lease: LeaseClaims | _LeaseOnDisk
    device_id: str
    app_version: str
    clock_skew_seconds: int = 30

    def _current(self) -> LeaseClaims:
        if isinstance(self.lease, LeaseClaims):
            return self.lease
        return self.lease.current()

    def require(
        self,
        feature: str,
        *,
        account_count: int | None = None,
        tab_count: int | None = None,
    ) -> None:
        claims = self._current()
        claims.assert_runtime_valid(
            device_id=self.device_id,
            app_version=self.app_version,
            component="backend",
            clock_skew_seconds=self.clock_skew_seconds,
        )
        claims.require(feature, account_count=account_count, tab_count=tab_count)

    def status(self) -> dict[str, object]:
        claims = self._current()
        return {
            "mode": "production",
            "licensed": True,
            "license_id": claims.license_id,
            "plan": claims.plan,
            "channel": claims.channel,
            "expires_at": claims.expires_at,
            "minimum_version": claims.minimum_version,
            "minimum_backend_version": claims.minimum_backend_version,
            "max_accounts": claims.max_accounts,
            "max_tabs": claims.max_tabs,
        }


_TASK_FEATURES = {
    "UPDATE_PROFILE": "profiles.update",
    "INTERACT_VIDEOS": "interaction.run",
    "SYNC_ANALYTICS": "analytics.sync",
    "UPLOAD_VIDEO": "upload.video",
    "UPLOAD_MEDIA": "upload.video",
    "UPLOAD_MEDIA_BATCH": "upload.video",
}


#: ⛔ DANH SACH QUYEN DUY NHAT, va cong cu phat key PHAI doc tu day.
#:
#: Truoc 16/10/2026 giao dien phat key tu go san mot danh sach trong HTML:
#: `accounts.manage,upload.video`. `accounts.manage` KHONG TON TAI - khong co
#: cho nao trong backend kiem tra no - va bon quyen that thi thieu. Hau qua:
#: **moi key tung phat deu chi dang duoc video**, con dang nhap, doi profile,
#: tuong tac, dong bo so lieu thi bao
#:
#:     Feature is not licensed: accounts.login
#:
#: ngay tren may khach. Loi khong lo ra luc phat key, cung khong lo ra luc mo
#: app (may chu tu them `app.start`), ma doi den luc nguoi dung bam nut.
#:
#: Hai danh sach go tay o hai noi thi som muon cung lech. Suy ra tu bang tren
#: thi khong lech duoc: them mot loai tac vu la quyen cua no co mat o day.
#:
#: `app.start` khong nam trong day vi may chu LUON tu them no khi phat lease
#: (`effectiveFeatures` trong supabase/functions/_shared/service.ts).
GRANTABLE_FEATURES: tuple[str, ...] = (
    "accounts.login",
    *sorted(set(_TASK_FEATURES.values())),
)


def feature_for_task(task_type: str) -> str:
    if task_type.startswith("LOGIN_"):
        return "accounts.login"
    try:
        return _TASK_FEATURES[task_type]
    except KeyError as exc:
        raise EntitlementError(
            f"Task type has no entitlement mapping: {task_type}"
        ) from exc


def build_entitlement_gate(settings) -> EntitlementGate:
    mode = str(getattr(settings, "SECURITY_MODE", "development")).strip().lower()
    if mode in {"development", "friends"}:
        return DevelopmentEntitlementGate(mode=mode)
    if mode != "production":
        raise RuntimeError(
            "SECURITY_MODE must be 'development', 'friends', or 'production'."
        )

    app_version = str(getattr(settings, "APP_VERSION", "")).strip()
    device_id = str(getattr(settings, "LICENSE_DEVICE_ID", "")).strip()
    lease_path_value = str(getattr(settings, "LICENSE_LEASE_PATH", "")).strip()
    raw_keys = str(getattr(settings, "LICENSE_PUBLIC_KEYS_JSON", "")).strip()
    if not app_version or not device_id or not lease_path_value or not raw_keys:
        raise RuntimeError(
            "Production security requires APP_VERSION, LICENSE_DEVICE_ID, "
            "LICENSE_LEASE_PATH, and LICENSE_PUBLIC_KEYS_JSON."
        )

    lease_path = Path(lease_path_value).expanduser().resolve()
    if not lease_path.is_file():
        raise RuntimeError("The production lease file does not exist.")
    token = lease_path.read_text(encoding="utf-8").strip()
    try:
        public_keys = json.loads(raw_keys)
    except json.JSONDecodeError as exc:
        raise RuntimeError("LICENSE_PUBLIC_KEYS_JSON is not valid JSON.") from exc
    if not isinstance(public_keys, dict) or not all(
        isinstance(key, str) and isinstance(value, str)
        for key, value in public_keys.items()
    ):
        raise RuntimeError(
            "LICENSE_PUBLIC_KEYS_JSON must map key IDs to PEM public keys."
        )

    verifier = LeaseVerifier.from_pem_mapping(public_keys)
    gate = SignedLeaseEntitlementGate(
        lease=_LeaseOnDisk(
            path=lease_path,
            verifier=verifier,
            claims=verifier.verify(token).claims,
            mtime_ns=lease_path.stat().st_mtime_ns,
        ),
        device_id=device_id,
        app_version=app_version,
        clock_skew_seconds=max(
            0, int(getattr(settings, "LICENSE_CLOCK_SKEW_SECONDS", 30))
        ),
    )
    gate.require("app.start")
    return gate
