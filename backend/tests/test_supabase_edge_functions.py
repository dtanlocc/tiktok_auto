"""Hai Edge Function duoc goi THAT, bang Node, roi lease duoc Python xac thuc.

⛔ DAY LA LAN CHAY THU DUY NHAT TRUOC KHI CHUNG CHAY TREN DU AN CUA KHACH.
Cach dung de thu la `supabase start`, nhung no can Docker: tren may nay Docker
Desktop co cai ma service `com.docker.service` o trang thai Stopped, va bat no
can quyen admin. Nen `handler_harness.ts` dung mot `Deno` gia va mot `fetch`
gia de chay chinh hai handler do, con bai test nay dung bang chung thiet bi
THAT (Ed25519 ky bang `cryptography`) va xac thuc lease tra ve bang
`LeaseVerifier` THAT.

⛔ CAI BAI TEST NAY KHONG KIEM DUOC, noi ro de khong ai tin qua: phan xet RPC
la do bai test dat san, nen toan bo logic plpgsql - khoa hang license, dem
thiet bi, chong phat lai nonce - KHONG duoc chay o day. Chung duoc kiem cu
phap boi `test_supabase_sql_is_parseable.py` va phai duoc kiem hanh vi bang
mot kich hoat that; xem docs/SUPABASE-KEY.md.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.security.license import LeaseVerifier

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HARNESS = _REPO_ROOT / "supabase" / "functions" / "_shared" / "handler_harness.ts"

_LEASE_SEED = bytes(range(64, 96))
_KEY_ID = "lease-dev-2026-01"
_PEPPER = "p" * 48
_GRANT_SECRET = "g" * 48
_BASE = "https://example.supabase.co/functions/v1"


def _node() -> str:
    found = shutil.which("node")
    if found is None:
        pytest.skip(
            "Khong tim thay 'node' nen khong chay duoc handler cua Edge "
            "Function. Cai Node >= 22 roi chay lai."
        )
    return found


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _lease_private_pem() -> str:
    return (
        Ed25519PrivateKey.from_private_bytes(_LEASE_SEED)
        .private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
        .decode("ascii")
    )


def _lease_public_pem() -> str:
    return (
        Ed25519PrivateKey.from_private_bytes(_LEASE_SEED)
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )


def _env(**overrides: str) -> dict[str, str]:
    base = {
        "TKAUTO_LICENSE_KEY_PEPPER": _PEPPER,
        "TKAUTO_LEASE_SIGNING_KEY_ID": _KEY_ID,
        "TKAUTO_LEASE_SIGNING_PRIVATE_KEY_PEM": _lease_private_pem(),
        "TKAUTO_MINIMUM_CLIENT_VERSION": "0.1.0",
        "TKAUTO_MINIMUM_BACKEND_VERSION": "0.1.0",
        "SUPABASE_URL": "https://example.supabase.co",
        "SUPABASE_SERVICE_ROLE_KEY": "service-role-key-for-the-test",
        # ⛔ `releases` va `artifacts` goi `requireDelivery`, nen thieu bi mat
        # nay la ca hai tra 500. `activate`/`lease` thi khong can no.
        "TKAUTO_DOWNLOAD_GRANT_SECRET": _GRANT_SECRET,
        "TKAUTO_RELEASE_BUCKET": "releases",
    }
    base.update(overrides)
    return base


def _run(job: dict) -> dict:
    result = subprocess.run(
        [_node(), str(_HARNESS)],
        input=json.dumps(job),
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(_REPO_ROOT),
    )
    if result.returncode != 0:
        pytest.fail(
            "handler_harness.ts khong chay duoc:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return json.loads(result.stdout)


class Device:
    """Mot thiet bi khach, voi khoa that va bang chung ky that."""

    def __init__(self, seed: bytes = b"") -> None:
        self.key = (
            Ed25519PrivateKey.from_private_bytes(seed)
            if seed
            else Ed25519PrivateKey.generate()
        )

    @property
    def public_pem(self) -> str:
        return (
            self.key.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode("ascii")
        )

    @property
    def device_id(self) -> str:
        raw = self.key.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )
        return "device_" + hashlib.sha256(raw).hexdigest()[:40]

    def proof(self, action: str, *, app_version: str = "0.1.11") -> dict:
        issued_at = int(time.time())
        nonce = secrets.token_urlsafe(24)
        canonical = (
            f"TKAUTO-{action}-v1\n{self.device_id}\n{issued_at}\n{nonce}\n{app_version}"
        ).encode("utf-8")
        return {
            "device_id": self.device_id,
            "issued_at": issued_at,
            "nonce": nonce,
            "signature": _b64url(self.key.sign(canonical)),
            "app_version": app_version,
        }


def _license_row(**overrides) -> dict:
    row = {
        "id": "lic_0123456789abcdef",
        "plan": "nhom",
        "features": ["accounts.manage", "upload.video", "group.hn01", "role.owner"],
        "max_accounts": 200,
        "max_tabs": 4,
        "channel": "stable",
        "expires_at": int(time.time()) + 30 * 86_400,
    }
    row.update(overrides)
    return row


def _activate_job(device: Device, *, rpc: dict, body_overrides: dict | None = None,
                  env_overrides: dict | None = None) -> dict:
    body = {
        **device.proof("ACTIVATE"),
        "license_key": "TKAUTO-ABCD-EFGH-IJKL-MNOP-QRST-UVWX",
        "device_public_key_pem": device.public_pem,
    }
    if body_overrides:
        body.update(body_overrides)
    return {
        "fn": "activate",
        "env": _env(**(env_overrides or {})),
        "request": {"url": f"{_BASE}/activate", "body": body},
        "rpc": rpc,
    }


def test_a_real_activation_returns_a_lease_the_python_verifier_accepts():
    device = Device()
    row = _license_row()
    result = _run(
        _activate_job(
            device,
            rpc={"tkauto_activate": [{"ok": True, "device_id": device.device_id, "license": row}]},
        )
    )
    assert result["status"] == 200, result
    body = result["body"]

    envelope = LeaseVerifier.from_pem_mapping({_KEY_ID: _lease_public_pem()}).verify(
        body["lease"]
    )
    claims = envelope.claims
    assert claims.license_id == row["id"]
    assert claims.device_id == device.device_id
    assert claims.max_accounts == 200
    assert claims.max_tabs == 4
    # `app.start` phai duoc them vao dau danh sach feature.
    assert claims.features[0] == "app.start"
    assert set(claims.features) == {"app.start", *row["features"]}
    # Group va role phai doc lai duoc qua thuoc tinh cua LeaseClaims.
    assert claims.group_id == "hn01"
    assert claims.role == "owner"
    assert body["expires_at"] == claims.expires_at
    # Launcher tu choi lease khi renew_after >= expires_at.
    assert body["renew_after"] < body["expires_at"]
    # Lease khong duoc cache o bat ky dau tren duong di.
    assert result["headers"]["cache-control"] == "no-store"

    claims_runtime_ok = claims.assert_runtime_valid(
        device_id=device.device_id, app_version="0.1.11", component="desktop"
    )
    assert claims_runtime_ok is None or claims_runtime_ok is not False


def test_the_key_digest_and_nonce_digest_sent_to_postgres_match_python():
    """⛔ Hai digest nay la hop dong giua TypeScript va Postgres.

    Digest key sai thi khong key nao tim thay duoc (401 cho moi khach). Digest
    nonce sai thi chong phat lai bam vao mot gia tri khac moi lan, tuc khong
    chong gi ca. Ca hai deu KHONG lam request that bai, nen chung se khong tu
    lo ra.
    """
    import hmac

    from control_plane.app.security import digest_license_key

    device = Device()
    row = _license_row()
    job = _activate_job(
        device,
        rpc={"tkauto_activate": [{"ok": True, "device_id": device.device_id, "license": row}]},
    )
    result = _run(job)
    assert result["status"] == 200, result

    (call,) = result["rpc_calls"]
    assert call["name"] == "tkauto_activate"
    args = call["args"]
    body = job["request"]["body"]

    assert args["p_key_digest"] == digest_license_key(body["license_key"], _PEPPER)
    expected_nonce = hashlib.sha256(
        f"activate:{body['device_id']}\n{body['issued_at']}\n{body['nonce']}".encode()
    ).hexdigest()
    assert args["p_nonce_digest"] == expected_nonce
    assert hmac.compare_digest(args["p_device_id"], device.device_id)
    assert args["p_rate_window"] == 60
    assert args["p_rate_limit"] == 10


def test_a_forged_device_proof_is_refused_before_postgres_is_touched():
    """⛔ Khong duoc goi RPC khi chu ky sai - no se dot nonce cua nguoi khac."""
    device = Device()
    other = Device()
    body = {
        **device.proof("ACTIVATE"),
        "license_key": "TKAUTO-ABCD-EFGH-IJKL-MNOP-QRST-UVWX",
        "device_public_key_pem": device.public_pem,
    }
    # Chu ky cua mot khoa khac, nhung device_id/khoa cong khai van cua device.
    canonical = (
        f"TKAUTO-ACTIVATE-v1\n{body['device_id']}\n{body['issued_at']}\n"
        f"{body['nonce']}\n{body['app_version']}"
    ).encode()
    body["signature"] = _b64url(other.key.sign(canonical))

    result = _run(
        {
            "fn": "activate",
            "env": _env(),
            "request": {"url": f"{_BASE}/activate", "body": body},
            "rpc": {},
        }
    )
    assert result["status"] == 401, result
    assert result["body"]["detail"] == "Device proof is invalid."
    assert result["rpc_calls"] == [], "Chu ky sai van goi vao Postgres."


def test_a_device_id_that_does_not_come_from_the_public_key_is_refused():
    device = Device()
    other = Device()
    result = _run(
        _activate_job(device, rpc={}, body_overrides={"device_id": other.device_id})
    )
    assert result["status"] == 400, result
    assert "does not match" in result["body"]["detail"]
    assert result["rpc_calls"] == []


def test_an_unknown_field_is_refused_instead_of_ignored():
    """`schemas.py` dat extra="forbid"; ban Edge Function phai giong."""
    device = Device()
    result = _run(_activate_job(device, rpc={}, body_overrides={"devide_id": "go sai"}))
    assert result["status"] == 422, result
    assert "devide_id" in result["body"]["detail"]
    assert result["rpc_calls"] == []


def test_an_old_client_is_told_to_upgrade_not_refused_as_unauthorized():
    device = Device()
    body = {
        **device.proof("ACTIVATE", app_version="0.0.9"),
        "license_key": "TKAUTO-ABCD-EFGH-IJKL-MNOP-QRST-UVWX",
        "device_public_key_pem": device.public_pem,
    }
    result = _run(
        {
            "fn": "activate",
            "env": _env(TKAUTO_MINIMUM_CLIENT_VERSION="0.1.0"),
            "request": {"url": f"{_BASE}/activate", "body": body},
            "rpc": {},
        }
    )
    assert result["status"] == 426, result
    assert result["rpc_calls"] == []


@pytest.mark.parametrize(
    ("code", "status"),
    [
        ("license_invalid", 401),
        ("license_revoked_or_expired", 403),
        ("device_conflict", 409),
        ("device_limit", 403),
        ("device_revoked", 403),
        ("nonce_replayed", 409),
        ("rate_limited", 429),
    ],
)
def test_every_postgres_verdict_maps_to_the_same_status_as_the_python_api(code, status):
    device = Device()
    result = _run(
        _activate_job(device, rpc={"tkauto_activate": [{"ok": False, "code": code}]})
    )
    assert result["status"] == status, (code, result)
    assert "lease" not in result["body"]


def test_an_unknown_verdict_code_fails_closed():
    """⛔ Ma la phai la tu choi, khong phai bo qua.

    Neu mot phien ban SQL sau nay tra ve mot ma ma TypeScript chua biet, hanh
    vi an toan duy nhat la tu choi. Mac dinh cho qua la mot cong mo.
    """
    device = Device()
    result = _run(
        _activate_job(device, rpc={"tkauto_activate": [{"ok": False, "code": "chua_biet"}]})
    )
    assert result["status"] == 403, result
    assert "lease" not in result["body"]


def test_an_expired_license_row_does_not_produce_a_lease():
    device = Device()
    result = _run(
        _activate_job(
            device,
            rpc={
                "tkauto_activate": [
                    {
                        "ok": True,
                        "device_id": device.device_id,
                        "license": _license_row(expires_at=int(time.time()) - 10),
                    }
                ]
            },
        )
    )
    assert result["status"] == 403, result
    assert "lease" not in result["body"]


def test_renew_verifies_the_signature_against_the_stored_key_not_a_supplied_one():
    """⛔ Diem khac quan trong nhat giua renew va activate.

    Neu renew nhan khoa cong khai tu request roi kiem chu ky bang chinh khoa
    do, bat ky ai cung gia han duoc lease cho bat ky device_id nao: ho ky bang
    khoa rieng cua ho va gui kem khoa cong khai cua ho. Bai test nay kiem rang
    khoa duoc dung la khoa DB tra ve.
    """
    real = Device()
    attacker = Device()
    row = _license_row()

    # Ke tan cong ky bang khoa cua minh, nhung khai device_id cua nguoi khac.
    body = attacker.proof("RENEW")
    body["device_id"] = real.device_id
    body["license_id"] = row["id"]
    canonical = (
        f"TKAUTO-RENEW-v1\n{real.device_id}\n{body['issued_at']}\n"
        f"{body['nonce']}\n{body['app_version']}"
    ).encode()
    body["signature"] = _b64url(attacker.key.sign(canonical))

    result = _run(
        {
            "fn": "lease",
            "env": _env(),
            "request": {"url": f"{_BASE}/lease/renew", "body": body},
            # DB tra ve khoa cong khai THAT cua thiet bi do.
            "rpc": {
                "tkauto_renew_device": [{"ok": True, "public_key_pem": real.public_pem}],
                "tkauto_renew": [
                    {"ok": True, "device_id": real.device_id, "license": row}
                ],
            },
        }
    )
    assert result["status"] == 401, result
    assert result["body"]["detail"] == "Device proof is invalid."
    # Chi duoc goi lan LAY KHOA; khong duoc goi lan gia han (lan do chiem nonce).
    assert [call["name"] for call in result["rpc_calls"]] == ["tkauto_renew_device"]


def test_renew_succeeds_for_the_real_device_and_the_lease_verifies():
    device = Device()
    row = _license_row()
    body = {**device.proof("RENEW"), "license_id": row["id"]}
    result = _run(
        {
            "fn": "lease",
            "env": _env(),
            "request": {"url": f"{_BASE}/lease/renew", "body": body},
            "rpc": {
                "tkauto_renew_device": [{"ok": True, "public_key_pem": device.public_pem}],
                "tkauto_renew": [
                    {"ok": True, "device_id": device.device_id, "license": row}
                ],
            },
        }
    )
    assert result["status"] == 200, result
    envelope = LeaseVerifier.from_pem_mapping({_KEY_ID: _lease_public_pem()}).verify(
        result["body"]["lease"]
    )
    assert envelope.claims.device_id == device.device_id
    assert [call["name"] for call in result["rpc_calls"]] == [
        "tkauto_renew_device",
        "tkauto_renew",
    ]
    nonce_digest = hashlib.sha256(
        f"renew:{row['id']}:{device.device_id}\n{body['issued_at']}\n{body['nonce']}".encode()
    ).hexdigest()
    assert result["rpc_calls"][1]["args"]["p_nonce_digest"] == nonce_digest


def test_the_lease_function_only_answers_on_the_renew_path():
    """Function `lease` nhan moi duong con; chi `/renew` duoc xu ly."""
    device = Device()
    body = {**device.proof("RENEW"), "license_id": "lic_0123456789abcdef"}
    for path in ("/lease", "/lease/", "/lease/bat-ky-gi"):
        result = _run(
            {
                "fn": "lease",
                "env": _env(),
                "request": {"url": f"https://example.supabase.co/functions/v1{path}", "body": body},
                "rpc": {},
            }
        )
        assert result["status"] == 404, (path, result)
        assert result["rpc_calls"] == []


def test_a_get_request_is_refused():
    result = _run(
        {
            "fn": "activate",
            "env": _env(),
            "request": {"url": f"{_BASE}/activate", "method": "GET"},
            "rpc": {},
        }
    )
    assert result["status"] == 405, result


@pytest.mark.parametrize(
    ("overrides", "why"),
    [
        ({"TKAUTO_LICENSE_KEY_PEPPER": "ngan-qua"}, "pepper ngan"),
        ({"TKAUTO_LEASE_TTL_SECONDS": "60"}, "ttl duoi 300"),
        ({"TKAUTO_LEASE_TTL_SECONDS": "999999"}, "ttl tren 7 ngay"),
        ({"TKAUTO_LEASE_SIGNING_KEY_ID": "x"}, "key id sai dinh dang"),
    ],
)
def test_a_weak_configuration_stops_the_function_instead_of_weakening_it(overrides, why):
    """⛔ Sai cau hinh phai lam function CHET, khong phai chay yeu hon.

    Mot pepper 8 ky tu hay mot TTL mot nam khong lam request nao that bai -
    no chi am tham ha thap bao mat, va se khong ai phat hien.
    """
    device = Device()
    result = _run(_activate_job(device, rpc={}, env_overrides=overrides))
    assert result["status"] == 500, (why, result)
    # Thong bao khong duoc noi ten bien moi truong hay do dai secret.
    assert result["body"]["detail"] == "Server is misconfigured."
    assert result["rpc_calls"] == []


def test_a_database_outage_is_reported_as_unavailable_not_as_a_refusal():
    """⛔ 503, khong phai 401/403.

    `lib.rs:is_authoritative_entitlement_denial` phan biet "may chu noi khong"
    voi "may chu khong tra loi duoc": mot su co DB tra ve 403 se bi client hieu
    la license bi thu hoi.
    """
    device = Device()
    # Khong dat phan xet nao -> harness tra ve HTTP 500 cho lan goi RPC.
    result = _run(_activate_job(device, rpc={}))
    assert result["status"] == 503, result
    assert result["body"]["detail"] == "License service is unavailable."


def _release_check_body(device: Device, license_id: str, *, current_version: str = "0.1.11",
                        component: str = "backend", target: str = "windows-x86_64") -> dict:
    """Bang chung ky dung action ma launcher dung: RELEASE:<component>:<ver>:<target>."""
    issued_at = int(time.time())
    nonce = secrets.token_urlsafe(24)
    app_version = "0.1.11"
    action = f"RELEASE:{component}:{current_version}:{target}"
    canonical = (
        f"TKAUTO-{action}-v1\n{device.device_id}\n{issued_at}\n{nonce}\n{app_version}"
    ).encode("utf-8")
    return {
        "device_id": device.device_id,
        "issued_at": issued_at,
        "nonce": nonce,
        "signature": _b64url(device.key.sign(canonical)),
        "app_version": app_version,
        "license_id": license_id,
        "component": component,
        "current_version": current_version,
        "target": target,
    }


def test_the_release_check_answers_so_the_app_can_actually_start():
    """⛔ Thieu endpoint nay la app dung o man hinh loi.

    `SecureBootstrap.tsx:62` goi `await invoke('check_backend_update')` KHONG
    co try/catch, ngay truoc `launch_backend`. Mot 404 o day lam phase thanh
    'error' va backend khong bao gio chay - du kich hoat da thanh cong. Nen
    bai test nay khong kiem mot tinh nang, no kiem dieu kien de app chay.
    """
    device = Device()
    row = _license_row()
    result = _run(
        {
            "fn": "releases",
            "env": _env(),
            "request": {
                "url": f"{_BASE}/releases/check",
                "body": _release_check_body(device, row["id"]),
            },
            "rpc": {
                "tkauto_renew_device": [{"ok": True, "public_key_pem": device.public_pem}],
                "tkauto_release_check": [{"ok": True}],
            },
        }
    )
    assert result["status"] == 200, result
    # `ReleaseCheckResponse` trong lib.rs: ba truong con lai la Option<_>, va
    # launcher thoat ngay o `if !response.update_available`.
    assert result["body"] == {"update_available": False}
    assert [call["name"] for call in result["rpc_calls"]] == [
        "tkauto_renew_device",
        "tkauto_release_check",
    ]


def test_the_release_check_still_verifies_the_device_proof():
    """Mot endpoint tra loi cho bat ky ai se duoc sao chep lam mau cho cai sau."""
    device = Device()
    attacker = Device()
    row = _license_row()
    body = _release_check_body(device, row["id"])
    # Chu ky cua ke khac, device_id van la cua device.
    action = f"RELEASE:backend:0.1.11:windows-x86_64"
    canonical = (
        f"TKAUTO-{action}-v1\n{device.device_id}\n{body['issued_at']}\n"
        f"{body['nonce']}\n{body['app_version']}"
    ).encode()
    body["signature"] = _b64url(attacker.key.sign(canonical))

    result = _run(
        {
            "fn": "releases",
            "env": _env(),
            "request": {"url": f"{_BASE}/releases/check", "body": body},
            "rpc": {"tkauto_renew_device": [{"ok": True, "public_key_pem": device.public_pem}]},
        }
    )
    assert result["status"] == 401, result
    # Chua chiem nonce: chi goi lan lay khoa.
    assert [call["name"] for call in result["rpc_calls"]] == ["tkauto_renew_device"]


def test_the_release_check_nonce_context_matches_the_python_api():
    device = Device()
    row = _license_row()
    body = _release_check_body(device, row["id"])
    result = _run(
        {
            "fn": "releases",
            "env": _env(),
            "request": {"url": f"{_BASE}/releases/check", "body": body},
            "rpc": {
                "tkauto_renew_device": [{"ok": True, "public_key_pem": device.public_pem}],
                "tkauto_release_check": [{"ok": True}],
            },
        }
    )
    assert result["status"] == 200, result
    expected = hashlib.sha256(
        f"release:{row['id']}:{device.device_id}:backend\n"
        f"{body['issued_at']}\n{body['nonce']}".encode()
    ).hexdigest()
    assert result["rpc_calls"][1]["args"]["p_nonce_digest"] == expected


def test_a_revoked_device_cannot_even_check_for_updates():
    device = Device()
    result = _run(
        {
            "fn": "releases",
            "env": _env(),
            "request": {
                "url": f"{_BASE}/releases/check",
                "body": _release_check_body(device, "lic_0123456789abcdef"),
            },
            "rpc": {"tkauto_renew_device": [{"ok": False, "code": "device_invalid"}]},
        }
    )
    assert result["status"] == 403, result


def test_an_unknown_component_or_target_is_refused():
    device = Device()
    for overrides in ({"component": "frontend"}, {"target": "linux-x86_64"}):
        body = _release_check_body(device, "lic_0123456789abcdef")
        body.update(overrides)
        result = _run(
            {
                "fn": "releases",
                "env": _env(),
                "request": {"url": f"{_BASE}/releases/check", "body": body},
                "rpc": {},
            }
        )
        assert result["status"] == 422, (overrides, result)
        assert result["rpc_calls"] == []


def test_the_lease_key_secret_works_as_a_single_line_without_pem_armor():
    """⛔ Vi secret nhieu dong la cho de sai nhat khi dat bang tay.

    `supabase secrets set` / dashboard / --env-file xu ly gia tri nhieu dong
    khac nhau, va mot PEM bi mat dau dong la mot function CHET luc deploy chu
    khong phai luc test. `importLeasePrivateKey` go dau PEM va moi khoang
    trang truoc khi base64-decode, nen dan nguyen phan base64 tren MOT dong
    cung chay - bai test nay la thu chung minh dieu do, de huong dan dam khuyen
    dung cach do.
    """
    import re

    armorless = re.sub(
        r"-----[A-Z ]+-----|\s+", "", _lease_private_pem()
    )
    assert "\n" not in armorless and "-----" not in armorless

    device = Device()
    row = _license_row()
    result = _run(
        _activate_job(
            device,
            rpc={"tkauto_activate": [{"ok": True, "device_id": device.device_id, "license": row}]},
            env_overrides={"TKAUTO_LEASE_SIGNING_PRIVATE_KEY_PEM": armorless},
        )
    )
    assert result["status"] == 200, result
    # Va lease do van phai la lease ky bang DUNG khoa do.
    envelope = LeaseVerifier.from_pem_mapping({_KEY_ID: _lease_public_pem()}).verify(
        result["body"]["lease"]
    )
    assert envelope.claims.device_id == device.device_id


def test_every_deployed_function_has_verify_jwt_turned_off_in_config():
    """⛔ Mot typo o `supabase/config.toml` lam MOI kich hoat nhan 401.

    Mac dinh Supabase doi header `Authorization: Bearer <anon key>` o moi Edge
    Function, con launcher khong gui header nao (`lib.rs` chi `.json()` roi
    `.send()`). Nen thieu `verify_jwt = false` cho mot function la function do
    tra 401 cho moi request - va loi do khong lo ra o bat ky bai test nao khac
    o day, vi harness goi handler TRUC TIEP chu khong qua cong cua Supabase.

    Bai test nay cung chan chieu nguoc lai: them mot function moi vao
    `supabase/functions/` ma quen khai trong config.
    """
    import tomllib

    config_path = _REPO_ROOT / "supabase" / "config.toml"
    config = tomllib.loads(config_path.read_text(encoding="utf-8"))

    functions_dir = _REPO_ROOT / "supabase" / "functions"
    on_disk = {
        item.name
        for item in functions_dir.iterdir()
        if item.is_dir() and not item.name.startswith("_")
    }
    assert on_disk, f"Khong thay function nao trong {functions_dir}."

    declared = set(config.get("functions", {}))
    assert on_disk == declared, (
        "Thu muc function va cau hinh lech nhau.\n"
        f"  co tren dia ma khong khai: {sorted(on_disk - declared) or 'khong'}\n"
        f"  khai ma khong co tren dia: {sorted(declared - on_disk) or 'khong'}"
    )
    for name in sorted(on_disk):
        assert config["functions"][name].get("verify_jwt") is False, (
            f"[functions.{name}] thieu `verify_jwt = false` trong config.toml; "
            "function do se tra 401 cho moi request cua launcher."
        )


def test_every_function_directory_holds_an_index_file():
    functions_dir = _REPO_ROOT / "supabase" / "functions"
    for item in sorted(functions_dir.iterdir()):
        if not item.is_dir() or item.name.startswith("_"):
            continue
        assert (item / "index.ts").is_file(), f"{item.name} khong co index.ts"


# =============================================================================
# Phan phat ban phat hanh: releases tra manifest + ve, artifacts doi ve lay file
# =============================================================================

def _candidate(version: str, *, min_launcher: str = "0.1.0", artifact_id: str | None = None) -> dict:
    flat = version.replace(".", "").replace("-", "")
    return {
        "id": artifact_id or f"artifact_{flat}",
        "version": version,
        "minimum_launcher_version": min_launcher,
        # Chuoi gia: bai test khong kiem noi dung manifest (no duoc ky o may
        # operator bang ReleaseManifestSigner, co test rieng), chi kiem rang
        # function phat lai DUNG chuoi do.
        "signed_manifest": f"manifest-da-ky-cho-{version}",
    }


def _release_job(device: Device, candidates: list[dict], *, current="0.0.0",
                 app_version="0.1.11", env_overrides: dict | None = None) -> dict:
    issued_at = int(time.time())
    nonce = secrets.token_urlsafe(24)
    action = f"RELEASE:backend:{current}:windows-x86_64"
    canonical = (
        f"TKAUTO-{action}-v1\n{device.device_id}\n{issued_at}\n{nonce}\n{app_version}"
    ).encode()
    return {
        "fn": "releases",
        "env": _env(**(env_overrides or {})),
        "request": {
            "url": f"{_BASE}/releases/check",
            "body": {
                "device_id": device.device_id,
                "issued_at": issued_at,
                "nonce": nonce,
                "signature": _b64url(device.key.sign(canonical)),
                "app_version": app_version,
                "license_id": "lic_0123456789abcdef",
                "component": "backend",
                "current_version": current,
                "target": "windows-x86_64",
            },
        },
        "rpc": {
            "tkauto_renew_device": [{"ok": True, "public_key_pem": device.public_pem}],
            "tkauto_release_check": [{"ok": True, "releases": candidates}],
        },
    }


def test_a_newer_release_comes_back_with_a_manifest_and_a_usable_grant():
    """⛔ Khong co duong nay thi ban licensed khong bao gio khoi dong duoc.

    `check_backend_update` la duong DUY NHAT launcher lay backend binary
    (lib.rs:833). Truoc 13/10/2026 function nay luon tra
    `update_available: false`, nen `launch_backend` chet voi "Backend binary
    chua duoc cai." ngay lan chay dau - khong phai sau khi het han.
    """
    from control_plane.app.security import verify_download_grant

    device = Device()
    result = _run(_release_job(device, [_candidate("0.1.12")]))
    assert result["status"] == 200, result
    body = result["body"]
    assert body["update_available"] is True
    assert body["signed_manifest"] == "manifest-da-ky-cho-0.1.12"

    # Ve phai dung duoc o ban Python, va phai rang buoc dung thiet bi.
    claims = verify_download_grant(
        _GRANT_SECRET, body["download_grant"], "artifact_0112"
    )
    assert claims["device_id"] == device.device_id
    assert claims["expires_at"] == body["grant_expires_at"]
    assert body["grant_expires_at"] > int(time.time())


def test_the_highest_version_wins_and_a_release_beats_its_own_prerelease():
    device = Device()
    result = _run(
        _release_job(
            device,
            [
                _candidate("0.1.12"),
                _candidate("0.2.0"),
                _candidate("0.2.0-rc.1"),
                _candidate("0.1.13"),
            ],
        )
    )
    assert result["status"] == 200, result
    assert result["body"]["signed_manifest"] == "manifest-da-ky-cho-0.2.0"


def test_a_release_not_newer_than_the_installed_one_is_not_offered():
    device = Device()
    result = _run(_release_job(device, [_candidate("0.1.11")], current="0.1.11"))
    assert result["status"] == 200, result
    assert result["body"] == {"update_available": False}


def test_a_release_that_needs_a_newer_launcher_is_skipped():
    """Giong ban Python: `app_version >= minimum_launcher_version` moi duoc."""
    device = Device()
    result = _run(
        _release_job(
            device,
            [_candidate("0.2.0", min_launcher="9.0.0"), _candidate("0.1.12")],
            app_version="0.1.11",
        )
    )
    assert result["status"] == 200, result
    assert result["body"]["signed_manifest"] == "manifest-da-ky-cho-0.1.12"


def test_no_candidate_at_all_is_a_clean_no_update():
    device = Device()
    result = _run(_release_job(device, []))
    assert result["status"] == 200, result
    assert result["body"] == {"update_available": False}


def test_a_malformed_version_in_the_registry_is_reported_not_crashed():
    device = Device()
    bad = _candidate("0.1.12")
    bad["version"] = "khong-phai-semver"
    result = _run(_release_job(device, [bad]))
    assert result["status"] == 422, result
    assert result["body"]["detail"] == "Release version is invalid."


def test_release_delivery_refuses_to_run_without_a_usable_grant_secret():
    """⛔ Mot HMAC voi khoa rong la mot ve ai cung ky duoc."""
    device = Device()
    for value in ("", "ngan-qua"):
        result = _run(
            _release_job(device, [_candidate("0.1.12")],
                         env_overrides={"TKAUTO_DOWNLOAD_GRANT_SECRET": value})
        )
        assert result["status"] == 500, (value, result)
        assert result["body"]["detail"] == "Server is misconfigured."
        assert result["rpc_calls"] == []


_SIGNED_OBJECT = {"ok": True, "storage_object": "backend-0.1.12.exe",
                  "sha256": "0" * 64, "size_bytes": 41_000_000}


def _artifact_job(artifact_id: str, token: str, *, rpc: dict,
                  storage="mac-dinh", path: str | None = None,
                  method: str = "GET") -> dict:
    url = (f"{_BASE}/artifacts/{artifact_id}/download" if path is None
           else f"https://example.supabase.co/functions/v1{path}")
    job = {
        "fn": "artifacts",
        "env": _env(),
        "request": {
            "url": url,
            "method": method,
            "headers": {"authorization": f"Bearer {token}"},
        },
        "rpc": rpc,
    }
    job["storage"] = (
        {"signedURL": "/object/sign/releases/backend-0.1.12.exe?token=abc"}
        if storage == "mac-dinh" else storage
    )
    return job


def _grant(device: Device, artifact_id: str, ttl: int = 300) -> str:
    from control_plane.app.security import issue_download_grant

    token, _ = issue_download_grant(
        _GRANT_SECRET, artifact_id=artifact_id, device_id=device.device_id,
        ttl_seconds=ttl,
    )
    return token


def test_a_valid_grant_redirects_to_a_short_lived_storage_url():
    device = Device()
    token = _grant(device, "artifact_0112")
    result = _run(
        _artifact_job("artifact_0112", token,
                      rpc={"tkauto_claim_download": [_SIGNED_OBJECT]})
    )
    assert result["status"] == 302, result
    assert result["headers"]["location"].startswith(
        "https://example.supabase.co/storage/v1/object/sign/releases/"
    )
    assert result["headers"]["cache-control"] == "no-store"
    # Phai ky dung object ma DB chi ra, khong phai artifact_id.
    assert result["storage_calls"] == [
        {"path": "releases/backend-0.1.12.exe", "body": {"expiresIn": 300}}
    ]
    # Dau ve gui len DB phai la sha256 cua chinh ve.
    assert result["rpc_calls"][0]["args"]["p_grant_digest"] == hashlib.sha256(
        token.encode()
    ).hexdigest()


def test_a_grant_for_a_different_artifact_is_refused():
    """⛔ Khong co phep kiem nay thi mot ve hop le tai duoc MOI file."""
    device = Device()
    token = _grant(device, "artifact_khac")
    result = _run(_artifact_job("artifact_0112", token, rpc={}))
    assert result["status"] == 401, result
    assert result["rpc_calls"] == [], "Ve sai artifact van goi vao DB"


@pytest.mark.parametrize(
    ("token", "why"),
    [("", "khong co ve"), ("rac", "ve rac"), ("a.b.c", "ba phan")],
)
def test_a_missing_or_malformed_grant_is_refused_before_the_database(token, why):
    result = _run(_artifact_job("artifact_0112", token, rpc={}))
    assert result["status"] == 401, (why, result)
    assert result["rpc_calls"] == []


def test_an_expired_grant_is_refused():
    device = Device()
    token = _grant(device, "artifact_0112", ttl=-10)
    result = _run(_artifact_job("artifact_0112", token, rpc={}))
    assert result["status"] == 401, result
    assert result["rpc_calls"] == []


def test_a_grant_already_used_cannot_be_used_again():
    device = Device()
    token = _grant(device, "artifact_0112")
    result = _run(
        _artifact_job("artifact_0112", token,
                      rpc={"tkauto_claim_download": [{"ok": False, "code": "grant_already_used"}]})
    )
    assert result["status"] == 401, result
    # Khong noi ro la "da dung" hay "khong ton tai".
    assert result["body"]["detail"] == "Download authorization is invalid."
    assert result["storage_calls"] == []


def test_a_revoked_device_cannot_download_even_with_a_fresh_grant():
    device = Device()
    token = _grant(device, "artifact_0112")
    result = _run(
        _artifact_job("artifact_0112", token,
                      rpc={"tkauto_claim_download": [{"ok": False, "code": "device_invalid"}]})
    )
    assert result["status"] == 403, result
    assert result["storage_calls"] == []


def test_a_storage_outage_is_reported_as_unavailable_not_as_a_refusal():
    device = Device()
    token = _grant(device, "artifact_0112")
    result = _run(
        _artifact_job("artifact_0112", token,
                      rpc={"tkauto_claim_download": [_SIGNED_OBJECT]},
                      storage=None)
    )
    assert result["status"] == 503, result
    assert result["body"]["detail"] == "Artifact storage is unavailable."


@pytest.mark.parametrize(
    "path",
    [
        "/artifacts",
        "/artifacts/download",
        "/artifacts/artifact_0112",
        "/artifacts/artifact_0112/khac",
        "/artifacts/a/b/download",
    ],
)
def test_only_the_exact_download_path_is_answered(path):
    device = Device()
    token = _grant(device, "artifact_0112")
    result = _run(_artifact_job("artifact_0112", token, rpc={}, path=path))
    assert result["status"] in (401, 404), (path, result)
    assert result["rpc_calls"] == []
    assert result["storage_calls"] == []


def test_a_post_to_the_artifact_endpoint_is_refused():
    device = Device()
    token = _grant(device, "artifact_0112")
    result = _run(_artifact_job("artifact_0112", token, rpc={}, method="POST"))
    assert result["status"] == 405, result
