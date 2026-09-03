from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from invisible_browser_studio.application.ports import BatchRepository
from invisible_browser_studio.domain import AutomationBatch, BatchStatus, BrowserMode


class SqliteBatchRepository(BatchRepository):
    """Persist non-secret batch metadata and normalized queue outcomes."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            await asyncio.to_thread(self._initialize_sync)
            self._initialized = True

    async def close(self) -> None:
        return None

    async def add(self, batch: AutomationBatch) -> None:
        await self.initialize()
        async with self._lock:
            await asyncio.to_thread(self._add_sync, batch)

    async def get(self, batch_id: str) -> AutomationBatch | None:
        await self.initialize()
        async with self._lock:
            payload = await asyncio.to_thread(self._get_payload_sync, batch_id)
        return _batch_from_payload(payload) if payload else None

    async def list(
        self, *, offset: int, limit: int
    ) -> tuple[list[AutomationBatch], int]:
        await self.initialize()
        async with self._lock:
            payloads, total = await asyncio.to_thread(self._list_payloads_sync, offset, limit)
        return [_batch_from_payload(payload) for payload in payloads], total

    async def update(self, batch: AutomationBatch) -> None:
        await self.initialize()
        async with self._lock:
            await asyncio.to_thread(self._update_sync, batch)

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
                CREATE TABLE IF NOT EXISTS automation_batches (
                    id TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    queue_status TEXT NOT NULL CHECK (
                        queue_status IN ('queued', 'running', 'succeeded', 'failed')
                    ),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_automation_batches_updated "
                "ON automation_batches(updated_at DESC)"
            )
            rows = connection.execute(
                "SELECT id, payload_json FROM automation_batches "
                "WHERE queue_status IN ('queued', 'running')"
            ).fetchall()
            for batch_id, payload in rows:
                batch = _batch_from_payload(payload)
                batch.mark_failed("Backend restarted before the batch finished")
                connection.execute(
                    "UPDATE automation_batches SET payload_json = ?, queue_status = ?, "
                    "updated_at = ? WHERE id = ?",
                    (
                        _batch_payload(batch),
                        batch.queue_status.value,
                        batch.updated_at.isoformat(),
                        batch_id,
                    ),
                )

    def _add_sync(self, batch: AutomationBatch) -> None:
        with self._connect() as connection:
            try:
                connection.execute(
                    "INSERT INTO automation_batches "
                    "(id, payload_json, queue_status, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        batch.id,
                        _batch_payload(batch),
                        batch.queue_status.value,
                        batch.created_at.isoformat(),
                        batch.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"Batch {batch.id} already exists") from exc

    def _get_payload_sync(self, batch_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM automation_batches WHERE id = ?",
                (batch_id,),
            ).fetchone()
        return str(row[0]) if row else None

    def _list_payloads_sync(self, offset: int, limit: int) -> tuple[list[str], int]:
        with self._connect() as connection:
            total = int(connection.execute("SELECT COUNT(*) FROM automation_batches").fetchone()[0])
            rows = connection.execute(
                "SELECT payload_json FROM automation_batches "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [str(row[0]) for row in rows], total

    def _update_sync(self, batch: AutomationBatch) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM automation_batches WHERE id = ?",
                (batch.id,),
            ).fetchone()
            if row is None:
                raise KeyError(batch.id)
            current = _batch_from_payload(str(row[0]))
            if batch.revision < current.revision:
                raise RuntimeError("Stale batch update")
            connection.execute(
                "UPDATE automation_batches SET payload_json = ?, queue_status = ?, "
                "updated_at = ? WHERE id = ?",
                (
                    _batch_payload(batch),
                    batch.queue_status.value,
                    batch.updated_at.isoformat(),
                    batch.id,
                ),
            )


def _batch_payload(batch: AutomationBatch) -> str:
    value = {
        "id": batch.id,
        "tenant_id": batch.tenant_id,
        "display_name": batch.display_name,
        "start_url": batch.start_url,
        "mode": batch.mode.value,
        "total_jobs": batch.total_jobs,
        "concurrency": batch.concurrency,
        "active_seconds": batch.active_seconds,
        "proxy_server": batch.proxy_server,
        "proxy_servers": batch.proxy_servers,
        "proxy_auth_required": batch.proxy_auth_required,
        "rotation_enabled": batch.rotation_enabled,
        "status": batch.status.value,
        "session_ids": batch.session_ids,
        "completed_jobs": batch.completed_jobs,
        "failed_jobs": batch.failed_jobs,
        "cancelled_jobs": batch.cancelled_jobs,
        "error_message": batch.error_message,
        "created_at": batch.created_at.isoformat(),
        "updated_at": batch.updated_at.isoformat(),
        "started_at": batch.started_at.isoformat() if batch.started_at else None,
        "finished_at": batch.finished_at.isoformat() if batch.finished_at else None,
        "revision": batch.revision,
    }
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _batch_from_payload(payload: str) -> AutomationBatch:
    value: dict[str, Any] = json.loads(payload)
    proxy_server = value.get("proxy_server")
    proxy_servers = value.get("proxy_servers") or ([proxy_server] if proxy_server else [])
    return AutomationBatch(
        id=str(value["id"]),
        tenant_id=str(value["tenant_id"]),
        display_name=str(value["display_name"]),
        start_url=str(value["start_url"]),
        mode=BrowserMode(str(value["mode"])),
        total_jobs=int(value["total_jobs"]),
        concurrency=int(value["concurrency"]),
        active_seconds=float(value["active_seconds"]),
        proxy_server=str(proxy_server) if proxy_server else None,
        proxy_servers=[str(item) for item in proxy_servers],
        proxy_auth_required=bool(value.get("proxy_auth_required", False)),
        rotation_enabled=bool(value.get("rotation_enabled", False)),
        status=BatchStatus(str(value["status"])),
        session_ids=[str(item) for item in value.get("session_ids", [])],
        completed_jobs=int(value.get("completed_jobs", 0)),
        failed_jobs=int(value.get("failed_jobs", 0)),
        cancelled_jobs=int(value.get("cancelled_jobs", 0)),
        error_message=(str(value["error_message"]) if value.get("error_message") else None),
        created_at=datetime.fromisoformat(str(value["created_at"])),
        updated_at=datetime.fromisoformat(str(value["updated_at"])),
        started_at=(
            datetime.fromisoformat(str(value["started_at"]))
            if value.get("started_at")
            else None
        ),
        finished_at=(
            datetime.fromisoformat(str(value["finished_at"]))
            if value.get("finished_at")
            else None
        ),
        revision=int(value.get("revision", 0)),
    )
