"""Assign a distinct video list to every account and run one browser per account."""

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from app.use_cases.upload.video_library import (
    archive_posted_video,
    archive_refused_video,
)


logger = logging.getLogger("BulkVideoQueue")


class BulkVideoQueueService:
    """Reserve each library video for exactly one account and archive successes."""

    def __init__(
        self,
        dispatcher,
        poll_seconds: float = 1.0,
        result_resolver: Optional[Callable[[str], dict[str, str]]] = None,
        archive_handler: Optional[Callable[[str, str], str]] = None,
        refusal_handler: Optional[Callable[[str, str, str], str]] = None,
    ):
        self.dispatcher = dispatcher
        self.poll_seconds = poll_seconds
        self.result_resolver = result_resolver
        self.archive_handler = archive_handler or archive_posted_video
        self.refusal_handler = refusal_handler or archive_refused_video
        self._batches: dict[str, dict[str, Any]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._batch_lock = asyncio.Lock()
        self._spare_lock = asyncio.Lock()
        # A synchronous reservation made inside add() prevents two API calls
        # from assigning the same file before either account starts running.
        self._claimed_video_keys: set[str] = set()
        self._stopping = False

    @staticmethod
    def _video_key(path: str) -> str:
        return str(Path(str(path)).expanduser().resolve()).casefold()

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
            key = BulkVideoQueueService._video_key(path)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            unique_paths.append(path)
        required_video_count = len(account_emails) * videos_per_account
        if len(unique_paths) < required_video_count:
            raise ValueError(
                f"Can it nhat {required_video_count} video khac nhau de "
                f"cap rieng {videos_per_account} video cho moi account; "
                "video khong duoc dung lai o account khac."
            )

        assignments: list[dict[str, Any]] = []
        item_index = 0
        for account_index, email in enumerate(account_emails):
            start = account_index * videos_per_account
            for slot in range(videos_per_account):
                path = unique_paths[start + slot]
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
        input_unique_count = len({self._video_key(path) for path in video_paths})
        available_paths: list[str] = []
        seen_paths: set[str] = set()
        claimed_count = 0
        for raw_path in video_paths:
            path = str(raw_path)
            key = self._video_key(path)
            if key in seen_paths:
                continue
            seen_paths.add(key)
            if key in self._claimed_video_keys:
                claimed_count += 1
                continue
            available_paths.append(path)

        assignments = self.build_assignments(
            unique_accounts,
            available_paths,
            videos_per_account,
        )
        required_video_count = len(assignments)
        spare_paths = available_paths[required_video_count:]
        spares = [
            {
                "video_path": path,
                "video_name": Path(path).name,
                "caption": Path(path).stem,
                "status": "AVAILABLE",
            }
            for path in spare_paths
        ]
        # Reserve primary assignments and all submitted fallback candidates.
        # This prevents another batch from consuming a fallback while the
        # current accounts are still posting concurrently.
        for path in available_paths:
            self._claimed_video_keys.add(self._video_key(path))
        batch = {
            "id": batch_id,
            "status": "PENDING",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "account_count": len(unique_accounts),
            "videos_per_account": videos_per_account,
            "library_video_count": input_unique_count,
            "available_video_count": len(available_paths),
            "claimed_video_count": claimed_count,
            "total": len(assignments),
            "submitted": 0,
            "completed": 0,
            "processed": 0,
            "failed": 0,
            "archived": 0,
            "archive_failed": 0,
            "duplicates": 0,
            "refused_parked": 0,
            "refused_park_failed": 0,
            "swallowed": 0,
            "replacements": 0,
            "cancel_requested": False,
            "assignments": assignments,
            "spares": spares,
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

    async def _submit_items(
        self,
        batch: dict[str, Any],
        email: str,
        items: list[dict[str, Any]],
    ) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
        if not await self._wait_until_free(email, batch):
            raise RuntimeError("Batch stopped before the upload task could start.")
        for item in items:
            item["status"] = "SUBMITTING"
        result_sink: list[dict[str, Any]] = []
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
        details_by_path = {
            self._video_key(str(detail.get("video_path") or "")): detail
            for detail in result_sink
            if detail.get("video_path")
        }
        if not result_sink:
            for item in items:
                details_by_path[self._video_key(item["video_path"])] = {
                    "video_path": item["video_path"],
                    "success": fallback.get("status") == "SUCCESS",
                    "error": fallback.get("step") or "Upload task failed.",
                    "code": "",
                }
        return details_by_path, fallback

    async def _take_spare(
        self, batch: dict[str, Any], email: str
    ) -> Optional[dict[str, Any]]:
        async with self._spare_lock:
            for spare in batch["spares"]:
                if spare["status"] != "AVAILABLE":
                    continue
                spare["status"] = "USED"
                spare["account_email"] = email
                return spare
        return None

    async def _park_refused_video(
        self, batch: dict[str, Any], email: str, refusal: dict[str, Any]
    ) -> None:
        """Move a refused video out of the library; never fail the batch."""
        video_path = str(refusal.get("video_path") or "")
        reason = str(refusal.get("error") or refusal.get("code") or "")
        try:
            refusal["parked_path"] = await asyncio.to_thread(
                self.refusal_handler, video_path, email, reason
            )
        except FileNotFoundError:
            # Already gone - another run moved it, or the operator did.
            logger.info("Refused video is no longer in the library: %s", video_path)
            return
        except Exception as park_exc:
            batch["refused_park_failed"] += 1
            refusal["park_error"] = str(park_exc)
            logger.exception(
                "Cannot move the refused video out of the library: %s", video_path
            )
            return
        batch["refused_parked"] += 1
        logger.info(
            "Refused video moved out of the library: %s -> %s",
            video_path,
            refusal["parked_path"],
        )

    @staticmethod
    def _detail_result(
        detail: Optional[dict[str, Any]],
        *,
        sink_had_results: bool,
        fallback: dict[str, str],
    ) -> tuple[bool, str, str]:
        if detail is not None:
            return (
                bool(detail.get("success")),
                str(detail.get("error") or ""),
                str(detail.get("code") or ""),
            )
        missing_error = (
            "Khong nhan duoc ket qua rieng cua video tu upload task."
            if sink_had_results
            else str(fallback.get("step") or "Upload task failed.")
        )
        return False, missing_error, ""

    async def _finish_item(
        self,
        batch: dict[str, Any],
        item: dict[str, Any],
        *,
        succeeded: bool,
        error: str,
        code: str,
    ) -> None:
        if succeeded:
            item["status"] = "DONE"
            try:
                item["archived_path"] = await asyncio.to_thread(
                    self.archive_handler,
                    item["video_path"],
                    item["account_email"],
                )
                batch["archived"] += 1
            except Exception as archive_exc:
                item["archive_error"] = str(archive_exc)
                batch["archive_failed"] += 1
                logger.exception(
                    "Posted video could not be archived for %s: %s",
                    item["account_email"],
                    item["video_path"],
                )
            batch["completed"] += 1
        else:
            if code == "VIDEO_DUPLICATE":
                item["status"] = "DUPLICATE"
            elif code == "VIDEO_SWALLOWED":
                item["status"] = "SWALLOWED"
                batch["swallowed"] += 1
            else:
                item["status"] = "ERROR"
            item["error"] = error or "Upload task failed."
            item["code"] = code
            batch["failed"] += 1
        batch["processed"] += 1

    async def _run_account(self, batch: dict[str, Any], items: list[dict[str, Any]]) -> None:
        email = items[0]["account_email"]
        try:
            details, fallback = await self._submit_items(batch, email, items)
            sink_had_results = bool(details)
            for item in items:
                detail = details.get(self._video_key(item["video_path"]))
                item_fallback = fallback
                item_sink_had_results = sink_had_results
                while True:
                    succeeded, error, code = self._detail_result(
                        detail,
                        sink_had_results=item_sink_had_results,
                        fallback=item_fallback,
                    )
                    if not succeeded and code == "VIDEO_DUPLICATE":
                        batch["duplicates"] += 1
                        refusal = {
                            "video_path": item["video_path"],
                            "error": error,
                            "code": code,
                        }
                        item.setdefault("replacement_history", []).append(refusal)
                        # TikTok refused the VIDEO. Take it out of the library
                        # now, before the pool can hand the same file to the
                        # next account for the same verdict.
                        await self._park_refused_video(batch, email, refusal)
                        spare = await self._take_spare(batch, email)
                        if spare is None:
                            error = (
                                (error + " ") if error else ""
                            ) + "Khong con video du phong chua dung de thay the."
                            await self._finish_item(
                                batch, item, succeeded=False, error=error, code=code
                            )
                            break
                        item["video_path"] = spare["video_path"]
                        item["video_name"] = spare["video_name"]
                        item["caption"] = spare["caption"]
                        item["replacement_number"] = len(item["replacement_history"])
                        batch["replacements"] += 1
                        logger.info(
                            "Replacing duplicate video for %s with %s",
                            email,
                            item["video_path"],
                        )
                        try:
                            replacement_details, item_fallback = (
                                await self._submit_items(batch, email, [item])
                            )
                            item_sink_had_results = bool(replacement_details)
                            detail = replacement_details.get(
                                self._video_key(item["video_path"])
                            )
                        except Exception as replacement_exc:
                            await self._finish_item(
                                batch,
                                item,
                                succeeded=False,
                                error=(
                                    "Khong chay duoc video thay the: "
                                    f"{replacement_exc}"
                                ),
                                code="",
                            )
                            break
                        continue

                    await self._finish_item(
                        batch,
                        item,
                        succeeded=succeeded,
                        error=error,
                        code=code,
                    )
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            for item in items:
                if item["status"] not in {
                    "DONE", "ERROR", "DUPLICATE", "SWALLOWED"
                }:
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
                elif (
                    any(item["status"] != "DONE" for item in batch["assignments"])
                    or batch["archive_failed"] > 0
                ):
                    batch["status"] = "DONE_WITH_ERRORS"
                else:
                    batch["status"] = "DONE"
        except asyncio.CancelledError:
            batch["status"] = "CANCELLED"
        finally:
            # Failed/cancelled items may be retried. Confirmed posts stay
            # claimed for this backend lifetime; normally their source path no
            # longer exists because it has been moved into DA_DANG/<account>.
            for item in batch["assignments"]:
                if item["status"] not in {"DONE", "DUPLICATE", "SWALLOWED"}:
                    self._claimed_video_keys.discard(
                        self._video_key(item["video_path"])
                    )
            for spare in batch.get("spares", []):
                if spare["status"] == "AVAILABLE":
                    self._claimed_video_keys.discard(
                        self._video_key(spare["video_path"])
                    )
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
        result["spares"] = [dict(item) for item in batch.get("spares", [])]
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
