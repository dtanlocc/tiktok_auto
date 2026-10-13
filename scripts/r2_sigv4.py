"""Ky request kieu AWS SigV4 cho Cloudflare R2. Ban tham chieu bang Python.

⛔ VI SAO CO HAI BAN CAI, VA DAY LA BAN TRUOC. Function `artifacts` (TypeScript)
phai ky URL tai cho khach, con script phat hanh (Python) phai ky request PUT de
day file len. SigV4 la mot chuoi bam long nhau; lech mot dau cham phay trong
canonical request la chu ky sai, va R2 tra ve 403 khong noi gi ve nguyen nhan.
Nen module nay la ban tham chieu, `supabase/functions/_shared/r2.ts` la ban
song doi, va `backend/tests/test_r2_sigv4_parity.py` so sanh TUNG BYTE ca
canonical request lan URL cuoi.

⛔ VA CA HAI NHAN `amz_date` TU NGOAI, khong tu goi `now()`. Mot ham ky tu lay
gio hien tai thi khong the do bang nhau giua hai ban cai - moi lan goi ra mot
ket qua khac. Truyen thoi diem vao la dieu kien de co bai test parity.

Tai lieu: AWS Signature Version 4, truy van duoc ky truoc (presigned query).
R2 nhan dung giao thuc do voi region "auto".
"""
from __future__ import annotations

import hashlib
import hmac
from urllib.parse import quote

_ALGORITHM = "AWS4-HMAC-SHA256"
_SERVICE = "s3"
#: R2 khong co khai niem region; S3 API cua no doi dung chuoi "auto".
_REGION = "auto"
#: ⛔ Voi presigned URL thi than request KHONG duoc dua vao chu ky - client co
#: the goi ma khong co than. Day la gia tri AWS quy dinh cho truong hop do.
_UNSIGNED = "UNSIGNED-PAYLOAD"


def _quote(value: str, *, path: bool = False) -> str:
    # ⛔ AWS doi RFC 3986: chi `-_.~` la khong escape. `quote` cua Python mac
    # dinh con bo qua `/`, nen phai khai ro - va voi duong dan thi `/` PHAI
    # duoc giu nguyen, con trong query thi PHAI duoc escape.
    return quote(value, safe="-_.~/" if path else "-_.~")


def canonical_request(
    *,
    method: str,
    canonical_uri: str,
    canonical_query: str,
    host: str,
    signed_headers: str,
    payload_hash: str,
    extra_headers: dict[str, str] | None = None,
) -> str:
    headers = {"host": host}
    headers.update({k.lower(): v for k, v in (extra_headers or {}).items()})
    # Header trong canonical request phai sort theo ten va trim gia tri.
    canonical_headers = "".join(
        f"{name}:{headers[name].strip()}\n" for name in sorted(headers)
    )
    return "\n".join([
        method,
        canonical_uri,
        canonical_query,
        canonical_headers,
        signed_headers,
        payload_hash,
    ])


def signing_key(secret: str, datestamp: str) -> bytes:
    key = f"AWS4{secret}".encode("utf-8")
    for part in (datestamp, _REGION, _SERVICE, "aws4_request"):
        key = hmac.new(key, part.encode("utf-8"), hashlib.sha256).digest()
    return key


def string_to_sign(*, amz_date: str, datestamp: str, canonical: str) -> str:
    scope = f"{datestamp}/{_REGION}/{_SERVICE}/aws4_request"
    return "\n".join([
        _ALGORITHM,
        amz_date,
        scope,
        hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    ])


def presign_get(
    *,
    account_id: str,
    bucket: str,
    key: str,
    access_key_id: str,
    secret_access_key: str,
    expires_seconds: int,
    amz_date: str,
) -> str:
    """URL GET da ky truoc. `amz_date` dang `YYYYMMDDTHHMMSSZ` (UTC)."""
    if len(amz_date) != 16 or not amz_date.endswith("Z") or amz_date[8] != "T":
        raise ValueError("amz_date phai dang YYYYMMDDTHHMMSSZ")
    if not 1 <= expires_seconds <= 604_800:
        raise ValueError("expires_seconds phai trong [1, 604800]")
    datestamp = amz_date[:8]
    host = f"{account_id}.r2.cloudflarestorage.com"
    canonical_uri = f"/{_quote(bucket, path=True)}/{_quote(key, path=True)}"
    scope = f"{datestamp}/{_REGION}/{_SERVICE}/aws4_request"

    query = {
        "X-Amz-Algorithm": _ALGORITHM,
        "X-Amz-Credential": f"{access_key_id}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires_seconds),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_query = "&".join(
        f"{_quote(name)}={_quote(query[name])}" for name in sorted(query)
    )
    canonical = canonical_request(
        method="GET",
        canonical_uri=canonical_uri,
        canonical_query=canonical_query,
        host=host,
        signed_headers="host",
        payload_hash=_UNSIGNED,
    )
    signature = hmac.new(
        signing_key(secret_access_key, datestamp),
        string_to_sign(amz_date=amz_date, datestamp=datestamp, canonical=canonical).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return (
        f"https://{host}{canonical_uri}?{canonical_query}"
        f"&X-Amz-Signature={signature}"
    )


def signed_put_headers(
    *,
    account_id: str,
    bucket: str,
    key: str,
    access_key_id: str,
    secret_access_key: str,
    payload_sha256: str,
    content_length: int,
    content_type: str,
    amz_date: str,
) -> tuple[str, dict[str, str]]:
    """URL va header cho mot PUT da ky. Dung khi day file len tu may operator.

    ⛔ PUT DUNG CHU KY TRONG HEADER, khong phai trong query, va payload hash la
    sha256 THAT cua file chu khong phai UNSIGNED-PAYLOAD. Nho vay R2 tu choi
    mot than request bi doi tren duong di - mot phep kiem mien phi, va la lop
    dau tien truoc khi launcher doi chieu sha256 voi manifest da ky.
    """
    datestamp = amz_date[:8]
    host = f"{account_id}.r2.cloudflarestorage.com"
    canonical_uri = f"/{_quote(bucket, path=True)}/{_quote(key, path=True)}"
    headers = {
        "content-length": str(content_length),
        "content-type": content_type,
        "x-amz-content-sha256": payload_sha256,
        "x-amz-date": amz_date,
    }
    signed_headers = ";".join(sorted(["host", *headers]))
    canonical = canonical_request(
        method="PUT",
        canonical_uri=canonical_uri,
        canonical_query="",
        host=host,
        signed_headers=signed_headers,
        payload_hash=payload_sha256,
        extra_headers=headers,
    )
    signature = hmac.new(
        signing_key(secret_access_key, datestamp),
        string_to_sign(amz_date=amz_date, datestamp=datestamp, canonical=canonical).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    scope = f"{datestamp}/{_REGION}/{_SERVICE}/aws4_request"
    headers["authorization"] = (
        f"{_ALGORITHM} Credential={access_key_id}/{scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    # ⛔ KHONG TRA VE `host`. No PHAI nam trong SignedHeaders va trong canonical
    # request (da co o tren), nhung KHONG duoc dat thanh header gui di: httpx va
    # moi client HTTP tu dat Host tu URL, va dat tay se thanh hai gia tri hoac
    # ghi de - ca hai deu lam chu ky lech voi thu R2 tinh lai.
    return f"https://{host}{canonical_uri}", headers
