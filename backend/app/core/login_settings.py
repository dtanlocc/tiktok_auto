"""Chon ten dang nhap go vao o "Email or username" cua TikTok.

⛔ DAY LA MOT LUA CHON, KHONG PHAI MOT CHAN LI - va ca hai huong deu tung sai
that tren may that:

  - 18/09/2026, treft21664: email bi tu choi "Account doesn't exist", vao duoc
    ngay bang @username;
  - 31/10/2026, chu so huu: username trong danh sach nhap vao hay sai (go nham,
    doi nick, nick cu), con hop thu thi gan voi tai khoan va khong doi.

Nen no thuoc ve nguoi van hanh chu khong phai ve mot dong code: ai biet lo
account cua minh duoc nhap tu dau thi nguoi do chon dung hon.

⛔ VA DOI DUOC MA KHONG RESTART, theo dung khuon cua `USE_PROXY`. Mot lua chon
chi song trong bo nho se tro ve mac dinh moi lan khoi dong lai backend - im
lang, trong khi trang dang mo van hien lua chon cu.
"""
from __future__ import annotations

from app.core.config import settings
from app.core.extension_settings import _read_document, _settings_path, _write_setting
from app.core.extension_settings import _SETTINGS_LOCK

_LOGIN_IDENTIFIER_KEY = "login_identifier"

#: ⛔ THEM MOT KIEU LA THEM MOT DONG O DAY, khong phai sua `_login_identifier`.
#: Moi gia tri la thu tu uu tien cac truong cua account; truong dau tien co du
#: lieu dung dang se duoc go vao form.
IDENTIFIER_ORDER: dict[str, tuple[str, ...]] = {
    "email": ("email", "username"),
    "username": ("username", "email"),
}

DEFAULT_IDENTIFIER = "email"


def normalize(mode: object) -> str | None:
    """Ten kieu hop le, hoac None. Nhan ca chu hoa va khoang trang thua."""
    if not isinstance(mode, str):
        return None
    value = mode.strip().lower()
    return value if value in IDENTIFIER_ORDER else None


def load_login_identifier() -> str:
    """Kieu nguoi van hanh chon lan cuoi, song qua restart."""
    with _SETTINGS_LOCK:
        stored = _read_document(_settings_path()).get(_LOGIN_IDENTIFIER_KEY)
    return (
        normalize(stored)
        or normalize(getattr(settings, "LOGIN_IDENTIFIER", None))
        or DEFAULT_IDENTIFIER
    )


def set_login_identifier(mode: str) -> str:
    value = normalize(mode)
    if value is None:
        raise ValueError(
            "Kieu dang nhap khong hop le: "
            + repr(mode)
            + ". Chon mot trong: "
            + ", ".join(sorted(IDENTIFIER_ORDER))
        )
    _write_setting(_LOGIN_IDENTIFIER_KEY, value)
    settings.LOGIN_IDENTIFIER = value
    return value
