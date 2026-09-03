import asyncio
import inspect

from app.core.config import settings
from app.infrastructure.automation.playwright_adapter import (
    InvisiblePlaywrightAdapter,
    _classify_distribution_text,
)
from app.use_cases.upload.caption_hashtags import (
    choose_stable_hashtag_suggestion,
    hashtag_query_candidates,
)


def test_background_mode_uses_invisible_playwright_cloak_not_native_headless():
    assert settings.BROWSER_HEADLESS is True
    assert settings.BROWSER_TRUE_HEADLESS is False


def test_stream_is_not_suspended_during_caption_or_publish_confirmation():
    source = inspect.getsource(InvisiblePlaywrightAdapter.upload_video)

    assert source.count("self._stream_suspended = True") == 1
    assert "await self._set_file_via_native_dialog(video_path)" in source
    assert "acknowledged = await self._click_publish_and_confirm" in source


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
