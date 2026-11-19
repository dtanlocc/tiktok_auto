"""Khoa 2FA di tron ven tu DB -> entity -> luong login.

Ba viec:
1. `totp_secret` luu xuong SQLite va doc lai dung (round-trip).
2. Man hinh authenticator duoc phan biet voi man email-OTP bang CHU, khong
   bang o nhap ma (hai man deu co o nhap).
3. Nhanh authenticator sinh ma tu khoa va go vao - KHONG dung toi hom thu;
   va nick khong co khoa thi bao loi ro chu khong di doc mail.

⛔ KHONG KHOA 2FA THAT NAO O DAY. base32 bia.
"""
from __future__ import annotations

import asyncio

import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.domain.entities.account import TikTokAccount
from app.infrastructure.database.schemas import AccountDbTable  # noqa: F401
from app.infrastructure.database.sqlite_repository import SQLiteAccountRepository
from app.use_cases.auth import login_strategies as L

_FAKE_SECRET = "ABCDEFGHIJKLMNOP2345672345674445"


# --------------------------------------------------------------------------
# 1) Round-trip luu tru
# --------------------------------------------------------------------------
def test_totp_secret_luu_va_doc_lai(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'acc.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as handle:
        repo = SQLiteAccountRepository(handle)
        repo.save(TikTokAccount(
            id="u@local.test", username="u_one", email="u@local.test",
            totp_secret=_FAKE_SECRET,
        ))
        doc = repo.get_by_id("u@local.test")
    assert doc is not None
    assert doc.totp_secret == _FAKE_SECRET


def test_nick_khong_co_2fa_thi_la_none(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'acc.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as handle:
        repo = SQLiteAccountRepository(handle)
        repo.save(TikTokAccount(id="v@local.test", username="v_one", email="v@local.test"))
        doc = repo.get_by_id("v@local.test")
    assert doc.totp_secret is None


# --------------------------------------------------------------------------
# 2) Phan biet man hinh
# --------------------------------------------------------------------------
class _FakePage:
    def __init__(self, body_text: str):
        self._text = body_text

    async def evaluate(self, _script, *args):
        return self._text


def test_man_authenticator_nhan_ra_bang_chu():
    page = _FakePage("Enter the 6-digit code from your authenticator app")
    assert asyncio.run(L._is_authenticator_screen(page)) is True


def test_man_email_khong_bi_nham_la_authenticator():
    page = _FakePage("Enter the 6-digit code sent to g***1@hotmail.com")
    assert asyncio.run(L._is_authenticator_screen(page)) is False


def test_doc_chu_hong_thi_coi_nhu_khong_phai_authenticator():
    class _Broken:
        async def evaluate(self, _s, *a):
            raise RuntimeError("context destroyed")
    assert asyncio.run(L._is_authenticator_screen(_Broken())) is False


# --------------------------------------------------------------------------
# 3) Detector chon dung nhanh (authenticator truoc email)
# --------------------------------------------------------------------------
class _Loc:
    """Locator gia: co visible hay khong theo co."""
    def __init__(self, visible: bool):
        self._v = visible

    def __call__(self, *a, **k):
        return self

    @property
    def first(self):
        return self

    async def is_visible(self):
        return self._v

    async def count(self):
        return 1 if self._v else 0


def _run_detect(page, email_vis, otp_vis):
    async def go():
        return await L._wait_verification_screen(
            page,
            browser=_NoCaptchaBrowser(),
            email_channel=_Loc(email_vis),
            otp_input=_Loc(otp_vis),
            timeout_seconds=2.0,
        )
    return asyncio.run(go())


class _NoCaptchaBrowser:
    is_captcha_present = None

    async def wait_captcha_cleared(self, *a, **k):
        return None


def test_detector_tra_authenticator_khi_co_o_ma_va_chu_authenticator():
    page = _FakePage("Enter the code from your authenticator app")
    assert _run_detect(page, email_vis=False, otp_vis=True) == "authenticator"


def test_detector_khong_nham_email_thanh_authenticator():
    # O ma hien, nhung chu la cua email -> phai ra "otp", khong phai authenticator.
    page = _FakePage("Enter the 6-digit code sent to g***@hotmail.com")
    assert _run_detect(page, email_vis=False, otp_vis=True) == "otp"
