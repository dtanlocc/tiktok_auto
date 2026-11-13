"""Dich cua lenh: lam gi, TREN NHUNG NICK NAO.

⛔ TRUOC DAY MOI LENH TU NGHI RA CACH CHON RIENG. `dong_bo` nhan
`chon=never`, `dang_nhap` nhan `lo=...`, `check_nhanh` cung `lo=...` nhung
khong nhan gi khac - nen "dong bo dung mot lo" khong co duong nao lam. Nguoi
dung phai nho lenh nao nhan gi.

⛔ VA DICH RONG LA MOT LOI, KHONG PHAI "KHONG LAM GI". Go nham ten lo ma lenh
lang le chay tren 0 nick thi nguoi dung tuong da chay xong, va doi mot ket qua
khong bao gio toi.
"""
from __future__ import annotations

import pytest

from app.use_cases.control import targets
from app.use_cases.control.targets import Dich, TargetError, tu_tham_so


class _Row:
    def __init__(self, email="a@b.com", username="nick1", batch_tag="lo1",
                 country="US", analytics_sync_status="NEVER", follower_count=None):
        self.email = email
        self.username = username
        self.batch_tag = batch_tag
        self.country = country
        self.analytics_sync_status = analytics_sync_status
        self.follower_count = follower_count


@pytest.fixture
def kho(monkeypatch):
    """Thay phan doc database bang mot kho trong bo nho."""
    rows = [
        _Row("a@b.com", "nick1", "lo1", "US"),
        _Row("c@d.com", "nick2", "lo1", "DE", "SUCCESS", 120),
        _Row("e@f.com", "nick3", "lo2", "US", "FAILED"),
    ]

    def gia(ham):
        def boc(session, gia_tri):
            return ham(rows, gia_tri)
        return boc

    monkeypatch.setitem(targets.RESOLVERS, "tat_ca", gia(lambda r, v: r))
    monkeypatch.setitem(targets.RESOLVERS, "lo",
                        gia(lambda r, v: [x for x in r if x.batch_tag == str(v)]))
    monkeypatch.setitem(targets.RESOLVERS, "nuoc",
                        gia(lambda r, v: [x for x in r if x.country == str(v).upper()]))
    monkeypatch.setitem(
        targets.RESOLVERS, "nick",
        gia(lambda r, v: [x for x in r
                          if x.email in targets._nhu_danh_sach(v)
                          or x.username in targets._nhu_danh_sach(v)]))
    monkeypatch.setattr(targets, "Session", lambda *a, **k: _NoSession())
    return rows


class _NoSession:
    def __enter__(self): return None
    def __exit__(self, *a): return False


def test_every_kind_of_target_resolves(kho):
    assert len(targets.phan_giai(Dich("tat_ca"))) == 3
    assert len(targets.phan_giai(Dich("lo", "lo1"))) == 2
    assert len(targets.phan_giai(Dich("nuoc", "us"))) == 2
    assert len(targets.phan_giai(Dich("nick", "nick3"))) == 1


def test_a_comma_separated_list_works_from_the_command_line(kho):
    """Dong lenh khong gui duoc mang JSON, nen "a,b" phai dung duoc."""
    assert len(targets.phan_giai(Dich("nick", "nick1,nick3"))) == 2


def test_an_empty_target_is_an_error_not_silence(kho):
    """⛔ Day la dieu de nham nhat: 0 nick trong im lang."""
    with pytest.raises(TargetError, match="Không có nick nào khớp"):
        targets.phan_giai(Dich("lo", "lo-khong-co"))


def test_a_target_that_needs_a_value_says_so(kho):
    with pytest.raises(TargetError, match="cần một giá trị"):
        targets.phan_giai(Dich("lo", ""))


def test_an_unknown_kind_lists_the_real_ones(kho):
    with pytest.raises(TargetError, match="tat_ca"):
        targets.phan_giai(Dich("lung_tung", "x"))


def test_tat_ca_does_not_need_a_value(kho):
    assert targets.phan_giai(Dich("tat_ca")) is not None


def test_the_description_is_readable():
    assert Dich("tat_ca").mo_ta() == "tất cả nick"
    assert "us sdt" in Dich("lo", "us sdt 9/11").mo_ta()


def test_building_a_target_from_parameters_is_forgiving():
    assert tu_tham_so("  LO  ", "x") == Dich("lo", "x")
    assert tu_tham_so() == Dich("tat_ca", None)


def test_adding_a_target_kind_is_one_line():
    """⛔ Yeu cau that: de mo rong. Them kieu dich khong duoc dong vao lenh nao."""
    targets.RESOLVERS["thu_nghiem"] = lambda session, v: [_Row()]
    try:
        assert "thu_nghiem" in targets.RESOLVERS
    finally:
        targets.RESOLVERS.pop("thu_nghiem")
