"""Ve tai do ban TypeScript phat phai la ve ban Python nhan, va nguoc lai.

⛔ VA O DAY HAI BAN CAI CHAM NHAU THAT, khac lease. Lease chi do mot ban ky
(TypeScript) va mot ban doc (Rust o may khach), con ve tai thi:
  - `releases` (TypeScript) phat ve;
  - `artifacts` (TypeScript) kiem lai ve;
  - `control_plane` (Python) lam ca hai, cho ai dung duong tunnel.
Mot lech byte trong JSON canonical nghia la MOI lan tai deu 401 - va no chi lo
ra khi khach bam cap nhat, tren may khach. Nen bai test nay do CA HAI CHIEU:
Python phat -> TypeScript nhan, va TypeScript phat -> Python nhan.
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from control_plane.app.security import (
    issue_download_grant,
    verify_download_grant,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER = _REPO_ROOT / "supabase" / "functions" / "_shared" / "parity_runner.ts"

_SECRET = "s" * 48
_ARTIFACT = "artifact_0123456789abcdef0123456789abcdef"
_DEVICE = "device_" + "ab" * 20


def _run_ops(operations: list[dict]) -> list:
    node = shutil.which("node")
    if node is None:
        pytest.skip(
            "Khong tim thay 'node' nen khong do duoc ve tai giua hai ban. "
            "Cai Node >= 22 roi chay lai."
        )
    result = subprocess.run(
        [node, str(_RUNNER)],
        input=json.dumps(operations),
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(_REPO_ROOT),
    )
    if result.returncode != 0:
        pytest.fail(
            "parity_runner.ts khong chay duoc:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return json.loads(result.stdout)


def test_a_grant_python_issued_is_accepted_by_typescript():
    token, expires_at = issue_download_grant(
        _SECRET, artifact_id=_ARTIFACT, device_id=_DEVICE, ttl_seconds=300
    )
    (claims,) = _run_ops(
        [
            {
                "op": "verify_grant",
                "secret": _SECRET,
                "token": token,
                "artifact_id": _ARTIFACT,
            }
        ]
    )
    assert claims is not None, "Ban TypeScript tu choi ve do Python phat."
    assert claims["artifact_id"] == _ARTIFACT
    assert claims["device_id"] == _DEVICE
    assert claims["expires_at"] == expires_at
    assert claims["v"] == 1


def test_a_grant_typescript_issued_is_accepted_by_python():
    (issued,) = _run_ops(
        [
            {
                "op": "issue_grant",
                "secret": _SECRET,
                "artifact_id": _ARTIFACT,
                "device_id": _DEVICE,
                "ttl": 300,
            }
        ]
    )
    claims = verify_download_grant(_SECRET, issued["token"], _ARTIFACT)
    assert claims["artifact_id"] == _ARTIFACT
    assert claims["device_id"] == _DEVICE
    assert claims["expires_at"] == issued["expires_at"]
    assert claims["v"] == 1


def test_the_two_implementations_produce_the_same_payload_bytes():
    """⛔ Khong chi "ca hai chap nhan", ma CUNG MOT chuoi payload.

    Hai ban co the cung chap nhan nhau ma van serialize khac nhau neu ca hai
    deu de dai o cho nao. So sanh phan payload (truoc dau cham) loai bo nonce
    va expires_at - hai thu doi theo tung lan phat.
    """
    now = int(time.time())
    token, _ = issue_download_grant(
        _SECRET, artifact_id=_ARTIFACT, device_id=_DEVICE, ttl_seconds=300
    )
    (issued,) = _run_ops(
        [
            {
                "op": "issue_grant",
                "secret": _SECRET,
                "artifact_id": _ARTIFACT,
                "device_id": _DEVICE,
                "ttl": 300,
            }
        ]
    )

    def shape(value: str) -> list[str]:
        payload = value.split(".")[0]
        raw = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        # Thu tu khoa trong chuoi tho la phan phai khop.
        text = raw.decode("ascii")
        assert text.startswith("{") and text.endswith("}")
        return [item.split(":")[0] for item in text[1:-1].split(",")]

    assert shape(token) == shape(issued["token"])
    assert shape(token) == ['"artifact_id"', '"device_id"', '"expires_at"', '"nonce"', '"v"']
    assert now <= issued["expires_at"] <= now + 301


def _flip_a_signature_byte(signature: str) -> str:
    """Doi THAT noi dung chu ky, bang cach dao mot byte roi ma hoa lai.

    ⛔ DOI MOT KY TU BASE64URL O CUOI THI KHONG CHAC DOI DUOC GI, va bai test
    nay da flaky vi dung cach do: HMAC-SHA256 dai 32 byte = 256 bit, ma hoa
    thanh 43 ky tu base64url = 258 bit, nen ky tu cuoi chi mang 2 bit co nghia
    va 4 bit con lai la dem. Doi ky tu cuoi co khoang 1/4 kha nang ra DUNG cung
    chuoi byte - luc do chu ky van hop le va bai test "chu ky bi sua phai bi tu
    choi" that bai mot cach ngau nhien. Da gap trong mot lan chay toan bo bo
    test, trong khi chay rieng thi xanh.
    """
    raw = bytearray(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)))
    raw[0] ^= 0x01
    return base64.urlsafe_b64encode(bytes(raw)).rstrip(b"=").decode("ascii")


def test_typescript_refuses_a_tampered_grant():
    token, _ = issue_download_grant(
        _SECRET, artifact_id=_ARTIFACT, device_id=_DEVICE, ttl_seconds=300
    )
    payload, signature = token.split(".")
    cases = {
        "chu ky bi dao mot byte": f"{payload}.{_flip_a_signature_byte(signature)}",
        "khoa bi mat khac": token,
        "artifact_id khac": token,
        "thieu dau cham": payload,
        "ba phan": f"{payload}.{signature}.x",
        "rong": "",
    }
    ops = [
        {"op": "verify_grant", "secret": _SECRET, "token": cases["chu ky bi dao mot byte"], "artifact_id": _ARTIFACT},
        {"op": "verify_grant", "secret": "k" * 48, "token": cases["khoa bi mat khac"], "artifact_id": _ARTIFACT},
        {"op": "verify_grant", "secret": _SECRET, "token": cases["artifact_id khac"], "artifact_id": "artifact_khac"},
        {"op": "verify_grant", "secret": _SECRET, "token": cases["thieu dau cham"], "artifact_id": _ARTIFACT},
        {"op": "verify_grant", "secret": _SECRET, "token": cases["ba phan"], "artifact_id": _ARTIFACT},
        {"op": "verify_grant", "secret": _SECRET, "token": cases["rong"], "artifact_id": _ARTIFACT},
    ]
    results = _run_ops(ops)
    for name, verdict in zip(cases, results, strict=True):
        assert verdict is None, f"{name}: ban TypeScript nhan mot ve khong duoc nhan"


def test_python_refuses_the_same_tampered_grants():
    """Hai ban phai tu choi CUNG nhung thu nhu nhau, khong chi tu choi gi do."""
    token, _ = issue_download_grant(
        _SECRET, artifact_id=_ARTIFACT, device_id=_DEVICE, ttl_seconds=300
    )
    payload, signature = token.split(".")
    bad = [
        (_SECRET, f"{payload}.{_flip_a_signature_byte(signature)}", _ARTIFACT),
        ("k" * 48, token, _ARTIFACT),
        (_SECRET, token, "artifact_khac"),
        (_SECRET, payload, _ARTIFACT),
        (_SECRET, f"{payload}.{signature}.x", _ARTIFACT),
        (_SECRET, "", _ARTIFACT),
    ]
    for secret, value, artifact in bad:
        with pytest.raises(HTTPException) as info:
            verify_download_grant(secret, value, artifact)
        assert info.value.status_code == 401


def test_an_expired_grant_is_refused_by_typescript():
    token, _ = issue_download_grant(
        _SECRET, artifact_id=_ARTIFACT, device_id=_DEVICE, ttl_seconds=-10
    )
    (verdict,) = _run_ops(
        [{"op": "verify_grant", "secret": _SECRET, "token": token, "artifact_id": _ARTIFACT}]
    )
    assert verdict is None


def test_an_extra_claim_is_refused_not_ignored():
    """⛔ Python kiem `set(claims) != {...}`; mot khoa LA phai bi tu choi.

    De lot thi mot ve co them truong la van qua, va truong do co the mang y
    nghia o mot ban sau - tuc mot ve cu bong dung co quyen moi.
    """
    import base64
    import hashlib
    import hmac

    claims = {
        "artifact_id": _ARTIFACT,
        "device_id": _DEVICE,
        "expires_at": int(time.time()) + 300,
        "nonce": "x" * 24,
        "v": 1,
        "them": "mot truong la",
    }
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
        )
        .rstrip(b"=")
        .decode("ascii")
    )
    signature = hmac.new(_SECRET.encode(), payload.encode("ascii"), hashlib.sha256).digest()
    token = f"{payload}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode('ascii')}"

    (verdict,) = _run_ops(
        [{"op": "verify_grant", "secret": _SECRET, "token": token, "artifact_id": _ARTIFACT}]
    )
    assert verdict is None, "Ban TypeScript bo qua mot truong la trong ve"
    with pytest.raises(HTTPException):
        verify_download_grant(_SECRET, token, _ARTIFACT)


def test_the_grant_digest_matches_python():
    """Dau ve (de chong dung lai) phai giong nhau, khong thi chong duoc gi."""
    import hashlib

    token, _ = issue_download_grant(
        _SECRET, artifact_id=_ARTIFACT, device_id=_DEVICE, ttl_seconds=300
    )
    (digest,) = _run_ops([{"op": "grant_digest", "token": token}])
    assert digest == hashlib.sha256(token.encode("utf-8")).hexdigest()
