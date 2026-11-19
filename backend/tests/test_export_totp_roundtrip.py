"""Export/Copy phai GIU khoa 2FA, va nick thuong giu nguyen 7 truong.

⛔ EXPORT XOA NICK KHOI DB. Neu dong xuat ra khong co khoa 2FA thi export mot
nick 2FA = mat khoa vinh vien. Bai nay giu cho dieu do khong tai dien, va cung
giu cho nick khong-2FA van dung dinh dang 7 truong cu (tuong thich nguoc).

⛔ base32 BIA, khong khoa that.
"""
from __future__ import annotations

from app.interfaces.api.accounts_router import _account_to_export_line
from app.domain.entities.account import TikTokAccount
from app.use_cases.auth.totp import looks_like_totp_secret

_FAKE = "ABCDEFGHIJKLMNOP2345672345674445"


def _reimport(line: str):
    """Lam dung vong phan loai cua endpoint import tren mot dong."""
    parts = line.split("|")
    cookies_raw, totp = "", ""
    for extra in parts[6:]:
        extra = extra.strip()
        if not extra:
            continue
        if not totp and looks_like_totp_secret(extra):
            totp = extra
        elif not cookies_raw:
            cookies_raw = extra
    return len(parts), cookies_raw, totp


def _acc(**kw):
    base = dict(id="x", username="u", password="p", email="e@h.com",
               email_password="ep", refresh_token="rt", client_id="cid")
    base.update(kw)
    return TikTokAccount(**base)


def test_nick_2fa_giu_khoa_khi_export():
    line = _account_to_export_line(_acc(totp_secret=_FAKE))
    n, cookies, totp = _reimport(line)
    assert totp == _FAKE, "export lam mat khoa 2FA!"
    assert cookies == ""


def test_nick_thuong_van_7_truong():
    # Khong 2FA, khong cookie -> dinh dang 7 truong nhu cu (tuong thich nguoc).
    line = _account_to_export_line(_acc())
    assert line.count("|") == 6   # 7 truong = 6 dau |
    _, _, totp = _reimport(line)
    assert totp == ""


def test_nick_co_ca_cookie_va_2fa():
    acc = _acc(
        cookies=[{"name": "sessionid", "value": "abc",
                  "domain": ".tiktok.com", "path": "/"}],
        totp_secret=_FAKE,
    )
    line = _account_to_export_line(acc)
    n, cookies, totp = _reimport(line)
    assert totp == _FAKE
    assert cookies != ""          # cookie khong bi khoa 2FA "nuot"


def test_khoa_2fa_khong_bi_nham_voi_cookie_khi_reimport():
    # Thu tu: cookie (truong 7) roi totp (truong 8). Phan loai theo hinh dang,
    # khong theo vi tri -> moi cai ve dung cho.
    acc = _acc(
        cookies=[{"name": "tt_csrf", "value": "xyz",
                  "domain": ".tiktok.com", "path": "/"}],
        totp_secret=_FAKE,
    )
    _, cookies, totp = _reimport(_account_to_export_line(acc))
    assert totp == _FAKE and cookies != ""
