import asyncio
import inspect
from pathlib import Path

from app.infrastructure.automation import playwright_adapter as adapter_module
from app.core.config import settings
from app.infrastructure.automation.playwright_adapter import (
    InvisiblePlaywrightAdapter,
    _classify_distribution_text,
)
from app.use_cases.upload.caption_hashtags import (
    choose_stable_hashtag_suggestion,
    hashtag_query_candidates,
)
from app.use_cases.orchestration.task_dispatcher import ConcurrentTaskDispatcher


def test_background_mode_uses_invisible_playwright_cloak_not_native_headless():
    assert settings.BROWSER_HEADLESS is True
    assert settings.BROWSER_TRUE_HEADLESS is False


def test_stream_is_not_suspended_during_caption_or_publish_confirmation():
    source = inspect.getsource(InvisiblePlaywrightAdapter.upload_video)
    native_source = inspect.getsource(
        InvisiblePlaywrightAdapter._attach_media_by_clicking_button
    )

    assert "self._stream_suspended" not in source
    assert "on_dialog_active=self._set_native_dialog_active" in native_source
    assert "await self._set_file_via_native_dialog(video_path)" in source
    assert "acknowledged = await self._click_publish_and_confirm" in source


def test_native_trigger_opens_only_the_resolved_input():
    class Handle:
        def __init__(self):
            self.scripts = []

        async def evaluate(self, script, *args):
            self.scripts.append((script, args))

    class Locator:
        def __init__(self, handle):
            self.handle = handle

        async def element_handle(self, **_kwargs):
            return self.handle

    adapter = InvisiblePlaywrightAdapter()
    input_handle = Handle()
    trigger_handle = Handle()

    asyncio.run(
        adapter._bridge_native_upload_trigger(
            Locator(input_handle), Locator(trigger_handle)
        )
    )

    bridge_script = input_handle.scripts[0][0]
    assert "event.preventDefault()" in bridge_script
    assert "event.stopImmediatePropagation()" in bridge_script


def test_the_select_video_button_is_pressed_before_the_input_is_touched(monkeypatch):
    """A person clicks Select video; set_input_files is only the last resort."""
    adapter = InvisiblePlaywrightAdapter()
    order = []

    async def click_button(_paths, _media_kind):
        order.append("button")
        return len(order) == 2          # the second press opens the chooser

    async def input_channel(_paths, _media_kind):
        order.append("input")
        return True

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(adapter, "_attach_media_by_clicking_button", click_button)
    monkeypatch.setattr(adapter, "_attach_media_via_input_channel", input_channel)
    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter_module.os.path, "isfile", lambda _path: True)

    assert asyncio.run(adapter._set_file_via_native_dialog("video.mp4")) is True
    assert order == ["button", "button"]


def test_the_input_is_used_only_after_two_failed_button_presses(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    order = []

    async def click_button(_paths, _media_kind):
        order.append("button")
        raise RuntimeError("windows file chooser did not appear")

    async def input_channel(_paths, _media_kind):
        order.append("input")
        return True

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(adapter, "_attach_media_by_clicking_button", click_button)
    monkeypatch.setattr(adapter, "_attach_media_via_input_channel", input_channel)
    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter_module.os.path, "isfile", lambda _path: True)

    assert asyncio.run(adapter._set_file_via_native_dialog("video.mp4")) is True
    assert order == ["button", "button", "input"]


def test_video_upload_reselects_once_after_confirmed_server_failure(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    class NoUploadButton:
        """Studio without a clickable Upload control: the URL is the fallback."""

        url = "https://www.tiktok.com/tiktokstudio/upload?lang=en"

        def locator(self, _selector):
            class Empty:
                async def count(self):
                    return 0

            return Empty()

    adapter._page = NoUploadButton()
    attachments = []
    navigations = []
    logs = []
    outcomes = [
        {
            "ready": False,
            "failure_text": "Upload failed",
            "timed_out": False,
            "state": {},
        },
        {
            "ready": True,
            "failure_text": None,
            "timed_out": False,
            "state": {},
        },
    ]

    async def no_op(*_args, **_kwargs):
        return None

    async def navigate(url):
        navigations.append(url)

    async def entry_ready():
        return True

    async def attach(path):
        attachments.append(path)
        return True

    async def wait_upload(**_kwargs):
        return outcomes.pop(0)

    async def prepare_caption(caption, _filename, **_kwargs):
        return caption

    async def publish(**_kwargs):
        return True

    async def finalize(*_args, **_kwargs):
        return True

    async def capture_log(message):
        logs.append(message)

    monkeypatch.setattr(adapter, "_wait_automation_gate", no_op)
    monkeypatch.setattr(adapter, "_handle_upload_interruptions", no_op)
    monkeypatch.setattr(adapter, "navigate_to", navigate)
    monkeypatch.setattr(adapter, "_video_upload_entry_ready", entry_ready)
    monkeypatch.setattr(adapter, "_set_file_via_native_dialog", attach)
    monkeypatch.setattr(adapter, "_wait_video_upload_completion", wait_upload)
    monkeypatch.setattr(adapter, "_prepare_video_caption", prepare_caption)
    monkeypatch.setattr(adapter, "_click_publish_and_confirm", publish)
    monkeypatch.setattr(adapter, "_finalize_immediate_video_publish", finalize)
    monkeypatch.setattr(adapter, "_consume_foryou_upload_ticket", lambda: None)
    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_op)

    result = asyncio.run(
        adapter.upload_video(
            "video.mp4",
            caption="caption",
            step_logger=capture_log,
        )
    )

    assert result is True
    assert attachments == ["video.mp4", "video.mp4"]
    assert len(navigations) == 2
    assert outcomes == []
    assert any("Đang thử lại video một lần" in message for message in logs)


def test_emergency_generation_discards_payload_dequeued_before_stop():
    async def exercise():
        dispatcher = object.__new__(ConcurrentTaskDispatcher)
        dispatcher.is_running = True
        dispatcher.queue = asyncio.Queue()
        dispatcher.semaphore = asyncio.Semaphore(0)
        dispatcher._pending_accounts = {"held@example.com"}
        dispatcher._emergency_stop_generation = 0
        dispatcher.active_tasks = {}
        dispatcher.global_task_counter = 0

        await dispatcher.queue.put(
            {
                "account_id": "held@example.com",
                "task_type": "UPLOAD_MEDIA_BATCH",
                "avatar_folder": None,
                "extra_config": {},
            }
        )
        loop_task = asyncio.create_task(dispatcher._process_queue_loop())
        await asyncio.sleep(0)
        assert dispatcher.queue.qsize() == 0

        dispatcher._emergency_stop_generation += 1
        dispatcher.semaphore.release()
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert dispatcher.active_tasks == {}
        assert "held@example.com" not in dispatcher._pending_accounts
        assert dispatcher.semaphore._value == 1
        dispatcher.is_running = False
        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)

    asyncio.run(exercise())


def test_worker_honors_pause_before_launching_browser():
    source = inspect.getsource(
        ConcurrentTaskDispatcher._execute_worker_with_semaphore
    )

    pause_index = source.index("await self._wait_if_paused(account_id)")
    browser_index = source.index("browser_service = InvisiblePlaywrightAdapter()")
    assert pause_index < browser_index


def test_photo_publish_uses_studio_posts_before_returning_failure():
    source = inspect.getsource(InvisiblePlaywrightAdapter._upload_photos)

    assert "acknowledged = await self._click_publish_and_confirm" in source
    assert "await self._finalize_immediate_media_publish" in source


def test_hashtag_queries_are_related_limited_and_respect_explicit_tags():
    assert hashtag_query_candidates(
        "Cảnh sát điều tra vụ án tam giác tình yêu",
        limit=4,
    ) == ["canhsat", "tinhyeu", "canh", "sat"]
    assert hashtag_query_candidates("My title #AlreadyChosen", limit=6) == []
    assert "fyp" not in hashtag_query_candidates("A completely new story", limit=6)
    assert "viral" not in hashtag_query_candidates("A completely new story", limit=6)


def test_hashtag_suggestion_choice_is_relevant_and_dom_order_independent():
    suggestions = [
        "#acpstopping 900K posts",
        "#unrelated 80M posts",
        "#acpstops 12.5K posts",
        "#acpstopsdaily 2M posts",
    ]
    choice = choose_stable_hashtag_suggestion("#acpstops", suggestions)
    reversed_choice = choose_stable_hashtag_suggestion(
        "#acpstops", list(reversed(suggestions))
    )

    assert choice is not None
    assert reversed_choice is not None
    assert choice.token == reversed_choice.token == "#acpstops"
    assert choice.usage_count == reversed_choice.usage_count == 12_500


def test_hashtag_suggestion_prefers_usage_within_same_relevance_tier():
    choice = choose_stable_hashtag_suggestion(
        "#plate",
        ["#platetest 18K posts", "#plateshot 1.4M posts", "#random 90M posts"],
    )

    assert choice is not None
    assert choice.token == "#plateshot"
    assert choice.usage_count == 1_400_000


def test_hashtag_suggestion_skips_already_selected_and_unrelated_tags():
    choice = choose_stable_hashtag_suggestion(
        "#acp",
        ["#viral 2B posts", "#acp 8M posts", "#acpdaily 600K posts"],
        excluded_tokens=["#acp"],
    )

    assert choice is not None
    assert choice.token == "#acpdaily"


def test_distribution_status_requires_an_explicit_tiktok_label():
    assert _classify_distribution_text("0 views") == "PUBLISHED"
    assert _classify_distribution_text("This post is under review") == "UNDER_REVIEW"
    assert (
        _classify_distribution_text("This post is not eligible for the For You feed")
        == "FYF_INELIGIBLE"
    )


class _EmptyState:
    @property
    def first(self):
        return self

    async def count(self):
        return 0

    async def is_visible(self):
        return False


class _WorkingHandle:
    def __init__(self):
        self.paths = None

    async def set_input_files(self, paths, **_kwargs):
        self.paths = paths


class _WorkingInput:
    def __init__(self, handle):
        self.handle = handle

    @property
    def first(self):
        return self

    def nth(self, _index):
        return self

    async def count(self):
        return 1

    async def get_attribute(self, _name, **_kwargs):
        return "video/mp4"

    async def element_handle(self, **_kwargs):
        return self.handle


class _WorkingUploadPage:
    def __init__(self, handle):
        self.input = _WorkingInput(handle)

    def locator(self, selector):
        if selector == 'input[type="file"]':
            return self.input
        return _EmptyState()

    def get_by_text(self, *_args, **_kwargs):
        return _EmptyState()


def test_background_attach_uses_playwright_channel_before_native_dialog(tmp_path, monkeypatch):
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"video")
    adapter = InvisiblePlaywrightAdapter()
    handle = _WorkingHandle()
    adapter._page = _WorkingUploadPage(handle)
    adapter._launch_headless = True

    async def forbidden_native_click(*_args, **_kwargs):
        raise AssertionError("native chooser must only be a fallback")

    monkeypatch.setattr(adapter, "_click_by_texts", forbidden_native_click)

    attached = asyncio.run(adapter._set_files_via_native_dialog([str(video)], "video"))

    assert attached is True
    assert handle.paths == [str(video.resolve())]


def test_native_zero_file_diagnostic_accepts_mounted_editor(tmp_path, monkeypatch):
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"video")
    broken_handle = type(
        "BrokenInvisibleHandle",
        (),
        {"__module__": "invisible_playwright.fake"},
    )()
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _WorkingUploadPage(broken_handle)

    async def no_sleep(_seconds):
        return None

    async def resolve_trigger(_target, _media_kind):
        return object()

    async def bridge_trigger(_target, _trigger):
        return None

    async def accepted(*_args, **_kwargs):
        return True

    async def zero_file_error(*_args, **_kwargs):
        raise RuntimeError(
            "File chooser closed but the input contains 0/1 file(s)."
        )

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "_resolve_native_upload_trigger", resolve_trigger)
    monkeypatch.setattr(adapter, "_bridge_native_upload_trigger", bridge_trigger)
    monkeypatch.setattr(adapter, "_wait_media_input_accepted", accepted)
    monkeypatch.setattr(adapter_module, "set_input_files_native", zero_file_error)

    attached = asyncio.run(
        adapter._set_files_via_native_dialog([str(video)], "video")
    )

    assert attached is True


def test_native_upload_uses_direct_human_gesture_before_bridge(tmp_path, monkeypatch):
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"video")
    broken_handle = type(
        "BrokenInvisibleHandle",
        (),
        {"__module__": "invisible_playwright.fake"},
    )()
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _WorkingUploadPage(broken_handle)
    calls = []

    class Trigger:
        async def hover(self, **_kwargs):
            return None

    trigger = Trigger()

    async def no_sleep(_seconds):
        return None

    async def resolve_trigger(_target, _media_kind):
        return trigger

    async def forbidden_bridge(_target, _trigger):
        raise AssertionError("bridge must not be installed before a direct click")

    async def native_attach(_target, _paths, **kwargs):
        calls.append(kwargs)

    async def accepted(*_args, **_kwargs):
        return True

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "_resolve_native_upload_trigger", resolve_trigger)
    monkeypatch.setattr(adapter, "_bridge_native_upload_trigger", forbidden_bridge)
    monkeypatch.setattr(adapter, "_wait_media_input_accepted", accepted)
    monkeypatch.setattr(adapter_module, "set_input_files_native", native_attach)

    attached = asyncio.run(
        adapter._set_files_via_native_dialog([str(video)], "video")
    )

    assert attached is True
    assert len(calls) == 1
    assert 160 <= calls[0]["trigger_dwell_ms"] <= 420
    assert 70 <= calls[0]["trigger_click_delay_ms"] <= 160


def test_native_upload_bridges_only_after_direct_click_opens_no_dialog(
    tmp_path, monkeypatch
):
    video = tmp_path / "sample.mp4"
    video.write_bytes(b"video")
    broken_handle = type(
        "BrokenInvisibleHandle",
        (),
        {"__module__": "invisible_playwright.fake"},
    )()
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _WorkingUploadPage(broken_handle)
    native_attempts = []
    bridge_calls = []

    class Trigger:
        async def hover(self, **_kwargs):
            return None

    async def no_sleep(_seconds):
        return None

    async def resolve_trigger(_target, _media_kind):
        return Trigger()

    async def bridge_trigger(_target, _trigger):
        bridge_calls.append(True)

    async def native_attach(_target, _paths, **_kwargs):
        native_attempts.append(True)
        if len(native_attempts) == 1:
            raise RuntimeError(
                "Windows file chooser did not appear "
                "(click completed without opening a dialog)"
            )

    async def accepted(*_args, **_kwargs):
        return True

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "_resolve_native_upload_trigger", resolve_trigger)
    monkeypatch.setattr(adapter, "_bridge_native_upload_trigger", bridge_trigger)
    monkeypatch.setattr(adapter, "_wait_media_input_accepted", accepted)
    monkeypatch.setattr(adapter_module, "set_input_files_native", native_attach)

    attached = asyncio.run(
        adapter._set_files_via_native_dialog([str(video)], "video")
    )

    assert attached is True
    assert len(native_attempts) == 2
    assert bridge_calls == [True]


def test_native_upload_keeps_unicode_paths_without_copying(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()
    video = tmp_path / "clip 💀.mp4"
    video.write_bytes(b"video-bytes")
    adapter = InvisiblePlaywrightAdapter()
    adapter._temp_profile_path = str(profile)

    staged = adapter._stage_native_upload_paths([str(video.resolve())])

    assert staged == [str(video.resolve())]
    assert adapter._native_upload_staging_dirs == set()
    assert Path(video).exists()


def test_native_upload_keeps_ascii_paths_unchanged(tmp_path):
    video = tmp_path / "plain-video.mp4"
    video.write_bytes(b"video")
    adapter = InvisiblePlaywrightAdapter()

    staged = adapter._stage_native_upload_paths([str(video.resolve())])

    assert staged == [str(video.resolve())]
    assert adapter._native_upload_staging_dirs == set()


def test_browser_close_timeout_reaps_only_its_session_and_returns_slot(monkeypatch):
    token = object()
    reaped = []

    class HungBrowserSession:
        _session_token = token

        async def __aexit__(self, *_args):
            await asyncio.Event().wait()

    adapter = InvisiblePlaywrightAdapter()
    adapter._invisible_pw = HungBrowserSession()
    adapter._browser = object()
    adapter._page = object()

    monkeypatch.setattr(settings, "BROWSER_CLOSE_TIMEOUT", 0.01)
    monkeypatch.setattr(
        adapter_module,
        "_reap_session_tree",
        lambda session_token: reaped.append(session_token) or 1,
    )

    asyncio.run(adapter.close())

    assert reaped == [token]
    assert adapter._invisible_pw is None
    assert adapter._browser is None
    assert adapter._page is None


class _UploadNavPage:
    """A page whose Upload control opens the upload screen when clicked."""

    def __init__(self, control_works=True):
        self.url = "https://www.tiktok.com/foryou?lang=en"
        self.control_works = control_works
        self.clicked = 0

    def locator(self, _selector):
        page = self

        class Candidate:
            async def is_visible(self):
                return True

            async def bounding_box(self):
                return {"x": 10, "y": 10, "width": 80, "height": 30}

        class Group:
            async def count(self):
                return 1

            def nth(self, _index):
                return Candidate()

        return Group()


def _open_upload_page(monkeypatch, page, entry_after_click=True):
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = page
    navigations = []

    async def human_click(_locator, timeout=5000):
        page.clicked += 1
        if page.control_works:
            page.url = "https://www.tiktok.com/tiktokstudio/upload?lang=en"

    async def entry_ready():
        return entry_after_click and page.control_works

    async def unobstructed(_candidate):
        return True

    async def navigate(url):
        navigations.append(url)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(adapter, "_human_click", human_click)
    monkeypatch.setattr(adapter, "_video_upload_entry_ready", entry_ready)
    monkeypatch.setattr(adapter, "_is_unobstructed", unobstructed)
    monkeypatch.setattr(adapter, "navigate_to", navigate)
    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    asyncio.run(adapter._open_studio_upload_page())
    return page, navigations


def test_the_upload_screen_is_opened_by_clicking_upload(monkeypatch):
    page, navigations = _open_upload_page(monkeypatch, _UploadNavPage())

    assert page.clicked == 1
    assert navigations == []          # the URL was never typed


def test_a_page_without_an_upload_control_falls_back_to_the_url(monkeypatch):
    page, navigations = _open_upload_page(
        monkeypatch, _UploadNavPage(control_works=False))

    assert page.clicked == 1
    assert navigations == ["https://www.tiktok.com/tiktokstudio/upload?lang=en"]
