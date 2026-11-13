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
from app.use_cases.control.backend_client import call as _backend
from app.use_cases.control.registry import register
from app.use_cases.control.targets import TargetError, phan_giai, tu_tham_so


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


@register("trang_thai", "Tong quan: bao nhieu nick, da dong bo bao nhieu, hong bao nhieu", remote=True)
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
    remote=True,
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
    remote=True,
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
    remote=True,
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
    remote=True,
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


# =============================================================================
# LENH DOC THEM - truy van thang database
# =============================================================================
@register("danh_sach_lo", "Cac lo (batch) va so nick moi lo", remote=True,
          params={"gioi_han": "So lo hien ra (mac dinh 30)"})
def danh_sach_lo(gioi_han: int = 30) -> dict[str, Any]:
    with Session(engine) as session:
        rows = list(session.exec(select(AccountDbTable)).all())
    dem: dict[str, dict[str, Any]] = {}
    for row in rows:
        lo = row.batch_tag or "(khong lo)"
        muc = dem.setdefault(lo, {"lo": lo, "so_nick": 0, "song": 0, "da_dong_bo": 0})
        muc["so_nick"] += 1
        if row.health_status == "ALIVE":
            muc["song"] += 1
        if row.analytics_sync_status in ("SUCCESS", "PARTIAL"):
            muc["da_dong_bo"] += 1
    xep = sorted(dem.values(), key=lambda m: -m["so_nick"])
    return {"tong_lo": len(xep), "lo": xep[: max(1, gioi_han)]}


@register("nick_trong_lo", "Danh sach nick trong mot lo", remote=True,
          params={"lo": "Ten lo", "gioi_han": "So nick hien ra (mac dinh 50)"})
def nick_trong_lo(lo: str, gioi_han: int = 50) -> dict[str, Any]:
    rows = _rows(select(AccountDbTable).where(AccountDbTable.batch_tag == lo))
    return {"lo": lo, "tong": len(rows),
            "nick": [_tom_tat(r) for r in rows[: max(1, gioi_han)]]}


@register("nick_hong", "Nick dong bo that bai, kem ly do", remote=True,
          params={"gioi_han": "So nick hien ra (mac dinh 30)"})
def nick_hong(gioi_han: int = 30) -> dict[str, Any]:
    rows = _rows(select(AccountDbTable).where(AccountDbTable.analytics_sync_status == "FAILED"))
    return {
        "tong": len(rows),
        "nick": [
            {**_tom_tat(r), "ly_do": (r.analytics_sync_error or "")[:160]}
            for r in rows[: max(1, gioi_han)]
        ],
    }


@register("proxy", "Danh sach proxy va tinh trang", remote=True)
def proxy() -> dict[str, Any]:
    from app.infrastructure.database.schemas import ProxyDbTable

    with Session(engine) as session:
        rows = list(session.exec(select(ProxyDbTable)).all())
    return {
        "tong": len(rows),
        "proxy": [
            {"id": r.id, "host": r.host, "port": r.port,
             "giao_thuc": getattr(r, "protocol", "?"),
             "nuoc": getattr(r, "country", "") or "",
             "trang_thai": getattr(r, "status", "") or ""}
            for r in rows
        ],
    }


# =============================================================================
# LENH HANH DONG - goi backend dang chay
# =============================================================================
@register("tinh_trang_chay", "Hang doi dang chay the nao (hoi backend)", remote=True)
def tinh_trang_chay() -> Any:
    return _backend("GET", "/api/v1/tasks/status")


@register("phien_ban", "Ban backend, thu vien trinh duyet, doi Firefox", remote=True)
def phien_ban() -> Any:
    return _backend("GET", "/api/v1/tasks/versions")


@register("dong_bo", "Chay dong bo so lieu", mutates=True, remote=True,
          params={"kieu": "tat_ca | lo | nuoc | nick | loc",
                  "gia_tri": "Gia tri cua dich (vi du ten lo)",
                  "luong": "So luong (mac dinh 4)"})
def dong_bo(kieu: str = "loc", gia_tri: Any = "never", luong: int = 4) -> Any:
    dich = tu_tham_so(kieu, gia_tri)
    rows = phan_giai(dich)
    ket_qua = _backend("POST", "/api/v1/tasks/sync-analytics",
                       {"account_ids": [r.email for r in rows],
                        "concurrency_limit": luong})
    event_log.record("sync.started",
                     f"Bắt đầu đồng bộ {len(rows)} nick ({dich.mo_ta()}).",
                     dich=dich.kieu, gia_tri=dich.gia_tri, so_nick=len(rows))
    return {"dich": dich.mo_ta(), "so_nick": len(rows), "backend": ket_qua}


@register("dang_nhap", "Dang nhap hang loat", mutates=True, remote=True,
          params={"kieu": "tat_ca | lo | nuoc | nick | loc", "gia_tri": "Gia tri cua dich",
                  "cach": "COOKIE hoac CREDENTIAL", "luong": "So luong"})
def dang_nhap(kieu: str = "lo", gia_tri: Any = None, cach: str = "COOKIE",
              luong: int = 8) -> Any:
    dich = tu_tham_so(kieu, gia_tri)
    rows = phan_giai(dich)
    ket_qua = _backend("POST", "/api/v1/tasks/bulk-login", {
        "account_ids": [r.email for r in rows],
        "login_method": cach.upper(),
        "proxy_concurrency": luong,
    })
    event_log.record("login.started",
                     f"Đăng nhập {len(rows)} nick ({dich.mo_ta()}).",
                     dich=dich.kieu, gia_tri=dich.gia_tri, cach=cach, so_nick=len(rows))
    return {"dich": dich.mo_ta(), "so_nick": len(rows), "backend": ket_qua}


@register("check_nhanh", "Kiem tra song/chet", mutates=True, remote=True,
          params={"kieu": "tat_ca | lo | nuoc | nick | loc", "gia_tri": "Gia tri cua dich",
                  "luong": "So luong (mac dinh 5)"})
def check_nhanh(kieu: str = "lo", gia_tri: Any = None, luong: int = 5) -> Any:
    dich = tu_tham_so(kieu, gia_tri)
    rows = phan_giai(dich)
    ket_qua = _backend("POST", "/api/v1/tasks/quick-health-check",
                       {"account_ids": [r.email for r in rows], "concurrency_limit": luong})
    return {"dich": dich.mo_ta(), "so_nick": len(rows), "backend": ket_qua}


@register("bat_dau", "Khoi dong hang doi", mutates=True, remote=True)
def bat_dau() -> Any:
    event_log.record("queue.start", "Khởi động hàng đợi.")
    return _backend("POST", "/api/v1/tasks/start-global")


@register("tam_dung", "Tam dung hang doi", mutates=True, remote=True)
def tam_dung() -> Any:
    event_log.record("queue.pause", "Tạm dừng hàng đợi.")
    return _backend("POST", "/api/v1/tasks/pause-global")


@register("tiep_tuc", "Tiep tuc hang doi", mutates=True, remote=True)
def tiep_tuc() -> Any:
    event_log.record("queue.resume", "Tiếp tục hàng đợi.")
    return _backend("POST", "/api/v1/tasks/resume-global")


@register("dung_khan", "DUNG KHAN CAP: huy moi tac vu dang chay", mutates=True, remote=True)
def dung_khan() -> Any:
    event_log.record("queue.stop", "DỪNG KHẨN CẤP.")
    return _backend("POST", "/api/v1/tasks/stop-global")


@register("mang", "Doi che do mang", mutates=True, remote=True,
          params={"dung_proxy": "true = dung proxy; false = mang that"})
def mang(dung_proxy: bool = True) -> Any:
    event_log.record("network.mode", f"Chế độ mạng: {'proxy' if dung_proxy else 'mạng thật'}.")
    return _backend("POST", "/api/v1/tasks/proxy-mode", {"use_proxy": bool(dung_proxy)})


@register("ten_dang_nhap", "Go hop thu hay username vao o dang nhap", mutates=True,
          remote=True,
          params={"kieu": "email hoac username"})
def ten_dang_nhap(kieu: str = "email") -> Any:
    return _backend("POST", "/api/v1/tasks/login-identifier", {"mode": kieu})


@register("phan_bo_proxy", "Tu phan bo proxy cho account", mutates=True, remote=True)
def phan_bo_proxy() -> Any:
    event_log.record("proxy.allocated", "Tự phân bổ proxy.")
    return _backend("POST", "/api/v1/accounts/auto-allocate-proxies")


@register("gom_1k_xem_truoc", "XEM TRUOC viec gom nick dat nguong follow", remote=True,
          params={"nguong": "So follow toi thieu (mac dinh 1000)"})
def gom_1k_xem_truoc(nguong: int = 1000) -> Any:
    return _backend("POST", "/api/v1/accounts/promote-by-followers",
                    {"threshold": nguong, "dry_run": True})
