from __future__ import annotations

import asyncio
import shutil
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from invisible_browser_studio.adapters.outbound.credential_cipher import (
    FernetCredentialCipher,
)
from invisible_browser_studio.adapters.outbound.sqlite_account_repository import (
    SqliteAccountRepository,
)
from invisible_browser_studio.application.account_services import AccountService
from invisible_browser_studio.application.dto import ImportAccountCommand
from invisible_browser_studio.infrastructure.config import Settings
from invisible_browser_studio.main import create_app


def _runtime_root(name: str) -> Path:
    root = Path(".test-runtime") / name
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_account_repository_preserves_all_four_columns_encrypted() -> None:
    root = _runtime_root("account-repository")

    async def scenario() -> None:
        database = root / "control-plane.sqlite3"
        key_path = root / "credentials.key"
        repository = SqliteAccountRepository(
            database,
            FernetCredentialCipher(key_path=key_path),
        )
        service = AccountService(repository)
        await service.initialize()

        result = await service.import_many(
            (
                ImportAccountCommand(
                    email="Owned@Outlook.com",
                    email_password="email-password",
                    refresh_token="refresh-token-value",
                    client_id="client-id-value",
                    source_name="owned.txt",
                ),
                ImportAccountCommand(
                    email="owned@outlook.com",
                    email_password="duplicate-password",
                    refresh_token="duplicate-refresh-token",
                    client_id="duplicate-client-id",
                    source_name="duplicate.txt",
                ),
            )
        )
        assert result.imported == 1
        assert result.duplicates == 1

        accounts, total = await service.list()
        assert total == 1
        account = accounts[0]
        assert account.email == "owned@outlook.com"
        assert account.email_password == "email-password"
        assert account.refresh_token == "refresh-token-value"
        assert account.client_id == "client-id-value"
        assert account.source_name == "owned.txt"
        await service.shutdown()

        with sqlite3.connect(database) as connection:
            stored = connection.execute(
                "SELECT email_password, refresh_token, client_id FROM accounts"
            ).fetchone()
        assert stored is not None
        assert "email-password" not in str(stored)
        assert "refresh-token-value" not in str(stored)
        assert "client-id-value" not in str(stored)
        assert key_path.exists()

        restarted = AccountService(
            SqliteAccountRepository(
                database,
                FernetCredentialCipher(key_path=key_path),
            )
        )
        await restarted.initialize()
        after_restart, restarted_total = await restarted.list()
        assert restarted_total == 1
        assert after_restart[0].email_password == "email-password"
        await restarted.shutdown()

    asyncio.run(scenario())
    shutil.rmtree(root, ignore_errors=True)


def test_accounts_api_imports_lists_and_skips_existing_email() -> None:
    root = _runtime_root("accounts-api")
    database = root / "control-plane.sqlite3"
    settings = Settings(
        upload_root=root / "uploads",
        database_path=database,
        credentials_key_path=root / "credentials.key",
        scheduler_workers=1,
        max_queued_jobs=4,
    )
    payload = {
        "rows": [
            {
                "email": "first@outlook.com",
                "email_password": "email-password",
                "refresh_token": "refresh-token-value",
                "client_id": "client-id-value",
                "source_name": "accounts.txt",
            }
        ]
    }

    with TestClient(create_app(settings)) as client:
        imported = client.post("/api/v1/accounts/import", json=payload)
        assert imported.status_code == 201
        assert imported.json() == {"imported": 1, "duplicates": 0, "total": 1}

        duplicate = client.post("/api/v1/accounts/import", json=payload)
        assert duplicate.status_code == 201
        assert duplicate.json() == {"imported": 0, "duplicates": 1, "total": 1}

        listing = client.get("/api/v1/accounts")
        assert listing.status_code == 200
        body = listing.json()
        assert body["total"] == 1
        assert body["items"][0]["email"] == "first@outlook.com"
        assert body["items"][0]["source_name"] == "accounts.txt"
        assert body["items"][0]["has_email_password"] is True
        serialized = listing.text
        assert "email-password" not in serialized
        assert "refresh-token-value" not in serialized
        assert "client-id-value" not in serialized
    shutil.rmtree(root, ignore_errors=True)
