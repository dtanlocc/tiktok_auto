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
        assert version.scalar() == "0001_baseline"


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
        # Put it back the way a pre-alembic install looks.
        setup.execute("drop table alembic_version")

    second = _connection_module(f"sqlite:///{path}", monkeypatch)
    second.init_db()

    with sqlite3.connect(path) as after:
        assert after.execute("select count(*) from accounts").fetchone()[0] == 1
        assert after.execute(
            "select username from accounts"
        ).fetchone()[0] == "keep_me"
        assert after.execute(
            "select version_num from alembic_version"
        ).fetchone()[0] == "0001_baseline"


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
