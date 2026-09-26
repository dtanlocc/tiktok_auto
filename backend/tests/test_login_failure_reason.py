"""A failed login must say what TikTok actually answered.

Measured 24/09/2026 on the THAITEST batch, three accounts, three answers:
"Incorrect account or password. 5 attempts remaining", "Maximum number of
attempts reached", and one account that was fine and only needed its 2-step
code. All three reached the operator as "Đăng nhập thất bại", so all three
were retried alike - and retrying the first is what produces the second.
"""
import asyncio

import pytest

from app.use_cases.auth import login_strategies
from app.use_cases.auth.tiktok_login import TikTokLoginUseCase, login_failure_step


def test_a_wrong_password_says_so_and_keeps_the_count():
    line = login_failure_step(
        "Incorrect account or password. 5 attempts remaining. Try again."
    )
    assert "Sai mật khẩu" in line
    assert "5" in line


def test_a_spent_allowance_is_not_called_a_wrong_password():
    line = login_failure_step("Maximum number of attempts reached. Try again later.")
    assert "Hết lượt" in line
    assert "Sai mật khẩu" not in line


def test_two_step_verification_is_named():
    line = login_failure_step(
        "We've turned on 2-step verification for your account. Go to tiktok.com"
    )
    assert "2 bước" in line


def test_a_missing_otp_mail_is_not_blamed_on_the_password():
    line = login_failure_step("Khong tim thay thu chua ma OTP trong hom thu.")
    assert "OTP" in line
    assert "Sai mật khẩu" not in line


def test_an_unknown_refusal_is_passed_through_verbatim():
    line = login_failure_step("Sesame street is closed")
    assert "Sesame street is closed" in line


def test_no_refusal_falls_back_to_the_plain_sentence():
    assert login_failure_step("") == "Đăng nhập thất bại"
    assert login_failure_step(None) == "Đăng nhập thất bại"


class _Account:
    def __init__(self):
        self.email = "someone@hotmail.com"
        self.username = "someone"
        self.password = "pw"
        self.cookies = []
        self.status = "IDLE"
        self.current_step = ""
        self.health_status = "ALIVE"


class _Repo:
    def __init__(self, account):
        self.account = account
        self.saved = []

    def get_by_id(self, _account_id):
        return self.account

    def save(self, account):
        self.saved.append(account.current_step)
        return account

    def save_prioritizing_username(self, account):
        return account, None


class _Browser:
    async def extract_cookies(self):
        return []


class _RefusingStrategy:
    def __init__(self, refusal):
        self.last_refusal = refusal

    async def login(self, *_args, **_kwargs):
        return False


def test_the_account_record_keeps_the_reason(monkeypatch):
    account = _Account()
    repo = _Repo(account)
    use_case = TikTokLoginUseCase(account_repo=repo, browser_service=_Browser())
    monkeypatch.setattr(
        "app.use_cases.auth.tiktok_login.LoginStrategyFactory.get_strategy",
        lambda _method: _RefusingStrategy(
            "Incorrect account or password. 5 attempts remaining."
        ),
    )

    assert asyncio.run(use_case.execute("someone@hotmail.com", "CREDENTIAL")) is False
    assert account.status == "ERROR"
    assert "Sai mật khẩu" in account.current_step
    assert "5" in account.current_step


def test_the_combined_strategy_hands_the_reason_up(monkeypatch):
    class _Inner:
        def __init__(self):
            self.last_refusal = ""

        async def login(self, *_args, **_kwargs):
            self.last_refusal = "Maximum number of attempts reached."
            return False

    monkeypatch.setattr(login_strategies, "CredentialEmailOtpLoginStrategy", _Inner)
    strategy = login_strategies.CookieThenCredentialLoginStrategy()

    class _NoCookieAccount(_Account):
        pass

    ok = asyncio.run(
        strategy.login(_Browser(), _NoCookieAccount(), step_logger=None)
    )
    assert ok is False
    assert strategy.last_refusal == "Maximum number of attempts reached."


def test_a_server_error_that_will_not_clear_is_named_for_what_it_is():
    """Measured on a whole batch: fields filled, button live, this line stuck."""
    line = login_failure_step("Internal server error. Please try again later.")
    assert "lỗi máy chủ" in line
    assert "Đăng nhập thất bại" not in line


def test_the_other_wrong_password_wording_is_recognised():
    """Measured over the VPN: TikTok says this instead of 'Incorrect ...'."""
    line = login_failure_step(
        "Username or password doesn't match our records. Try again."
    )
    assert "Sai mật khẩu" in line
    assert "Đăng nhập thất bại" not in line
