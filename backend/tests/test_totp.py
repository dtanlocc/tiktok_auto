"""TOTP doi chieu VECTOR CHUAN cua RFC 6238, va bo phan loai khoa vs cookie.

Neu bai nay do, dung tin ham sinh ma: mot nick se login hong giua dem va
khong ai biet vi sao.

⛔ KHONG DAT KHOA 2FA THAT VAO DAY. Repo nay public, va khoa 2FA la ca yeu to
thu hai - vao git la mai mai. Moi khoa duoi day la base32 BIA, chi de thu
hinh dang va thuat toan.
"""
from __future__ import annotations

import base64

import pytest

from app.use_cases.auth.totp import (
    looks_like_totp_secret,
    normalize_secret,
    seconds_remaining,
    totp_now,
)

# RFC 6238 Appendix B: khoa ASCII "12345678901234567890" (20 byte), SHA1.
# Base32 cua khoa do (cong khai, nam trong chinh ban RFC).
_RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()

# Cac moc thoi gian va ma 8 chu so trong bang cua RFC (SHA1). Ta cat 6 chu so
# cuoi vi dich vu that (TikTok) dung 6.
_RFC_VECTORS = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]

# Khoa BIA cho cac bai hinh-dang (khong phai khoa cua ai).
_FAKE_32 = "ABCDEFGHIJKLMNOP2345672345674445"


@pytest.mark.parametrize("t, eight", _RFC_VECTORS)
def test_khop_vector_chuan_rfc6238(t, eight):
    assert totp_now(_RFC_SECRET, at=t, digits=8) == eight
    # 6 chu so la 6 chu so CUOI cua ma 8 chu so.
    assert totp_now(_RFC_SECRET, at=t) == eight[-6:]


def test_luon_du_sau_chu_so_ke_ca_khi_co_so_0_dau():
    # Quet nhieu moc, khong ma nao duoc ngan hon 6 ky tu.
    for t in range(0, 300, 7):
        assert len(totp_now(_RFC_SECRET, at=t)) == 6


def test_normalize_bo_dau_cach_viet_hoa_va_dem():
    # Khoa that nguoi dung hay dan kem dau cach va chu thuong.
    goc = "abcdefgh ijklmnop 23456723 45674445"
    chuan = normalize_secret(goc)
    assert chuan == _FAKE_32                # 32 ky tu, du boi so 8
    assert len(chuan) % 8 == 0
    # Dan lech khoang trang van ra cung mot ma.
    assert totp_now(goc, at=59) == totp_now(_FAKE_32, at=59)


def test_khoa_rong_la_loi_ro_rang():
    with pytest.raises(ValueError):
        normalize_secret("   -- __ ")


def test_phan_loai_khoa_2fa():
    # base32 lien tuc, du dai (16 hoac 32) -> la khoa.
    assert looks_like_totp_secret(_FAKE_32)
    assert looks_like_totp_secret("ABCDEFGHIJKLMNOP")      # dung 16


def test_cookie_khong_bi_nham_la_khoa_2fa():
    # Cookie dang JSON mang Playwright.
    assert not looks_like_totp_secret('[{"name":"sessionid","value":"abc"}]')
    # Cookie dang chuoi 'a=b; c=d'.
    assert not looks_like_totp_secret("sessionid=abc123; tt_csrf=xyz")
    # Co '=' hoac ';' la khong con base32 thuan.
    assert not looks_like_totp_secret("ABCDEFGH=IJKLMNOP")
    # Chua ky tu '0','1','8','9' (khong thuoc base32) -> khong phai khoa.
    assert not looks_like_totp_secret("ABCDEFGH01189999")


def test_chuoi_ngan_khong_phai_khoa():
    # Nguong 16: ngan hon thi coi nhu khong phai khoa (tranh manh cookie ky la).
    assert not looks_like_totp_secret("ABCDEFG")           # 7 ky tu
    assert not looks_like_totp_secret("")


def test_so_giay_con_lai_nam_trong_chu_ky():
    assert seconds_remaining(at=0) == 30
    assert seconds_remaining(at=25) == 5
    assert 0 < seconds_remaining(at=29.5) <= 30
