import asyncio
from pathlib import Path

import pytest

from invisible_playwright import native_upload
from invisible_playwright.native_upload import _normalise_files


def test_normalise_files_resolves_and_validates(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"media")

    assert _normalise_files([media]) == [str(media.resolve())]


def test_normalise_files_rejects_empty_and_missing(tmp_path):
    with pytest.raises(ValueError, match="At least one file"):
        _normalise_files([])
    with pytest.raises(FileNotFoundError):
        _normalise_files([Path(tmp_path) / "missing.mp4"])


def test_native_upload_allows_pending_click_after_windows_accepts(monkeypatch):
    class Locator:
        async def get_attribute(self, _name):
            return None

        async def evaluate(self, _expression, timeout):
            return 1

    class Trigger:
        async def click(self, **_kwargs):
            await asyncio.Event().wait()

    def find_dialog(_before, found, _stop, result, _timeout_seconds):
        result["hwnd"] = 123
        found.set()

    monkeypatch.setattr(native_upload, "_snapshot_dialogs", lambda: set())
    monkeypatch.setattr(native_upload, "_watch_new_dialog", find_dialog)
    monkeypatch.setattr(native_upload, "_fill_and_accept", lambda _hwnd, _files: None)
    monkeypatch.setattr(native_upload, "_cancel_dialog", lambda _hwnd: None)
    monkeypatch.setattr(native_upload, "_CLICK_COMPLETION_TIMEOUT_SECONDS", 0.01)

    asyncio.run(
        native_upload.set_input_files_native(
            Locator(),
            [__file__],
            trigger=Trigger(),
            allow_input_replacement=True,
            timeout_ms=1_000,
        )
    )
