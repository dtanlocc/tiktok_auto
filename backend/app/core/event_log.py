"""Nhat ky su kien ghi ra FILE, de doc duoc ma khong can mo giao dien.

⛔ VI SAO LA FILE CHU KHONG PHAI MOT BANG TRONG DATABASE. Cai nay sinh ra cho
mot may VPS khong ai ngoi truoc man hinh: con bot doc no, nguoi van hanh doc
no qua SSH, va khi backend chet thi no VAN CON. Mot bang trong cung cai
database ma backend dang giu se im lang dung luc can nhat.

⛔ MOI DONG LA MOT JSON DOC LAP (JSONL), khong phai mot mang JSON. Mot mang
phai doc ca tep roi moi hieu, va mot lan ghi dang do la hong ca tep. JSONL
doc duoc tung dong, ghi them duoc bang mot lan `open(..., "a")`, va mot dong
hong khong keo theo dong nao khac.

⛔ VA GHI NHAT KY KHONG BAO GIO DUOC LAM HONG VIEC DANG LAM. Dia day, tep bi
khoa, thu muc khong tao duoc - tat ca deu bi nuot o day. Mot tac vu dang dang
video khong duoc chet vi khong ghi duoc mot dong nhat ky.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from app.core.config import BASE_DIR

logger = logging.getLogger("EventLog")

_LOCK = threading.Lock()

#: Thu muc nhat ky. Nam canh database de "du lieu cua may nay" o mot cho.
DEFAULT_DIR = BASE_DIR / "event-log"

#: Giu bao nhieu ngay. Mot may chay lien tuc se sinh file moi ngay, va khong
#: ai don thu cong tren VPS ca.
KEEP_DAYS = 30


def log_dir() -> Path:
    return Path(os.environ.get("TKAUTO_EVENT_LOG_DIR") or DEFAULT_DIR)


def _file_for(ngay: str) -> Path:
    return log_dir() / f"events-{ngay}.jsonl"


def record(kind: str, message: str, **fields: Any) -> dict[str, Any]:
    """Ghi mot su kien. Tra ve chinh ban ghi, de nguoi goi dung lai duoc.

    `kind` la MA loai su kien (de loc), `message` la cau nguoi doc.
    """
    ban_ghi: dict[str, Any] = {
        "at": datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "message": message,
    }
    for key, value in fields.items():
        if key not in ban_ghi:
            ban_ghi[key] = value
    try:
        thu_muc = log_dir()
        with _LOCK:
            thu_muc.mkdir(parents=True, exist_ok=True)
            with _file_for(datetime.now().strftime("%Y-%m-%d")).open(
                "a", encoding="utf-8"
            ) as handle:
                handle.write(json.dumps(ban_ghi, ensure_ascii=False) + "\n")
    except Exception as exc:
        # ⛔ NUOT, KHONG NEM. Xem docstring dau tep.
        logger.warning("Khong ghi duoc nhat ky su kien: %s", exc)
    return ban_ghi


def _files_newest_first() -> list[Path]:
    thu_muc = log_dir()
    if not thu_muc.is_dir():
        return []
    return sorted(thu_muc.glob("events-*.jsonl"), reverse=True)


def read_recent(limit: int = 50, kind: str = "") -> list[dict[str, Any]]:
    """Nhung su kien gan nhat, moi nhat truoc.

    Doc nguoc tu file moi nhat, va DUNG khi du so luong - mot may chay vai
    thang co the co hang tram nghin dong, va con bot chi hoi "co gi moi".
    """
    ket_qua: list[dict[str, Any]] = []
    for tep in _files_newest_first():
        try:
            dong = tep.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in reversed(dong):
            raw = raw.strip()
            if not raw:
                continue
            try:
                ban_ghi = json.loads(raw)
            except ValueError:
                continue   # mot dong hong khong lam hong nhung dong khac
            if kind and ban_ghi.get("kind") != kind:
                continue
            ket_qua.append(ban_ghi)
            if len(ket_qua) >= limit:
                return ket_qua
    return ket_qua


def iter_all() -> Iterator[dict[str, Any]]:
    """Moi su kien con giu, cu nhat truoc. Dung de thong ke."""
    for tep in reversed(_files_newest_first()):
        try:
            dong = tep.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in dong:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except ValueError:
                continue


def prune(keep_days: int = KEEP_DAYS) -> list[str]:
    """Xoa file cu hon `keep_days`. Tra ve ten nhung file da xoa."""
    tep_list = _files_newest_first()
    da_xoa: list[str] = []
    for tep in tep_list[max(0, keep_days):]:
        try:
            tep.unlink()
            da_xoa.append(tep.name)
        except OSError as exc:
            logger.warning("Khong xoa duoc %s: %s", tep.name, exc)
    return da_xoa
