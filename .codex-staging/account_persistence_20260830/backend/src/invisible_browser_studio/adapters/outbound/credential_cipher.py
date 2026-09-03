from __future__ import annotations

import asyncio
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class FernetCredentialCipher:
    """Authenticated encryption with a key stored separately from SQLite."""

    def __init__(self, *, key_path: Path, configured_key: str = "") -> None:
        self._key_path = key_path
        self._configured_key = configured_key.strip()
        self._fernet: Fernet | None = None
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        if self._fernet is not None:
            return
        async with self._lock:
            if self._fernet is not None:
                return
            key = await asyncio.to_thread(self._load_or_create_key)
            try:
                self._fernet = Fernet(key)
            except (TypeError, ValueError) as exc:
                raise ValueError("IBS_CREDENTIALS_KEY is not a valid Fernet key") from exc

    def encrypt(self, value: str) -> str:
        if self._fernet is None:
            raise RuntimeError("Credential cipher is not initialized")
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        if self._fernet is None:
            raise RuntimeError("Credential cipher is not initialized")
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except InvalidToken as exc:
            raise RuntimeError(
                "Stored credentials cannot be decrypted with the configured key"
            ) from exc

    def _load_or_create_key(self) -> bytes:
        if self._configured_key:
            return self._configured_key.encode("ascii")
        try:
            return self._key_path.read_bytes().strip()
        except FileNotFoundError:
            pass

        self._key_path.parent.mkdir(parents=True, exist_ok=True)
        key = Fernet.generate_key()
        try:
            with self._key_path.open("xb") as output:
                output.write(key)
            try:
                self._key_path.chmod(0o600)
            except OSError:
                pass
            return key
        except FileExistsError:
            return self._key_path.read_bytes().strip()
