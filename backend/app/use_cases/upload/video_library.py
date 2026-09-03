"""Discover supported local videos for the batch publishing UI."""

from pathlib import Path
from typing import Iterable

from app.use_cases.upload.media_selection import VIDEO_EXTENSIONS


MAX_LIBRARY_VIDEOS = 2000


def scan_video_paths(raw_paths: Iterable[str], limit: int = MAX_LIBRARY_VIDEOS) -> list[dict]:
    """Expand files/directories, de-duplicate them, and return stable metadata."""
    discovered: dict[str, Path] = {}
    for raw_path in raw_paths:
        value = str(raw_path or "").strip().strip('"')
        if not value:
            continue
        path = Path(value).expanduser()
        candidates = [path] if path.is_file() else path.rglob("*") if path.is_dir() else []
        for candidate in candidates:
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
