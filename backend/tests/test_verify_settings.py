"""Chon 2FA hay OTP: mac dinh uu tien 2FA, va quy tac should_use_2fa."""
from __future__ import annotations

import pytest

from app.core import verify_settings as V


def test_mac_dinh_la_2fa():
    assert V.DEFAULT_VERIFY_METHOD == "2fa"
    assert set(V.VERIFY_METHODS) == {"2fa", "otp"}


def test_normalize():
    assert V.normalize("  2FA ") == "2fa"
    assert V.normalize("OTP") == "otp"
    assert V.normalize("linh tinh") is None
    assert V.normalize(None) is None


def test_should_use_2fa():
    # "2fa": co khoa -> dung; khong khoa -> khong (tu lui ve OTP)
    assert V.should_use_2fa("2fa", True) is True
    assert V.should_use_2fa("2fa", False) is False
    # "otp": luon khong, du co khoa
    assert V.should_use_2fa("otp", True) is False
    assert V.should_use_2fa("otp", False) is False


def test_set_sai_thi_nem():
    with pytest.raises(ValueError):
        V.set_verify_method("sms")
