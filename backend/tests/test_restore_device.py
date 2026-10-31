"""Go thu hoi mot thiet bi: co that su mo duoc ngo cut, va khong lach gioi han.

⛔ BAI TEST NAY RA DOI TU MOT MAY DA BI KET THAT (31/10/2026).

Thu hoi thiet bi xong thi may do khong con duong nao quay lai:
  - kich hoat lai bang CHINH key cu -> `device_revoked` (cung license thi thu
    hoi van chan, co y);
  - kich hoat bang key MOI          -> HTTP 409 `device_conflict`, vi chuyen
    thiet bi sang license khac doi license CU phai da chet - ma no con 13 ngay.

Duong duy nhat con lai la thu hoi CA license, tuc giet mot key con han chi de
cuu mot cai may. Mot hanh dong thu hoi do chinh operator lam khong nen la mot
ban an vinh vien, nen co `restore-device`.

⛔ NHUNG NO KHONG DUOC THANH DUONG VONG QUA `max_devices`. Go thu hoi la THEM
LAI mot may dang hoat dong. Neu bo phep dem o day thi mot license 1 may co the
nuoi hai may: thu hoi may A, kich hoat may B, roi go thu hoi may A.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import supabase_license_admin as cli  # noqa: E402

_NOW = int(time.time())


class _Cursor:
    """Tra loi hai cau truy van ma `restore_device` goi, theo dung thu tu."""

    def __init__(self, hang_thiet_bi, so_may_dang_dung=0, rowcount=1):
        self.hang_thiet_bi = hang_thiet_bi
        self.so_may_dang_dung = so_may_dang_dung
        self.rowcount = rowcount
        self.cau_lenh: list[str] = []
        self._tra_ve = None

    def execute(self, sql, params=()):
        self.cau_lenh.append(" ".join(sql.split()))
        if "from license.devices d" in sql:
            self._tra_ve = self.hang_thiet_bi
        elif "select count(*)" in sql:
            self._tra_ve = (self.so_may_dang_dung,)
        else:
            self._tra_ve = None

    def fetchone(self):
        return self._tra_ve


def _thiet_bi(device_revoked=_NOW - 60, lic_revoked=None, expires_at=_NOW + 86_400,
              max_devices=1, khach="ban A"):
    return ("lic_x", device_revoked, lic_revoked, expires_at, max_devices, khach)


def test_a_revoked_device_comes_back():
    cursor = _Cursor(_thiet_bi(), so_may_dang_dung=0)
    ket_qua = cli.restore_device(cursor, "device_x", "go ket", "operator")

    assert ket_qua["dang_dung"] == 1
    assert ket_qua["max_devices"] == 1
    assert any("set revoked_at = null" in c for c in cursor.cau_lenh)
    assert any("device.restored" in c for c in cursor.cau_lenh)


def test_an_unknown_device_is_not_invented():
    cursor = _Cursor(None)
    with pytest.raises(LookupError, match="Khong thay thiet bi"):
        cli.restore_device(cursor, "device_la", "", "operator")


def test_a_device_that_was_never_revoked_is_left_alone():
    cursor = _Cursor(_thiet_bi(device_revoked=None))
    with pytest.raises(cli.RestoreRefused, match="dang hieu luc"):
        cli.restore_device(cursor, "device_x", "", "operator")


@pytest.mark.parametrize(
    ("lic_revoked", "expires_at"),
    [(_NOW - 10, _NOW + 86_400), (None, _NOW - 1)],
)
def test_a_dead_license_cannot_be_revived_through_its_device(lic_revoked, expires_at):
    """Go thu hoi thiet bi duoi mot key da chet thi khong giup gi - noi that."""
    cursor = _Cursor(_thiet_bi(lic_revoked=lic_revoked, expires_at=expires_at))
    with pytest.raises(cli.RestoreRefused, match="thu hoi hoac het han"):
        cli.restore_device(cursor, "device_x", "", "operator")


def test_restoring_cannot_push_a_license_past_its_device_limit():
    """⛔ Khong thi: thu hoi may A, kich hoat may B, go thu hoi may A -> 2 may."""
    cursor = _Cursor(_thiet_bi(max_devices=1), so_may_dang_dung=1)
    with pytest.raises(cli.RestoreRefused, match="1/1 may"):
        cli.restore_device(cursor, "device_x", "", "operator")

    assert not any("set revoked_at = null" in c for c in cursor.cau_lenh)


def test_the_row_is_locked_while_it_is_checked():
    """Khong khoa thi hai lan go thu hoi song song deu thay con cho trong."""
    cursor = _Cursor(_thiet_bi())
    cli.restore_device(cursor, "device_x", "", "operator")
    assert any("for update of d, l" in c for c in cursor.cau_lenh)


def test_the_cli_and_the_web_page_share_one_implementation():
    """Hai ban kiem tra o hai noi thi som muon cung lech."""
    page = (_REPO_ROOT / "scripts" / "key_manager.py").read_text(encoding="utf-8")
    assert "cli.restore_device(" in page
    assert "cli.RestoreRefused" in page
