"""Nhung lenh con bot tra loi duoc.

Moi lenh tra ve DU LIEU, khong phai chuoi da trinh bay - xem registry.py.

⛔ DOC THANG TU DATABASE, khong goi qua HTTP cua chinh minh. Lop nay chay
TRONG backend, nen mot vong HTTP ve 127.0.0.1 chi them mot cho de hong: cong
doi, chu ky phien, backend dang khoi dong lai. Lenh nao can chay mot tac vu
nen thi moi goi sang dich vu tuong ung.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from app.core import event_log
from app.infrastructure.database.connection import engine
from app.infrastructure.database.schemas import AccountDbTable
from app.use_cases.control.registry import register


def _rows(statement) -> list[AccountDbTable]:
    with Session(engine) as session:
        return list(session.exec(statement).all())


def _tom_tat(row: AccountDbTable) -> dict[str, Any]:
    return {
        "username": row.username,
        "email": row.email,
        "nuoc": row.country,
        "lo": row.batch_tag,
        "suc_khoe": row.health_status,
        "follower": row.follower_count,
        "video": row.video_count,
        "dong_bo": row.analytics_sync_status,
        "cap_nhat": row.metrics_updated_at,
    }


@register("trang_thai", "Tong quan: bao nhieu nick, da dong bo bao nhieu, hong bao nhieu")
def trang_thai() -> dict[str, Any]:
    with Session(engine) as session:
        tat_ca = list(session.exec(select(AccountDbTable)).all())
    theo_dong_bo: dict[str, int] = {}
    theo_suc_khoe: dict[str, int] = {}
    theo_nuoc: dict[str, int] = {}
    co_so_lieu = 0
    for row in tat_ca:
        theo_dong_bo[row.analytics_sync_status or "NEVER"] = (
            theo_dong_bo.get(row.analytics_sync_status or "NEVER", 0) + 1
        )
        theo_suc_khoe[row.health_status or "UNKNOWN"] = (
            theo_suc_khoe.get(row.health_status or "UNKNOWN", 0) + 1
        )
        theo_nuoc[row.country or "??"] = theo_nuoc.get(row.country or "??", 0) + 1
        if row.follower_count is not None:
            co_so_lieu += 1
    return {
        "tong": len(tat_ca),
        "co_so_lieu_follower": co_so_lieu,
        "chua_co_so_lieu": len(tat_ca) - co_so_lieu,
        "theo_dong_bo": dict(sorted(theo_dong_bo.items(), key=lambda kv: -kv[1])),
        "theo_suc_khoe": dict(sorted(theo_suc_khoe.items(), key=lambda kv: -kv[1])),
        "theo_nuoc": dict(sorted(theo_nuoc.items(), key=lambda kv: -kv[1])),
    }


@register(
    "tren_nguong",
    "Nick dat tu N follow tro len, tach theo nuoc",
    params={"nguong": "So follow toi thieu (mac dinh 1000)",
            "nuoc": "Chi mot nuoc, vi du US (de trong = moi nuoc)"},
)
def tren_nguong(nguong: int = 1000, nuoc: str = "") -> dict[str, Any]:
    statement = select(AccountDbTable).where(AccountDbTable.follower_count >= nguong)
    if nuoc:
        statement = statement.where(AccountDbTable.country == nuoc.upper())
    rows = sorted(_rows(statement), key=lambda r: -(r.follower_count or 0))
    theo_nuoc: dict[str, int] = {}
    for row in rows:
        theo_nuoc[row.country or "??"] = theo_nuoc.get(row.country or "??", 0) + 1
    return {
        "nguong": nguong,
        "tong": len(rows),
        "theo_nuoc": theo_nuoc,
        "nick": [_tom_tat(r) for r in rows],
    }


@register(
    "nick",
    "Thong tin mot nick cu the",
    params={"ten": "username hoac email cua nick"},
)
def nick(ten: str) -> dict[str, Any]:
    khoa = (ten or "").strip().lstrip("@")
    if not khoa:
        return {"tim_thay": False, "ly_do": "Chua nhap ten nick."}
    with Session(engine) as session:
        row = session.exec(
            select(AccountDbTable).where(AccountDbTable.username == khoa)
        ).first()
        if row is None:
            row = session.exec(
                select(AccountDbTable).where(AccountDbTable.email == khoa.lower())
            ).first()
        if row is None:
            return {"tim_thay": False, "ly_do": f"Khong co nick nao ten {ten!r}."}
        chi_tiet = _tom_tat(row)
        chi_tiet.update({
            "view_tong": row.total_views,
            "thich": row.likes_count,
            "binh_luan": row.total_comments,
            "chia_se": row.total_shares,
            "loi_dong_bo": row.analytics_sync_error or "",
            "proxy_id": row.proxy_id,
        })
    return {"tim_thay": True, "nick": chi_tiet}


@register(
    "lo_hong",
    "Nhung lo dang co nick dong bo that bai, ke ca ly do",
    params={"gioi_han": "So lo hien ra (mac dinh 10)"},
)
def lo_hong(gioi_han: int = 10) -> dict[str, Any]:
    rows = _rows(select(AccountDbTable).where(AccountDbTable.analytics_sync_status == "FAILED"))
    theo_lo: dict[str, dict[str, Any]] = {}
    for row in rows:
        lo = row.batch_tag or "(khong lo)"
        muc = theo_lo.setdefault(lo, {"lo": lo, "so_nick": 0, "ly_do": {}})
        muc["so_nick"] += 1
        ly_do = (row.analytics_sync_error or "").strip()[:120] or "(khong ghi)"
        muc["ly_do"][ly_do] = muc["ly_do"].get(ly_do, 0) + 1
    xep = sorted(theo_lo.values(), key=lambda m: -m["so_nick"])
    return {"tong_nick_hong": len(rows), "lo": xep[: max(1, gioi_han)]}


@register(
    "su_kien",
    "Nhung su kien gan nhat trong nhat ky",
    params={"gioi_han": "So dong (mac dinh 20)", "loai": "Loc theo loai su kien"},
)
def su_kien(gioi_han: int = 20, loai: str = "") -> dict[str, Any]:
    ban_ghi = event_log.read_recent(limit=max(1, gioi_han), kind=loai)
    return {"so_dong": len(ban_ghi), "su_kien": ban_ghi}


@register(
    "gom_tren_nguong",
    "Chuyen nick dat nguong follow sang lo rieng theo nuoc + ngay",
    mutates=True,
    params={"nguong": "So follow toi thieu (mac dinh 1000)"},
)
def gom_tren_nguong(nguong: int = 1000) -> dict[str, Any]:
    """⛔ LENH NAY SUA DU LIEU. `registry.run` doi `confirm=True`.

    Dung chung logic voi endpoint HTTP, de hai duong khong the lech nhau.
    """
    from app.interfaces.api.accounts_router import _already_promoted, _promoted_batch

    ngay = datetime.now().strftime("%Y-%m-%d")
    da_chuyen: dict[str, int] = {}
    with Session(engine) as session:
        rows = list(session.exec(select(AccountDbTable)).all())
        for row in rows:
            so = row.follower_count
            if so is None or so < nguong:
                continue
            if _already_promoted(row.batch_tag or "", nguong):
                continue
            lo = _promoted_batch(nguong, row.country or "??", ngay)
            row.batch_tag = lo
            session.add(row)
            da_chuyen[lo] = da_chuyen.get(lo, 0) + 1
        session.commit()
    event_log.record(
        "accounts.promoted",
        f"Đã gom {sum(da_chuyen.values())} nick đạt {nguong} follow sang {len(da_chuyen)} lô.",
        nguong=nguong,
        theo_lo=da_chuyen,
    )
    return {"nguong": nguong, "ngay": ngay, "theo_lo": da_chuyen,
            "tong": sum(da_chuyen.values())}
