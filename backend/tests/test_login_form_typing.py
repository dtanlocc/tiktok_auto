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
    assert page.looks == 0      # moved on: no need to read the form for an error


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

    async def respond(_page, timeout_seconds=12.0, ignore_error=""):
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


class _StaleGuestPageBrowser(_AfterLoginBrowser):
    """The login modal signed in on /foryou; the old guest navbar shows until a reload."""

    def __init__(self, cookies, signed_in_after_reloads=1):
        super().__init__(blank=0, cookies=cookies)
        self.signed_in_after_reloads = signed_in_after_reloads

    async def check_login_status(self):
        self.looks += 1
        return self.reloads >= self.signed_in_after_reloads


def _no_wait(monkeypatch):
    async def instant(_seconds):
        return None

    monkeypatch.setattr(login_strategies.asyncio, "sleep", instant)


def test_a_guest_navbar_left_from_before_the_code_is_reloaded_not_reported(monkeypatch):
    _no_wait(monkeypatch)
    browser = _StaleGuestPageBrowser(cookies=[{"name": "sessionid", "value": "fresh"}])

    assert asyncio.run(login_strategies._confirm_logged_in(browser)) == (True, True)
    assert browser.reloads == 1


def test_a_guest_page_without_a_session_cookie_is_a_failure_at_once(monkeypatch):
    _no_wait(monkeypatch)
    browser = _StaleGuestPageBrowser(cookies=[{"name": "ttwid", "value": "guest"}])

    assert asyncio.run(login_strategies._confirm_logged_in(browser)) == (False, True)
    assert browser.reloads == 0


def test_a_session_cookie_that_never_signs_the_page_in_fails_after_the_reloads(monkeypatch):
    _no_wait(monkeypatch)
    browser = _StaleGuestPageBrowser(
        cookies=[{"name": "sessionid", "value": "dead"}], signed_in_after_reloads=99)

    assert asyncio.run(login_strategies._confirm_logged_in(browser)) == (False, True)
    assert browser.reloads == 3


class _CodeBox:
    def __init__(self, visible_looks):
        self.visible_looks = visible_looks
        self.looks = 0

    @property
    def first(self):
        return self

    async def count(self):
        return 1

    async def is_visible(self):
        self.looks += 1
        return self.looks <= self.visible_looks


class _CodePage:
    def __init__(self, text):
        self.text = text

    async def evaluate(self, _script):
        return self.text


def test_a_refused_code_is_reported(monkeypatch):
    _no_wait(monkeypatch)
    page = _CodePage("2-step verification\nVerification code is expired or incorrect. Try again.\nNext")

    assert "expired or incorrect" in asyncio.run(
        login_strategies._await_otp_result(page, _CodeBox(visible_looks=99)))


def test_the_code_screen_going_away_is_an_accepted_code(monkeypatch):
    _no_wait(monkeypatch)
    page = _CodePage("2-step verification\nYour code was emailed to a***3@hotmail.com.\nResend code: 44s\nNext")

    assert asyncio.run(login_strategies._await_otp_result(page, _CodeBox(visible_looks=3))) == ""


# --- a credential login into a differently named account ------------------------

class _AnyLocator:
    @property
    def first(self):
        return self

    def filter(self, **_kw):
        return self

    async def wait_for(self, **_kw):
        return None

    async def click(self, **_kw):
        return None

    async def count(self):
        return 0

    async def is_visible(self):
        return False


class _AnyPage:
    url = "https://www.tiktok.com/foryou?lang=en"

    def locator(self, _selector):
        return _AnyLocator()


class _SignedInElsewhereBrowser:
    """The credentials open @maryannfranze while the app saved @mo91trow4_spau."""

    def __init__(self):
        self._page = _AnyPage()
        self.last_observed_identity = ""

    async def navigate_to(self, _url):
        return None

    async def is_account_banned(self):
        return False

    async def validate_authenticated_identity(self, expected):
        self.last_observed_identity = "maryannfranze"
        return expected == "maryannfranze"


def test_a_credential_login_into_a_renamed_account_is_a_success_not_a_mismatch(monkeypatch):
    async def form(browser, step_logger=None):
        return browser._page, _AnyLocator()

    async def nothing(*_a, **_kw):
        return None

    async def loaded(*_a, **_kw):
        return True

    async def submitted(*_a, **_kw):
        return ""

    async def no_code_screen(*_a, **_kw):
        return "none"

    async def signed_in(*_a, **_kw):
        return True, True

    monkeypatch.setattr(login_strategies, "_open_email_login_form", form)
    monkeypatch.setattr(login_strategies, "_wait_page_fully_loaded", loaded)
    monkeypatch.setattr(login_strategies, "_fill_login_form", nothing)
    monkeypatch.setattr(login_strategies, "_submit_login", submitted)
    monkeypatch.setattr(login_strategies, "_wait_verification_screen", no_code_screen)
    monkeypatch.setattr(login_strategies, "_confirm_logged_in", signed_in)
    messages = []

    async def log(message):
        messages.append(message)

    from types import SimpleNamespace
    account = SimpleNamespace(email="maryannfranze570852@hotmail.com", username="mo91trow4_spau",
                              password="pw", refresh_token="t", client_id="c")
    ok = asyncio.run(login_strategies.CredentialEmailOtpLoginStrategy().login(
        _SignedInElsewhereBrowser(), account, step_logger=log))

    assert ok is True, messages           # the use case's username sync corrects the name next
    assert any("@maryannfranze" in m and "cap nhat username" in m for m in messages)


class _StaleErrorPage:
    """After a re-press the old red line stays while TikTok opens the Email dialog."""

    def __init__(self, moves_on_after_looks):
        self.looks = 0
        self.moves_on_after_looks = moves_on_after_looks

    async def evaluate(self, script):
        if "innerText.slice(0, 4000)" in script:
            return "Log in\nInternal server error. Please try again later.\nLog in"
        self.looks += 1
        return self.looks > self.moves_on_after_looks


def test_an_old_error_line_is_not_read_as_a_new_refusal(monkeypatch):
    _clock_only(monkeypatch)
    page = _StaleErrorPage(moves_on_after_looks=3)
    stale = "Internal server error. Please try again later."

    assert asyncio.run(login_strategies._await_login_response(page, ignore_error=stale)) == ""


def test_a_press_blocked_by_the_email_dialog_counts_as_moved_on(monkeypatch):
    class CoveredButton(_SubmitButton):
        async def click(self):
            raise RuntimeError("the event would have landed elsewhere (<form>)")

    async def moved(_page):
        return True

    monkeypatch.setattr(login_strategies, "_moved_past_login_form", moved)
    assert asyncio.run(login_strategies._submit_login(object(), _CaptchaFreeBrowser(), CoveredButton())) == ""


# --- the For You login modal stays the main way in ------------------------------

class _ModalPage:
    """For You: the first Log in press opens nothing (page still loading), the
    second opens the modal; then "Use phone or email" -> "Use email or username"."""

    def __init__(self):
        self.login_presses = 0
        self.step = "feed"      # feed -> modal -> phone -> email

    def locator(self, selector):
        return _ModalLocator(self, selector)

    def get_by_text(self, *_a, **_kw):
        return _ModalLocator(self, "text")


class _ModalLocator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector

    @property
    def first(self):
        return self

    def filter(self, **_kw):
        return self

    def _visible(self):
        sel, step = self.selector, self.page.step
        if "login-modal" in sel:
            return step in ("modal", "phone", "email")
        if "channel-item" in sel:
            return step == "modal"
        if "login/phone-or-email/email" in sel or "Use email or username" in sel:
            return step == "phone"
        if "input" in sel:
            return step == "email"
        if "Log in" in sel or "login-button" in sel:
            return step == "feed"
        return False

    async def count(self):
        return 1 if self._visible() else 0

    async def is_visible(self):
        return self._visible()

    async def wait_for(self, **_kw):
        if not self._visible():
            raise RuntimeError(f"not visible: {self.selector[:40]}")

    async def click(self, **_kw):
        sel = self.selector
        if "Log in" in sel or "login-button" in sel:
            self.page.login_presses += 1
            if self.page.login_presses >= 2:
                self.page.step = "modal"
        elif "channel-item" in sel:
            self.page.step = "phone"
        elif "Use email or username" in sel or "login/phone-or-email/email" in sel:
            self.page.step = "email"


def test_the_for_you_modal_is_walked_before_any_direct_url(monkeypatch):
    _clock_only(monkeypatch)
    navigated = []

    class Browser:
        def __init__(self):
            self._page = _ModalPage()

        async def navigate_to(self, url):
            navigated.append(url)

    browser = Browser()
    page, email_input = asyncio.run(login_strategies._open_email_login_form(browser))

    assert navigated == []                      # no fallback to the direct login URL
    assert browser._page.login_presses == 2     # pressed again when the modal did not open
    assert asyncio.run(email_input.first.is_visible())


# --- the server, not the paint, decides whether a stored cookie is dead ---------

class _CookieBrowser:
    """For You shows a guest navbar for `guest_looks` looks; TikTok's server says `server`."""

    def __init__(self, guest_looks, server, identity=True):
        self.guest_looks = guest_looks
        self.server = server
        self.identity = identity
        self.looks = 0
        self.navigations = 0
        self.cleared = 0

    async def navigate_to(self, _url):
        self.navigations += 1

    async def inject_cookies(self, _cookies):
        return None

    async def check_login_status(self):
        self.looks += 1
        return self.looks > self.guest_looks

    async def read_session_account(self):
        return dict(self.server)

    async def validate_authenticated_identity(self, _expected):
        return self.identity

    async def clear_auth_session(self):
        self.cleared += 1


def _cookie_account():
    from types import SimpleNamespace
    return SimpleNamespace(username="norvi4671", email="n@hotmail.com", password="pw",
                           cookies=[{"name": "sessionid", "value": "live"}])


_ALIVE = {"state": "alive", "username": "norvi4671", "detail": "1"}
_ENDED = {"state": "signed_out", "username": "", "detail": "session expired, please sign in again"}


def test_a_guest_looking_page_with_a_live_session_is_reloaded_not_logged_out():
    browser = _CookieBrowser(guest_looks=1, server=_ALIVE)
    messages = []

    async def log(m):
        messages.append(m)

    assert asyncio.run(login_strategies.CookieLoginStrategy().login(
        browser, _cookie_account(), step_logger=log)) is True
    assert browser.looks == 2
    assert any("VAN CON" in m.upper() or "van con hieu luc" in m for m in messages)


def test_a_live_session_the_page_never_shows_keeps_the_cookies_and_stops():
    browser = _CookieBrowser(guest_looks=99, server=_ALIVE)

    with pytest.raises(AuthenticationPageNotReady):
        asyncio.run(login_strategies.CookieLoginStrategy().login(browser, _cookie_account()))
    assert browser.looks == 3


def test_a_session_tiktok_ended_is_a_failed_cookie_login():
    browser = _CookieBrowser(guest_looks=99, server=_ENDED)
    messages = []

    async def log(m):
        messages.append(m)

    assert asyncio.run(login_strategies.CookieLoginStrategy().login(
        browser, _cookie_account(), step_logger=log)) is False
    assert browser.looks == 1
    assert any("session expired" in m for m in messages)


def test_the_server_names_the_account_when_the_nav_does_not():
    browser = _CookieBrowser(guest_looks=0, server=_ALIVE, identity=False)

    assert asyncio.run(login_strategies.CookieLoginStrategy().login(browser, _cookie_account())) is True


def test_a_live_session_is_not_cleared_for_an_otp_login():
    """CookieThenCredential used to clear the jar and log in by OTP on a guest-looking page."""
    browser = _CookieBrowser(guest_looks=99, server=_ALIVE)

    with pytest.raises(AuthenticationPageNotReady):
        asyncio.run(login_strategies.CookieThenCredentialLoginStrategy().login(
            browser, _cookie_account()))
    assert browser.cleared == 0
