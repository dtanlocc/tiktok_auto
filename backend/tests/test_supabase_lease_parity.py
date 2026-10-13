"""Ban TypeScript tren Supabase phai ky ra DUNG TUNG BYTE nhu ban Python.

⛔ DAY LA BAI TEST GIU CA HUONG SUPABASE DUNG DUOC. Chuyen phat lease sang
Edge Function nghia la co HAI ban cai cua mot thu, va client xac thuc lease
bang CHU KY: lech mot byte trong JSON canonical la lease khong verify duoc.
Loi do khong lo ra luc deploy - no lo ra luc khach bam Kich hoat, tren may cua
khach, khong co log nao cua minh o do.

Nen cho nay khong test "y tuong giong nhau". No chay THAT file
`supabase/functions/_shared/lease.ts` bang Node, ky cung mot claims bang cung
mot khoa, roi so sanh chuoi token. Ed25519 la xac dinh (RFC 8032): cung khoa,
cung thong diep thi cung chu ky, nen so sanh byte la hop le.

⛔ VI SAO LA Node CHU KHONG Deno. Edge Function chay tren Deno, nhung hai
module duoc kiem o day chi dung WebCrypto + TextEncoder - khong import gi ca -
nen Node 22+ chay y nguyen. Doi Deno hoac Supabase CLI chi de chay mot bai
test la bien no thanh bai test khong ai chay.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.security.license import (
    LeaseClaims,
    LeaseSigner,
    LeaseVerifier,
    _canonical_json,
    _compare_semver,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RUNNER = _REPO_ROOT / "supabase" / "functions" / "_shared" / "parity_runner.ts"
_SERVICE_TS = _REPO_ROOT / "supabase" / "functions" / "_shared" / "service.ts"

#: Hat co dinh -> khoa co dinh -> chu ky co dinh. Khong phai khoa that nao.
_SEED = bytes(range(32))


def _node() -> str:
    found = shutil.which("node")
    if found is None:
        pytest.skip(
            "Khong tim thay 'node' nen khong do duoc ban TypeScript. Cai Node "
            ">= 22 roi chay lai: bai test nay la thu duy nhat chan viec ban "
            "Edge Function ky lech voi ban Python."
        )
    return found


def _run_ops(operations: list[dict]) -> list:
    result = subprocess.run(
        [_node(), str(_RUNNER)],
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


@pytest.fixture(scope="module")
def private_pem() -> str:
    key = Ed25519PrivateKey.from_private_bytes(_SEED)
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")


@pytest.fixture(scope="module")
def claims() -> LeaseClaims:
    return LeaseClaims(
        license_id="lic_0123456789abcdef",
        device_id="device_" + "ab" * 20,
        plan="nhom-ban-be",
        # ⛔ Co y de ca group./role. va mot ban TRUNG cua app.start o day: thu
        # tu va viec loai trung la phan de lech nhat, va `features` duoc ky.
        features=("app.start", "accounts.manage", "upload.video", "group.hn01", "role.owner"),
        max_accounts=200,
        max_tabs=4,
        channel="stable",
        minimum_version="0.1.0",
        minimum_backend_version="0.1.0",
        issued_at=1_760_000_000,
        not_before=1_759_999_995,
        expires_at=1_760_003_600,
        jti="lease_00112233445566778899aabbccddeeff",
    )


def test_typescript_signs_the_same_token_byte_for_byte(private_pem, claims):
    python_token = LeaseSigner.from_pem("lease-dev-2026-01", private_pem).sign(claims)
    (node_token,) = _run_ops(
        [
            {
                "op": "sign_lease",
                "pem": private_pem,
                "kid": "lease-dev-2026-01",
                "claims": claims.model_dump(mode="json"),
            }
        ]
    )
    assert node_token == python_token


def test_a_token_signed_by_typescript_verifies_with_the_python_verifier(
    private_pem, claims
):
    """So sanh chuoi la mot nua; nua con lai la no PHAI qua duoc verifier."""
    public_pem = (
        Ed25519PrivateKey.from_private_bytes(_SEED)
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    (node_token,) = _run_ops(
        [
            {
                "op": "sign_lease",
                "pem": private_pem,
                "kid": "lease-dev-2026-01",
                "claims": claims.model_dump(mode="json"),
            }
        ]
    )
    envelope = LeaseVerifier.from_pem_mapping({"lease-dev-2026-01": public_pem}).verify(
        node_token
    )
    assert envelope.claims == claims
    assert envelope.key_id == "lease-dev-2026-01"


def test_canonical_json_matches_on_every_claim_shape(claims):
    import base64

    payloads = [
        claims.model_dump(mode="json"),
        {"alg": "EdDSA", "kid": "lease-dev-2026-01", "typ": "TKAUTO-LEASE", "v": 1},
        # Khoa co y dat sai thu tu: ca hai ban phai sort truoc khi serialize.
        {"z": "sau", "a": "truoc", "m": 10},
        {"features": [], "plan": "x"},
        # Ky tu phai escape.
        {"text": 'co "ngoac" va \\ gach cheo\nva dong moi'},
    ]
    results = _run_ops([{"op": "canonical_json", "value": item} for item in payloads])
    for item, encoded in zip(payloads, results, strict=True):
        assert base64.b64decode(encoded) == _canonical_json(item), item


def test_license_key_digest_matches(claims):
    from control_plane.app.security import digest_license_key

    pepper = "x" * 48
    keys = [
        "TKAUTO-ABCD-EFGH-IJKL-MNOP-QRST-UVWX",
        "tkauto-abcd-efgh-ijkl-mnop-qrst-uvwx",
        "  TKAUTO-ABCD  EFGH\tIJKL\nMNOP  ",
    ]
    results = _run_ops(
        [{"op": "digest_key", "license_key": key, "pepper": pepper} for key in keys]
    )
    for key, digest in zip(keys, results, strict=True):
        assert digest == digest_license_key(key, pepper), key


def test_a_non_ascii_license_key_is_refused_instead_of_digested():
    """⛔ Khong phai parity - la cho hai ban KHONG THE lech.

    `str.upper()` cua Python va `toUpperCase()` cua JS khac nhau o vai ky tu
    Unicode, nen mot key chua chung se ra hai digest khac nhau: hop le o ban
    nay, sai o ban kia. Ban TS tra ve null (-> 401) thay vi bam.
    """
    (digest,) = _run_ops(
        [{"op": "digest_key", "license_key": "TKAUTO-ABCD-ıııı", "pepper": "y" * 40}]
    )
    assert digest is None


def test_device_id_derivation_matches():
    from control_plane.app.security import expected_device_id, load_device_public_key

    public_pem = (
        Ed25519PrivateKey.from_private_bytes(bytes(range(32, 64)))
        .public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    (device_id,) = _run_ops([{"op": "device_id", "public_key_pem": public_pem}])
    assert device_id == expected_device_id(load_device_public_key(public_pem))


def test_device_proof_canonical_bytes_match():
    import base64

    from control_plane.app.security import canonical_device_proof

    cases = [
        ("ACTIVATE", "device_" + "cd" * 20, 1_760_000_000, "nonce-0123456789abcd", "0.1.11"),
        ("RENEW", "device_" + "ef" * 20, 1_760_000_123, "nonce_abcdefghijklmn", "1.0.0-rc.1"),
    ]
    results = _run_ops(
        [
            {
                "op": "device_proof",
                "action": action,
                "device_id": device_id,
                "issued_at": issued_at,
                "nonce": nonce,
                "app_version": app_version,
            }
            for action, device_id, issued_at, nonce, app_version in cases
        ]
    )
    for case, encoded in zip(cases, results, strict=True):
        assert base64.b64decode(encoded) == canonical_device_proof(*case), case


def test_semver_comparison_matches_including_prereleases():
    pairs = [
        ("1.0.0", "1.0.0"),
        ("1.0.1", "1.0.0"),
        ("1.0.0", "1.1.0"),
        ("2.0.0", "10.0.0"),
        ("1.0.0-alpha", "1.0.0"),
        ("1.0.0", "1.0.0-alpha"),
        ("1.0.0-alpha", "1.0.0-beta"),
        ("1.0.0-alpha.1", "1.0.0-alpha.2"),
        ("1.0.0-alpha.1", "1.0.0-alpha.beta"),
        ("1.0.0-alpha", "1.0.0-alpha.1"),
        ("1.0.0+build1", "1.0.0+build2"),
        ("1.0.0-rc.1", "1.0.0-rc.1"),
    ]
    results = _run_ops(
        [{"op": "compare_semver", "left": left, "right": right} for left, right in pairs]
    )
    for (left, right), verdict in zip(pairs, results, strict=True):
        assert verdict == _compare_semver(left, right), f"{left} vs {right}"


def test_the_feature_list_that_gets_signed_matches():
    """`app.start` dung dau, trung lap bi loai, thu tu con lai giu nguyen."""
    cases = [
        [],
        ["accounts.manage"],
        ["app.start", "accounts.manage"],
        ["accounts.manage", "app.start", "accounts.manage", "upload.video"],
        ["group.hn01", "role.owner", "app.start"],
    ]
    results = _run_ops([{"op": "effective_features", "features": item} for item in cases])
    for item, features in zip(cases, results, strict=True):
        expected = list(dict.fromkeys(("app.start", *item)))
        assert features == expected, item


def test_the_typescript_claims_object_names_every_python_claim_field():
    """Chan truong hop them mot claim o Python ma quen them o TypeScript.

    ⛔ Hai bai test tren khong bat duoc viec nay: chung dua CUNG MOT dict claims
    cho ca hai ban, nen mot truong moi se duoc ca hai serialize giong nhau va
    test van xanh - trong khi `issueLease` tren Supabase khong he dat truong do
    va lease that se thieu no.
    """
    source = _SERVICE_TS.read_text(encoding="utf-8")
    opening = "const claims: LeaseClaims = {"
    start = source.find(opening)
    assert start != -1, (
        "Khong tim thay literal `const claims: LeaseClaims = {` trong "
        "service.ts. Bai test nay doc chinh khoi do, nen doi ten bien hay doi "
        "cach dung claims thi phai sua lai day - dung xoa bai test."
    )
    end = source.find("};", start)
    assert end != -1, "Khong thay cho dong literal claims trong service.ts."
    # ⛔ CHI DOC TRONG KHOI LITERAL, khong grep ca file. Grep ca file la mot bai
    # test gia: `features` cung xuat hien o `interface LicenseRow`, nen mot
    # claim bi quen van "tim thay" o mot dong khong lien quan.
    block = source[start + len(opening) : end]
    missing = [
        name
        for name in LeaseClaims.model_fields
        # Nhan ca `plan: license.plan` va dang viet tat `features,`.
        if f"{name}:" not in block and f"{name}," not in block
    ]
    assert not missing, (
        "service.ts khong dat nhung claim nay khi phat lease: "
        f"{', '.join(missing)}. Them vao `issueLease` truoc khi deploy."
    )
