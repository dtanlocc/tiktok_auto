import asyncio

from app.domain.entities.account import TikTokAccount
from app.interfaces.api import accounts_router


class FakeUpload:
    def __init__(self, text: str):
        self._content = text.encode("utf-8")

    async def read(self) -> bytes:
        return self._content


class FakeAccountRepository:
    def __init__(self, accounts):
        self.accounts = list(accounts)
        self.saved = []

    def get_all(self):
        return list(self.accounts)

    def save(self, account):
        self.saved.append(account)
        self.accounts.append(account)
        return account


class FakeProxyRepository:
    def get_all(self):
        return []


def test_file_import_skips_existing_and_in_request_duplicates(monkeypatch):
    original_cookies = [{"name": "sessionid", "value": "keep-me"}]
    existing = TikTokAccount(
        id="old@example.com",
        email="old@example.com",
        username="old_user",
        password="original-password",
        cookies=original_cookies.copy(),
        country="US",
        batch_tag="ORIGINAL_BATCH",
        status="SUCCESS",
        profile_status="COMPLETED",
    )
    account_repo = FakeAccountRepository([existing])
    broadcasts = []

    async def capture_broadcast(payload):
        broadcasts.append(payload)

    monkeypatch.setattr(accounts_router.ws_manager, "broadcast", capture_broadcast)

    first_file = FakeUpload(
        "\n".join(
            [
                # Same email: must not overwrite the existing row.
                "changed_user|changed-password|OLD@EXAMPLE.COM|mailpass|refresh|client|sessionid=replace-me",
                # Same username: must not create a second row under another email.
                "OLD_USER|other-password|other@example.com|mailpass|refresh|client",
                "new_user|new-password|new@example.com|mailpass|refresh|client|sessionid=new-cookie",
                "invalid|line",
            ]
        )
    )
    second_file = FakeUpload(
        # Duplicate of the new account from the first file.
        "NEW_USER|duplicate-password|NEW@EXAMPLE.COM|mailpass|refresh|client"
    )

    result = asyncio.run(
        accounts_router.import_accounts_from_files(
            files=[first_file, second_file],
            country="VN",
            batch_tag="NEW_BATCH",
            account_repo=account_repo,
            proxy_repo=FakeProxyRepository(),
        )
    )

    assert result["imported"] == 1
    assert result["updated"] == 0
    assert result["skipped_existing"] == 3
    assert result["skipped_invalid"] == 1
    assert result["failed"] == 0

    assert existing.username == "old_user"
    assert existing.password == "original-password"
    assert existing.cookies == original_cookies
    assert existing.country == "US"
    assert existing.batch_tag == "ORIGINAL_BATCH"

    assert len(account_repo.saved) == 1
    assert account_repo.saved[0].email == "new@example.com"
    assert account_repo.saved[0].username == "new_user"
    assert [message["event"] for message in broadcasts] == ["ACCOUNT_ADDED"]
