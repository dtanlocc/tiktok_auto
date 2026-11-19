"""DB cu (chua co cot totp_secret) phai tu co cot do, khong mat du lieu.

⛔ DAY LA RUI RO THAT. DB cua nguoi dung da ton tai (ban do 05/10/2026: 1.974
account). Mot migration hong khong phai "thieu tinh nang" ma la "app khong mo
duoc". Bai nay dung mot DB kieu cu roi chay dung ham catch-up that.
"""
from __future__ import annotations

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy import text

from app.infrastructure.database import connection as C
from app.infrastructure.database.schemas import AccountDbTable, ProxyDbTable  # noqa: F401


def _cot(engine, bang) -> set[str]:
    with Session(engine) as s:
        rows = s.execute(text(f"PRAGMA table_info({bang})")).fetchall()
    return {r[1] for r in rows}


def test_db_cu_tu_them_cot_totp_secret(tmp_path, monkeypatch):
    db = tmp_path / "cu.db"
    engine = create_engine(f"sqlite:///{db}")
    SQLModel.metadata.create_all(engine)

    # Dat mot account san co bang ORM (du cac cot NOT NULL), roi gia lap DB CU
    # bang cach bo cot totp_secret di.
    with Session(engine) as s:
        s.add(AccountDbTable(email="old@local.test", username="old_one"))
        s.commit()
        s.execute(text("ALTER TABLE accounts DROP COLUMN totp_secret"))
        s.commit()
    assert "totp_secret" not in _cot(engine, "accounts")

    # Chay dung ham catch-up that, tren engine tam nay.
    monkeypatch.setattr(C, "engine", engine)
    monkeypatch.setattr(C, "_IS_SQLITE", True)
    C._legacy_sqlite_catch_up()

    # Cot da co, va account cu van con (totp_secret = NULL).
    assert "totp_secret" in _cot(engine, "accounts")
    with Session(engine) as s:
        row = s.execute(
            text("SELECT username, totp_secret FROM accounts WHERE email='old@local.test'")
        ).fetchone()
    assert row[0] == "old_one"
    assert row[1] is None


def test_chay_lai_catch_up_khong_loi(tmp_path, monkeypatch):
    """Cot da co roi thi chay lai la no-op, khong nem loi 'duplicate column'."""
    db = tmp_path / "moi.db"
    engine = create_engine(f"sqlite:///{db}")
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(C, "engine", engine)
    monkeypatch.setattr(C, "_IS_SQLITE", True)
    C._legacy_sqlite_catch_up()   # khong duoc nem gi
    assert "totp_secret" in _cot(engine, "accounts")
