"""Goi backend dang chay, cho nhung lenh KHOI DONG TAC VU.

⛔ VI SAO KHONG LAM THANG MA PHAI GOI QUA HTTP. Hang doi tac vu, bo dieu phoi
va cac dich vu nen SONG TRONG TIEN TRINH BACKEND. Mot script rieng goi thang
vao chung se dung len mot bo dieu phoi THU HAI: hai hang doi, hai gioi han
luong, hai cai cung mo trinh duyet cho cung mot nick. Nen lenh hanh dong phai
di qua backend dang chay, con lenh DOC thi truy van database truc tiep (nhanh
hon, va chay duoc ca khi backend tat).

⛔ VA HIEN TAI CHI CHAY NOI BO. Chu ky cuc bo (`local_auth`) chi bat o che do
`production`/`friends`; o che do nay no tat, nen goi thang duoc. Mot ban da
cap phep tren may khach thi KHONG goi duoc kieu nay - bi mat phien do launcher
sinh ra va giu trong bo nho, khong ghi ra dau ca. Do la viec phai giai rieng
khi lam bot tu xa, chu khong phai mot thu lam tam roi quen.
"""
from __future__ import annotations

import json
import os
from typing import Any, Optional
from urllib import error, request

DEFAULT_BASE = "http://127.0.0.1:9000"


class BackendUnavailable(RuntimeError):
    """Backend khong chay, hoac khong goi toi duoc."""


def base_url() -> str:
    return os.environ.get("TKAUTO_BACKEND_URL") or DEFAULT_BASE


def call(method: str, path: str, payload: Optional[dict] = None, timeout: float = 30.0) -> Any:
    url = base_url().rstrip("/") + path
    data = None
    headers = {"accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["content-type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", "replace")
    except error.HTTPError as exc:
        chi_tiet = exc.read().decode("utf-8", "replace")[:300]
        try:
            chi_tiet = json.loads(chi_tiet).get("detail", chi_tiet)
        except ValueError:
            pass
        raise RuntimeError(f"Backend từ chối ({exc.code}): {chi_tiet}") from exc
    except (error.URLError, OSError) as exc:
        raise BackendUnavailable(
            f"Không gọi được backend tại {base_url()} ({type(exc).__name__}). "
            "Backend có đang chạy không?"
        ) from exc
    try:
        return json.loads(raw)
    except ValueError:
        return {"raw": raw[:500]}
