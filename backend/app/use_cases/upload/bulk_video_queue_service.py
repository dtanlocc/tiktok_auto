"""Assign a distinct video list to every account and run one browser per account."""

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional


logger = logging.getLogger("BulkVideoQueue")


class BulkVideoQueueService:
    """Reuse library videos across accounts, never within one account's list."""

    def __init__(
        self,
        dispatcher,
        poll_seconds: float = 1.0,
        result_resolver: Optional[Callable[[str], dict[str, str]]] = None,
    ):
        self.dispatcher = dispatcher
        self.poll_seconds = poll_seconds
        self.result_resolver = result_resolver
        self._batches: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._batch_lock = asyncio.Lock()
        self._stopping = False

    @staticmethod
    def build_assignments(
        account_emails: list[str],
        video_paths: list[str],
        videos_per_account: int,
    ) -> list[dict[str, Any]]:
        if not account_emails:
            raise ValueError("Can it nhat mot account.")
        if videos_per_account < 1:
            raise ValueError("So video moi account phai lon hon 0.")

        unique_paths: list[str] = []
        seen_paths: set[str] = set()
        for raw_path in video_paths:
            path = str(raw_path)
            key = path.casefold()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            unique_paths.append(path)
        if len(unique_paths) < videos_per_account:
            raise ValueError(
                f"Can it nhat {videos_per_account} video khac nhau de moi account "
                "khong bi trung video."
            )

        assignments: list[dict[str, Any]] = []
        item_index = 0
        video_count = len(unique_paths)
        for account_index, email in enumerate(account_emails):
            start = (account_index * videos_per_account) % video_count
            for slot in range(videos_per_account):
                path = unique_paths[(start + slot) % video_count]
                item_index += 1
                assignments.append({
                    "index": item_index,
                    "account_slot": slot + 1,
                    "account_email": email,
                    "video_path": path,
                    "video_name": Path(path).name,
                    "caption": Path(path).stem,
                    "status": "PENDING",
                })
        return assignments

    def add(
        self,
        account_emails: list[str],
        video_paths: list[str],
        videos_per_account: int = 1,
    ) -> dict[str, Any]:
        batch_id = str(uuid.uuid4())
        unique_accounts: list[str] = []
        seen_accounts: set[str] = set()
        for raw_email in account_emails:
            email = str(raw_email).strip()
            key = email.casefold()
            if not email or key in seen_accounts:
                continue
            seen_accounts.add(key)
            unique_accounts.append(email)
        assignments = self.build_assignments(
            unique_accounts,
            video_paths,
            videos_per_account,
        )
        batch = {
            "id": batch_id,
            "status": "PENDING",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "account_count": len(unique_accounts),
            "videos_per_account": videos_per_account,
            "library_video_count": len({str(path).casefold() for path in video_paths}),
            "total": len(assignments),
            "submitted": 0,
            "completed": 0,
            "processed": 0,
            "failed": 0,
            "cancel_requested": False,
            "assignments": assignments,
        }
        self._batches[batch_id] = batch
        self._tasks[batch_id] = asyncio.create_task(self._run(batch_id))
        return self._public(batch)

    async def _wait_until_free(self, email: str, batch: dict[str, Any]) -> bool:
        while self.dispatcher.is_account_busy(email):
            if self._stopping or batch["cancel_requested"]:
                return False
            await asyncio.sleep(self.poll_seconds)
        return not self._stopping and not batch["cancel_requested"]

    async def _run_account(self, batch: dict[str, Any], items: list[dict[str, Any]]) -> None:
        email = items[0]["account_email"]
        if not await self._wait_until_free(email, batch):
            return
        for item in items:
            item["status"] = "SUBMITTING"
        result_sink: list[dict[str, Any]] = []
        try:
            accepted = await self.dispatcher.submit_task(
                account_id=email,
                task_type="UPLOAD_MEDIA_BATCH",
                extra_config={
                    "video_paths": [item["video_path"] for item in items],
                    "captions": [item["caption"] for item in items],
                    "schedule_at": None,
                    "_result_sink": result_sink,
                },
            )
            if accepted is not True or not self.dispatcher.is_account_busy(email):
                raise RuntimeError("Dispatcher did not accept the account video batch.")
            for item in items:
                item["status"] = "RUNNING"
            batch["submitted"] += len(items)
            while self.dispatcher.is_account_busy(email):
                await asyncio.sleep(self.poll_seconds)

            fallback = self.result_resolver(email) if self.result_resolver else {
                "status": "SUCCESS", "step": ""
            }
            for position, item in enumerate(items):
                detail = result_sink[position] if position < len(result_sink) else None
                succeeded = (
                    bool(detail.get("success"))
                    if detail is not None
                    else fallback.get("status") == "SUCCESS"
                )
                item["status"] = "DONE" if succeeded else "ERROR"
                if not succeeded:
                    item["error"] = (
                        (detail or {}).get("error")
                        or fallback.get("step")
                        or "Upload task failed."
                    )
                batch["processed"] += 1
                if succeeded:
                    batch["completed"] += 1
                else:
                    batch["failed"] += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            for item in items:
                if item["status"] not in {"DONE", "ERROR"}:
                    item["status"] = "ERROR"
                    item["error"] = str(exc)
                    batch["processed"] += 1
                    batch["failed"] += 1
            logger.exception("Cannot submit account video batch for %s", email)

    async def _run(self, batch_id: str) -> None:
        batch = self._batches[batch_id]
        try:
            async with self._batch_lock:
                if batch["cancel_requested"]:
                    batch["status"] = "CANCELLED"
                    return
                batch["status"] = "RUNNING"
                grouped: dict[str, list[dict[str, Any]]] = {}
                for item in batch["assignments"]:
                    grouped.setdefault(item["account_email"], []).append(item)
                await asyncio.gather(*(self._run_account(batch, items) for items in grouped.values()))
                if batch["cancel_requested"]:
                    batch["status"] = "CANCELLED"
                elif any(item["status"] == "ERROR" for item in batch["assignments"]):
                    batch["status"] = "DONE_WITH_ERRORS"
                else:
                    batch["status"] = "DONE"
        except asyncio.CancelledError:
            batch["status"] = "CANCELLED"
        finally:
            batch["finished_at"] = datetime.now().isoformat(timespec="seconds")

    def cancel(self, batch_id: str) -> bool:
        batch = self._batches.get(batch_id)
        if not batch or batch["status"] in {"DONE", "DONE_WITH_ERRORS", "CANCELLED"}:
            return False
        batch["cancel_requested"] = True
        return True

    def list(self) -> list[dict[str, Any]]:
        values = sorted(self._batches.values(), key=lambda item: item["created_at"], reverse=True)
        return [self._public(batch) for batch in values]

    @staticmethod
    def _public(batch: dict[str, Any]) -> dict[str, Any]:
        result = dict(batch)
        result["assignments"] = [dict(item) for item in batch["assignments"]]
        return result

    async def shutdown(self) -> None:
        self._stopping = True
        for batch in self._batches.values():
            batch["cancel_requested"] = True
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
