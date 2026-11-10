"""Ten dang nhap go vao TikTok la mot LUA CHON, doi duoc ma khong restart.

⛔ VI SAO DAY PHAI LA CAI DAT CHU KHONG PHAI MOT HANG SO. Ca hai ten deu tung
sai that tren may that:

  - 18/09/2026, treft21664: email bi tu choi "Account doesn't exist", vao duoc
    ngay bang @username;
  - 31/10/2026, chu so huu: username trong danh sach nhap vao hay sai (go nham,
    doi nick, nick cu), con hop thu thi gan voi tai khoan va khong doi.

Ghim cung huong nao cung dung voi mot nguoi va sai voi nguoi kia. Ai biet lo
account cua minh duoc nhap tu dau thi nguoi do chon dung hon.

⛔ VA NO PHAI SONG QUA RESTART. Mot lua chon chi nam trong bo nho se tro ve mac
dinh moi lan khoi dong lai backend - im lang, trong khi trang dang mo van hien
lua chon cu. Day la dung loi `USE_PROXY` tung mac va da duoc sua.
"""
from __future__ import annotations

import pytest

from app.core import login_settings
from app.domain.entities.account import TikTokAccount
from app.use_cases.auth import login_strategies


@pytest.fixture
def kho(tmp_path, monkeypatch):
    """Tai lieu cai dat that, tren dia that - khong phai mot ban gia."""
    monkeypatch.setattr(
        login_settings.settings,
        "BROWSER_EXTENSION_SETTINGS_PATH",
        str(tmp_path / "settings.json"),
        raising=False,
    )
    monkeypatch.setattr(
        login_settings.settings, "LOGIN_IDENTIFIER", "email", raising=False
    )
    return tmp_path / "settings.json"


def _account(username="stor1285", email="garrikbilliob@hotmail.com"):
    return TikTokAccount(id="acc-1", username=username, email=email)


def test_the_default_is_the_mailbox(kho):
    assert login_settings.load_login_identifier() == "email"
    assert login_strategies._login_identifier(_account()) == "garrikbilliob@hotmail.com"


def test_choosing_username_changes_what_gets_typed(kho):
    login_settings.set_login_identifier("username")
    assert login_strategies._login_identifier(_account()) == "stor1285"


def test_the_choice_survives_a_restart(kho):
    """⛔ Chi nam trong bo nho thi restart la mat, va khong ai duoc bao."""
    login_settings.set_login_identifier("username")
    assert kho.is_file(), "lua chon phai duoc ghi ra dia"

    # Khoi dong lai = doc lai tu dia, mac dinh trong code khong con anh huong.
    login_settings.settings.LOGIN_IDENTIFIER = "email"
    assert login_settings.load_login_identifier() == "username"


def test_a_name_that_is_missing_falls_through_to_the_other_one(kho):
    login_settings.set_login_identifier("email")
    assert login_strategies._login_identifier(_account(email="")) == "stor1285"

    login_settings.set_login_identifier("username")
    assert (
        login_strategies._login_identifier(_account(username=""))
        == "garrikbilliob@hotmail.com"
    )


def test_a_note_in_the_email_box_is_not_a_mailbox(kho):
    """⛔ Truong `email` co luc la ghi chu. Go no vao la vut di mot lan bam."""
    login_settings.set_login_identifier("email")
    assert login_strategies._login_identifier(_account(email="chua co mail")) == "stor1285"


def test_an_account_with_neither_name_has_nothing_to_type(kho):
    assert login_strategies._login_identifier(_account("", "")) == ""


@pytest.mark.parametrize("gia_tri", ["  USERNAME ", "Email", "username"])
def test_spelling_and_spacing_are_forgiven(kho, gia_tri):
    assert login_settings.normalize(gia_tri) is not None


@pytest.mark.parametrize("gia_tri", ["", "lung tung", None, 7, "e-mail"])
def test_an_unknown_mode_is_refused_instead_of_silently_ignored(kho, gia_tri):
    assert login_settings.normalize(gia_tri) is None
    with pytest.raises(ValueError, match="khong hop le"):
        login_settings.set_login_identifier(gia_tri)


def test_a_corrupt_settings_file_does_not_break_logins(kho):
    """Doc hong thi quay ve mac dinh, khong duoc nem len duong dang nhap."""
    kho.write_text("{ day khong phai json", encoding="utf-8")
    assert login_settings.load_login_identifier() == "email"


def test_adding_a_mode_is_one_line(kho):
    """⛔ Yeu cau that: 'de mo rong hon'. Them kieu moi khong duoc dong vao
    `_login_identifier` - chi them mot dong vao bang thu tu."""
    login_settings.IDENTIFIER_ORDER["chi_email"] = ("email",)
    try:
        login_settings.set_login_identifier("chi_email")
        assert login_strategies._login_identifier(_account()) == "garrikbilliob@hotmail.com"
        # Khong co email hop le -> khong go gi, KHONG roi ve username.
        assert login_strategies._login_identifier(_account(email="")) == ""
    finally:
        login_settings.IDENTIFIER_ORDER.pop("chi_email")
