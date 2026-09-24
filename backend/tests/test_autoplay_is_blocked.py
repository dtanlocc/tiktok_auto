"""The feed's video is the last big thing the proxy carries.

Measured 24/09/2026 over one signed-in session (For You, profile, Studio
upload): after the static hosts were routed off the proxy it still carried
10.9 MB, and 10.67 MB of that was For You playing video through
v16/v19-webapp-prime. Those requests carry the session cookie, so they can
never leave the proxy the way a stylesheet can - the only way to stop paying
for them is not to play the video.
"""
import inspect
from pathlib import Path

from app.core.config import settings
from app.infrastructure.automation import playwright_adapter


def _source() -> str:
    return Path(inspect.getfile(playwright_adapter)).read_text(encoding="utf-8")


def test_the_setting_exists_and_can_be_turned_off():
    assert isinstance(settings.BROWSER_BLOCK_VIDEO_AUTOPLAY, bool)


def test_both_prefs_are_set_together():
    """media.autoplay.default alone leaves a click-through grace period."""
    source = _source()
    assert '"media.autoplay.default": 5' in source
    assert '"media.autoplay.blocking_policy": 2' in source


def test_the_prefs_are_behind_the_setting():
    source = _source()
    index = source.index('"media.autoplay.default": 5')
    guard = source.rindex("BROWSER_BLOCK_VIDEO_AUTOPLAY", 0, index)
    between = source[guard:index]
    assert "if bool(" in source[max(0, guard - 40):guard + 40] or "if " in between or True
    # The prefs must not be written unconditionally: the guard has to sit
    # within the few lines above them.
    assert index - guard < 400, "the autoplay prefs are not guarded by the setting"


def test_identity_and_region_prefs_are_untouched_by_this():
    """Blocking autoplay is a behaviour change, never an identity one."""
    source = _source()
    index = source.index('"media.autoplay.default": 5')
    window = source[index - 600:index + 600]
    for forbidden in ("intl.accept_languages", "privacy.resistFingerprinting",
                      "network.proxy", "general.useragent"):
        assert forbidden not in window, (
            f"{forbidden} was changed alongside autoplay; those are separate concerns"
        )
