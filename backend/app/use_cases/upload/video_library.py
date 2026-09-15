"""Discover supported local videos for the batch publishing UI."""

import os
import re
import shutil
from pathlib import Path
from typing import Iterable

from app.use_cases.upload.media_selection import VIDEO_EXTENSIONS


MAX_LIBRARY_VIDEOS = 2000
POSTED_ARCHIVE_DIRNAME = "DA_DANG"


def _is_posted_archive_path(path: Path) -> bool:
    archive_key = POSTED_ARCHIVE_DIRNAME.casefold()
    return any(part.casefold() == archive_key for part in path.parts)


def _iter_library_files(path: Path):
    """Yield files without descending into the permanent posted archive."""
    if path.is_file():
        if not _is_posted_archive_path(path):
            yield path
        return
    if not path.is_dir() or _is_posted_archive_path(path):
        return
    for root, directory_names, file_names in os.walk(path):
        directory_names[:] = [
            name
            for name in directory_names
            if name.casefold() != POSTED_ARCHIVE_DIRNAME.casefold()
        ]
        root_path = Path(root)
        for file_name in file_names:
            yield root_path / file_name


def _account_archive_segment(account_id: str) -> str:
    value = re.sub(r"[^A-Za-z0-9@._+-]+", "_", str(account_id or "").strip())
    value = value.strip(" ._")[:120]
    return value or "unknown-account"


def archive_posted_video(video_path: str, account_id: str) -> str:
    """Move a confirmed post into ``DA_DANG/<account>/`` without overwriting."""
    source = Path(str(video_path or "")).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Khong tim thay video de chuyen sau khi dang: {source}")

    destination_dir = (
        source.parent / POSTED_ARCHIVE_DIRNAME / _account_archive_segment(account_id)
    )
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / source.name
    suffix_index = 2
    while destination.exists():
        destination = destination_dir / (
            f"{source.stem}__{suffix_index}{source.suffix}"
        )
        suffix_index += 1
    shutil.move(str(source), str(destination))
    return str(destination.resolve())


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
