"""Tai lieu lenh phai phu het so dang ky.

⛔ MOT TAI LIEU LAC HAU TE HON KHONG CO TAI LIEU. Nguoi doc tin no, go theo
no, va that bai o mot cho khong lien quan den viec ho dang lam. Them mot lenh
roi quen ghi vao docs la chuyen xay ra trong mot phut; phat hien ra no la
chuyen cua vai tuan sau.

Bai test nay doc SO DANG KY THAT va doi moi lenh, moi tham so deu xuat hien
trong docs/LENH-BOT.md.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.use_cases.control import commands  # noqa: F401  (dang ky lenh)
from app.use_cases.control.registry import REGISTRY

_DOC = Path(__file__).resolve().parents[2] / "docs" / "LENH-BOT.md"


@pytest.fixture(scope="module")
def noi_dung() -> str:
    assert _DOC.is_file(), f"Thieu {_DOC}"
    return _DOC.read_text(encoding="utf-8")


def test_every_command_is_documented(noi_dung):
    thieu = [ten for ten in REGISTRY if f"`{ten}`" not in noi_dung]
    assert not thieu, f"Lenh chua co trong docs: {thieu}"


def test_every_parameter_is_documented(noi_dung):
    thieu = []
    for ten, lenh in REGISTRY.items():
        for tham_so in lenh.params:
            if f"`{tham_so}`" not in noi_dung:
                thieu.append(f"{ten}.{tham_so}")
    assert not thieu, f"Tham so chua co trong docs: {thieu}"


def test_the_docs_do_not_invent_commands(noi_dung):
    """Chieu nguoc lai: docs khong duoc ta mot lenh khong ton tai."""
    import re

    # Chi xet cac tieu de muc lenh, dang "## `ten`".
    ten_trong_docs = set(re.findall(r"^## `([a-z_0-9]+)`", noi_dung, re.M))
    # Mot vai muc gop nhieu lenh tren cung mot tieu de.
    ten_trong_docs |= set(re.findall(r"^## ((?:`[a-z_0-9]+`(?: · )?)+)$", noi_dung, re.M))
    tach = set()
    for muc in ten_trong_docs:
        tach |= set(re.findall(r"[a-z_0-9]+", muc))
    bia = tach - set(REGISTRY)
    assert not bia, f"Docs ta lenh khong co that: {sorted(bia)}"


def test_mutating_commands_are_marked_as_needing_confirmation(noi_dung):
    """⛔ Nguoi doc phai biet lenh nao doi `--xac-nhan` TRUOC khi go."""
    assert "--xac-nhan" in noi_dung
    for ten, lenh in REGISTRY.items():
        if not lenh.mutates:
            continue
        # Ten lenh phai xuat hien sau tieu de phan "LENH SUA DU LIEU".
        phan_sua = noi_dung.split("# LỆNH SỬA DỮ LIỆU", 1)
        assert len(phan_sua) == 2, "Docs thieu phan 'LENH SUA DU LIEU'"
        assert f"`{ten}`" in phan_sua[1], f"{ten} sua du lieu nhung khong nam trong phan do"
