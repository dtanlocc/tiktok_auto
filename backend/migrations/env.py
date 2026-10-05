"""Alembic environment, shared by the CLI and by init_db().

⛔ THE URL COMES FROM settings, NEVER FROM alembic.ini. One install points at a
local SQLite file and another at the shared Postgres; the application also calls
alembic itself during startup. A URL written into the ini would be right on one
machine and wrong on every other.
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# `backend/` on the path, so `app.*` imports work whether alembic was started
# from the CLI in this directory or called from inside the application.
_BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

# ⛔ IMPORTED FOR ITS SIDE EFFECT. SQLModel.metadata is only populated by
# importing the modules that declare the tables; without this line autogenerate
# compares the live database against an EMPTY metadata and cheerfully writes a
# migration that drops every table.
from app.infrastructure.database import schemas  # noqa: E402,F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = SQLModel.metadata


def _database_url() -> str:
    return settings.DATABASE_URL


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite cannot ALTER a column in place; batch mode rewrites the table
        # instead. Harmless on PostgreSQL, essential on SQLite.
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
