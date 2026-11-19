"""Chon cach vuot man xac minh khi dang nhap: 2FA (authenticator) hay OTP email.

⛔ UU TIEN 2FA. Nick co khoa 2FA thi dung 2FA - nhanh, khong phu thuoc hom
thu. OTP email la duong lui (va cho nick khong co khoa). Che do "otp" ep doc
hom thu DU nick co khoa - de khi 2FA truc trac van dang nhap duoc.

⛔ VA DOI DUOC MA KHONG RESTART, theo dung khuon cua `login_settings`. Mot lua
chon chi song trong bo nho se tro ve mac dinh moi lan khoi dong lai backend.
"""
from __future__ import annotations

from app.core.config import settings
from app.core.extension_settings import _read_document, _settings_path, _write_setting
from app.core.extension_settings import _SETTINGS_LOCK

_VERIFY_METHOD_KEY = "verify_method"

#: Hai huong. "2fa" = co khoa thi dung authenticator, khong co thi lui ve OTP
#: email. "otp" = luon doc hom thu, bo qua khoa 2FA.
VERIFY_METHODS: tuple[str, ...] = ("2fa", "otp")

#: Mac dinh uu tien 2FA (dung mong doi cua nguoi dung).
DEFAULT_VERIFY_METHOD = "2fa"


def normalize(mode: object) -> str | None:
    """Ten che do hop le, hoac None. Nhan ca chu hoa va khoang trang thua."""
    if not isinstance(mode, str):
        return None
    value = mode.strip().lower()
    return value if value in VERIFY_METHODS else None


def load_verify_method() -> str:
    """Che do nguoi van hanh chon lan cuoi, song qua restart."""
    with _SETTINGS_LOCK:
        stored = _read_document(_settings_path()).get(_VERIFY_METHOD_KEY)
    return (
        normalize(stored)
        or normalize(getattr(settings, "VERIFY_METHOD", None))
        or DEFAULT_VERIFY_METHOD
    )


def set_verify_method(mode: str) -> str:
    value = normalize(mode)
    if value is None:
        raise ValueError(
            "Che do xac minh khong hop le: "
            + repr(mode)
            + ". Chon mot trong: "
            + ", ".join(sorted(VERIFY_METHODS))
        )
    _write_setting(_VERIFY_METHOD_KEY, value)
    settings.VERIFY_METHOD = value
    return value


def should_use_2fa(mode: str, has_key: bool) -> bool:
    """Co nen dung authenticator khong, theo che do + nick co khoa hay khong.

    - "otp": khong bao gio (luon doc hom thu).
    - "2fa": co khoa thi co; khong co khoa thi khong (tu lui ve OTP).
    """
    return mode != "otp" and bool(has_key)
