"""Resolve publishing media with valid photos taking priority over video."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}
MAX_IMAGES = 35


@dataclass(frozen=True)
class SelectedMedia:
    kind: str
    image_paths: tuple[str, ...] = ()
    video_path: Optional[str] = None


def _resolve_images(raw_path: Optional[str]) -> tuple[str, ...]:
    if not raw_path or not raw_path.strip():
        return ()
    path = Path(raw_path.strip()).expanduser()
    if not path.exists():
        return ()
    if path.is_file():
        return (str(path.resolve()),) if path.suffix.lower() in IMAGE_EXTENSIONS else ()
    images = sorted(
        (item.resolve() for item in path.iterdir() if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS),
        key=lambda item: item.name.lower(),
    )
    return tuple(str(item) for item in images[:MAX_IMAGES])


def select_preferred_media(image_path: Optional[str], video_path: Optional[str]) -> SelectedMedia:
    """A valid image file/folder wins; video is used only as fallback."""
    images = _resolve_images(image_path)
    if images:
        return SelectedMedia(kind="photo", image_paths=images)

    if video_path and video_path.strip():
        video = Path(video_path.strip()).expanduser()
        if video.is_file() and video.suffix.lower() in VIDEO_EXTENSIONS:
            return SelectedMedia(kind="video", video_path=str(video.resolve()))

    image_note = f" Khong tim thay anh hop le tai: {image_path}." if image_path else ""
    video_note = f" Khong tim thay video hop le tai: {video_path}." if video_path else ""
    raise ValueError(
        "Can it nhat mot anh (.jpg/.jpeg/.png/.webp), hoac mot video du phong "
        "(.mp4/.mov/.webm/.m4v)." + image_note + video_note
    )
