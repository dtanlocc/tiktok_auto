import asyncio

from app.infrastructure.automation import playwright_adapter as adapter_module
from app.infrastructure.automation.playwright_adapter import (
    InvisiblePlaywrightAdapter,
    _caption_hashtags,
    _is_studio_posts_url,
    _normalize_caption_text,
    _studio_post_text_matches,
    _studio_posts_body_ready,
    _upload_progress_percent,
    _video_upload_finished,
)


class _Mouse:
    def __init__(self):
        self.moves = []
        self.clicks = []

    async def move(self, x, y, **kwargs):
        self.moves.append((x, y, kwargs))

    async def click(self, x, y, **kwargs):
        self.clicks.append((x, y, kwargs))


class _EmptyScopes:
    @property
    def first(self):
        return self

    async def count(self):
        return 0

    def nth(self, _index):
        return self


class _ButtonQuery:
    def __init__(self, page, pattern=None):
        self.page = page
        self.pattern = pattern

    @property
    def first(self):
        return self

    def filter(self, has_text=None):
        return _ButtonQuery(self.page, has_text)

    async def count(self):
        label = self.page.current_label
        return int(bool(label and self.pattern and self.pattern.fullmatch(label)))

    async def is_visible(self):
        return bool(await self.count())

    async def is_enabled(self):
        return bool(await self.count())

    async def bounding_box(self):
        return {"x": 100, "y": 40, "width": 120, "height": 36}

    async def wait_for(self, **_kwargs):
        if not await self.count():
            raise TimeoutError("button is not visible")

    async def click(self, **_kwargs):
        self.page.clicked.append(self.page.current_label)
        self.page.labels.pop(0)


class _DisabledButtonQuery(_ButtonQuery):
    def filter(self, has_text=None):
        return _DisabledButtonQuery(self.page, has_text)

    async def is_enabled(self):
        return False

    async def click(self, **_kwargs):
        raise TimeoutError("button is disabled")


class _BodyText:
    def __init__(self, page, fixed_text=None):
        self.page = page
        self.fixed_text = fixed_text

    async def inner_text(self, **_kwargs):
        if self.fixed_text is not None:
            return self.fixed_text
        return self.page.current_label or ""


class _PopupPage:
    def __init__(self):
        self.labels = ["Turn on", "Got it"]
        self.clicked = []
        self.mouse = _Mouse()

    @property
    def current_label(self):
        return self.labels[0] if self.labels else None

    def locator(self, selector):
        if selector == "body":
            return _BodyText(self)
        if selector == "button:visible":
            return _ButtonQuery(self)
        return _EmptyScopes()


class _DisabledPopupPage(_PopupPage):
    def locator(self, selector):
        if selector == "body":
            return _BodyText(self)
        if selector == "button:visible":
            return _DisabledButtonQuery(self)
        return _EmptyScopes()


class _VisibleText:
    @property
    def first(self):
        return self

    async def count(self):
        return 1

    async def is_visible(self):
        return True


class _PublishPage:
    def __init__(self):
        self.url = "https://www.tiktok.com/tiktokstudio/upload?lang=en"

    def get_by_text(self, *_args, **_kwargs):
        # Deliberately visible to prove Post now is checked first. TikTok's
        # dialog copy may contain words such as "posted" before confirmation.
        return _VisibleText()


class _NoticeScopes(_EmptyScopes):
    def __init__(self, page):
        self.page = page

    async def count(self):
        return int(bool(self.page.notice))

    async def is_visible(self):
        return bool(self.page.notice)

    async def inner_text(self, **_kwargs):
        return self.page.notice


class _UnexpectedNoticePage(_PublishPage):
    def __init__(self):
        super().__init__()
        self.notice = ""

    def locator(self, selector):
        if '[role="dialog"]:visible' in selector:
            return _NoticeScopes(self)
        if selector == "body":
            return _BodyText(self, self.notice)
        return _EmptyScopes()


class _SemanticUploadPage:
    def get_by_role(self, *_args, **_kwargs):
        return _VisibleText()

    def get_by_text(self, *_args, **_kwargs):
        return _VisibleText()

    def locator(self, _selector):
        return _BodyText(self, "Select video to upload")


class _VideoInputQuery(_EmptyScopes):
    async def count(self):
        return 1

    def nth(self, _index):
        return self

    async def get_attribute(self, name, **_kwargs):
        return "video/mp4" if name == "accept" else None


class _InputOnlyUploadPage:
    def locator(self, selector):
        if selector == "body":
            return _BodyText(self, "TikTok Studio Upload")
        if selector == 'input[type="file"]':
            return _VideoInputQuery()
        return _EmptyScopes()


class _BodyOnlyPage:
    def __init__(self, text):
        self.text = text

    def locator(self, selector):
        if selector == "body":
            return _BodyText(self, self.text)
        return _EmptyScopes()


class _StudioCaptionMatch:
    def __init__(self, nearby_text=""):
        self.nearby_text = nearby_text

    async def count(self):
        return 1

    def nth(self, _index):
        return self

    async def is_visible(self):
        return True

    async def evaluate(self, _script):
        return self.nearby_text


class _AutoRedirectStudioPage:
    def __init__(self):
        self.url = "https://www.tiktok.com/tiktokstudio/content?lang=en"

    def get_by_text(self, *_args, **_kwargs):
        return _StudioCaptionMatch()


class _TruncatedStudioPage:
    def __init__(self, body_text):
        self.url = "https://www.tiktok.com/tiktokstudio/content?lang=en"
        self.body_text = body_text

    def get_by_text(self, *_args, **_kwargs):
        return _EmptyScopes()

    def locator(self, selector):
        if selector == "body":
            return _BodyText(self, self.body_text)
        return _EmptyScopes()


class _StaleUrlStudioPage:
    def __init__(self, caption):
        self.url = "https://www.tiktok.com/tiktokstudio/upload?lang=en"
        self.caption = caption
        self.body_text = f"TikTok Studio\nPosts\n{caption}\nViews\n0"

    def get_by_text(self, *_args, **_kwargs):
        return _StudioCaptionMatch()

    def locator(self, selector):
        if selector == "body":
            return _BodyText(self, self.body_text)
        return _EmptyScopes()


class _NoFallbackBrowser:
    async def new_page(self):
        raise AssertionError("auto-redirect must keep the current Studio page")


class _CaptionKeyboard:
    def __init__(self, editor):
        self.editor = editor
        self.select_all = False
        self.insertions = []

    async def press(self, key):
        if key == "Control+A":
            self.select_all = True
        elif key in {"Backspace", "Delete"} and self.select_all:
            self.editor.text = ""
            self.select_all = False

    async def insert_text(self, value):
        self.insertions.append(value)
        if self.select_all:
            self.editor.text = ""
            self.select_all = False
        self.editor.text += value


class _CaptionEditor:
    def __init__(self, fail_auto_hashtag=False):
        self.text = "filename"
        self.fail_auto_hashtag = fail_auto_hashtag
        self.typing_calls = []

    @property
    def first(self):
        return self

    async def wait_for(self, **_kwargs):
        return None

    async def click(self, **_kwargs):
        return None

    async def inner_text(self, **_kwargs):
        return self.text

    async def press_sequentially(self, value, **kwargs):
        self.typing_calls.append((value, kwargs))
        if self.fail_auto_hashtag and value.startswith(" #"):
            self.text += " #part"
            raise TimeoutError("hashtag suggestions detached the editor")
        self.text += value


class _CaptionPage:
    def __init__(self, fail_auto_hashtag=False):
        self.editor = _CaptionEditor(fail_auto_hashtag=fail_auto_hashtag)
        self.keyboard = _CaptionKeyboard(self.editor)
        self.mouse = _Mouse()

    def locator(self, selector):
        if selector.startswith(".public-DraftEditor-content"):
            return self.editor
        return _EmptyScopes()


def test_upload_popups_accept_turn_on_then_got_it(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _PopupPage()

    accepted = asyncio.run(adapter._dismiss_upload_popups())

    assert accepted == 2
    assert adapter._page.clicked == ["Turn on", "Got it"]


def test_upload_popups_find_exact_labels_inside_full_page_text(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _PopupPage()

    async def full_page_text():
        if not adapter._page.labels:
            return "TikTok Studio\nUpload"
        return (
            "TikTok Studio\nUpload\nCheck your content before posting\n"
            f"{adapter._page.labels[0]}\nLearn more\nCaption"
        )

    monkeypatch.setattr(adapter, "_read_visible_page_text", full_page_text)

    accepted = asyncio.run(adapter._dismiss_upload_popups())

    assert accepted == 2
    assert adapter._page.clicked == ["Turn on", "Got it"]
    assert adapter._page.mouse.moves == []


def test_upload_interruptions_continue_immediately_when_nothing_appears(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    calls = []

    async def dismiss():
        calls.append("popup-check")
        return 0

    async def read_text():
        calls.append("text-check")
        return "Select video to upload"

    monkeypatch.setattr(adapter, "_dismiss_upload_popups", dismiss)
    monkeypatch.setattr(adapter, "_read_visible_page_text", read_text)

    interruptions = asyncio.run(adapter._handle_upload_interruptions())

    assert interruptions == 0
    assert calls == ["popup-check", "text-check"]


def test_upload_interruptions_wait_for_captcha_then_clear_revealed_popups(monkeypatch):
    async def no_sleep(_seconds):
        return None

    async def no_gate():
        return None

    adapter = InvisiblePlaywrightAdapter()
    visible_states = iter([
        "Verify to continue",
        "Verify to continue",
        "Select video to upload",
    ])
    popup_results = iter([0, 2])
    logs = []

    async def dismiss():
        return next(popup_results)

    async def read_text():
        return next(visible_states)

    async def log(message):
        logs.append(message)

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "_wait_automation_gate", no_gate)
    monkeypatch.setattr(adapter, "_dismiss_upload_popups", dismiss)
    monkeypatch.setattr(adapter, "_read_visible_page_text", read_text)

    interruptions = asyncio.run(adapter._handle_upload_interruptions(step_logger=log))

    assert interruptions == 3
    assert any("CAPTCHA" in message for message in logs)
    assert any("tiếp tục" in message for message in logs)


def test_video_upload_ready_prefers_visible_semantic_text():
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _SemanticUploadPage()

    ready = asyncio.run(adapter._video_upload_entry_ready())

    assert ready is True


def test_video_upload_ready_accepts_real_video_input_without_old_prompt():
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _InputOnlyUploadPage()

    ready = asyncio.run(adapter._video_upload_entry_ready())

    assert ready is True


def test_future_tense_publish_dialog_is_not_success():
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _BodyOnlyPage("Your video will be published immediately")

    assert asyncio.run(adapter._publish_success_visible()) is False


def test_explicit_completed_publish_message_is_success():
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _BodyOnlyPage("TikTok Studio\nYour video has been posted.\nUpload")

    assert asyncio.run(adapter._publish_success_visible()) is True


def test_studio_verification_keeps_tiktoks_auto_redirect_page(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _AutoRedirectStudioPage()
    adapter._browser = _NoFallbackBrowser()
    logs = []

    async def no_sleep(_seconds):
        return None

    async def no_gate():
        return None

    async def capture_log(message):
        logs.append(message)

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "_wait_automation_gate", no_gate)

    verified = asyncio.run(adapter._verify_post_in_studio(
        "A unique caption",
        step_logger=capture_log,
    ))

    assert verified is True
    assert any("tự chuyển" in message for message in logs)


def test_studio_verification_adopts_new_posts_tab(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    upload_page = _PublishPage()
    posts_page = _AutoRedirectStudioPage()

    class SessionBrowser:
        pages = [upload_page, posts_page]

    async def no_sleep(_seconds):
        return None

    async def no_gate():
        return None

    adapter._page = upload_page
    adapter._browser = SessionBrowser()
    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "_wait_automation_gate", no_gate)

    verified = asyncio.run(adapter._verify_post_in_studio(
        "A unique caption",
        timeout_seconds=5,
        require_auto_redirect=True,
        allow_reload=False,
    ))

    assert verified is True
    assert adapter._page is posts_page


def test_studio_verification_accepts_posts_ui_when_spa_url_is_stale(monkeypatch):
    caption = "A unique visible video title"
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _StaleUrlStudioPage(caption)

    async def no_sleep(_seconds):
        return None

    async def no_gate():
        return None

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "_wait_automation_gate", no_gate)

    verified = asyncio.run(adapter._verify_post_in_studio(
        caption,
        timeout_seconds=5,
        require_auto_redirect=True,
        allow_reload=False,
    ))

    assert verified is True
    assert _studio_posts_body_ready(adapter._page.body_text, [caption]) is True
    assert _is_studio_posts_url(adapter._page.url) is False


def test_studio_post_title_matching_accepts_longer_and_truncated_text():
    expected = (
        "NoobHunter - LOL_Riot_s Ballance Team Needs To Be Fired "
        "#leagueoflegends__3JGFrnWd0g.mp4"
    )

    assert _studio_post_text_matches(
        expected,
        expected + " #league #gaming 0 views",
    )
    assert _studio_post_text_matches(
        expected,
        "NoobHunter - LOL_Riot_s Ballance Team Needs…",
    )
    assert not _studio_post_text_matches(
        expected,
        "NoobHunter - LOL_Tyler1 crashes out coaching Jynxzi",
    )


def test_upload_progress_normalizes_percent_fraction_and_maximum():
    assert _upload_progress_percent("100") == 100.0
    assert _upload_progress_percent("1", "1") == 100.0
    assert _upload_progress_percent("99", "100") == 99.0
    assert _upload_progress_percent(None, None, "Uploading 100%") == 100.0
    assert _caption_hashtags("Filename #one #two") == ["#one", "#two"]
    assert _normalize_caption_text("Title\u2063 #fyp\u200b") == "Title #fyp"


def test_video_preview_without_upload_activity_is_a_completion_signal():
    preview = {
        "has_progress": False,
        "uploading": False,
        "complete": False,
        "preview_ready": True,
    }

    assert _video_upload_finished(preview) is True
    assert _video_upload_finished({**preview, "uploading": True}) is False
    assert _video_upload_finished({**preview, "has_progress": True}) is False


def test_upload_state_records_generic_retry_copy_without_failing_upload():
    class Candidate:
        def __init__(self, text):
            self.text = text

        async def is_visible(self):
            return True

        async def inner_text(self, **_kwargs):
            return self.text

    class Matches:
        def __init__(self, values):
            self.values = values

        async def count(self):
            return len(self.values)

        def nth(self, index):
            return Candidate(self.values[index])

    class StatePage:
        def __init__(self, texts):
            self.texts = texts

        def locator(self, _selector):
            return Matches([])

        def get_by_text(self, pattern):
            return Matches(
                [text for text in self.texts if pattern.search(text)]
            )

    adapter = InvisiblePlaywrightAdapter()
    adapter._page = StatePage(["Please try again", "Edit cover"])

    state = asyncio.run(adapter._read_video_upload_state())

    assert state["failed"] is False
    assert state["failure_text"] is None
    assert state["warning_text"] == "Please try again"
    assert state["preview_ready"] is True

    adapter._page = StatePage(["Upload failed. Please try again"])
    failed_state = asyncio.run(adapter._read_video_upload_state())

    assert failed_state["failed"] is True
    assert failed_state["failure_text"] == "Upload failed. Please try again"


def test_upload_completion_requires_three_consecutive_explicit_failures(monkeypatch):
    class ReadyControl:
        @property
        def first(self):
            return self

        async def count(self):
            return 1

        async def is_visible(self):
            return True

        async def is_enabled(self):
            return True

    class ReadyPage:
        def locator(self, _selector):
            return ReadyControl()

    states = [
        {
            "has_progress": False,
            "percent": None,
            "uploading": False,
            "complete": False,
            "preview_ready": False,
            "failed": True,
            "failure_text": "Upload failed",
            "warning_text": None,
        }
        for _ in range(3)
    ]
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = ReadyPage()

    async def state_reader():
        return states.pop(0)

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(adapter, "_read_video_upload_state", state_reader)
    monkeypatch.setattr(adapter, "_wait_automation_gate", no_op)
    monkeypatch.setattr(adapter, "_handle_upload_interruptions", no_op)
    monkeypatch.setattr(adapter, "_publish_button", lambda: ReadyControl())
    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_op)

    outcome = asyncio.run(
        adapter._wait_video_upload_completion(timeout_seconds=5)
    )

    assert outcome["ready"] is False
    assert outcome["failure_text"] == "Upload failed"
    assert states == []


def test_upload_completion_ignores_one_transient_failure_sample(monkeypatch):
    class ReadyControl:
        @property
        def first(self):
            return self

        async def count(self):
            return 1

        async def is_visible(self):
            return True

        async def is_enabled(self):
            return True

    class ReadyPage:
        def locator(self, _selector):
            return ReadyControl()

    states = [
        {
            "has_progress": False,
            "percent": None,
            "uploading": True,
            "complete": False,
            "preview_ready": True,
            "failed": True,
            "failure_text": "Upload failed",
            "warning_text": None,
        },
        {
            "has_progress": False,
            "percent": None,
            "uploading": False,
            "complete": False,
            "preview_ready": True,
            "failed": False,
            "failure_text": None,
            "warning_text": "Please try again",
        },
    ]
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = ReadyPage()

    async def state_reader():
        return states.pop(0)

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(adapter, "_read_video_upload_state", state_reader)
    monkeypatch.setattr(adapter, "_wait_automation_gate", no_op)
    monkeypatch.setattr(adapter, "_handle_upload_interruptions", no_op)
    monkeypatch.setattr(adapter, "_publish_button", lambda: ReadyControl())
    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_op)

    outcome = asyncio.run(
        adapter._wait_video_upload_completion(timeout_seconds=5)
    )

    assert outcome["ready"] is True
    assert outcome["failure_text"] is None
    assert states == []


def test_filename_caption_without_hashtag_is_never_typed_or_clicked():
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _CaptionPage()
    adapter._page.editor.text = "Filename already supplied by TikTok"

    caption = asyncio.run(adapter._activate_filename_hashtags())

    assert caption == "Filename already supplied by TikTok"
    assert adapter._page.editor.typing_calls == []
    assert adapter._page.mouse.moves == []
    assert adapter._page.mouse.clicks == []


def test_existing_filename_hashtags_use_caret_clicks_without_retyping(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _CaptionPage()
    adapter._page.editor.text = "Filename #fyp #trend #viral"
    selected = []
    scripts = []

    async def no_interruptions(**_kwargs):
        return 0

    async def hashtag_point(script, _target):
        scripts.append(script)
        return {"x": 120, "y": 80}

    async def select(token, **_kwargs):
        selected.append(token)
        return token

    monkeypatch.setattr(adapter, "_handle_upload_interruptions", no_interruptions)
    monkeypatch.setattr(adapter._page.editor, "evaluate", hashtag_point, raising=False)
    monkeypatch.setattr(adapter, "_select_existing_hashtag_suggestion", select)

    caption = asyncio.run(adapter._activate_filename_hashtags())

    assert caption == "Filename #fyp #trend #viral"
    assert selected == ["#viral", "#trend", "#fyp"]
    assert "rect.right - Math.max" in scripts[0]
    assert "selection.addRange" not in scripts[0]
    assert "rect.right +" not in scripts[0]
    assert adapter._page.editor.typing_calls == []
    assert adapter._page.keyboard.insertions == []
    assert adapter._page.mouse.clicks == [(120, 80, {})] * 3


def test_missing_hashtag_suggestions_keeps_caption_and_continues(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _CaptionPage()
    adapter._page.editor.text = "Filename #fyp #trend #viral"
    logs = []

    async def no_interruptions(**_kwargs):
        return 0

    async def hashtag_point(_script, _target):
        return {"x": 120, "y": 80}

    async def no_suggestion(_token, **_kwargs):
        return None

    async def capture_log(message):
        logs.append(message)

    monkeypatch.setattr(adapter, "_handle_upload_interruptions", no_interruptions)
    monkeypatch.setattr(adapter._page.editor, "evaluate", hashtag_point, raising=False)
    monkeypatch.setattr(adapter, "_select_existing_hashtag_suggestion", no_suggestion)

    caption = asyncio.run(adapter._activate_filename_hashtags(
        step_logger=capture_log,
    ))

    assert caption == "Filename #fyp #trend #viral"
    assert adapter._page.keyboard.insertions == []
    assert adapter._page.editor.typing_calls == []
    assert any("Không làm đậm được 3/3 hashtag" in message for message in logs)
    assert any("tiếp tục đăng" in message for message in logs)


def test_prepare_video_caption_uses_existing_filename_caption_without_typing(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _CaptionPage()
    desired = "Acara 7 bulanan 🍚 #fyp #trend #viral"
    activated = []

    async def retyping_must_not_run(*_args, **_kwargs):
        raise AssertionError("the existing filename caption must not be replaced")

    async def activate_existing(media_name="", step_logger=None):
        activated.append((media_name, step_logger))
        adapter._page.editor.text = desired
        return desired

    monkeypatch.setattr(adapter, "_fill_publish_caption", retyping_must_not_run)
    monkeypatch.setattr(adapter, "_activate_filename_hashtags", activate_existing)

    caption = asyncio.run(adapter._prepare_video_caption(
        desired,
        "Acara 7 bulanan 🍚 #fyp #trend #viral.mp4",
    ))

    assert caption == desired
    assert activated == [("Acara 7 bulanan 🍚 #fyp #trend #viral.mp4", None)]
    assert adapter._page.keyboard.insertions == []
    assert adapter._page.editor.typing_calls == []


def test_prepare_video_caption_returns_actual_untouched_filename_caption(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _CaptionPage()
    filename_caption = "Filename #topic"

    async def activate_existing(media_name="", step_logger=None):
        assert media_name == "Filename #topic.mp4"
        return filename_caption

    monkeypatch.setattr(adapter, "_activate_filename_hashtags", activate_existing)

    caption = asyncio.run(adapter._prepare_video_caption(
        "A different custom caption",
        "Filename #topic.mp4",
    ))

    assert caption == filename_caption
    assert adapter._page.keyboard.insertions == []
    assert adapter._page.editor.typing_calls == []


def test_transition_receipt_accepts_filename_before_studio_list(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _BodyOnlyPage(
        "Your video has been posted successfully\nFilename #topic.mp4"
    )
    adapter._page.url = "https://www.tiktok.com/tiktokstudio/upload?lang=en"

    async def no_gate():
        return None

    async def editor_gone():
        return False

    monkeypatch.setattr(adapter, "_wait_automation_gate", no_gate)
    monkeypatch.setattr(adapter, "_upload_editor_is_active", editor_gone)

    verified = asyncio.run(adapter._wait_for_transition_publish_receipt(
        "Filename #topic.mp4",
        timeout_seconds=1,
    ))

    assert verified is True
    assert adapter.last_publish_distribution_status == "PUBLISHED"


def test_studio_verification_accepts_truncated_body_and_filename(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _TruncatedStudioPage(
        "Posts\nNoobHunter - LOL_Riot_s Ballance Team Needs…\n0 views"
    )
    adapter._browser = _NoFallbackBrowser()

    async def no_sleep(_seconds):
        return None

    async def no_gate():
        return None

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "_wait_automation_gate", no_gate)

    verified = asyncio.run(adapter._verify_post_in_studio(
        "a different explicit caption",
        media_name=(
            "NoobHunter - LOL_Riot_s Ballance Team Needs To Be Fired "
            "#leagueoflegends__3JGFrnWd0g.mp4"
        ),
    ))

    assert verified is True


def test_immediate_video_verification_starts_five_seconds_after_redirect(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    clock = [0.0]

    class DelayedRedirectPage:
        @property
        def url(self):
            if clock[0] < 3.0:
                return "https://www.tiktok.com/tiktokstudio/upload?lang=en"
            return "https://www.tiktok.com/tiktokstudio/content?lang=en"

        def get_by_text(self, *_args, **_kwargs):
            return _EmptyScopes()

        def locator(self, selector):
            if selector == "body":
                return _BodyText(self, "Posts\na different video\n0 views")
            return _EmptyScopes()

    adapter._page = DelayedRedirectPage()
    adapter._browser = _NoFallbackBrowser()

    async def advance_clock(seconds):
        clock[0] += seconds

    async def no_gate():
        return None

    monkeypatch.setattr(adapter_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(adapter_module.asyncio, "sleep", advance_clock)
    monkeypatch.setattr(adapter, "_wait_automation_gate", no_gate)

    verified = asyncio.run(adapter._verify_post_in_studio(
        "a unique missing caption",
        timeout_seconds=5,
        require_auto_redirect=True,
        allow_reload=False,
        auto_redirect_timeout_seconds=20,
        poll_seconds=0.2,
    ))

    assert verified is False
    assert 7.99 <= clock[0] <= 8.01


def test_missing_toast_still_checks_studio_posts(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    calls = []

    async def verified_in_studio(caption, media_name=None, **_kwargs):
        calls.append((caption, media_name))
        return True

    monkeypatch.setattr(adapter, "_verify_post_in_studio", verified_in_studio)

    result = asyncio.run(adapter._finalize_immediate_video_publish(
        False,
        "caption from UI",
        r"C:\videos\long-video-name.mp4",
    ))

    assert result is True
    assert calls == [("caption from UI", "long-video-name.mp4")]
    assert adapter.last_publish_acknowledged is True


def test_missing_video_in_posts_is_marked_swallowed_without_reload(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    observed = {}
    logs = []

    async def absent_from_studio(caption, media_name=None, **kwargs):
        observed.update(kwargs)
        return False

    async def capture_log(message):
        logs.append(message)

    monkeypatch.setattr(adapter, "_verify_post_in_studio", absent_from_studio)
    monkeypatch.setattr(
        adapter, "_adopt_studio_posts_page_by_url", lambda: object()
    )

    result = asyncio.run(adapter._finalize_immediate_video_publish(
        True,
        "caption from UI",
        r"C:\videos\missing-video.mp4",
        step_logger=capture_log,
    ))

    assert result is False
    assert adapter.last_publish_failure_code == "VIDEO_SWALLOWED"
    assert observed["require_auto_redirect"] is True
    assert observed["allow_reload"] is False
    assert observed["timeout_seconds"] == 5
    assert observed["auto_redirect_timeout_seconds"] == 20.0
    assert observed["poll_seconds"] <= 0.2
    assert any("VIDEO_BI_NUOT" in message for message in logs)


def test_post_now_without_observed_posts_is_not_mislabeled_swallowed(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _PublishPage()
    adapter.last_publish_acknowledged = True
    adapter.last_publish_ack_source = "post_now_clicked"
    logs = []

    async def not_in_studio(*_args, **_kwargs):
        return False

    async def capture_log(message):
        logs.append(message)

    monkeypatch.setattr(adapter, "_verify_post_in_studio", not_in_studio)

    result = asyncio.run(adapter._finalize_immediate_video_publish(
        True,
        "caption from UI",
        r"C:\videos\no-transition.mp4",
        step_logger=capture_log,
    ))

    assert result is False
    assert adapter.last_publish_failure_code == "PUBLISH_NOT_CONFIRMED"
    assert "post_now_clicked" in adapter.last_publish_failure_detail
    assert any("KHONG_CHUYEN_SANG_POSTS" in message for message in logs)


def test_unconfirmed_publish_is_not_mislabeled_as_swallowed(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _PublishPage()

    async def not_in_studio(*_args, **_kwargs):
        return False

    monkeypatch.setattr(adapter, "_verify_post_in_studio", not_in_studio)

    result = asyncio.run(adapter._finalize_immediate_video_publish(
        False,
        "caption from UI",
        r"C:\videos\not-confirmed.mp4",
    ))

    assert result is False
    assert adapter.last_publish_failure_code == "PUBLISH_NOT_CONFIRMED"


def test_missing_photo_toast_still_checks_studio_posts(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    calls = []

    async def verified_in_studio(caption, media_name=None, **_kwargs):
        calls.append((caption, media_name))
        return True

    monkeypatch.setattr(adapter, "_verify_post_in_studio", verified_in_studio)

    result = asyncio.run(adapter._finalize_immediate_media_publish(
        False,
        "unique photo caption",
        r"C:\photos\test-photo.png",
    ))

    assert result is True
    assert calls == [("unique photo caption", "test-photo.png")]
    assert adapter.last_publish_acknowledged is True


def test_post_now_is_a_separate_explicit_confirmation(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _PopupPage()
    adapter._page.labels = ["Post now"]

    confirmed = asyncio.run(adapter._confirm_post_now_popup())

    assert confirmed is True
    assert adapter._page.clicked == ["Post now"]
    assert adapter._page.mouse.moves == []


def test_post_now_matches_one_exact_line_inside_full_page_text(monkeypatch):
    async def no_sleep(_seconds):
        return None

    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _PopupPage()
    adapter._page.labels = ["Post now"]

    async def full_page_text():
        return "TikTok Studio\nYour video is ready\nPost now\nCancel"

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "_read_visible_page_text", full_page_text)

    confirmed = asyncio.run(adapter._confirm_post_now_popup())

    assert confirmed is True
    assert adapter._page.clicked == ["Post now"]


def test_missing_post_now_popup_is_optional(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _PopupPage()
    adapter._page.labels = []

    confirmed = asyncio.run(adapter._confirm_post_now_popup())

    assert confirmed is False
    assert adapter._page.clicked == []


def test_visible_post_now_waits_when_button_is_not_enabled(monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _DisabledPopupPage()
    adapter._page.labels = ["Post now"]

    confirmed = asyncio.run(adapter._confirm_post_now_popup())

    assert confirmed is None
    assert adapter._page.clicked == []


def test_post_now_is_checked_before_generic_success_text(monkeypatch):
    async def no_sleep(_seconds):
        return None

    async def no_gate():
        return None

    async def dismiss():
        return 0

    async def find_button(**_kwargs):
        return object()

    async def click_button(_button, **_kwargs):
        return None

    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _PublishPage()
    confirmations = []

    async def confirm_post_now():
        confirmations.append("Post now")
        adapter._page.url = "https://www.tiktok.com/tiktokstudio/content?lang=en"
        return True

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "_wait_automation_gate", no_gate)
    monkeypatch.setattr(adapter, "_dismiss_upload_popups", dismiss)
    monkeypatch.setattr(adapter, "_publish_button_in_viewport", find_button)
    monkeypatch.setattr(adapter, "_publish_button", lambda **_kwargs: object())
    monkeypatch.setattr(adapter, "_human_click", click_button)
    monkeypatch.setattr(adapter, "_confirm_post_now_popup", confirm_post_now)

    result = asyncio.run(adapter._click_publish_and_confirm())

    assert result is True
    assert confirmations == ["Post now"]
    assert adapter.last_publish_ack_source == "post_now_clicked"


def test_duplicate_notice_stops_before_post_now_and_sets_distinct_code(monkeypatch):
    async def no_sleep(_seconds):
        return None

    async def no_gate():
        return None

    async def no_interruptions(**_kwargs):
        return 0

    async def find_button(**_kwargs):
        return object()

    async def click_button(_button, **_kwargs):
        return None

    async def duplicate_notice():
        return "VIDEO_DUPLICATE", "You've already posted this video"

    async def post_now_must_not_run():
        raise AssertionError("Post now must not be clicked after duplicate rejection")

    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _PublishPage()

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "_wait_automation_gate", no_gate)
    monkeypatch.setattr(adapter, "_handle_upload_interruptions", no_interruptions)
    monkeypatch.setattr(adapter, "_publish_button_in_viewport", find_button)
    monkeypatch.setattr(adapter, "_publish_button", lambda **_kwargs: object())
    monkeypatch.setattr(adapter, "_human_click", click_button)
    monkeypatch.setattr(adapter, "_read_publish_blocking_failure", duplicate_notice)
    monkeypatch.setattr(adapter, "_confirm_post_now_popup", post_now_must_not_run)

    result = asyncio.run(adapter._click_publish_and_confirm())

    assert result is False
    assert adapter.last_publish_failure_code == "VIDEO_DUPLICATE"
    assert "already posted" in adapter.last_publish_failure_detail


def test_unknown_notice_without_post_now_is_classified_as_duplicate(monkeypatch):
    async def no_sleep(_seconds):
        return None

    async def no_gate():
        return None

    async def no_interruptions(**_kwargs):
        return 0

    async def find_button(**_kwargs):
        return object()

    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _UnexpectedNoticePage()
    logs = []

    async def click_button(_button, **_kwargs):
        adapter._page.notice = "This action cannot be completed. Try another video."

    async def no_blocking_failure():
        return None

    async def no_post_now():
        return False

    async def no_success():
        return False

    async def editor_still_active():
        return True

    async def capture_log(message):
        logs.append(message)

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter, "_wait_automation_gate", no_gate)
    monkeypatch.setattr(adapter, "_handle_upload_interruptions", no_interruptions)
    monkeypatch.setattr(adapter, "_publish_button_in_viewport", find_button)
    monkeypatch.setattr(adapter, "_publish_button", lambda **_kwargs: object())
    monkeypatch.setattr(adapter, "_human_click", click_button)
    monkeypatch.setattr(adapter, "_read_publish_blocking_failure", no_blocking_failure)
    monkeypatch.setattr(adapter, "_confirm_post_now_popup", no_post_now)
    monkeypatch.setattr(adapter, "_publish_success_visible", no_success)
    monkeypatch.setattr(adapter, "_upload_editor_is_active", editor_still_active)

    result = asyncio.run(adapter._click_publish_and_confirm(
        step_logger=capture_log
    ))

    assert result is False
    assert adapter.last_publish_failure_code == "VIDEO_DUPLICATE"
    assert "cannot be completed" in adapter.last_publish_failure_detail
    assert any("VIDEO_TRUNG" in message for message in logs)


def test_duplicate_notice_text_is_classified_from_visible_page(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()

    async def visible_text():
        return "TikTok Studio\nYou've already posted this video\nTry another video"

    monkeypatch.setattr(adapter, "_read_visible_page_text", visible_text)

    result = asyncio.run(adapter._read_publish_blocking_failure())

    assert result == (
        "VIDEO_DUPLICATE",
        "You've already posted this video",
    )


def test_auto_hashtag_failure_restores_caption_and_continues(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _CaptionPage(fail_auto_hashtag=True)
    logs = []

    async def no_sleep(_seconds):
        return None

    async def no_interruptions(**_kwargs):
        return 0

    async def capture_log(message):
        logs.append(message)

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter_module, "hashtag_query_candidates", lambda *_args, **_kwargs: ["topic"])
    monkeypatch.setattr(adapter, "_handle_upload_interruptions", no_interruptions)

    asyncio.run(adapter._fill_publish_caption("A useful title", step_logger=capture_log))

    assert adapter._page.editor.text == "A useful title"
    assert any("hashtag" in message.casefold() and "caption gốc" in message for message in logs)


def test_long_caption_gets_length_aware_typing_timeout(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _CaptionPage()
    caption = "x" * 500

    async def no_sleep(_seconds):
        return None

    async def no_interruptions(**_kwargs):
        return 0

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter_module.settings, "AUTO_HASHTAGS_ENABLED", False)
    monkeypatch.setattr(adapter, "_handle_upload_interruptions", no_interruptions)

    asyncio.run(adapter._fill_publish_caption(caption))

    typed_caption, options = adapter._page.editor.typing_calls[0]
    assert typed_caption == caption
    assert options["timeout"] > 20_000


def test_unicode_glyph_uses_insert_text_but_hashtags_use_real_keys(monkeypatch):
    adapter = InvisiblePlaywrightAdapter()
    adapter._page = _CaptionPage()
    caption = "Title 🍚 #fyp #trend #viral"

    async def no_sleep(_seconds):
        return None

    async def no_interruptions(**_kwargs):
        return 0

    async def select(token, **_kwargs):
        return token

    monkeypatch.setattr(adapter_module.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(adapter_module.settings, "AUTO_HASHTAGS_ENABLED", False)
    monkeypatch.setattr(adapter, "_handle_upload_interruptions", no_interruptions)
    monkeypatch.setattr(adapter, "_select_existing_hashtag_suggestion", select)

    asyncio.run(adapter._fill_publish_caption(caption))

    typed = [value for value, _options in adapter._page.editor.typing_calls]
    assert "🍚" in adapter._page.keyboard.insertions
    assert "#fyp" in typed
    assert "#trend" in typed
    assert "#viral" in typed
    assert adapter._page.editor.text == caption
