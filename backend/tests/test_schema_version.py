"""A database must come out of init_db() usable and version-stamped.

⛔ EVERY CHECK HERE WAS A REAL FAILURE FIRST, not a precaution. Writing the
alembic wiring on 05/10/2026 produced, in order: a fresh database containing
nothing but `alembic_version`, and an engine that could not open PostgreSQL at
all. Both were silent - the first one even reported success.
"""
import importlib
import sqlite3

import pytest
from sqlalchemy import inspect, text


def _connection_module(url: str, monkeypatch):
    """Reload the module against `url`.

    The engine is built at import time from settings, so a different URL means
    a different module instance - there is no way to retarget it in place.
    """
    from app.core import config

    monkeypatch.setattr(config.settings, "DATABASE_URL", url, raising=False)
    from app.infrastructure.database import connection

    return importlib.reload(connection)


def _head_revision(module) -> str:
    """Head doc tu thu muc script, khong viet cung trong test.

    ⛔ MOT MA REVISION VIET CUNG SE DO MOI LAN THEM MIGRATION, va no do vi test
    cu chu khong vi code sai - loai that bai day nguoi ta di tat. Da gap ngay
    06/10/2026: ba test o duoi khang dinh "0001_baseline" va cung do ngay khi
    revision group_id duoc them.
    """
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(module._alembic_config()).get_current_head()


def test_a_fresh_database_gets_every_table_and_a_version(tmp_path, monkeypatch):
    """⛔ THE TABLES ARE THE POINT. `SQLModel.metadata` is only populated by
    importing the module that declares the models, so init_db() used to depend
    on its caller having imported them first: called on its own, it created
    `alembic_version` and nothing else, and said nothing."""
    module = _connection_module(f"sqlite:///{tmp_path / 'fresh.db'}", monkeypatch)
    module.init_db()

    tables = set(inspect(module.engine).get_table_names())
    assert {"accounts", "proxies", "tiktok_video_metrics"} <= tables
    assert "alembic_version" in tables
    with module.engine.connect() as handle:
        version = handle.execute(text("select version_num from alembic_version"))
        assert version.scalar() == _head_revision(module)


def test_an_existing_database_is_stamped_without_losing_a_row(tmp_path, monkeypatch):
    """A database from before alembic existed: no version table, real rows.

    It must be adopted, not rebuilt. Customer databases are in this state and
    there is no second copy of them.
    """
    from sqlmodel import Session

    from app.infrastructure.database.schemas import AccountDbTable

    path = tmp_path / "existing.db"
    first = _connection_module(f"sqlite:///{path}", monkeypatch)
    first.init_db()
    # Through the model, not raw SQL: `accounts` has 29 NOT NULL columns with
    # no database-side default, and spelling them out here would mean this test
    # breaking every time a column is added - for no gain.
    with Session(first.engine) as session:
        session.add(AccountDbTable(email="keep.me@example.com", username="keep_me"))
        session.commit()
    with sqlite3.connect(path) as setup:
        # ⛔ A PRE-ALEMBIC DATABASE IS NOT JUST ONE MISSING ITS VERSION TABLE.
        # It also lacks every column added after the baseline, so dropping the
        # version table alone would simulate a state that has never existed and
        # test the wrong branch.
        setup.execute("drop table alembic_version")
        for table in ("accounts", "proxies", "tiktok_video_metrics"):
            # Index truoc cot: SQLite de index tro vao mot cot khong con, roi
            # moi lenh sau do deu loi "no such column".
            setup.execute(f"drop index if exists ix_{table}_group_id")
            setup.execute(f"alter table {table} drop column group_id")

    second = _connection_module(f"sqlite:///{path}", monkeypatch)
    second.init_db()

    with sqlite3.connect(path) as after:
        assert after.execute("select count(*) from accounts").fetchone()[0] == 1
        assert after.execute(
            "select username from accounts"
        ).fetchone()[0] == "keep_me"
        assert after.execute(
            "select version_num from alembic_version"
        ).fetchone()[0] == _head_revision(second)


def test_running_it_twice_changes_nothing(tmp_path, monkeypatch):
    """init_db() runs on every startup, so it has to be safe to repeat."""
    path = tmp_path / "twice.db"
    module = _connection_module(f"sqlite:///{path}", monkeypatch)
    module.init_db()
    module.init_db()

    with sqlite3.connect(path) as handle:
        assert handle.execute(
            "select count(*) from alembic_version"
        ).fetchone()[0] == 1


@pytest.mark.parametrize("url, expect_sqlite_args", [
    ("sqlite:///./x.db", True),
    ("postgresql+psycopg://user:pw@host/db", False),
])
def test_sqlite_only_connect_args_never_reach_postgresql(
    url, expect_sqlite_args, monkeypatch
):
    """⛔ THIS LINE WAS THE EARLIEST POSTGRESQL BLOCKER IN THE PROJECT, earlier
    than the migrations: `check_same_thread` and `timeout` are sqlite3
    arguments, and the driver refuses them while opening the first connection.
    Reloading never connects, so this asserts the decision, not a server."""
    module = _connection_module(url, monkeypatch)

    assert module._IS_SQLITE is expect_sqlite_args
    assert bool(module._CONNECT_ARGS) is expect_sqlite_args


def test_the_pragma_hook_is_not_armed_for_postgresql(monkeypatch):
    """PRAGMA is sqlite syntax; a listener left armed would make the very first
    statement on a PostgreSQL connection a syntax error."""
    from sqlalchemy import event

    module = _connection_module("postgresql+psycopg://user:pw@host/db", monkeypatch)

    assert not event.contains(module.engine, "connect", module._set_sqlite_pragma)


def test_group_id_reaches_every_scoped_table(tmp_path, monkeypatch):
    """⛔ CA BA BANG, khong chi `accounts`. DB dung chung tach theo cot nay, va
    mot bang thieu no la mot bang khong loc duoc - tuc du lieu cua nhom khac
    doc ra duoc ma khong co dieu kien nao chan."""
    module = _connection_module(f"sqlite:///{tmp_path / 'scoped.db'}", monkeypatch)
    module.init_db()

    inspector = inspect(module.engine)
    for table in ("accounts", "proxies", "tiktok_video_metrics"):
        columns = {column["name"] for column in inspector.get_columns(table)}
        assert "group_id" in columns, table
        indexes = {index["name"] for index in inspector.get_indexes(table)}
        assert f"ix_{table}_group_id" in indexes, table


def test_an_existing_row_belongs_to_no_group(tmp_path, monkeypatch):
    """NULL la "ban cai rieng", va moi dong co tu truoc phai o lai nhu vay.

    Dat mot server_default se bien toan bo du lieu cu thanh thanh vien cua mot
    nhom nao do - va tren DB dung chung, do la cho no cho ca nguoi khac xem.
    """
    from sqlmodel import Session

    from app.infrastructure.database.schemas import AccountDbTable

    module = _connection_module(f"sqlite:///{tmp_path / 'solo.db'}", monkeypatch)
    module.init_db()
    with Session(module.engine) as session:
        session.add(AccountDbTable(email="solo@example.com", username="solo"))
        session.commit()

    with module.engine.connect() as handle:
        value = handle.execute(text("select group_id from accounts")).scalar()
    assert value is None


def test_a_wal_less_copy_is_adopted_at_head_not_rebuilt(tmp_path, monkeypatch):
    """A `cp` of database.db that leaves the -wal behind: new schema, no version.

    ⛔ THIS HAPPENED, it is not a hypothetical. The version table had just been
    created and was still in the write-ahead log, so a plain file copy carried
    the columns but not the row that records them. Stamping such a database at
    the baseline and upgrading would re-add columns it already has and die on
    `duplicate column name`.
    """
    path = tmp_path / "walless.db"
    first = _connection_module(f"sqlite:///{path}", monkeypatch)
    first.init_db()
    head = _head_revision(first)
    with sqlite3.connect(path) as surgery:
        surgery.execute("drop table alembic_version")

    second = _connection_module(f"sqlite:///{path}", monkeypatch)
    second.init_db()

    with sqlite3.connect(path) as after:
        assert after.execute(
            "select version_num from alembic_version"
        ).fetchone()[0] == head
