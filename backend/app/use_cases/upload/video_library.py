"""Discover supported local videos for the batch publishing UI."""

import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Iterable

from app.use_cases.upload.media_selection import VIDEO_EXTENSIONS


MAX_LIBRARY_VIDEOS = 2000
POSTED_ARCHIVE_DIRNAME = "DA_DANG"
#: Where a video TikTok refused goes, and the note that says why.
REFUSED_ARCHIVE_DIRNAME = "BI_TU_CHOI"
REFUSED_NOTE_FILENAME = "_nhat_ky.txt"

_ARCHIVE_DIRNAMES = (POSTED_ARCHIVE_DIRNAME, REFUSED_ARCHIVE_DIRNAME)


def _is_archive_path(path: Path) -> bool:
    """True for anything inside a folder the library must not offer again."""
    archive_keys = {name.casefold() for name in _ARCHIVE_DIRNAMES}
    return any(part.casefold() in archive_keys for part in path.parts)


def _iter_library_files(path: Path):
    """Yield files without descending into the permanent posted archive."""
    if path.is_file():
        if not _is_archive_path(path):
            yield path
        return
    if not path.is_dir() or _is_archive_path(path):
        return
    archive_keys = {name.casefold() for name in _ARCHIVE_DIRNAMES}
    for root, directory_names, file_names in os.walk(path):
        directory_names[:] = [
            name for name in directory_names if name.casefold() not in archive_keys
        ]
        root_path = Path(root)
        for file_name in file_names:
            yield root_path / file_name


def _account_archive_segment(account_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9@._+-]+", "_", str(account_id or "").strip())
    value = value.strip(" ._")[:120]
    return value or "unknown-account"


def _move_without_overwriting(source: Path, destination_dir: Path) -> Path:
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    suffix_index = 2
    while destination.exists():
        destination = destination_dir / (
            f"{source.stem}__{suffix_index}{source.suffix}"
        )
        suffix_index += 1
    shutil.move(str(source), str(destination))
    return destination


def archive_posted_video(video_path: str, account_id: str) -> str:
    """Move a confirmed post into ``DA_DANG/<account>/`` without overwriting."""
    source = Path(str(video_path or "")).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Khong tim thay video de chuyen sau khi dang: {source}")

    destination_dir = (
        source.parent / POSTED_ARCHIVE_DIRNAME / _account_archive_segment(account_id)
    )
    return str(_move_without_overwriting(source, destination_dir).resolve())


def archive_refused_video(video_path: str, account_id: str, reason: str = "") -> str:
    """Take a video TikTok refused out of the library, keeping the reason.

    ⛔ A REFUSAL IS ABOUT THE VIDEO, NOT THE ACCOUNT. Measured 22-23/09/2026:
    " 2008 pagi hai hai" came back VIDEO_TRUNG on @catali7_daily95, stayed in
    the folder, and came back the same way on @abbiewilsterman855875 a day
    later. Every repeat costs an account slot and a browser session for a
    verdict TikTok has already given, and hands that account a refusal it did
    not need to collect.

    The file is MOVED, never deleted: a verdict we read wrong can be walked
    back by hand from ``BI_TU_CHOI/``.
    """
    source = Path(str(video_path or "")).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(
            f"Khong tim thay video bi TikTok tu choi de chuyen: {source}"
        )

    destination_dir = source.parent / REFUSED_ARCHIVE_DIRNAME
    destination = _move_without_overwriting(source, destination_dir)
    _write_refusal_note(destination_dir, destination, account_id, reason)
    return str(destination.resolve())


def _write_refusal_note(
    directory: Path, destination: Path, account_id: str, reason: str
) -> None:
    """One line per refused video, so the folder explains itself later."""
    line = " | ".join([
        datetime.now().isoformat(timespec="seconds"),
        str(account_id or "?"),
        destination.name,
        " ".join(str(reason or "").split())[:300] or "khong co chi tiet",
    ])
    try:
        with (directory / REFUSED_NOTE_FILENAME).open("a", encoding="utf-8") as note:
            note.write(line + "\n")
    except OSError:
        # The note is a convenience. Moving the file out of the library is
        # the part that must not fail silently, and that already happened.
        pass


def scan_video_paths(raw_paths: Iterable[str], limit: int = MAX_LIBRARY_VIDEOS) -> list[dict]:
    """Expand files/directories, de-duplicate them, and return stable metadata."""
    discovered: dict[str, Path] = {}
    for raw_path in raw_paths:
        value = str(raw_path or "").strip().strip('"')
        if not value:
            continue
        path = Path(value).expanduser()
        for candidate in _iter_library_files(path):
            if not candidate.is_file() or candidate.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            resolved = candidate.resolve()
            discovered.setdefault(str(resolved).casefold(), resolved)
            if len(discovered) >= limit:
                break
        if len(discovered) >= limit:
            break

    videos = sorted(discovered.values(), key=lambda item: (item.name.casefold(), str(item).casefold()))
    return [
        {"id": str(path), "name": path.name, "path": str(path), "size_bytes": path.stat().st_size}
        for path in videos
    ]
