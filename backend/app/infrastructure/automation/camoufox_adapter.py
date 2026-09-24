"""The second engine: Camoufox, behind the same browser port.

⛔ ONLY THE LAUNCH DIFFERS. Everything the app asks a browser to do - open
For You, type a login, read cookies, walk Studio - is ordinary Playwright,
and that code is already written once in InvisiblePlaywrightAdapter. This
subclass replaces the two methods that know which engine is running, so the
two engines cannot drift apart in the behaviour that matters.

What Camoufox does NOT bring, and the app must degrade rather than pretend:
  - no session token, so no token-scoped process reaping;
  - no private Win32 desktop and no HWND, so the live screen falls back to
    page.screenshot() instead of PrintWindow;
  - no native Windows file chooser helper, so uploads go through the DOM
    input channel that already exists as the fallback.
"""
import asyncio
import logging
import os
import shutil
import tempfile
import uuid
from typing import Any, Dict, Optional

from app.core.config import settings
from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter

logger = logging.getLogger("CamoufoxAdapter")


class CamoufoxAdapter(InvisiblePlaywrightAdapter):
    """Camoufox in the place invisible_playwright usually stands."""

    #: Camoufox speaks its geo/locale spoofing from the proxy's exit IP when
    #: asked; that is the one setting that keeps region consistent with the
    #: address the session actually leaves from.
    def __init__(self) -> None:
        super().__init__()
        self._camoufox = None

    async def initialize(
        self,
        proxy_config: Optional[Dict[str, Any]] = None,
        seed: Optional[int] = None,
        force_visible: bool = False,
        _launch_retry: int = 0,
    ) -> None:
        from camoufox.async_api import AsyncCamoufox

        self._foryou_ready_at = None
        self._init_proxy_config = proxy_config
        self._init_seed = seed
        self._init_force_visible = force_visible

        proxy_opts = None
        if proxy_config and proxy_config.get("server"):
            proxy_opts = {"server": proxy_config["server"]}
            if proxy_config.get("username"):
                proxy_opts["username"] = proxy_config["username"]
            if proxy_config.get("password"):
                proxy_opts["password"] = proxy_config["password"]

        temp_root = os.path.join(tempfile.gettempdir(), "camoufox_profiles")
        os.makedirs(temp_root, exist_ok=True)
        self._temp_profile_path = os.path.join(temp_root, f"temp_{uuid.uuid4()}")

        # ⛔ NO persistent_context ON THIS MACHINE. Measured 24/09/2026:
        # with persistent_context=True the launch never returns - three tries,
        # 90s each, the camoufox processes alive the whole time - while the
        # plain launch comes back in seconds. The profile directory buys
        # nothing here anyway: every session starts from injected cookies.
        options: Dict[str, Any] = {
            "headless": not force_visible,
            "humanize": True,
            "locale": getattr(settings, "TIKTOK_WEB_LOCALE", "en-US"),
        }
        if proxy_opts:
            options["proxy"] = proxy_opts
            # Let the fingerprint follow the address the session leaves from,
            # rather than the machine it runs on.
            options["geoip"] = True

        launch_timeout = max(15, int(getattr(settings, "BROWSER_LAUNCH_TIMEOUT", 25)))
        tries = max(1, int(getattr(settings, "BROWSER_LAUNCH_MAX_TRIES", 2)))
        last_error: Optional[BaseException] = None
        for attempt in range(1, tries + 1):
            self._camoufox = AsyncCamoufox(**options)
            try:
                self._browser = await asyncio.wait_for(
                    self._camoufox.__aenter__(), timeout=launch_timeout * 2
                )
                # ⛔ THE FIRST PAGE IS PART OF THE LAUNCH. Camoufox can hand
                # back a Browser whose process has already died; asking for a
                # page is what finds out, and a dead one is a failed launch
                # rather than a failed account.
                self._page = await asyncio.wait_for(
                    self._browser.new_page(), timeout=launch_timeout
                )
                last_error = None
                break
            except BaseException as exc:
                last_error = exc
                logger.warning(
                    "[CAMOUFOX] Lan %d/%d hong (%s: %s) -> don va mo lai.",
                    attempt, tries, type(exc).__name__, str(exc)[:80],
                )
                await self._quiet_close()
                if attempt < tries:
                    await asyncio.sleep(2.0)
        if last_error is not None:
            raise last_error
        logger.info(
            "[CAMOUFOX] Phien san sang (%s, proxy %s).",
            "hien" if force_visible else "headless",
            proxy_opts.get("server") if proxy_opts else "mang that",
        )

    async def close(self) -> None:
        await self._quiet_close()
        if self._temp_profile_path and os.path.exists(self._temp_profile_path):
            path, self._temp_profile_path = self._temp_profile_path, None
            await asyncio.to_thread(shutil.rmtree, path, ignore_errors=True)
        for staging_dir in list(self._native_upload_staging_dirs):
            await asyncio.to_thread(shutil.rmtree, staging_dir, ignore_errors=True)
        self._native_upload_staging_dirs.clear()

    async def _quiet_close(self) -> None:
        camoufox, self._camoufox = self._camoufox, None
        self._browser = None
        self._page = None
        if camoufox is None:
            return
        try:
            await asyncio.wait_for(
                camoufox.__aexit__(None, None, None),
                timeout=max(0.1, float(getattr(settings, "BROWSER_CLOSE_TIMEOUT", 15.0))),
            )
        except Exception as exc:
            logger.warning("[CAMOUFOX] Dong phien khong sach: %s", exc)

    async def recover_stream_hwnd(self) -> Optional[int]:
        """Camoufox has no private desktop and no session token to look one up."""
        return None
