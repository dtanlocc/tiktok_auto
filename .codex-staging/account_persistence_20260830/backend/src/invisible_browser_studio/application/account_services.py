from __future__ import annotations

import uuid

from invisible_browser_studio.domain import ImportedAccount

from .dto import ImportAccountCommand, ImportAccountsResult
from .ports import AccountRepository


class AccountService:
    def __init__(self, repository: AccountRepository) -> None:
        self._repository = repository

    async def initialize(self) -> None:
        await self._repository.initialize()

    async def shutdown(self) -> None:
        await self._repository.close()

    async def import_many(
        self, commands: tuple[ImportAccountCommand, ...]
    ) -> ImportAccountsResult:
        unique: dict[str, ImportAccountCommand] = {}
        request_duplicates = 0
        for command in commands:
            normalized = command.email.strip().casefold()
            if normalized in unique:
                request_duplicates += 1
                continue
            unique[normalized] = command

        accounts = tuple(
            ImportedAccount(
                id=str(uuid.uuid4()),
                email=command.email,
                email_password=command.email_password,
                refresh_token=command.refresh_token,
                client_id=command.client_id,
                source_name=command.source_name,
            )
            for command in unique.values()
        )
        inserted, database_duplicates = await self._repository.add_many(accounts)
        return ImportAccountsResult(
            imported=len(inserted),
            duplicates=request_duplicates + database_duplicates,
            total=len(commands),
        )

    async def list(
        self, *, offset: int = 0, limit: int = 500
    ) -> tuple[list[ImportedAccount], int]:
        return await self._repository.list(offset=offset, limit=limit)
