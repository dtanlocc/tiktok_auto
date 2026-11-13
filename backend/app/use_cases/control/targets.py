"""Dich cua mot lenh: lam gi, TREN NHUNG NICK NAO.

⛔ MOT CACH CHON DUNG CHUNG CHO MOI LENH. Truoc day moi lenh tu nghi ra cach
chon rieng: `dong_bo` nhan `chon=never`, `dang_nhap` nhan `lo=...`,
`check_nhanh` cung nhan `lo=...` nhung khong nhan gi khac. Nguoi dung phai nho
lenh nao nhan gi, va muon dong bo DUNG MOT LO thi khong co duong nao.

Gio moi lenh hanh dong nhan cung mot `dich`, nen "lam X tren Y" la mot cau
noi duoc voi moi X va moi Y.

⛔ VA DICH RONG LA MOT LOI, KHONG PHAI "KHONG LAM GI". Mot lenh go nham ten lo
ma lang le chay tren 0 nick thi nguoi dung tuong da chay xong. Phai noi ro:
khong co nick nao khop.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Iterable

from sqlmodel import Session, select

from app.infrastructure.database.connection import engine
from app.infrastructure.database.schemas import AccountDbTable


class TargetError(ValueError):
    """Dich khong hop le, hoac khong khop nick nao."""


@dataclass(frozen=True)
class Dich:
    kieu: str
    gia_tri: Any = None

    def mo_ta(self) -> str:
        if self.kieu == "tat_ca":
            return "tất cả nick"
        return f"{self.kieu} = {self.gia_tri!r}"


def _tat_ca(session: Session, _gia_tri: Any) -> Iterable[AccountDbTable]:
    return session.exec(select(AccountDbTable)).all()


def _theo_lo(session: Session, gia_tri: Any) -> Iterable[AccountDbTable]:
    return session.exec(
        select(AccountDbTable).where(AccountDbTable.batch_tag == str(gia_tri))
    ).all()


def _theo_nuoc(session: Session, gia_tri: Any) -> Iterable[AccountDbTable]:
    return session.exec(
        select(AccountDbTable).where(AccountDbTable.country == str(gia_tri).upper())
    ).all()


def _theo_nick(session: Session, gia_tri: Any) -> Iterable[AccountDbTable]:
    ten = [str(v).strip().lstrip("@").lower() for v in _nhu_danh_sach(gia_tri)]
    if not ten:
        return []
    tat_ca = session.exec(select(AccountDbTable)).all()
    return [
        row for row in tat_ca
        if (row.email or "").lower() in ten or (row.username or "").lower() in ten
    ]


def _theo_loc(session: Session, gia_tri: Any) -> Iterable[AccountDbTable]:
    """Loc theo trang thai dong bo - dung chung bang voi endpoint HTTP."""
    from app.interfaces.api.tasks_router import ANALYTICS_SELECTORS

    ten = str(gia_tri)
    loc = ANALYTICS_SELECTORS.get(ten)
    if loc is None:
        raise TargetError(
            f"Bộ lọc không có thật: {ten!r}. "
            "Chọn một trong: " + ", ".join(sorted(ANALYTICS_SELECTORS))
        )
    return [row for row in session.exec(select(AccountDbTable)).all() if loc(row)]


def _nhu_danh_sach(gia_tri: Any) -> list[Any]:
    if gia_tri is None:
        return []
    if isinstance(gia_tri, (list, tuple, set)):
        return list(gia_tri)
    # Mot chuoi "a@b.com,c@d.com" tu dong lenh cung phai dung duoc.
    return [phan for phan in str(gia_tri).split(",") if phan.strip()]


#: ⛔ THEM MOT KIEU DICH LA THEM MOT DONG O DAY. Khong lenh nao phai sua.
RESOLVERS: dict[str, Callable[[Session, Any], Iterable[AccountDbTable]]] = {
    "tat_ca": _tat_ca,
    "lo": _theo_lo,
    "nuoc": _theo_nuoc,
    "nick": _theo_nick,
    "loc": _theo_loc,
}

#: Kieu dich doi mot gia tri di kem; "tat_ca" thi khong.
CAN_GIA_TRI = frozenset({"lo", "nuoc", "nick", "loc"})


def phan_giai(dich: Dich) -> list[AccountDbTable]:
    """Dich -> danh sach nick. Nem `TargetError` neu khong khop gi."""
    resolver = RESOLVERS.get(dich.kieu)
    if resolver is None:
        raise TargetError(
            f"Kiểu đích không có thật: {dich.kieu!r}. "
            "Chọn một trong: " + ", ".join(sorted(RESOLVERS))
        )
    if dich.kieu in CAN_GIA_TRI and not str(dich.gia_tri or "").strip():
        raise TargetError(f"Đích {dich.kieu!r} cần một giá trị đi kèm.")
    with Session(engine) as session:
        rows = list(resolver(session, dich.gia_tri))
    if not rows:
        # ⛔ Xem docstring dau tep: rong la loi, khong phai "khong lam gi".
        raise TargetError(f"Không có nick nào khớp {dich.mo_ta()}.")
    return rows


def tu_tham_so(kieu: str = "tat_ca", gia_tri: Any = None) -> Dich:
    """Dung `Dich` tu tham so dong lenh / JSON."""
    return Dich(kieu=str(kieu or "tat_ca").strip().lower(), gia_tri=gia_tri)
