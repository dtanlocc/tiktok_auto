"""One place that decides which browser engine a session runs on.

Two engines are wired: `invisible_playwright` (the vendored fork this app was
built around) and `camoufox`. They answer the same port, so the rest of the
app never asks which one it got - but they are NOT equivalent, and the
difference is worth stating where the choice is made:

  invisible_playwright  native Windows file chooser for uploads, a private
                        desktop with HWND capture for the live screen, and a
                        session token that lets a stuck browser tree be reaped
                        exactly.
  camoufox              a maintained anti-fingerprint Firefox with geoip-based
                        locale/timezone spoofing from the proxy exit IP; no
                        session token, no private desktop, no native chooser,
                        so uploads use the DOM input channel and the live
                        screen falls back to page.screenshot().
"""
import logging

from app.core.config import settings
from app.domain.ports.browser import IBrowserService
from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter

logger = logging.getLogger("BrowserFactory")

INVISIBLE_PLAYWRIGHT = "invisible_playwright"
CAMOUFOX = "camoufox"
SUPPORTED_ENGINES = (INVISIBLE_PLAYWRIGHT, CAMOUFOX)


def selected_engine() -> str:
    """The engine name the operator asked for, normalised."""
    value = str(getattr(settings, "BROWSER_ENGINE", INVISIBLE_PLAYWRIGHT) or "")
    name = value.strip().lower().replace("-", "_")
    if name not in SUPPORTED_ENGINES:
        if name:
            logger.warning(
                "[ENGINE] Khong biet engine %r; dung %s.", value, INVISIBLE_PLAYWRIGHT
            )
        return INVISIBLE_PLAYWRIGHT
    return name


def create_browser_service() -> IBrowserService:
    """Build the browser the operator chose.

    ⛔ A MISSING ENGINE IS NOT A REASON TO RUN THE OTHER ONE SILENTLY. If
    camoufox is selected but not installed, the account would otherwise run on
    a different browser than the operator believes, and any comparison between
    the two would be quietly meaningless.
    """
    engine = selected_engine()
    if engine == CAMOUFOX:
        try:
            from app.infrastructure.automation.camoufox_adapter import CamoufoxAdapter
        except ImportError as exc:
            raise RuntimeError(
                "BROWSER_ENGINE=camoufox nhung chua cai duoc camoufox: "
                f"{exc}. Cai bang `uv pip install camoufox[geoip]` va "
                "`python -m camoufox fetch`."
            ) from exc
        logger.info("[ENGINE] Phien nay chay tren Camoufox.")
        return CamoufoxAdapter()
    return InvisiblePlaywrightAdapter()
