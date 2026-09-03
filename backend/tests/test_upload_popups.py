import asyncio

from app.infrastructure.automation import playwright_adapter as adapter_module
from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter


class _Mouse:
    def __init__(self):
        self.moves = []

    async def move(self, x, y, **kwargs):
        self.moves.append((x, y, kwargs))


class _EmptyScopes:
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


class _NoFallbackBrowser:
    async def new_page(self):
        raise AssertionError("auto-redirect must keep the current Studio page")


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


def test_review_pause_waits_before_publish_and_remains_interruptible(monkeypatch):
    clock = [100.0]
    slept = []
    logs = []
    adapter = InvisiblePlaywrightAdapter()

    async def fake_sleep(seconds):
        slept.append(seconds)
        clock[0] += seconds

    async def no_gate():
        return None

    async def no_interruptions(**_kwargs):
        return 0

    async def capture_log(message):
        logs.append(message)

    monkeypatch.setattr(adapter_module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(adapter_module.random, "uniform", lambda low, high: (low + high) / 2)
    monkeypatch.setattr(adapter_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(adapter, "_wait_automation_gate", no_gate)
    monkeypatch.setattr(adapter, "_handle_upload_interruptions", no_interruptions)

    asyncio.run(adapter._review_before_publish(
        step_logger=capture_log,
        min_seconds=5,
        max_seconds=5,
    ))

    assert sum(slept) >= 4.99
    assert any("rà soát" in message for message in logs)


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
