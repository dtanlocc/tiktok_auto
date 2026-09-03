import asyncio

import pytest

from app.use_cases.upload.bulk_video_queue_service import BulkVideoQueueService
from app.use_cases.upload.video_library import scan_video_paths


def test_scan_video_paths_recurses_sorts_and_deduplicates(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    first = nested / "A clip.mp4"
    second = tmp_path / "b clip.MOV"
    ignored = tmp_path / "readme.txt"
    first.write_bytes(b"a")
    second.write_bytes(b"bb")
    ignored.write_text("ignore", encoding="utf-8")

    videos = scan_video_paths([str(tmp_path), str(first)])

    assert [video["name"] for video in videos] == ["A clip.mp4", "b clip.MOV"]
    assert [video["size_bytes"] for video in videos] == [1, 2]


class _FakeDispatcher:
    def __init__(self, failed_accounts=None):
        self.busy = set()
        self.calls = []
        self.failed_accounts = set(failed_accounts or [])

    def is_account_busy(self, account_id):
        return account_id in self.busy

    async def submit_task(self, account_id, task_type, extra_config):
        assert account_id not in self.busy
        assert task_type == "UPLOAD_MEDIA_BATCH"
        self.busy.add(account_id)
        paths = list(extra_config["video_paths"])
        captions = list(extra_config["captions"])
        self.calls.append((account_id, paths, captions))
        for path in paths:
            failed = account_id in self.failed_accounts
            extra_config["_result_sink"].append({
                "video_path": path,
                "success": not failed,
                "error": "Caption timeout" if failed else "",
            })
        asyncio.get_running_loop().call_later(0.005, self.busy.remove, account_id)
        return True


def test_batch_assigns_two_distinct_videos_per_account_and_reuses_across_accounts():
    async def scenario():
        dispatcher = _FakeDispatcher()
        service = BulkVideoQueueService(dispatcher, poll_seconds=0.001)
        batch = service.add(
            [
                "one@hotmail.com",
                "two@hotmail.com",
                "three@hotmail.com",
                "four@hotmail.com",
                "five@hotmail.com",
            ],
            ["1.mp4", "2.mp4", "3.mp4"],
            videos_per_account=2,
        )
        await service._tasks[batch["id"]]
        result = service.list()[0]

        assert result["status"] == "DONE"
        assert result["account_count"] == 5
        assert result["videos_per_account"] == 2
        assert result["total"] == 10
        assert result["completed"] == 10
        assert result["processed"] == 10
        assert result["failed"] == 0
        grouped = {}
        for item in result["assignments"]:
            grouped.setdefault(item["account_email"], []).append(item["video_path"])
        assert grouped == {
            "one@hotmail.com": ["1.mp4", "2.mp4"],
            "two@hotmail.com": ["3.mp4", "1.mp4"],
            "three@hotmail.com": ["2.mp4", "3.mp4"],
            "four@hotmail.com": ["1.mp4", "2.mp4"],
            "five@hotmail.com": ["3.mp4", "1.mp4"],
        }
        assert all(len(paths) == len(set(paths)) == 2 for paths in grouped.values())
        assert len(dispatcher.calls) == 5
        assert {email: paths for email, paths, _captions in dispatcher.calls} == grouped

    asyncio.run(scenario())


def test_batch_deduplicates_accounts_case_insensitively():
    async def scenario():
        dispatcher = _FakeDispatcher()
        service = BulkVideoQueueService(dispatcher, poll_seconds=0.001)
        batch = service.add(
            ["Same@Hotmail.com", "same@hotmail.com"],
            ["1.mp4", "2.mp4"],
            videos_per_account=2,
        )
        await service._tasks[batch["id"]]
        result = service.list()[0]

        assert result["account_count"] == 1
        assert result["total"] == 2
        assert len(dispatcher.calls) == 1

    asyncio.run(scenario())


def test_batch_requires_enough_unique_videos_for_each_account():
    dispatcher = _FakeDispatcher()
    service = BulkVideoQueueService(dispatcher, poll_seconds=0.001)

    with pytest.raises(ValueError, match="Can it nhat 2 video khac nhau"):
        service.add(
            ["one@hotmail.com"],
            ["same.mp4", "SAME.MP4"],
            videos_per_account=2,
        )


def test_batch_counts_each_video_result_instead_of_treating_account_as_success():
    async def scenario():
        dispatcher = _FakeDispatcher(failed_accounts={"bad@hotmail.com"})
        service = BulkVideoQueueService(dispatcher, poll_seconds=0.001)
        batch = service.add(
            ["good@hotmail.com", "bad@hotmail.com"],
            ["1.mp4", "2.mp4", "3.mp4"],
            videos_per_account=2,
        )
        await service._tasks[batch["id"]]
        result = service.list()[0]

        assert result["status"] == "DONE_WITH_ERRORS"
        assert result["processed"] == 4
        assert result["completed"] == 2
        assert result["failed"] == 2
        failed = [item for item in result["assignments"] if item["status"] == "ERROR"]
        assert [(item["account_email"], item["video_path"], item["error"]) for item in failed] == [
            ("bad@hotmail.com", "3.mp4", "Caption timeout"),
            ("bad@hotmail.com", "1.mp4", "Caption timeout"),
        ]

    asyncio.run(scenario())
