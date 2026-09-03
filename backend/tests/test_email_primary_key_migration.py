import sqlite3

from app.core.config import settings
from app.infrastructure.database.connection import _migrate_accounts_primary_key_to_email


def test_migration_rekeys_accounts_by_email_and_preserves_missing_row(tmp_path):
    database = tmp_path / "accounts.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE proxies (id VARCHAR PRIMARY KEY);
            CREATE TABLE accounts (
                id VARCHAR PRIMARY KEY, username VARCHAR NOT NULL UNIQUE, password VARCHAR,
                email VARCHAR, email_password VARCHAR, refresh_token VARCHAR, client_id VARCHAR,
                cookies_json VARCHAR NOT NULL DEFAULT '[]', status VARCHAR NOT NULL DEFAULT 'IDLE',
                current_step VARCHAR NOT NULL DEFAULT '', proxy_id VARCHAR,
                health_status VARCHAR, profile_status VARCHAR, country VARCHAR,
                batch_tag VARCHAR, created_at VARCHAR, note VARCHAR
            );
            INSERT INTO accounts (id, username, email) VALUES
                ('old-1', 'first', 'First@Hotmail.com'),
                ('old-2', 'legacy', '');
            """
        )

    original_url = settings.DATABASE_URL
    settings.DATABASE_URL = f"sqlite:///{database}"
    try:
        _migrate_accounts_primary_key_to_email()
    finally:
        settings.DATABASE_URL = original_url

    with sqlite3.connect(database) as connection:
        primary_key = next(row[1] for row in connection.execute("PRAGMA table_info(accounts)") if row[5] == 1)
        emails = [row[0] for row in connection.execute("SELECT email FROM accounts ORDER BY username")]

    assert primary_key == "email"
    assert emails == ["first@hotmail.com", "legacy+old-2@local.invalid"]
    assert list(tmp_path.glob("accounts.before_email_pk_*.db"))
