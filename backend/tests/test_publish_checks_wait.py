"""The pause between the caption and Post.

Studio enables Post while the file is still uploading and while the music
check is still running. These tests pin the wait that keeps the tool off that
path: it holds until the upload line reads "Uploaded（…）" and the two check
rows stop saying they are busy.
"""
import asyncio

from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter


class _ScriptedPage:
    """Hands out one measured sample of the publish screen per evaluate()."""

    def __init__(self, samples):
        self.samples = list(samples)
        self.evaluations = 0

    async def evaluate(self, _script, *args):
        self.evaluations += 1
        index = min(self.evaluations - 1, len(self.samples) - 1)
        return self.samples[index]


def _adapter(monkeypatch, samples):
    adapter = InvisiblePlaywrightAdapter()
    page = _ScriptedPage(samples)
    adapter._page = page

    async def no_gate():
        return None

    async def no_popup(step_logger=None):
        return False

    async def no_pause(_seconds):
        return None

    monkeypatch.setattr(adapter, "_wait_automation_gate", no_gate)
    monkeypatch.setattr(adapter, "_handle_upload_interruptions", no_popup)
    monkeypatch.setattr(asyncio, "sleep", no_pause)
    return adapter, page


# The real wording, copied from a measured run on 23/09/2026.
_UPLOADING = {
    "status": ["70.95MB/79.55MB, 3 seconds left"],
    "checks": [
        "Checks can only start after the file is uploaded.",
        "Music copyright check We'll check if your video has any unauthorized music.",
    ],
}
_CHECKING = {
    "status": ["Uploaded（79.55MB）"],
    "checks": ["Music copyright check Checking in progress. This will take a while."],
}
_CLEAR = {
    "status": ["Uploaded（79.55MB）"],
    "checks": ["Music copyright check No issues found."],
}


def test_wait_holds_while_the_file_is_still_going_up(monkeypatch):
    adapter, page = _adapter(monkeypatch, [_UPLOADING, _UPLOADING, _CLEAR, _CLEAR])
    result = asyncio.run(adapter._wait_publish_checks(timeout_seconds=30.0))
    assert result["settled"] is True
    assert "No issues found." in result["checks"][0]
    # Two busy samples, then two stable ones: it never settled early.
    assert page.evaluations == 4


def test_wait_holds_while_the_music_check_runs(monkeypatch):
    adapter, page = _adapter(monkeypatch, [_CHECKING, _CHECKING, _CHECKING, _CLEAR, _CLEAR])
    result = asyncio.run(adapter._wait_publish_checks(timeout_seconds=30.0))
    assert result["settled"] is True
    assert page.evaluations == 5


def test_a_settled_screen_needs_two_agreeing_samples(monkeypatch):
    adapter, page = _adapter(monkeypatch, [_CLEAR])
    result = asyncio.run(adapter._wait_publish_checks(timeout_seconds=30.0))
    assert result["settled"] is True
    assert page.evaluations == 2


def test_a_check_that_never_settles_reports_instead_of_raising(monkeypatch):
    adapter, _page = _adapter(monkeypatch, [_CHECKING])
    said = []

    async def log(message):
        said.append(message)

    result = asyncio.run(
        adapter._wait_publish_checks(timeout_seconds=0.05, step_logger=log)
    )
    assert result["settled"] is False
    assert any("kiểm tra" in message for message in said)


def test_a_screen_without_check_rows_does_not_stall(monkeypatch):
    """Photo posts and older layouts show no check rows at all."""
    adapter, page = _adapter(monkeypatch, [{"status": [], "checks": []}])
    result = asyncio.run(adapter._wait_publish_checks(timeout_seconds=30.0))
    assert result["settled"] is True
    assert page.evaluations == 2


def test_a_read_failure_is_survivable(monkeypatch):
    class _BrokenThenFine(_ScriptedPage):
        async def evaluate(self, script, *args):
            self.evaluations += 1
            if self.evaluations == 1:
                raise RuntimeError("Execution context was destroyed")
            return _CLEAR

    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _BrokenThenFine([_CLEAR])

    async def no_gate():
        return None

    async def no_popup(step_logger=None):
        return False

    async def no_pause(_seconds):
        return None

    monkeypatch.setattr(adapter, "_wait_automation_gate", no_gate)
    monkeypatch.setattr(adapter, "_handle_upload_interruptions", no_popup)
    monkeypatch.setattr(asyncio, "sleep", no_pause)
    result = asyncio.run(adapter._wait_publish_checks(timeout_seconds=30.0))
    assert result["settled"] is True
