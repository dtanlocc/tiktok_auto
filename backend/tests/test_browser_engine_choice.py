"""Which browser a session runs on is the operator's choice, and it must be honest.

Two engines answer the same port, but they are not equivalent: camoufox has
no session token, no private desktop and no native file chooser. A run that
silently fell back to the other engine would make any comparison between them
meaningless, and would hand an account a browser the operator did not pick.
"""
import pytest

from app.infrastructure.automation import browser_factory
from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter


def test_the_default_is_the_engine_this_app_was_built_around(monkeypatch):
    monkeypatch.setattr(browser_factory.settings, "BROWSER_ENGINE", "invisible_playwright")
    assert browser_factory.selected_engine() == "invisible_playwright"
    assert isinstance(browser_factory.create_browser_service(), InvisiblePlaywrightAdapter)


def test_camoufox_is_selectable(monkeypatch):
    monkeypatch.setattr(browser_factory.settings, "BROWSER_ENGINE", "camoufox")
    service = browser_factory.create_browser_service()
    assert type(service).__name__ == "CamoufoxAdapter"


def test_the_name_is_read_forgivingly(monkeypatch):
    for written in ("  CAMOUFOX ", "Camoufox", "camoufox	"):
        monkeypatch.setattr(browser_factory.settings, "BROWSER_ENGINE", written)
        assert browser_factory.selected_engine() == "camoufox", written
    # A hyphen is the one substitution worth making: invisible-playwright is
    # how the package is spelled on PyPI.
    monkeypatch.setattr(browser_factory.settings, "BROWSER_ENGINE", "invisible-playwright")
    assert browser_factory.selected_engine() == "invisible_playwright"


def test_an_unknown_name_falls_back_to_the_built_in_engine(monkeypatch):
    monkeypatch.setattr(browser_factory.settings, "BROWSER_ENGINE", "chromium?")
    assert browser_factory.selected_engine() == "invisible_playwright"


def test_a_missing_camoufox_is_refused_rather_than_swapped(monkeypatch):
    """The operator must not be handed the other browser without being told."""
    monkeypatch.setattr(browser_factory.settings, "BROWSER_ENGINE", "camoufox")

    real_import = __import__

    def refuse_camoufox(name, *args, **kwargs):
        if "camoufox_adapter" in name:
            raise ImportError("No module named 'camoufox'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", refuse_camoufox)
    with pytest.raises(RuntimeError) as failure:
        browser_factory.create_browser_service()
    assert "camoufox" in str(failure.value)
    assert "fetch" in str(failure.value), "the message should say how to install it"


def test_camoufox_declares_what_it_cannot_do():
    """A stream that asks for an HWND must get an honest 'none', not a guess."""
    from app.infrastructure.automation.camoufox_adapter import CamoufoxAdapter
    import asyncio

    assert asyncio.run(CamoufoxAdapter().recover_stream_hwnd()) is None
