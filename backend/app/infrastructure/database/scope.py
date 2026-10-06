"""Chỗ duy nhất áp bộ lọc `group_id`. Mọi truy cập dữ liệu đi qua đây.

⛔ "NHỚ THÊM `.where(group_id == ...)`" LÀ MỘT KẾ HOẠCH SAI, vì có những chỗ
không có `where` để thêm. Đo ngày 06/10/2026 trên `sqlite_repository.py`: 18 chỗ
truy vấn, trong đó **8 chỗ dùng `session.get(Model, pk)`** — hàm đó tra thẳng
khoá chính và trả về dòng đó **bất kể `group_id`**. Mà khoá chính của `accounts`
là **email**: thứ dễ đoán nhất trong cả schema. Nên một quy ước nhớ-thêm-điều-
kiện sẽ để nguyên 8 đường đọc chéo dữ liệu giữa hai khách hàng.

Vì vậy module này **thay thế** `session.get` thay vì bổ sung cho nó, và chỗ nào
còn gọi `session.get` trực tiếp trên bảng có `group_id` thì coi là lỗi.

⛔ VÀ ĐÂY KHÔNG PHẢI LỚP BẢO VỆ CUỐI. Nó là lớp của ứng dụng, nên nó chỉ đúng
chừng nào mọi đường đều đi qua nó — một câu query viết sót vẫn vượt được. Lớp mà
một câu viết sót KHÔNG vượt được là RLS trong Postgres, và đó là nơi phải bật
khi DB dùng chung đi vào hoạt động. Bản cài local không cần: nó chỉ có một nhóm,
nên không có ai để rò sang.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any, Optional, TypeVar

from sqlmodel import Session, select

#: ContextVar chứ không phải biến toàn cục: một tiến trình phục vụ nhiều nhóm
#: (data plane) cần phạm vi theo từng request, và contextvar cho đúng điều đó mà
#: không đổi gì với bản cài local - nơi nó chỉ được đặt một lần hoặc không đặt.
_active_group: ContextVar[Optional[str]] = ContextVar("active_group", default=None)

T = TypeVar("T")


def active_group() -> Optional[str]:
    """Nhóm mà ngữ cảnh hiện tại được phép thấy. None = bản cài riêng."""
    return _active_group.get()


def use_group(group_id: Optional[str]) -> Token:
    """Đặt phạm vi; trả về token để `reset_group` đưa về trạng thái trước.

    Giá trị này đến từ `LeaseClaims.group_id` — tức từ một lease đã ký, không
    phải từ cấu hình người dùng sửa được.
    """
    return _active_group.set(group_id)


def reset_group(token: Token) -> None:
    _active_group.reset(token)


def _condition(model: Any):
    """Điều kiện lọc cho phạm vi hiện tại.

    ⛔ KHÔNG CÓ PHẠM VI THÌ LỌC `IS NULL`, KHÔNG PHẢI BỎ LỌC. Một bản cài riêng
    có mọi dòng `group_id = NULL`, nên `IS NULL` cho nó thấy đúng toàn bộ dữ
    liệu của nó — không đổi gì so với hôm nay. Nhưng nếu một DB của nhóm bị
    copy vào một máy chạy không có lease nhóm, bỏ lọc sẽ cho máy đó đọc sạch,
    còn `IS NULL` thì không. Chọn cách không cho đọc.
    """
    group = active_group()
    if group is None:
        return model.group_id.is_(None)
    return model.group_id == group


def scoped(statement, model: Any):
    """Gắn điều kiện phạm vi vào một `select()`."""
    return statement.where(_condition(model))


def scoped_get(session: Session, model: type[T], primary_key: Any) -> Optional[T]:
    """Tra theo khoá chính **trong phạm vi**. Thay cho `session.get`.

    ⛔ KHÔNG DÙNG `session.get` RỒI KIỂM `group_id` SAU. Hai lý do: `session.get`
    trả về đối tượng đã nằm trong identity map mà không chạy query nào, nên
    "kiểm sau" có thể đọc một dòng đã bị sửa trong session khác; và cách đó để
    dòng của nhóm khác đi vào bộ nhớ của tiến trình rồi mới loại ra, tức một
    chỗ viết sót sau đó là lại rò. Ở đây điều kiện nằm trong chính câu SQL.
    """
    columns = list(model.__table__.primary_key.columns)
    if len(columns) != 1:
        raise ValueError(
            f"{model.__name__} có khoá chính ghép; dùng `scoped` với select() "
            "và nêu rõ từng cột khoá."
        )
    statement = select(model).where(columns[0] == primary_key)
    return session.exec(scoped(statement, model)).first()


def stamp(row: Any) -> Any:
    """Đóng phạm vi hiện tại vào một dòng sắp ghi.

    ⛔ NỬA CÒN LẠI CỦA VIỆC LỌC. Lọc khi đọc mà không đóng dấu khi ghi thì mỗi
    dòng mới sinh ra với `group_id = NULL`, và ngay lần đọc sau chính người tạo
    nó cũng không thấy nó nữa — một bản ghi mất tích thay vì một thông báo lỗi.
    """
    row.group_id = active_group()
    return row
