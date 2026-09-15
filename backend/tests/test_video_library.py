import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.interfaces.api import tasks_router
from app.use_cases.upload.bulk_video_queue_service import BulkVideoQueueService
from app.use_cases.upload.video_library import scan_video_paths


def test_windows_picker_helper_decodes_multiple_paths(monkeypatch):
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(
            returncode=0,
            stdout='["C:\\\\videos\\\\one.mp4","D:\\\\two.mov"]',
            stderr="",
        )

    monkeypatch.setattr(tasks_router.subprocess, "run", fake_run)

    selected = tasks_router._pick_video_paths_windows(False)

    assert selected == [r"C:\videos\one.mp4", r"D:\two.mov"]
    assert "-STA" in captured["command"]
    assert captured["kwargs"]["env"]["TIKTOK_PICK_FOLDER"] == "0"


def test_windows_folder_picker_helper_sets_folder_mode(monkeypatch):
    captured = {}

    def fake_run(_command, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout='["D:\\\\videos"]', stderr="")

    monkeypatch.setattr(tasks_router.subprocess, "run", fake_run)

    assert tasks_router._pick_video_paths_windows(True) == [r"D:\videos"]
    assert captured["env"]["TIKTOK_PICK_FOLDER"] == "1"


def test_picker_failure_is_returned_as_json_safe_http_error(monkeypatch):
    async def fail_to_open_picker(*_args, **_kwargs):
        raise RuntimeError("missing Tk runtime")

    monkeypatch.setattr(tasks_router.asyncio, "to_thread", fail_to_open_picker)

    with pytest.raises(HTTPException) as error:
        asyncio.run(tasks_router._pick_video_library(False))

    assert error.value.status_code == 500
    assert "hộp thoại" in error.value.detail


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


def _fake_archive(video_path, account_email):
    return f"DA_DANG/{account_email}/{video_path}"


def test_batch_assigns_each_video_to_exactly_one_account():
    async def scenario():
        dispatcher = _FakeDispatcher()
        service = BulkVideoQueueService(
            dispatcher, poll_seconds=0.001, archive_handler=_fake_archive
        )
        batch = service.add(
            [
                "one@hotmail.com",
                "two@hotmail.com",
                "three@hotmail.com",
                "four@hotmail.com",
                "five@hotmail.com",
            ],
            [f"{index}.mp4" for index in range(1, 11)],
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
        assert result["archived"] == 10
        assert result["archive_failed"] == 0
        grouped = {}
        for item in result["assignments"]:
            grouped.setdefault(item["account_email"], []).append(item["video_path"])
        assert grouped == {
            "one@hotmail.com": ["1.mp4", "2.mp4"],
            "two@hotmail.com": ["3.mp4", "4.mp4"],
            "three@hotmail.com": ["5.mp4", "6.mp4"],
            "four@hotmail.com": ["7.mp4", "8.mp4"],
            "five@hotmail.com": ["9.mp4", "10.mp4"],
        }
        assert all(len(paths) == len(set(paths)) == 2 for paths in grouped.values())
        assert len({path for paths in grouped.values() for path in paths}) == 10
        assert len(dispatcher.calls) == 5
        assert {email: paths for email, paths, _captions in dispatcher.calls} == grouped

    asyncio.run(scenario())


def test_batch_deduplicates_accounts_case_insensitively():
    async def scenario():
        dispatcher = _FakeDispatcher()
        service = BulkVideoQueueService(
            dispatcher, poll_seconds=0.001, archive_handler=_fake_archive
        )
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


def test_batch_requires_enough_unique_videos_across_all_accounts():
    dispatcher = _FakeDispatcher()
    service = BulkVideoQueueService(
        dispatcher, poll_seconds=0.001, archive_handler=_fake_archive
    )

    with pytest.raises(ValueError, match="Can it nhat 4 video khac nhau"):
        service.add(
            ["one@hotmail.com", "two@hotmail.com"],
            ["1.mp4", "2.mp4", "3.mp4"],
            videos_per_account=2,
        )


def test_batch_counts_each_video_result_instead_of_treating_account_as_success():
    async def scenario():
        dispatcher = _FakeDispatcher(failed_accounts={"bad@hotmail.com"})
        service = BulkVideoQueueService(
            dispatcher, poll_seconds=0.001, archive_handler=_fake_archive
        )
        batch = service.add(
            ["good@hotmail.com", "bad@hotmail.com"],
            ["1.mp4", "2.mp4", "3.mp4", "4.mp4"],
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
            ("bad@hotmail.com", "4.mp4", "Caption timeout"),
        ]

    asyncio.run(scenario())


def test_active_batch_reserves_video_against_another_batch():
    async def scenario():
        dispatcher = _FakeDispatcher()
        service = BulkVideoQueueService(
            dispatcher, poll_seconds=0.001, archive_handler=_fake_archive
        )
        first = service.add(["one@hotmail.com"], ["only.mp4"])

        with pytest.raises(ValueError, match="Can it nhat 1 video khac nhau"):
            service.add(["two@hotmail.com"], ["only.mp4"])

        await service._tasks[first["id"]]

    asyncio.run(scenario())


def test_result_is_matched_by_video_path_even_when_sink_order_changes():
    async def scenario():
        archived = []

        class OutOfOrderDispatcher(_FakeDispatcher):
            async def submit_task(self, account_id, task_type, extra_config):
                self.busy.add(account_id)
                first, second = extra_config["video_paths"]
                extra_config["_result_sink"].extend([
                    {"video_path": second, "success": True, "error": ""},
                    {"video_path": first, "success": False, "error": "first failed"},
                ])
                asyncio.get_running_loop().call_later(
                    0.005, self.busy.remove, account_id
                )
                return True

        def archive(video_path, account_email):
            archived.append((video_path, account_email))
            return f"DA_DANG/{account_email}/{video_path}"

        dispatcher = OutOfOrderDispatcher()
        service = BulkVideoQueueService(
            dispatcher, poll_seconds=0.001, archive_handler=archive
        )
        batch = service.add(
            ["owner@hotmail.com"], ["first.mp4", "second.mp4"],
            videos_per_account=2,
        )

        await service._tasks[batch["id"]]
        result = service.list()[0]

        assert [item["status"] for item in result["assignments"]] == [
            "ERROR", "DONE"
        ]
        assert result["assignments"][0]["error"] == "first failed"
        assert archived == [("second.mp4", "owner@hotmail.com")]

    asyncio.run(scenario())


def test_duplicate_video_is_replaced_with_one_reserved_spare():
    async def scenario():
        archived = []

        class DuplicateThenSuccessDispatcher(_FakeDispatcher):
            async def submit_task(self, account_id, task_type, extra_config):
                self.busy.add(account_id)
                path = extra_config["video_paths"][0]
                self.calls.append((account_id, [path], list(extra_config["captions"])))
                if path == "primary.mp4":
                    detail = {
                        "video_path": path,
                        "success": False,
                        "error": "VIDEO_TRUNG",
                        "code": "VIDEO_DUPLICATE",
                    }
                else:
                    detail = {
                        "video_path": path,
                        "success": True,
                        "error": "",
                        "code": "",
                    }
                extra_config["_result_sink"].append(detail)
                asyncio.get_running_loop().call_later(
                    0.005, self.busy.remove, account_id
                )
                return True

        def archive(video_path, account_email):
            archived.append((video_path, account_email))
            return f"DA_DANG/{account_email}/{video_path}"

        dispatcher = DuplicateThenSuccessDispatcher()
        service = BulkVideoQueueService(
            dispatcher, poll_seconds=0.001, archive_handler=archive
        )
        batch = service.add(
            ["owner@hotmail.com"], ["primary.mp4", "backup.mp4"]
        )

        await service._tasks[batch["id"]]
        result = service.list()[0]
        item = result["assignments"][0]

        assert result["status"] == "DONE"
        assert result["duplicates"] == 1
        assert result["replacements"] == 1
        assert result["completed"] == 1
        assert result["failed"] == 0
        assert item["video_path"] == "backup.mp4"
        assert item["replacement_history"][0]["video_path"] == "primary.mp4"
        assert archived == [("backup.mp4", "owner@hotmail.com")]
        assert [call[1] for call in dispatcher.calls] == [
            ["primary.mp4"], ["backup.mp4"]
        ]

    asyncio.run(scenario())


def test_duplicate_without_spare_is_not_mislabelled_as_swallowed():
    async def scenario():
        class DuplicateDispatcher(_FakeDispatcher):
            async def submit_task(self, account_id, task_type, extra_config):
                self.busy.add(account_id)
                path = extra_config["video_paths"][0]
                extra_config["_result_sink"].append({
                    "video_path": path,
                    "success": False,
                    "error": "VIDEO_TRUNG",
                    "code": "VIDEO_DUPLICATE",
                })
                asyncio.get_running_loop().call_later(
                    0.005, self.busy.remove, account_id
                )
                return True

        service = BulkVideoQueueService(
            DuplicateDispatcher(),
            poll_seconds=0.001,
            archive_handler=_fake_archive,
        )
        batch = service.add(["owner@hotmail.com"], ["only.mp4"])

        await service._tasks[batch["id"]]
        result = service.list()[0]

        assert result["status"] == "DONE_WITH_ERRORS"
        assert result["assignments"][0]["status"] == "DUPLICATE"
        assert result["assignments"][0]["code"] == "VIDEO_DUPLICATE"
        assert result["duplicates"] == 1
        assert result["swallowed"] == 0
        assert result["replacements"] == 0

    asyncio.run(scenario())


def test_swallowed_video_is_final_and_does_not_consume_spare():
    async def scenario():
        class SwallowedDispatcher(_FakeDispatcher):
            async def submit_task(self, account_id, task_type, extra_config):
                self.busy.add(account_id)
                path = extra_config["video_paths"][0]
                self.calls.append(path)
                extra_config["_result_sink"].append({
                    "video_path": path,
                    "success": False,
                    "error": "VIDEO_BI_NUOT",
                    "code": "VIDEO_SWALLOWED",
                })
                asyncio.get_running_loop().call_later(
                    0.005, self.busy.remove, account_id
                )
                return True

        dispatcher = SwallowedDispatcher()
        service = BulkVideoQueueService(
            dispatcher, poll_seconds=0.001, archive_handler=_fake_archive
        )
        batch = service.add(
            ["owner@hotmail.com"], ["primary.mp4", "unused-spare.mp4"]
        )

        await service._tasks[batch["id"]]
        result = service.list()[0]

        assert result["assignments"][0]["status"] == "SWALLOWED"
        assert result["swallowed"] == 1
        assert result["duplicates"] == 0
        assert result["replacements"] == 0
        assert dispatcher.calls == ["primary.mp4"]
        assert result["spares"][0]["status"] == "AVAILABLE"

    asyncio.run(scenario())


def test_successful_video_moves_to_account_archive_and_is_not_scanned_again(tmp_path):
    async def scenario():
        source = tmp_path / "clip.mp4"
        source.write_bytes(b"video")
        dispatcher = _FakeDispatcher()
        service = BulkVideoQueueService(dispatcher, poll_seconds=0.001)
        batch = service.add(["owner@hotmail.com"], [str(source)])

        await service._tasks[batch["id"]]
        result = service.list()[0]
        archived = tmp_path / "DA_DANG" / "owner@hotmail.com" / "clip.mp4"

        assert result["status"] == "DONE"
        assert result["archived"] == 1
        assert result["assignments"][0]["archived_path"] == str(archived.resolve())
        assert not source.exists()
        assert archived.read_bytes() == b"video"
        assert scan_video_paths([str(tmp_path)]) == []

    asyncio.run(scenario())
