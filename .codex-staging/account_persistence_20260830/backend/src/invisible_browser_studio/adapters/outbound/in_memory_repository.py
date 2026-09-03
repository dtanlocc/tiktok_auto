from __future__ import annotations

import asyncio
from copy import deepcopy

from invisible_browser_studio.application.ports import SessionRepository
from invisible_browser_studio.domain import BrowserSession, SessionStatus


class InMemorySessionRepository(SessionRepository):
    """Concurrency-safe development adapter. Replace with PostgreSQL in production."""

    def __init__(self) -> None:
        self._sessions: dict[str, BrowserSession] = {}
        self._idempotency: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    async def add(self, session: BrowserSession) -> None:
        async with self._lock:
            if session.id in self._sessions:
                raise ValueError(f"Session {session.id} already exists")
            if session.idempotency_key:
                key = (session.tenant_id, session.idempotency_key)
                if key in self._idempotency:
                    raise ValueError("Idempotency key already exists")
                self._idempotency[key] = session.id
            self._sessions[session.id] = deepcopy(session)

    async def get(self, session_id: str) -> BrowserSession | None:
        async with self._lock:
            session = self._sessions.get(session_id)
            return deepcopy(session) if session else None

    async def find_by_idempotency_key(
        self, tenant_id: str, idempotency_key: str
    ) -> BrowserSession | None:
        async with self._lock:
            session_id = self._idempotency.get((tenant_id, idempotency_key))
            session = self._sessions.get(session_id) if session_id else None
            return deepcopy(session) if session else None

    async def list(
        self, *, tenant_id: str | None, offset: int, limit: int
    ) -> tuple[list[BrowserSession], int]:
        async with self._lock:
            sessions = [
                session
                for session in self._sessions.values()
                if tenant_id is None or session.tenant_id == tenant_id
            ]
            sessions.sort(key=lambda item: item.created_at, reverse=True)
            return deepcopy(sessions[offset : offset + limit]), len(sessions)

    async def update(self, session: BrowserSession) -> None:
        async with self._lock:
            if session.id not in self._sessions:
                raise KeyError(session.id)
            current = self._sessions[session.id]
            if session.revision < current.revision:
                raise RuntimeError("Stale session update")
            self._sessions[session.id] = deepcopy(session)

    async def count_active(self) -> int:
        async with self._lock:
            return sum(
                session.status not in {SessionStatus.CLOSED, SessionStatus.FAILED}
                for session in self._sessions.values()
            )

