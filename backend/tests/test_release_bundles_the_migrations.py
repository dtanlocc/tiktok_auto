"""Mọi file migration phải được khai TƯỜNG MINH trong script build.

⛔ BÀI TEST NÀY CHẶN ĐÚNG LỖI ĐÃ LÀM BẢN 0.1.12 KHÔNG KHỞI ĐỘNG ĐƯỢC.
`--include-data-dir` của Nuitka **bỏ file `.py`** — nó cố ý loại mã nguồn Python
để không vô tình ship source. Nên `--include-data-dir=backend\\migrations=migrations`
bundle được `script.py.mako` mà **bỏ lại** `env.py` và `versions/*.py`, tức đúng
những file alembic cần. Hậu quả, theo đúng chuỗi đã xảy ra:

1. alembic báo `Can't find Python file <tmp>\\migrations\\env.py`;
2. `_align_schema_version` **bắt và chỉ in** lỗi đó (có chủ ý: trước khi có
   alembic app vẫn chạy, nên migration thất bại không được giết app);
3. backend đi tiếp, rồi chết ở truy vấn đầu tiên với
   `no such column: accounts.group_id` — một thông báo không nói gì về nguyên
   nhân thật;
4. launcher kill backend và không để lại dấu gì.

Ba tầng đó mỗi tầng đều hợp lý riêng, và cộng lại thì một cờ build sai biến
thành một lỗi SQL không liên quan. Bài test này cắt chuỗi ở tầng đầu.

⛔ VÀ NÓ CHỈ ĐỌC VĂN BẢN, không chạy build. Một bản build thật tốn ~20 phút,
nên không thể là thứ chạy trong bộ test; đọc cờ là phép kiểm rẻ duy nhất còn
lại, và nó đủ để bắt việc thêm một migration mới mà quên khai.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "build_backend_release.ps1"
_MIGRATIONS = _REPO_ROOT / "backend" / "migrations"


def _script_text() -> str:
    if not _SCRIPT.is_file():
        pytest.fail(f"Không thấy script build: {_SCRIPT}")
    return _SCRIPT.read_text(encoding="utf-8")


def test_env_py_is_included_explicitly():
    text = _script_text()
    assert "migrations\\env.py=migrations\\env.py" in text, (
        "env.py phải được khai bằng --include-data-files. "
        "--include-data-dir bỏ file .py, và alembic sẽ báo "
        "\"Can't find Python file ...\\migrations\\env.py\"."
    )


def test_every_revision_file_is_covered():
    """Hoặc có glob cho versions/, hoặc từng file được khai riêng."""
    text = _script_text()
    revisions = sorted(
        path
        for path in (_MIGRATIONS / "versions").glob("*.py")
        if path.name != "__init__.py"
    )
    assert revisions, (
        f"Không thấy file revision nào trong {_MIGRATIONS / 'versions'} — "
        "nếu thư mục migration đổi chỗ thì phải sửa bài test này, không phải xoá nó."
    )
    glob_covered = "migrations\\versions\\*.py=migrations\\versions\\" in text
    if glob_covered:
        return
    missing = [path.name for path in revisions if path.name not in text]
    assert not missing, (
        "Những revision này không được khai trong script build: "
        f"{', '.join(missing)}. Thêm vào --include-data-files, hoặc dùng glob "
        "versions\\*.py."
    )


def test_the_migration_directory_is_still_where_the_code_looks_for_it():
    """`_alembic_config()` dựng đường dẫn từ `__file__`; hai bên phải khớp."""
    from app.infrastructure.database import connection

    config = connection._alembic_config()
    location = Path(config.get_main_option("script_location"))
    assert location.name == "migrations", location
    assert (location / "env.py").is_file(), (
        f"{location / 'env.py'} không tồn tại. `_alembic_config()` và cấu trúc "
        "thư mục đã lệch nhau."
    )
