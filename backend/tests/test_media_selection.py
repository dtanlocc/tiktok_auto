from pathlib import Path
from types import SimpleNamespace
import asyncio
import time

import pytest

from app.core.exceptions import StudioReauthenticationRequired
from app.use_cases.upload.media_selection import MAX_IMAGES, select_preferred_media
from app.use_cases.upload.tiktok_upload_video import (
    TikTokUploadMediaUseCase,
    _has_tiktok_auth_cookies,
    _matches_recent_public_post,
)


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
    assert browser.calls == ["login", "foryou", "publish_photo", "foryou"]


def test_upload_partial_post_cookie_snapshot_does_not_overwrite_saved_cookie_jar(tmp_path: Path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"image")
    saved_cookies = [{"name": "sessionid", "value": "keep-me"}]
    account = SimpleNamespace(
        id="account",
        cookies=saved_cookies,
        health_status="ALIVE",
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

    class CookieLogin:
        last_login_method = "COOKIE"

        async def login(self, *_args, **_kwargs):
            return True

    class Browser:
        def __init__(self):
            self.publish_done = False
            self.extract_calls = 0

        async def prepare_foryou_home(self, **_kwargs):
            return True

        async def extract_cookies(self):
            assert self.publish_done is True
            self.extract_calls += 1
            return [{"name": "tt_csrf_token", "value": "temporary"}]

        async def publish_media(self, **_kwargs):
            self.publish_done = True
            return True

    browser = Browser()
    use_case = TikTokUploadMediaUseCase(
        Repo(), browser, CookieLogin(), email_service=None
    )

    assert asyncio.run(use_case.execute("account", image_path=str(photo))) is True
    assert browser.extract_calls == 1
    assert account.cookies is saved_cookies


def test_upload_saves_rotated_auth_cookie_after_successful_publish(tmp_path: Path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"image")
    account = SimpleNamespace(
        id="account",
        cookies=[{"name": "sessionid", "value": "before-upload", "domain": ".tiktok.com"}],
        health_status="ALIVE",
        status="IDLE",
        current_step="",
        upload_success_count=0,
        upload_failure_count=0,
        last_upload_status="NEVER",
        last_upload_error="",
        last_upload_at="",
    )

    class Repo:
        def __init__(self):
            self.saved = []

        def get_by_id(self, _account_id):
            return account

        def save(self, saved_account):
            self.saved.append(list(saved_account.cookies))

    class CookieLogin:
        last_login_method = "COOKIE"

        async def login(self, *_args, **_kwargs):
            return True

    class Browser:
        def __init__(self):
            self.publish_done = False
            self.foryou_calls = 0

        async def prepare_foryou_home(self, **_kwargs):
            self.foryou_calls += 1
            return True

        async def extract_cookies(self):
            assert self.publish_done is True
            return [
                {"name": "sessionid", "value": "after-upload", "domain": ".tiktok.com"},
                {"name": "tt_csrf_token", "value": "fresh-csrf", "domain": ".tiktok.com"},
            ]

        async def publish_media(self, **_kwargs):
            self.publish_done = True
            return True

    repo = Repo()
    browser = Browser()
    use_case = TikTokUploadMediaUseCase(
        repo, browser, CookieLogin(), email_service=None
    )

    assert asyncio.run(use_case.execute("account", image_path=str(photo))) is True
    assert browser.foryou_calls == 2
    assert account.cookies[0]["value"] == "after-upload"
    assert any(
        jar and jar[0].get("value") == "after-upload" for jar in repo.saved
    )


def test_upload_does_not_checkpoint_cookies_for_wrong_identity(tmp_path: Path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"image")
    saved_cookies = [{"name": "sessionid", "value": "keep-me"}]
    account = SimpleNamespace(
        id="account",
        username="expected_user",
        cookies=saved_cookies,
        health_status="ALIVE",
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

    class CookieLogin:
        last_login_method = "COOKIE"

        async def login(self, *_args, **_kwargs):
            return True

    class Browser:
        async def prepare_foryou_home(self, **_kwargs):
            return True

        async def validate_authenticated_identity(self, expected_username):
            assert expected_username == "expected_user"
            return False

        async def extract_cookies(self):
            raise AssertionError("wrong identity cookie jar must not be read")

        async def publish_media(self, **_kwargs):
            return True

    use_case = TikTokUploadMediaUseCase(
        Repo(), Browser(), CookieLogin(), email_service=None
    )

    assert asyncio.run(use_case.execute("account", image_path=str(photo))) is True
    assert account.cookies is saved_cookies


def test_partial_cookie_snapshot_never_replaces_existing_auth_cookies():
    saved_cookies = [{"name": "sessionid", "value": "keep-me"}]
    account = SimpleNamespace(
        id="account",
        cookies=saved_cookies,
        health_status="ALIVE",
    )

    class Repo:
        def get_by_id(self, _account_id):
            return account

        def save(self, _account):
            raise AssertionError("partial cookie snapshot must not be saved")

    class Browser:
        async def extract_cookies(self):
            return [{"name": "tt_csrf_token", "value": "temporary"}]

    use_case = TikTokUploadMediaUseCase(
        Repo(), Browser(), object(), email_service=None
    )

    result = asyncio.run(
        use_case._persist_authenticated_cookie_snapshot("account", account)
    )

    assert result is account
    assert account.cookies is saved_cookies
    assert _has_tiktok_auth_cookies(saved_cookies) is True


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
    # One readiness check before Studio and one authenticated-cookie checkpoint
    # after the final successful publish.
    assert browser.foryou_calls == 2
    assert [call["video_path"] for call in browser.published] == [
        str(Path(path).resolve()) for path in videos
    ]
    assert [call["continue_session"] for call in browser.published] == [False, True, True]
    assert [item["success"] for item in sink] == [True, True, True]
    assert account.upload_success_count == 3
    assert account.upload_failure_count == 0
    assert account.status == "SUCCESS"
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
    assert account.status == "ERROR"
    assert account.last_upload_status == "FAILED"
    assert any("Caption editor detached" in message for message in logs)


def test_video_batch_propagates_duplicate_code_without_public_fallback(
    tmp_path: Path, monkeypatch
):
    video = tmp_path / "duplicate.mp4"
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
        last_publish_failure_code = "VIDEO_DUPLICATE"
        last_publish_failure_detail = "You've already posted this video"
        last_publish_acknowledged = False

        async def prepare_foryou_home(self, **_kwargs):
            return True

        async def extract_cookies(self):
            return []

        async def publish_media(self, **_kwargs):
            return False

    use_case = TikTokUploadMediaUseCase(
        Repo(), Browser(), Login(), email_service=None
    )

    async def public_fallback_must_not_run(*_args, **_kwargs):
        raise AssertionError("duplicate rejection must not use public fallback")

    monkeypatch.setattr(
        use_case, "_verify_recent_public_post", public_fallback_must_not_run
    )
    sink = []

    result = asyncio.run(use_case.execute_video_batch(
        "account", video_paths=[str(video)], result_sink=sink
    ))

    assert result is False
    assert sink[0]["code"] == "VIDEO_DUPLICATE"
    assert "VIDEO_TRUNG" in sink[0]["error"]
    assert "VIDEO_TRUNG" in account.current_step


def test_public_post_match_requires_caption_and_new_creation_time():
    videos = [
        {
            "title": "Karen argues with police and refuses to exit vehicle #karen",
            "create_time": 1_788_506_730,
        },
        {
            "title": "An unrelated new upload",
            "create_time": 1_788_506_800,
        },
    ]

    assert _matches_recent_public_post(
        videos,
        "Karen argues with police and refuses to exit vehicle",
        1_788_506_600,
    ) is True
    assert _matches_recent_public_post(
        videos,
        "Karen argues with police and refuses to exit vehicle",
        1_788_506_790,
    ) is False


def test_video_batch_recovers_studio_false_negative_from_public_profile(tmp_path: Path):
    video = tmp_path / "Karen argues with police.mp4"
    video.write_bytes(b"video")
    account = SimpleNamespace(
        id="account",
        username="fru86sta2_vi9pl",
        tiktok_sec_uid="sec-uid",
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
        last_publish_acknowledged = True

        async def prepare_foryou_home(self, **_kwargs):
            return True

        async def extract_cookies(self):
            return []

        async def publish_media(self, **_kwargs):
            return False

    class PublicClient:
        async def fetch_videos(self, **_kwargs):
            return ([{
                "title": "Karen argues with police #karen",
                "create_time": int(time.time()) + 1,
            }], True)

        async def close(self):
            return None

    use_case = TikTokUploadMediaUseCase(
        Repo(),
        Browser(),
        Login(),
        email_service=None,
        public_video_client_factory=PublicClient,
    )

    result = asyncio.run(use_case.execute_video_batch(
        "account",
        video_paths=[str(video)],
        captions=["Karen argues with police"],
    ))

    assert result is True
    assert account.upload_success_count == 1
    assert account.upload_failure_count == 0


def test_video_batch_forces_otp_once_when_studio_rejects_cookie(tmp_path: Path):
    video = tmp_path / "studio-reauth.mp4"
    video.write_bytes(b"video")
    account = SimpleNamespace(
        id="account",
        username="account_user",
        cookies=[{"name": "sessionid", "value": "old"}],
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

    class InitialLogin:
        async def login(self, *_args, **_kwargs):
            return True

    class ForcedOtpLogin:
        def __init__(self):
            self.calls = 0

        async def login(self, *_args, **_kwargs):
            self.calls += 1
            return True

    class Browser:
        def __init__(self):
            self.publish_calls = []
            self.foryou_calls = 0
            self.clears = 0

        async def prepare_foryou_home(self, **_kwargs):
            self.foryou_calls += 1
            return True

        async def extract_cookies(self):
            return [{"name": "sessionid", "value": "fresh"}]

        async def clear_auth_session(self):
            self.clears += 1

        async def publish_media(self, **kwargs):
            self.publish_calls.append(kwargs)
            if len(self.publish_calls) == 1:
                raise StudioReauthenticationRequired("Studio login redirect")
            return True

    browser = Browser()
    forced_login = ForcedOtpLogin()
    use_case = TikTokUploadMediaUseCase(
        Repo(),
        browser,
        InitialLogin(),
        email_service=None,
        credential_login_strategy_factory=lambda: forced_login,
    )

    result = asyncio.run(use_case.execute_video_batch(
        "account",
        video_paths=[str(video)],
    ))

    assert result is True
    assert browser.clears == 1
    assert forced_login.calls == 1
    assert browser.foryou_calls == 3
    assert len(browser.publish_calls) == 2
    assert [call["continue_session"] for call in browser.publish_calls] == [False, False]
    assert account.cookies == [{"name": "sessionid", "value": "fresh"}]


def test_photo_execute_forces_otp_once_when_studio_rejects_cookie(tmp_path: Path):
    photo = tmp_path / "studio-reauth.jpg"
    photo.write_bytes(b"image")
    account = SimpleNamespace(
        id="account",
        username="account_user",
        cookies=[{"name": "sessionid", "value": "old"}],
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

    class InitialLogin:
        async def login(self, *_args, **_kwargs):
            return True

    class ForcedOtpLogin:
        def __init__(self):
            self.calls = 0

        async def login(self, *_args, **_kwargs):
            self.calls += 1
            return True

    class Browser:
        def __init__(self):
            self.publish_calls = []
            self.foryou_calls = 0
            self.clears = 0

        async def prepare_foryou_home(self, **_kwargs):
            self.foryou_calls += 1
            return True

        async def extract_cookies(self):
            return [{"name": "sessionid", "value": "fresh"}]

        async def clear_auth_session(self):
            self.clears += 1

        async def publish_media(self, **kwargs):
            self.publish_calls.append(kwargs)
            if len(self.publish_calls) == 1:
                raise StudioReauthenticationRequired("Studio login redirect")
            return True

    browser = Browser()
    forced_login = ForcedOtpLogin()
    use_case = TikTokUploadMediaUseCase(
        Repo(),
        browser,
        InitialLogin(),
        email_service=None,
        credential_login_strategy_factory=lambda: forced_login,
    )

    result = asyncio.run(use_case.execute(
        "account",
        image_path=str(photo),
        caption="Photo retry",
    ))

    assert result is True
    assert browser.clears == 1
    assert forced_login.calls == 1
    assert browser.foryou_calls == 3
    assert len(browser.publish_calls) == 2
    assert all(call["image_paths"] for call in browser.publish_calls)
    assert account.cookies == [{"name": "sessionid", "value": "fresh"}]


def _partial_batch_account(note=""):
    return SimpleNamespace(
        id="account",
        username="nick",
        cookies=[],
        health_status="UNKNOWN",
        status="IDLE",
        current_step="",
        note=note,
        upload_success_count=0,
        upload_failure_count=0,
        last_upload_status="NEVER",
        last_upload_error="",
        last_upload_at="",
    )


def _run_two_video_batch(account, tmp_path, fail_index):
    """Publish two videos where exactly one of them fails."""
    first = tmp_path / "01. Cabai dicampur andaliman pedas.mp4"
    second = tmp_path / "02. Mak Nong ngatur dompet kosong.mp4"
    for path in (first, second):
        path.write_bytes(b"video")

    class Repo:
        def get_by_id(self, _account_id):
            return account

        def save(self, _account):
            return None

    class Login:
        async def login(self, *_args, **_kwargs):
            return True

    class Browser:
        last_publish_failure_code = ""
        last_publish_failure_detail = ""
        last_publish_acknowledged = False

        def __init__(self):
            self.calls = 0

        async def prepare_foryou_home(self, **_kwargs):
            return True

        async def extract_cookies(self):
            return [{"name": "sessionid", "value": "v", "domain": ".tiktok.com"}]

        async def publish_media(self, **_kwargs):
            self.calls += 1
            return self.calls != fail_index

    use_case = TikTokUploadMediaUseCase(
        Repo(), Browser(), Login(), email_service=None
    )
    sink = []
    result = asyncio.run(use_case.execute_video_batch(
        "account", video_paths=[str(first), str(second)], result_sink=sink
    ))
    return result, sink


def test_one_published_video_keeps_the_account_successful(tmp_path: Path):
    """A batch that published something is not the same as one that published
    nothing, and the operator retries the two cases differently."""
    account = _partial_batch_account()

    result, sink = _run_two_video_batch(account, tmp_path, fail_index=2)

    assert result is True
    assert account.status == "SUCCESS"
    assert account.last_upload_status == "SUCCESS"
    # The failure is still reported, it just no longer decides the state.
    assert "2/2" in account.current_step
    assert account.last_upload_error
    assert sink[0]["success"] is True
    assert sink[1]["success"] is False


def test_partial_batch_note_names_the_failed_slot(tmp_path: Path):
    account = _partial_batch_account()

    _run_two_video_batch(account, tmp_path, fail_index=1)

    # The slot leads: a filename alone cannot say whether anything published.
    assert "1/2" in account.note
    assert "Cabai dicampur" in account.note
    assert "toàn bộ" not in account.note


def test_total_failure_note_is_distinguishable_from_a_partial_one(tmp_path: Path):
    video = tmp_path / "only.mp4"
    video.write_bytes(b"video")
    account = _partial_batch_account()

    class Repo:
        def get_by_id(self, _account_id):
            return account

        def save(self, _account):
            return None

    class Login:
        async def login(self, *_args, **_kwargs):
            return True

    class Browser:
        last_publish_failure_code = ""
        last_publish_failure_detail = ""
        last_publish_acknowledged = False

        async def prepare_foryou_home(self, **_kwargs):
            return True

        async def extract_cookies(self):
            return []

        async def publish_media(self, **_kwargs):
            return False

    use_case = TikTokUploadMediaUseCase(
        Repo(), Browser(), Login(), email_service=None
    )
    result = asyncio.run(use_case.execute_video_batch(
        "account", video_paths=[str(video)]
    ))

    assert result is False
    assert account.status == "ERROR"
    assert account.last_upload_status == "FAILED"
    assert "toàn bộ" in account.note


def test_the_uploader_note_never_eats_a_hand_written_one(tmp_path: Path):
    """Five accounts carry notes a person typed; the uploader appends."""
    account = _partial_batch_account(note="video đăng tay")

    _run_two_video_batch(account, tmp_path, fail_index=2)
    assert account.note.startswith("video đăng tay")
    assert "2/2" in account.note

    # A second run replaces only the uploader's half.
    account.status = "IDLE"
    _run_two_video_batch(account, tmp_path, fail_index=1)
    assert account.note.startswith("video đăng tay")
    assert account.note.count("[auto]") == 1
    assert "1/2" in account.note
    assert "2/2" not in account.note


def test_a_clean_rerun_clears_the_stale_failure_note(tmp_path: Path):
    account = _partial_batch_account(note="nick VIP")
    _run_two_video_batch(account, tmp_path, fail_index=2)
    assert "[auto]" in account.note

    account.status = "IDLE"
    _run_two_video_batch(account, tmp_path, fail_index=0)   # nothing fails
    assert account.note == "nick VIP"


def _studio_bounce_run(tmp_path: Path, server: dict, bounces: int):
    """Studio sends the first `bounces` publishes to /login; TikTok's server says `server`."""
    video = tmp_path / "studio-bounce.mp4"
    video.write_bytes(b"video")
    account = SimpleNamespace(
        id="account", username="norvi4671",
        cookies=[{"name": "sessionid", "value": "stored"}],
        health_status="ALIVE", status="IDLE", current_step="", note="",
        upload_success_count=0, upload_failure_count=0,
        last_upload_status="NEVER", last_upload_error="", last_upload_at="",
    )

    class Repo:
        def get_by_id(self, _account_id):
            return account

        def save(self, _account):
            return None

    class InitialLogin:
        async def login(self, *_args, **_kwargs):
            return True

    class ForcedOtpLogin:
        calls = 0

        async def login(self, *_args, **_kwargs):
            ForcedOtpLogin.calls += 1
            return True

    class Browser:
        def __init__(self):
            self.publish_calls = 0
            self.clears = 0

        async def prepare_foryou_home(self, **_kwargs):
            return True

        async def extract_cookies(self):
            return [{"name": "sessionid", "value": "stored"}]

        async def read_session_account(self):
            return dict(server)

        async def clear_auth_session(self):
            self.clears += 1

        async def publish_media(self, **_kwargs):
            self.publish_calls += 1
            if self.publish_calls <= bounces:
                raise StudioReauthenticationRequired("Studio login redirect")
            return True

    browser = Browser()
    use_case = TikTokUploadMediaUseCase(
        Repo(), browser, InitialLogin(), email_service=None,
        credential_login_strategy_factory=ForcedOtpLogin,
    )
    result = asyncio.run(use_case.execute_video_batch("account", video_paths=[str(video)]))
    return result, browser, ForcedOtpLogin.calls


def test_a_studio_login_bounce_with_a_live_session_keeps_the_cookies(tmp_path: Path):
    result, browser, otp_logins = _studio_bounce_run(
        tmp_path, {"state": "alive", "username": "norvi4671"}, bounces=1)

    assert result is True
    assert browser.clears == 0 and otp_logins == 0
    assert browser.publish_calls == 2


def test_a_studio_bounce_on_a_session_tiktok_ended_logs_in_by_otp_once(tmp_path: Path):
    result, browser, otp_logins = _studio_bounce_run(
        tmp_path, {"state": "signed_out", "detail": "session expired"}, bounces=1)

    assert result is True
    assert browser.clears == 1 and otp_logins == 1


def test_a_bounce_that_survives_the_retry_still_gets_the_one_otp_login(tmp_path: Path):
    result, browser, otp_logins = _studio_bounce_run(
        tmp_path, {"state": "alive", "username": "norvi4671"}, bounces=2)

    assert result is True
    assert browser.clears == 1 and otp_logins == 1
    assert browser.publish_calls == 3
