"""A video TikTok refused must not be handed to the next account.

Measured 22-23/09/2026: " 2008 pagi hai hai" came back VIDEO_TRUNG on
@catali7_daily95, stayed in the folder, and came back the same way on
@abbiewilsterman855875 a day later. Each repeat spends an account slot and a
browser session on a verdict TikTok has already given.
"""
import asyncio

import pytest

from app.use_cases.upload.bulk_video_queue_service import BulkVideoQueueService
from app.use_cases.upload.video_library import (
    REFUSED_ARCHIVE_DIRNAME,
    REFUSED_NOTE_FILENAME,
    archive_refused_video,
    scan_video_paths,
)
from tests.test_video_library import _FakeDispatcher


def test_a_refused_video_is_moved_out_and_the_reason_is_kept(tmp_path):
    video = tmp_path / "refused.mp4"
    video.write_bytes(b"data")

    parked = archive_refused_video(
        str(video), "owner@hotmail.com", "VIDEO_TRUNG: TikTok báo video đã tồn tại"
    )

    assert not video.exists()
    assert REFUSED_ARCHIVE_DIRNAME in parked
    note = (tmp_path / REFUSED_ARCHIVE_DIRNAME / REFUSED_NOTE_FILENAME).read_text(
        encoding="utf-8"
    )
    assert "owner@hotmail.com" in note
    assert "refused.mp4" in note
    assert "TikTok" in note


def test_the_library_never_offers_a_refused_video_again(tmp_path):
    keep = tmp_path / "keep.mp4"
    keep.write_bytes(b"data")
    refused = tmp_path / "refused.mp4"
    refused.write_bytes(b"data")
    archive_refused_video(str(refused), "owner@hotmail.com", "VIDEO_TRUNG")

    offered = [item["name"] for item in scan_video_paths([str(tmp_path)])]

    assert offered == ["keep.mp4"]


def test_a_second_refusal_of_the_same_name_does_not_overwrite_the_first(tmp_path):
    first = tmp_path / "clip.mp4"
    first.write_bytes(b"one")
    archive_refused_video(str(first), "a@hotmail.com", "VIDEO_TRUNG")
    second = tmp_path / "clip.mp4"
    second.write_bytes(b"two")

    archive_refused_video(str(second), "b@hotmail.com", "VIDEO_TRUNG")

    parked = sorted(
        path.name for path in (tmp_path / REFUSED_ARCHIVE_DIRNAME).glob("*.mp4")
    )
    assert parked == ["clip.mp4", "clip__2.mp4"]


def test_a_missing_file_is_reported_rather_than_moved(tmp_path):
    with pytest.raises(FileNotFoundError):
        archive_refused_video(str(tmp_path / "gone.mp4"), "owner@hotmail.com", "")


def _fake_archive(video_path, account_email):
    return f"DA_DANG/{account_email}/{video_path}"


class _DuplicateThenSuccessDispatcher(_FakeDispatcher):
    async def submit_task(self, account_id, task_type, extra_config):
        self.busy.add(account_id)
        path = extra_config["video_paths"][0]
        self.calls.append((account_id, [path], list(extra_config["captions"])))
        refused = path == "primary.mp4"
        extra_config["_result_sink"].append({
            "video_path": path,
            "success": not refused,
            "error": "VIDEO_TRUNG: TikTok báo video đã tồn tại" if refused else "",
            "code": "VIDEO_DUPLICATE" if refused else "",
        })
        asyncio.get_running_loop().call_later(0.005, self.busy.remove, account_id)
        return True


def test_the_batch_parks_the_refused_video_and_posts_the_replacement():
    async def scenario():
        parked = []

        def park(video_path, account_email, reason):
            parked.append((video_path, account_email, reason))
            return f"BI_TU_CHOI/{video_path}"

        service = BulkVideoQueueService(
            _DuplicateThenSuccessDispatcher(),
            poll_seconds=0.001,
            archive_handler=_fake_archive,
            refusal_handler=park,
        )
        batch = service.add(["owner@hotmail.com"], ["primary.mp4", "backup.mp4"])
        await service._tasks[batch["id"]]

        result = service.list()[0]
        item = result["assignments"][0]
        assert [entry[0] for entry in parked] == ["primary.mp4"]
        assert "TikTok" in parked[0][2]
        assert result["refused_parked"] == 1
        assert result["refused_park_failed"] == 0
        assert item["replacement_history"][0]["parked_path"] == "BI_TU_CHOI/primary.mp4"
        # The replacement still ran and still went to the posted archive.
        assert item["video_path"] == "backup.mp4"
        assert result["completed"] == 1

    asyncio.run(scenario())


def test_a_park_that_fails_is_recorded_without_failing_the_batch():
    async def scenario():
        def park(_video_path, _account_email, _reason):
            raise OSError("disk is read-only")

        service = BulkVideoQueueService(
            _DuplicateThenSuccessDispatcher(),
            poll_seconds=0.001,
            archive_handler=_fake_archive,
            refusal_handler=park,
        )
        batch = service.add(["owner@hotmail.com"], ["primary.mp4", "backup.mp4"])
        await service._tasks[batch["id"]]

        result = service.list()[0]
        item = result["assignments"][0]
        assert result["refused_park_failed"] == 1
        assert result["refused_parked"] == 0
        assert "read-only" in item["replacement_history"][0]["park_error"]
        assert result["completed"] == 1, "the replacement must still be posted"

    asyncio.run(scenario())
