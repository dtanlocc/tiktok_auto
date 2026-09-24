"""The line about our own cleanup must not become the account's state.

Measured 24/09/2026: the login use case wrote "Sai mật khẩu - TikTok còn cho
5 lượt" onto the account, and a second later the worker's closing line - "Đã
đóng trình duyệt; luồng và proxy của account đã được giải phóng." - replaced
it. Every outcome, success or refusal, ended up reading the same sentence in
the account list, which is the problem the reason was written to solve.
"""
import asyncio
from types import SimpleNamespace

import pytest

from app.use_cases.orchestration import task_dispatcher as dispatcher_module
from app.use_cases.orchestration.task_dispatcher import ConcurrentTaskDispatcher


class _Account:
    def __init__(self):
        self.email = "someone@hotmail.com"
        self.username = "someone"
        self.current_step = "Sai mật khẩu - TikTok còn cho 5 lượt"


class _Repo:
    def __init__(self, account):
        self.account = account
        self.writes = 0

    def get_by_id(self, _account_id):
        return self.account

    def save(self, account):
        self.writes += 1
        return account


@pytest.fixture
def wired(monkeypatch):
    account = _Account()
    repo = _Repo(account)
    monkeypatch.setattr(dispatcher_module, "SQLiteAccountRepository", lambda _s: repo)

    async def no_broadcast(_payload):
        return None

    monkeypatch.setattr(dispatcher_module.ws_manager, "broadcast", no_broadcast)
    return account, repo


def test_a_cleanup_line_leaves_the_verdict_alone(wired):
    account, repo = wired
    dispatcher = ConcurrentTaskDispatcher.__new__(ConcurrentTaskDispatcher)

    asyncio.run(
        dispatcher._update_step_log(
            account.email,
            "Đã đóng trình duyệt; luồng và proxy của account đã được giải phóng.",
            SimpleNamespace(),
            persist=False,
        )
    )

    assert account.current_step == "Sai mật khẩu - TikTok còn cho 5 lượt"
    assert repo.writes == 0


def test_an_ordinary_step_still_becomes_the_account_state(wired):
    account, repo = wired
    dispatcher = ConcurrentTaskDispatcher.__new__(ConcurrentTaskDispatcher)

    asyncio.run(
        dispatcher._update_step_log(
            account.email, "Đang tải video lên máy chủ TikTok...", SimpleNamespace()
        )
    )

    assert account.current_step == "Đang tải video lên máy chủ TikTok..."
    assert repo.writes == 1


class _Failed:
    def __init__(self, status, step):
        self.status = status
        self.current_step = step


def test_a_login_verdict_is_kept_instead_of_the_placeholder():
    account = _Failed("ERROR", "TikTok trả lời lỗi máy chủ cho tài khoản này - đợi rồi thử lại")
    kept = dispatcher_module.failure_step_for("LOGIN_CREDENTIAL", account)
    assert kept == account.current_step


def test_an_upload_batch_keeps_its_own_summary_as_before():
    account = _Failed("RUNNING", "⚠️ VIDEO_TRUNG · Đã đăng 0/1")
    kept = dispatcher_module.failure_step_for("UPLOAD_MEDIA_BATCH", account)
    assert kept == account.current_step


def test_a_task_that_fell_over_mid_step_does_not_keep_a_progress_line():
    account = _Failed("RUNNING", "Dang nhap Password tu tu...")
    assert dispatcher_module.failure_step_for("LOGIN_CREDENTIAL", account) == "Thất bại"


def test_nothing_recorded_falls_back_to_the_placeholder():
    assert dispatcher_module.failure_step_for("LOGIN_CREDENTIAL", _Failed("ERROR", "")) == "Thất bại"
    assert dispatcher_module.failure_step_for("LOGIN_CREDENTIAL", None) == "Thất bại"
