"""Bu lech dong ho cho TOTP: cache, fallback, va ap dung dung dau.

⛔ KHONG GOI NTP THAT trong test - mock `_hoi_mot_may`. Test khong duoc phu
thuoc mang, va cang khong duoc gui goi ra ngoai moi lan chay.
"""
from __future__ import annotations

import time

import pytest

from app.use_cases.auth import clock


@pytest.fixture(autouse=True)
def reset_cache():
    # Moi test bat dau voi cache sach, va khong de lai cho test sau.
    clock._cache.update(offset=0.0, do_luc=0.0, ok=False)
    yield
    clock._cache.update(offset=0.0, do_luc=0.0, ok=False)


def test_do_duoc_lech_thi_tra_ve_lech(monkeypatch):
    monkeypatch.setattr(clock, "_hoi_mot_may", lambda host, timeout=3.0: 6.85)
    assert clock.do_lech() == pytest.approx(6.85)


def test_cache_khong_hoi_lai_trong_ttl(monkeypatch):
    lan = {"n": 0}

    def gia(host, timeout=3.0):
        lan["n"] += 1
        return 3.0
    monkeypatch.setattr(clock, "_hoi_mot_may", gia)
    clock.do_lech()
    clock.do_lech()
    clock.do_lech()
    assert lan["n"] == 1, "da hoi NTP nhieu lan du con trong TTL"


def test_force_do_lai(monkeypatch):
    lan = {"n": 0}

    def gia(host, timeout=3.0):
        lan["n"] += 1
        return 1.0
    monkeypatch.setattr(clock, "_hoi_mot_may", gia)
    clock.do_lech()
    clock.do_lech(force=True)
    assert lan["n"] == 2


def test_ntp_hong_thi_lui_ve_khong_khong_nem(monkeypatch):
    def hong(host, timeout=3.0):
        raise OSError("mang chet")
    monkeypatch.setattr(clock, "_hoi_mot_may", hong)
    # Khong duoc nem, va tra 0.0 (dung dong ho may).
    assert clock.do_lech() == 0.0


def test_gio_chuan_bang_dong_ho_cong_lech(monkeypatch):
    monkeypatch.setattr(clock, "_hoi_mot_may", lambda host, timeout=3.0: 10.0)
    truoc = time.time() + 10.0
    g = clock.gio_chuan()
    sau = time.time() + 10.0
    assert truoc - 1 <= g <= sau + 1


def test_gio_chuan_dung_de_sinh_ma_lech_di(monkeypatch):
    # Chung minh bu lech thuc su doi ma: cung khoa, hai moc gio cach 30s+ ra
    # hai ma khac nhau, nen mot offset du lon se doi ma.
    from app.use_cases.auth.totp import totp_now
    secret = "ABCDEFGHIJKLMNOP2345672345674445"
    t = 1_700_000_000
    ma_goc = totp_now(secret, at=t)
    ma_lech = totp_now(secret, at=t + 45)   # sang cua so khac
    assert ma_goc != ma_lech
