from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime
from pathlib import Path

from invisible_browser_studio.application.ports import AccountRepository
from invisible_browser_studio.domain import ImportedAccount

from .credential_cipher import FernetCredentialCipher


class SqliteAccountRepository(AccountRepository):
    def __init__(self, path: Path, cipher: FernetCredentialCipher) -> None:
        self._path = path
        self._cipher = cipher
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            await self._cipher.initialize()
            await asyncio.to_thread(self._initialize_sync)
            self._initialized = True

    async def close(self) -> None:
        return None

    async def add_many(
        self, accounts: tuple[ImportedAccount, ...]
    ) -> tuple[list[ImportedAccount], int]:
        await self.initialize()
        async with self._lock:
            inserted_ids = await asyncio.to_thread(self._add_many_sync, accounts)
        inserted = [account for account in accounts if account.id in inserted_ids]
        return inserted, len(accounts) - len(inserted)

    async def list(
        self, *, offset: int, limit: int
    ) -> tuple[list[ImportedAccount], int]:
        await self.initialize()
        async with self._lock:
            rows, total = await asyncio.to_thread(self._list_sync, offset, limit)
        return [self._from_row(row) for row in rows], total

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=10)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize_sync(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL COLLATE NOCASE UNIQUE,
                    email_password TEXT NOT NULL,
                    refresh_token TEXT NOT NULL,
                    client_id TEXT NOT NULL,
                    source_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_accounts_created "
                "ON accounts(created_at DESC)"
            )

    def _add_many_sync(self, accounts: tuple[ImportedAccount, ...]) -> set[str]:
        inserted: set[str] = set()
        with self._connect() as connection:
            for account in accounts:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO accounts "
                    "(id, email, email_password, refresh_token, client_id, source_name, "
                    "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        account.id,
                        account.email,
                        self._cipher.encrypt(account.email_password),
                        self._cipher.encrypt(account.refresh_token),
                        self._cipher.encrypt(account.client_id),
                        account.source_name,
                        account.created_at.isoformat(),
                        account.updated_at.isoformat(),
                    ),
                )
                if cursor.rowcount == 1:
                    inserted.add(account.id)
        return inserted

    def _list_sync(self, offset: int, limit: int) -> tuple[list[tuple], int]:
        with self._connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0])
            rows = connection.execute(
                "SELECT id, email, email_password, refresh_token, client_id, source_name, "
                "created_at, updated_at FROM accounts "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return rows, total

    def _from_row(self, row: tuple) -> ImportedAccount:
        return ImportedAccount(
            id=str(row[0]),
            email=str(row[1]),
            email_password=self._cipher.decrypt(str(row[2])),
            refresh_token=self._cipher.decrypt(str(row[3])),
            client_id=self._cipher.decrypt(str(row[4])),
            source_name=str(row[5]),
            created_at=datetime.fromisoformat(str(row[6])),
            updated_at=datetime.fromisoformat(str(row[7])),
        )
