"""Lop dieu khien: mot so dang ky lenh, dung chung cho moi mat truoc.

Hom nay mat truoc la mot script chay tay tren may. Sau nay la Telegram hay
mail. CA HAI goi cung nhung lenh nay, nen mot lenh viet mot lan la moi mat
truoc deu co.

⛔ LENH DOC VA LENH SUA DUOC TACH RO, va day la rang buoc quan trong nhat o
day. Cai nay sinh ra de dieu khieu TU XA mot may VPS khong ai ngoi truoc man
hinh. Mot lenh chuyen 2000 account sang lo khac khong duoc nam cach mot cu go
nham trong khung chat.

Nen `mutates=True` la mot khai bao, va mat truoc PHAI hoi lai truoc khi chay
chung. Mac dinh cua moi lenh la doc.

⛔ VA MOI LENH TRA VE DU LIEU, KHONG PHAI CHUOI DA TRINH BAY. Mat truoc quyet
dinh cach hien: terminal in bang, Telegram in gon, mail in day du. Tron hai
viec vao nhau thi mat truoc thu hai phai go lai tu dau.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

#: Chu ky cua mot lenh: nhan tham so dang dict, tra ve du lieu.
Handler = Callable[..., Any]


@dataclass(frozen=True)
class Command:
    name: str
    summary: str
    handler: Handler
    #: True = lenh THAY DOI du lieu. Mat truoc phai hoi lai truoc khi chay.
    mutates: bool = False
    #: Ten tham so -> mo ta, de mat truoc tu dung duoc huong dan.
    params: Dict[str, str] = field(default_factory=dict)


REGISTRY: Dict[str, Command] = {}


def register(
    name: str,
    summary: str,
    *,
    mutates: bool = False,
    params: Optional[Dict[str, str]] = None,
) -> Callable[[Handler], Handler]:
    """Dang ky mot lenh. Them lenh moi = them mot ham co decorator nay."""

    def decorator(handler: Handler) -> Handler:
        if name in REGISTRY:
            raise ValueError(f"Lenh {name!r} da duoc dang ky.")
        REGISTRY[name] = Command(
            name=name,
            summary=summary,
            handler=handler,
            mutates=mutates,
            params=dict(params or {}),
        )
        return handler

    return decorator


class UnknownCommand(KeyError):
    """Khong co lenh do. Kem danh sach lenh co that."""


class ConfirmationRequired(PermissionError):
    """Lenh nay thay doi du lieu va chua duoc xac nhan."""


def run(name: str, params: Optional[Dict[str, Any]] = None, *, confirm: bool = False) -> Any:
    """Chay mot lenh.

    ⛔ LENH SUA DOI HOI `confirm=True`. Mat truoc tu xa (Telegram) phai hoi
    nguoi dung truoc roi moi goi lai voi co nay - chu khong phai tu dat no.
    """
    lenh = REGISTRY.get(name)
    if lenh is None:
        raise UnknownCommand(
            f"Khong co lenh {name!r}. Co: " + ", ".join(sorted(REGISTRY))
        )
    if lenh.mutates and not confirm:
        raise ConfirmationRequired(
            f"Lenh {name!r} thay doi du lieu ({lenh.summary}). "
            "Xac nhan roi chay lai."
        )
    return lenh.handler(**(params or {}))


def describe() -> list[dict[str, Any]]:
    """Danh sach lenh, de mat truoc tu sinh huong dan."""
    return [
        {
            "name": c.name,
            "summary": c.summary,
            "mutates": c.mutates,
            "params": c.params,
        }
        for c in sorted(REGISTRY.values(), key=lambda x: (x.mutates, x.name))
    ]
