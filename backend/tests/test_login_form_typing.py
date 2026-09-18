import asyncio

import pytest

from app.use_cases.auth import login_strategies
from app.use_cases.auth.login_strategies import _type_login_field, _wait_submit_enabled


class _Keyboard:
    def __init__(self, field):
        self.field = field

    async def press(self, key):
        if key == "Backspace":
            self.field.value = ""


class _Field:
    """A login input that drops the first `drops` typings, like TikTok's form did."""

    def __init__(self, drops=0):
        self.drops = drops
        self.value = ""
        self.typed = 0

    @property
    def first(self):
        return self

    async def click(self):
        return None

    async def press_sequentially(self, text, delay=None):
        self.typed += 1
        self.value = "" if self.typed <= self.drops else text

    async def input_value(self):
        return self.value


class _Page:
    def __init__(self, field):
        self.keyboard = _Keyboard(field)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def no_sleep(_s):
        return None
    monkeypatch.setattr(login_strategies.asyncio, "sleep", no_sleep)


def test_a_field_the_form_kept_is_typed_once():
    field = _Field()
    asyncio.run(_type_login_field(_Page(field), field, "user@hotmail.com", "Email"))
    assert field.value == "user@hotmail.com" and field.typed == 1


def test_a_field_the_form_dropped_is_typed_again():
    field = _Field(drops=1)
    asyncio.run(_type_login_field(_Page(field), field, "user@hotmail.com", "Email"))
    assert field.value == "user@hotmail.com" and field.typed == 2


def test_a_field_the_form_never_keeps_is_a_clear_error():
    field = _Field(drops=99)
    with pytest.raises(RuntimeError, match="khong giu noi dung o Email"):
        asyncio.run(_type_login_field(_Page(field), field, "user@hotmail.com", "Email"))
    assert field.typed == 3


class _Button:
    def __init__(self, enabled):
        self.enabled = enabled

    @property
    def first(self):
        return self

    async def is_enabled(self):
        return self.enabled


def test_a_disabled_log_in_button_is_reported_instead_of_clicked(monkeypatch):
    class Clock:
        now = 0.0

        def time(self):
            Clock.now += 1.0
            return Clock.now

    monkeypatch.setattr(login_strategies.asyncio, "get_running_loop", lambda: Clock())
    assert asyncio.run(_wait_submit_enabled(_Button(enabled=False), timeout_seconds=5)) is False
    assert asyncio.run(_wait_submit_enabled(_Button(enabled=True), timeout_seconds=5)) is True


class _FormPage:
    def __init__(self, text):
        self.text = text

    async def evaluate(self, _script):
        return self.text


def test_tiktoks_refusal_line_is_read_from_the_form():
    page = _FormPage("Log in\nEmail or username\nLog in with phone\nAccount doesn't exist\nForgot password?\nLog in")
    assert asyncio.run(login_strategies._login_form_error(page)) == "Account doesn't exist"
    assert login_strategies._ACCOUNT_MISSING.search("Account doesn't exist")


def test_a_clean_form_reports_no_refusal():
    page = _FormPage("Log in\nEmail or username\nForgot password?\nLog in")
    assert asyncio.run(login_strategies._login_form_error(page)) == ""


class _Clock:
    def __init__(self):
        self.now = 0.0

    def time(self):
        return self.now


class _RebuildingForm:
    """TikTok re-mounting the login form: at `rebuild_at` both fields go empty.
    `every` > 0 keeps rebuilding that often (a form that never settles)."""

    def __init__(self, clock, rebuild_at, every=0.0):
        self.clock, self.rebuild_at, self.every = clock, rebuild_at, every
        self.fields = []

    def maybe_rebuild(self):
        if self.rebuild_at is not None and self.clock.now >= self.rebuild_at:
            for field in self.fields:
                field.value = ""
            self.rebuild_at = self.clock.now + self.every if self.every else None


class _FormField(_Field):
    def __init__(self, form):
        super().__init__()
        self.form = form
        form.fields.append(self)

    async def input_value(self):
        self.form.maybe_rebuild()
        return self.value


class _Kbd:
    async def press(self, _key):
        return None


class _PageWithKeyboard:
    keyboard = _Kbd()


def _run_fill(monkeypatch, rebuild_at, every=0.0):
    clock = _Clock()

    async def tick(seconds):
        clock.now += seconds

    monkeypatch.setattr(login_strategies.asyncio, "sleep", tick)
    monkeypatch.setattr(login_strategies.asyncio, "get_running_loop", lambda: clock)
    form = _RebuildingForm(clock, rebuild_at, every)
    email, password = _FormField(form), _FormField(form)
    coro = login_strategies._fill_login_form(
        _PageWithKeyboard(), email, password, "user@hotmail.com", "secret")
    return form, email, password, coro


def test_a_form_rebuilt_after_typing_is_typed_again(monkeypatch):
    # Both fields are typed and read back by ~2.5s; the rebuild at 3s wipes them.
    _form, email, password, coro = _run_fill(monkeypatch, rebuild_at=3.0)

    asyncio.run(coro)

    assert email.value == "user@hotmail.com" and password.value == "secret"
    assert email.typed == 2        # typed, wiped by the rebuild, typed again


def test_a_form_that_keeps_resetting_is_a_clear_error(monkeypatch):
    _form, _email, _password, coro = _run_fill(monkeypatch, rebuild_at=3.0, every=2.5)

    with pytest.raises(RuntimeError, match="xoa noi dung form dang nhap"):
        asyncio.run(coro)


@pytest.mark.parametrize("masked, email, same", [
    ("g***1@hotmail.com", "garrikbilliob@hotmail.com", False),   # treft21664, 2026-09-18
    ("g***b@hotmail.com", "garrikbilliob@hotmail.com", True),
    ("ga***@hotmail.com", "garrikbilliob@hotmail.com", True),
    ("g***b@outlook.com", "garrikbilliob@hotmail.com", False),
    ("not-an-email", "garrikbilliob@hotmail.com", True),          # cannot tell: never block on it
])
def test_the_mailbox_tiktok_names_is_compared_with_the_accounts(masked, email, same):
    assert login_strategies.masked_email_matches(masked, email) is same


def test_the_masked_destination_is_read_from_the_verify_screen():
    page = _FormPage("Verify identity\nVerify your identity by entering the code sent to\ng***1@hotmail.com.\nResend code")
    assert asyncio.run(login_strategies._otp_destination(page)) == "g***1@hotmail.com"


class _SlowRefusalPage:
    """The refusal line shows up only on the third look, like through a slow proxy."""

    def __init__(self, looks_before_error, moved_on=False):
        self.looks = 0
        self.looks_before_error = looks_before_error
        self.moved_on = moved_on

    async def evaluate(self, script):
        if "innerText.slice(0, 4000)" in script:
            self.looks += 1
            if self.looks > self.looks_before_error:
                return "Log in\nIncorrect account or password. 3 attempts remaining. Try again.\nLog in"
            return "Log in\nForgot password?"
        return self.moved_on


def test_a_late_refusal_is_still_caught(monkeypatch):
    _clock_only(monkeypatch)
    page = _SlowRefusalPage(looks_before_error=2)
    assert asyncio.run(login_strategies._await_login_response(page)).startswith("Incorrect account or password")


def test_moving_on_to_the_code_screen_is_not_a_refusal(monkeypatch):
    _clock_only(monkeypatch)
    page = _SlowRefusalPage(looks_before_error=99, moved_on=True)
    assert asyncio.run(login_strategies._await_login_response(page)) == ""
    assert page.looks == 1


def _clock_only(monkeypatch):
    clock = _Clock()

    async def tick(seconds):
        clock.now += seconds

    monkeypatch.setattr(login_strategies.asyncio, "sleep", tick)
    monkeypatch.setattr(login_strategies.asyncio, "get_running_loop", lambda: clock)


class _LoadingPage:
    """readyState / network quiet as a function of the fake clock."""

    def __init__(self, clock, complete_at=None, quiet_from=None):
        self.clock, self.complete_at, self.quiet_from = clock, complete_at, quiet_from

    async def evaluate(self, _script):
        now = self.clock.now
        ready = "complete" if self.complete_at is not None and now >= self.complete_at else "interactive"
        quiet = self.quiet_from is not None and now >= self.quiet_from
        return {"ready": ready, "quietMs": 2000 if quiet else 0}


def _load_clock(monkeypatch):
    clock = _Clock()

    async def tick(seconds):
        clock.now += seconds

    monkeypatch.setattr(login_strategies.asyncio, "sleep", tick)
    monkeypatch.setattr(login_strategies.asyncio, "get_running_loop", lambda: clock)
    return clock


def test_typing_waits_for_the_load_event_not_the_first_visible_field(monkeypatch):
    clock = _load_clock(monkeypatch)
    page = _LoadingPage(clock, complete_at=18.4, quiet_from=18.4)   # measured on the login page

    assert asyncio.run(login_strategies._wait_page_fully_loaded(page)) is True
    assert 18.4 <= clock.now < 19.5


def test_a_page_that_streams_video_is_ready_a_few_seconds_after_it_loaded(monkeypatch):
    clock = _load_clock(monkeypatch)
    page = _LoadingPage(clock, complete_at=5.0, quiet_from=None)     # For You never goes quiet

    assert asyncio.run(login_strategies._wait_page_fully_loaded(page)) is True
    assert 8.0 <= clock.now < 9.0


def test_a_page_stuck_loading_is_given_up_on_at_the_cap(monkeypatch):
    clock = _load_clock(monkeypatch)
    page = _LoadingPage(clock)                                        # CDN blocked: never completes

    assert asyncio.run(login_strategies._wait_page_fully_loaded(page, timeout_seconds=30)) is False
    assert 30.0 <= clock.now < 31.0


# --- TikTok server hiccups and pages that cannot paint after login ------------

from app.core.exceptions import AuthenticationPageNotReady


@pytest.mark.parametrize("message, transient", [
    ("Internal server error. Please try again later.", True),          # mo91trow4_spau
    ("Something went wrong", True),
    ("Maximum number of attempts reached. Try again later.", False),   # pressing again = locked longer
    ("Incorrect account or password. 3 attempts remaining. Try again.", False),
    ("Account doesn't exist", False),
    ("", False),
])
def test_only_tiktoks_own_hiccups_are_pressed_again(message, transient):
    assert login_strategies.is_transient_login_error(message) is transient


class _SubmitButton:
    def __init__(self):
        self.clicks = 0

    @property
    def first(self):
        return self

    async def click(self):
        self.clicks += 1

    async def is_enabled(self):
        return True


class _CaptchaFreeBrowser:
    async def wait_captcha_cleared(self, **_kw):
        return None


def _answers(monkeypatch, answers):
    replies = list(answers)

    async def respond(_page, timeout_seconds=12.0):
        return replies.pop(0)

    monkeypatch.setattr(login_strategies, "_await_login_response", respond)


def test_an_internal_server_error_is_pressed_again_until_tiktok_moves_on(monkeypatch):
    _answers(monkeypatch, ["Internal server error. Please try again later.",
                           "Internal server error. Please try again later.", ""])
    button = _SubmitButton()

    error = asyncio.run(login_strategies._submit_login(object(), _CaptchaFreeBrowser(), button))

    assert error == "" and button.clicks == 3


def test_a_wrong_password_is_never_pressed_again(monkeypatch):
    _answers(monkeypatch, ["Incorrect account or password. 3 attempts remaining. Try again."])
    button = _SubmitButton()

    error = asyncio.run(login_strategies._submit_login(object(), _CaptchaFreeBrowser(), button))

    assert error.startswith("Incorrect") and button.clicks == 1


def test_a_server_error_that_never_clears_is_reported_after_the_retries(monkeypatch):
    _answers(monkeypatch, ["Internal server error. Please try again later."] * 4)
    button = _SubmitButton()

    error = asyncio.run(login_strategies._submit_login(object(), _CaptchaFreeBrowser(), button))

    assert error.startswith("Internal server error") and button.clicks == 4


class _AfterLoginBrowser:
    """check_login_status raises NotReady for the first `blank` looks."""

    def __init__(self, blank, cookies=()):
        self.blank = blank
        self.cookies = list(cookies)
        self.looks = 0
        self.reloads = 0

    async def check_login_status(self):
        self.looks += 1
        if self.looks <= self.blank:
            raise AuthenticationPageNotReady("CDN refused")
        return True

    async def navigate_to(self, _url):
        self.reloads += 1

    async def extract_cookies(self):
        return self.cookies


def test_a_page_that_paints_after_a_reload_confirms_the_login(monkeypatch):
    browser = _AfterLoginBrowser(blank=2)

    assert asyncio.run(login_strategies._confirm_logged_in(browser)) == (True, True)
    assert browser.reloads == 2


def test_a_page_that_never_paints_falls_back_to_the_session_cookie(monkeypatch):
    browser = _AfterLoginBrowser(blank=99, cookies=[{"name": "sessionid", "value": "abc"}])

    assert asyncio.run(login_strategies._confirm_logged_in(browser)) == (True, False)
    assert browser.reloads == 3


def test_no_page_and_no_session_cookie_is_still_a_failure(monkeypatch):
    browser = _AfterLoginBrowser(blank=99, cookies=[{"name": "ttwid", "value": "guest"}])

    with pytest.raises(AuthenticationPageNotReady):
        asyncio.run(login_strategies._confirm_logged_in(browser))
