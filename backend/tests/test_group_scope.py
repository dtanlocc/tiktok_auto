"""Nhóm A không được thấy dữ liệu nhóm B. Kể cả khi biết khoá chính.

⛔ TEST NÀY PHẢI CHẠY MỖI LẦN PUSH. Rò dữ liệu giữa hai khách hàng là loại lỗi
không ai nhìn thấy lúc nó xảy ra: không có ngoại lệ, không có dòng log, chỉ là
một người đọc được thứ của người khác. DB dùng chung tách theo `group_id` nên
cách ly là thuộc tính của code — không có tường nào bên dưới chặn hộ.
"""
import pytest
from sqlmodel import Session, SQLModel, create_engine

from app.domain.entities.account import TikTokAccount
from app.infrastructure.database import scope
from app.infrastructure.database.schemas import AccountDbTable, ProxyDbTable
from app.infrastructure.database.sqlite_repository import (
    SQLiteAccountRepository,
    SQLiteProxyRepository,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'scope.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as handle:
        handle.add_all([
            AccountDbTable(email="a@acme.test", username="acme_one", group_id="acme"),
            AccountDbTable(email="b@other.test", username="other_one", group_id="other"),
            AccountDbTable(email="solo@local.test", username="solo_one", group_id=None),
            ProxyDbTable(id="px-acme", host="1.1.1.1", port=1, group_id="acme"),
            ProxyDbTable(id="px-other", host="2.2.2.2", port=2, group_id="other"),
        ])
        handle.commit()
        yield handle


@pytest.fixture(autouse=True)
def solo_by_default():
    """Mỗi test bắt đầu không có phạm vi, và không để lại phạm vi cho test sau."""
    token = scope.use_group(None)
    yield
    scope.reset_group(token)


def test_a_known_primary_key_does_not_cross_the_group(session):
    """⛔ ĐÂY LÀ TEST QUAN TRỌNG NHẤT FILE NÀY. Khoá chính của `accounts` là
    email, nên "biết khoá chính" không phải một giả định xa vời - nó là thứ dễ
    đoán nhất trong schema. Trước chốt lọc, `session.get` trả về dòng này."""
    token = scope.use_group("acme")
    try:
        repo = SQLiteAccountRepository(session)
        assert repo.get_by_id("a@acme.test") is not None
        assert repo.get_by_id("b@other.test") is None
        assert repo.get_by_id("solo@local.test") is None
    finally:
        scope.reset_group(token)


def test_listing_shows_only_the_group(session):
    token = scope.use_group("acme")
    try:
        emails = {a.email for a in SQLiteAccountRepository(session).get_all()}
        assert emails == {"a@acme.test"}
    finally:
        scope.reset_group(token)


def test_without_a_group_only_unowned_rows_are_visible(session):
    """Bản cài riêng: mọi dòng của nó là NULL, nên nó thấy đúng dữ liệu của nó.

    ⛔ VÀ KHÔNG THẤY DỮ LIỆU CỦA NHÓM. Nếu một DB của nhóm bị copy sang một máy
    chạy không có lease nhóm, bỏ lọc sẽ cho máy đó đọc sạch; `IS NULL` thì không.
    """
    emails = {a.email for a in SQLiteAccountRepository(session).get_all()}
    assert emails == {"solo@local.test"}


def test_a_new_row_is_stamped_with_the_active_group(session):
    """⛔ NỬA CÒN LẠI CỦA VIỆC LỌC. Lọc khi đọc mà không đóng dấu khi ghi thì
    dòng mới sinh ra với group_id=NULL và chính người tạo nó mất dấu nó ngay lần
    đọc sau - một bản ghi mất tích thay vì một lỗi."""
    token = scope.use_group("acme")
    try:
        repo = SQLiteAccountRepository(session)
        repo.save(TikTokAccount(id=None, email="new@acme.test", username="acme_new"))
        row = session.get(AccountDbTable, "new@acme.test")
        assert row.group_id == "acme"
        assert repo.get_by_id("new@acme.test") is not None
    finally:
        scope.reset_group(token)


def test_another_groups_row_cannot_be_deleted(session):
    token = scope.use_group("acme")
    try:
        assert SQLiteAccountRepository(session).delete("b@other.test") is False
    finally:
        scope.reset_group(token)
    assert session.get(AccountDbTable, "b@other.test") is not None


def test_another_groups_row_cannot_have_its_status_changed(session):
    token = scope.use_group("acme")
    try:
        SQLiteAccountRepository(session).update_status("b@other.test", "HACKED")
    finally:
        scope.reset_group(token)
    session.expire_all()
    assert session.get(AccountDbTable, "b@other.test").status != "HACKED"


def test_proxies_are_scoped_the_same_way(session):
    token = scope.use_group("acme")
    try:
        repo = SQLiteProxyRepository(session)
        assert {p.id for p in repo.get_all()} == {"px-acme"}
        assert repo.get_by_id("px-other") is None
        assert repo.delete("px-other") is False
    finally:
        scope.reset_group(token)


def test_a_composite_primary_key_is_refused_rather_than_half_filtered(session):
    """`tiktok_video_metrics` có khoá ghép (account_email, video_id). Lặng lẽ
    lọc theo một cột là tệ hơn từ chối, nên hàm từ chối."""
    from app.infrastructure.database.schemas import TikTokVideoMetricDbTable

    with pytest.raises(ValueError, match="khoá chính ghép"):
        scope.scoped_get(session, TikTokVideoMetricDbTable, "a@acme.test")


def test_the_repository_never_calls_session_get_directly():
    """⛔ QUY ƯỚC ĐƯỢC KIỂM, KHÔNG PHẢI ĐƯỢC NHỚ. `session.get` tra khoá chính
    mà không có mệnh đề WHERE nào để lọc, nên mỗi lần gọi trực tiếp nó trên bảng
    có `group_id` là một đường đọc chéo nhóm. Có 8 lần như vậy trước hôm nay."""
    import inspect as inspect_module

    from app.infrastructure.database import sqlite_repository

    source = inspect_module.getsource(sqlite_repository)
    assert "self.session.get(" not in source
