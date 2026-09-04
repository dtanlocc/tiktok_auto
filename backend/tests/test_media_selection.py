from pathlib import Path
from types import SimpleNamespace
import asyncio

import pytest

from app.use_cases.upload.media_selection import MAX_IMAGES, select_preferred_media
from app.use_cases.upload.tiktok_upload_video import TikTokUploadMediaUseCase


def test_photos_are_preferred_over_video(tmp_path: Path):
    image_dir = tmp_path / "photos"
    image_dir.mkdir()
    (image_dir / "02.png").write_bytes(b"image")
    (image_dir / "01.jpg").write_bytes(b"image")
    video = tmp_path / "fallback.mp4"
    video.write_bytes(b"video")

    selected = select_preferred_media(str(image_dir), str(video))

    assert selected.kind == "photo"
    assert [Path(path).name for path in selected.image_paths] == ["01.jpg", "02.png"]
    assert selected.video_path is None


def test_video_is_used_when_image_path_has_no_valid_images(tmp_path: Path):
    image_dir = tmp_path / "empty"
    image_dir.mkdir()
    (image_dir / "note.txt").write_text("not an image", encoding="utf-8")
    video = tmp_path / "fallback.webm"
    video.write_bytes(b"video")

    selected = select_preferred_media(str(image_dir), str(video))

    assert selected.kind == "video"
    assert selected.video_path == str(video.resolve())


def test_image_folder_is_sorted_and_limited(tmp_path: Path):
    for index in range(MAX_IMAGES + 4):
        (tmp_path / f"{index:02}.webp").write_bytes(b"image")

    selected = select_preferred_media(str(tmp_path), None)

    assert len(selected.image_paths) == MAX_IMAGES
    assert Path(selected.image_paths[0]).name == "00.webp"
    assert Path(selected.image_paths[-1]).name == "34.webp"


def test_invalid_media_raises_clear_error(tmp_path: Path):
    with pytest.raises(ValueError, match="Can it nhat mot anh"):
        select_preferred_media(str(tmp_path / "missing"), str(tmp_path / "missing.mp4"))


def test_use_case_waits_for_foryou_before_publishing(tmp_path: Path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"image")
    account = SimpleNamespace(id="account", cookies=[], health_status="UNKNOWN", status="IDLE", current_step="")

    class Repo:
        def get_by_id(self, _account_id):
            return account

        def save(self, _account):
            return None

    class Login:
        async def login(self, browser, _account, **_kwargs):
            browser.calls.append("login")
            return True

    class Browser:
        def __init__(self):
            self.calls = []

        async def prepare_foryou_home(self, **_kwargs):
            self.calls.append("foryou")
            return True

        async def extract_cookies(self):
            return []

        async def publish_media(self, **kwargs):
            self.calls.append("publish_photo" if kwargs["image_paths"] else "publish_video")
            self.caption = kwargs["caption"]
            return True

    browser = Browser()
    use_case = TikTokUploadMediaUseCase(Repo(), browser, Login(), email_service=None)

    result = asyncio.run(use_case.execute("account", image_path=str(photo)))

    assert result is True
    assert browser.calls == ["login", "foryou", "publish_photo"]


def test_blank_caption_uses_video_filename_even_when_photo_wins(tmp_path: Path):
    photo = tmp_path / "cover.jpg"
    photo.write_bytes(b"image")
    video = tmp_path / "Police suspect a meticulously planned murder love triangle.mp4"
    video.write_bytes(b"video")
    account = SimpleNamespace(id="account", cookies=[], health_status="UNKNOWN", status="IDLE", current_step="")

    class Repo:
        def get_by_id(self, _account_id):
            return account

        def save(self, _account):
            return None

    class Login:
        async def login(self, *_args, **_kwargs):
            return True

    class Browser:
        async def prepare_foryou_home(self, **_kwargs):
            return True

        async def extract_cookies(self):
            return []

        async def publish_media(self, **kwargs):
            self.published = kwargs
            return True

    browser = Browser()
    use_case = TikTokUploadMediaUseCase(Repo(), browser, Login(), email_service=None)

    result = asyncio.run(use_case.execute(
        "account", image_path=str(photo), video_path=str(video), caption=""
    ))

    assert result is True
    assert browser.published["image_paths"] == [str(photo.resolve())]
    assert browser.published["video_path"] is None
    assert browser.published["caption"] == "Police suspect a meticulously planned murder love triangle"


def test_video_batch_logs_in_once_and_reuses_same_browser_for_all_videos(tmp_path: Path):
    videos = []
    for name in ("first.mp4", "second.mp4", "third.mp4"):
        path = tmp_path / name
        path.write_bytes(b"video")
        videos.append(str(path))
    account = SimpleNamespace(
        id="account",
        cookies=[],
        health_status="UNKNOWN",
        status="IDLE",
        current_step="",
        upload_success_count=0,
        upload_failure_count=0,
        last_upload_status="NEVER",
        last_upload_error="",
        last_upload_at="",
    )

    class Repo:
        def get_by_id(self, _account_id):
            return account

        def save(self, _account):
            return None

    class Login:
        def __init__(self):
            self.calls = 0

        async def login(self, *_args, **_kwargs):
            self.calls += 1
            return True

    class Browser:
        def __init__(self):
            self.foryou_calls = 0
            self.published = []

        async def prepare_foryou_home(self, **_kwargs):
            self.foryou_calls += 1
            return True

        async def extract_cookies(self):
            return []

        async def publish_media(self, **kwargs):
            self.published.append(kwargs)
            return True

    login = Login()
    browser = Browser()
    sink = []
    use_case = TikTokUploadMediaUseCase(Repo(), browser, login, email_service=None)

    result = asyncio.run(use_case.execute_video_batch(
        "account",
        video_paths=videos,
        captions=["one", "two", "three"],
        result_sink=sink,
    ))

    assert result is True
    assert login.calls == 1
    assert browser.foryou_calls == 1
    assert [call["video_path"] for call in browser.published] == [
        str(Path(path).resolve()) for path in videos
    ]
    assert [call["continue_session"] for call in browser.published] == [False, True, True]
    assert [item["success"] for item in sink] == [True, True, True]
    assert account.upload_success_count == 3
    assert account.upload_failure_count == 0
    assert account.last_upload_status == "SUCCESS"
    assert account.current_step == "✅ Đã đăng 3/3 video trong cùng phiên"


def test_video_batch_rejects_duplicate_video_before_login(tmp_path: Path):
    video = tmp_path / "same.mp4"
    video.write_bytes(b"video")

    class Repo:
        def get_by_id(self, _account_id):
            raise AssertionError("duplicate validation must happen before repository/login")

    class Login:
        async def login(self, *_args, **_kwargs):
            raise AssertionError("must not login for an invalid batch")

    use_case = TikTokUploadMediaUseCase(Repo(), object(), Login(), email_service=None)

    with pytest.raises(ValueError, match="không được chứa video trùng"):
        asyncio.run(use_case.execute_video_batch(
            "account",
            video_paths=[str(video), str(video)],
        ))


def test_video_batch_logs_the_per_video_failure_reason(tmp_path: Path):
    video = tmp_path / "broken.mp4"
    video.write_bytes(b"video")
    account = SimpleNamespace(
        id="account",
        cookies=[],
        health_status="UNKNOWN",
        status="IDLE",
        current_step="",
        upload_success_count=0,
        upload_failure_count=0,
        last_upload_status="NEVER",
        last_upload_error="",
        last_upload_at="",
    )

    class Repo:
        def get_by_id(self, _account_id):
            return account

        def save(self, _account):
            return None

    class Login:
        async def login(self, *_args, **_kwargs):
            return True

    class Browser:
        async def prepare_foryou_home(self, **_kwargs):
            return True

        async def extract_cookies(self):
            return []

        async def publish_media(self, **_kwargs):
            raise RuntimeError("Caption editor detached")

    logs = []

    async def capture_log(message):
        logs.append(message)

    use_case = TikTokUploadMediaUseCase(
        Repo(), Browser(), Login(), email_service=None, step_logger=capture_log
    )

    result = asyncio.run(use_case.execute_video_batch(
        "account",
        video_paths=[str(video)],
    ))

    assert result is False
    assert any("Caption editor detached" in message for message in logs)
