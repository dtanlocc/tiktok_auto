"""URL R2 do TypeScript ky phai y het ban Python ky.

⛔ VI SAO BAI TEST NAY TON TAI. Function `artifacts` (TypeScript) ky URL tai
cho khach, con script phat hanh (Python) ky request PUT de day file len. SigV4
la mot chuoi bam long nhau - canonical request, string-to-sign, roi bon vong
HMAC - va lech MOT dau xuong dong o bat ky tang nao cung ra chu ky khac. R2 tra
ve 403 ma khong noi gi ve nguyen nhan, nen loi do khong lo ra o deploy: no lo
ra khi khach bam cap nhat, tren may khach.

So sanh ca hai tang, khong chi ket qua cuoi: canonical request tho (de biet
lech o dau neu lech) va URL da ky (de biet co lech khong).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER = _REPO_ROOT / "supabase" / "functions" / "_shared" / "parity_runner.ts"
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import r2_sigv4  # noqa: E402

_CONFIG = {
    "accountId": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
    "bucket": "releases",
    "accessKeyId": "AKIAEXAMPLEKEYID0000",
    "secretAccessKey": "wJalrXUtnFEMI-K7MDENG-bPxRfiCYEXAMPLEKEY",
}
_AMZ_DATE = "20261013T120000Z"


def _run_ops(operations: list[dict]) -> list:
    node = shutil.which("node")
    if node is None:
        pytest.skip(
            "Khong tim thay 'node' nen khong do duoc chu ky R2 giua hai ban. "
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


def _python_presign(key: str, expires: int = 300, amz_date: str = _AMZ_DATE) -> str:
    return r2_sigv4.presign_get(
        account_id=_CONFIG["accountId"],
        bucket=_CONFIG["bucket"],
        key=key,
        access_key_id=_CONFIG["accessKeyId"],
        secret_access_key=_CONFIG["secretAccessKey"],
        expires_seconds=expires,
        amz_date=amz_date,
    )


_KEYS = [
    "backend-0.1.12-windows-x86_64.exe",
    "backend-1.0.0-rc.1-windows-x86_64.exe",
    # ⛔ Nhung ky tu ma `encodeURIComponent` cua JS BO QUA: !'()* . Neu ban TS
    # dung ham do thay vi escape tay thi dung may truong hop nay se lech.
    "ban (thu nghiem)!'*.exe",
    "co dau cach va +cong.exe",
    "duong/long/nhau.exe",
    "100%-xong.exe",
]


@pytest.mark.parametrize("key", _KEYS, ids=lambda item: item[:28])
def test_the_presigned_url_matches_python_byte_for_byte(key):
    (from_node,) = _run_ops(
        [
            {
                "op": "r2_presign",
                "config": _CONFIG,
                "key": key,
                "expires": 300,
                "amz_date": _AMZ_DATE,
            }
        ]
    )
    assert from_node == _python_presign(key)


@pytest.mark.parametrize("key", _KEYS, ids=lambda item: item[:28])
def test_the_canonical_request_matches_python_byte_for_byte(key):
    """So ca tang trung gian, de khi lech thi biet lech o dau."""
    (from_node,) = _run_ops(
        [
            {
                "op": "r2_canonical",
                "config": _CONFIG,
                "key": key,
                "expires": 300,
                "amz_date": _AMZ_DATE,
            }
        ]
    )
    datestamp = _AMZ_DATE[:8]
    scope = f"{datestamp}/auto/s3/aws4_request"
    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{_CONFIG['accessKeyId']}/{scope}",
        "X-Amz-Date": _AMZ_DATE,
        "X-Amz-Expires": "300",
        "X-Amz-SignedHeaders": "host",
    }
    canonical_query = "&".join(
        f"{r2_sigv4._quote(name)}={r2_sigv4._quote(query[name])}"
        for name in sorted(query)
    )
    host = f"{_CONFIG['accountId']}.r2.cloudflarestorage.com"
    expected = r2_sigv4.canonical_request(
        method="GET",
        canonical_uri=(
            f"/{r2_sigv4._quote(_CONFIG['bucket'], path=True)}"
            f"/{r2_sigv4._quote(key, path=True)}"
        ),
        canonical_query=canonical_query,
        host=host,
        signed_headers="host",
        payload_hash="UNSIGNED-PAYLOAD",
    )
    assert from_node == expected


@pytest.mark.parametrize("expires", [1, 60, 300, 900, 3600, 604_800])
def test_every_expiry_matches(expires):
    (from_node,) = _run_ops(
        [
            {
                "op": "r2_presign",
                "config": _CONFIG,
                "key": "backend.exe",
                "expires": expires,
                "amz_date": _AMZ_DATE,
            }
        ]
    )
    assert from_node == _python_presign("backend.exe", expires=expires)


def test_a_different_timestamp_changes_the_signature_in_both_implementations():
    """Chan truong hop ca hai ban cung bo qua `amz_date`."""
    other = "20261014T235959Z"
    results = _run_ops(
        [
            {"op": "r2_presign", "config": _CONFIG, "key": "backend.exe",
             "expires": 300, "amz_date": _AMZ_DATE},
            {"op": "r2_presign", "config": _CONFIG, "key": "backend.exe",
             "expires": 300, "amz_date": other},
        ]
    )
    assert results[0] != results[1]
    assert results[0] == _python_presign("backend.exe")
    assert results[1] == _python_presign("backend.exe", amz_date=other)


@pytest.mark.parametrize(
    "amz_date",
    ["20261013T120000", "2026-10-13T12:00:00Z", "20261013120000Z", ""],
)
def test_both_implementations_refuse_a_malformed_timestamp(amz_date):
    (verdict,) = _run_ops(
        [
            {"op": "r2_presign", "config": _CONFIG, "key": "backend.exe",
             "expires": 300, "amz_date": amz_date}
        ]
    )
    assert isinstance(verdict, dict) and "error" in verdict, verdict
    with pytest.raises(ValueError):
        _python_presign("backend.exe", amz_date=amz_date)


@pytest.mark.parametrize("epoch", [0, 1_760_000_000, 1_791_000_000])
def test_the_timestamp_formatter_matches_python(epoch):
    import time

    (from_node,) = _run_ops([{"op": "amz_date", "epoch": epoch}])
    expected = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(epoch))
    assert from_node == expected


def test_the_signature_is_not_a_constant():
    """⛔ Chan mot ban cai tra ve chu ky co dinh ma van 'khop'.

    Hai bai test tren so hai ban voi nhau; neu ca hai deu tra ve mot chuoi co
    dinh thi chung van khop. Phep nay buoc chu ky phai doi theo khoa bi mat.
    """
    base = _python_presign("backend.exe")
    other = r2_sigv4.presign_get(
        account_id=_CONFIG["accountId"],
        bucket=_CONFIG["bucket"],
        key="backend.exe",
        access_key_id=_CONFIG["accessKeyId"],
        secret_access_key="mot-khoa-khac-hoan-toan",
        expires_seconds=300,
        amz_date=_AMZ_DATE,
    )
    assert base != other
    assert base.split("X-Amz-Signature=")[1] != other.split("X-Amz-Signature=")[1]
