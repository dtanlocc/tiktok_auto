from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from invisible_browser_studio.adapters.outbound.extension_profile_builder import (
    ExtensionProfileBuilder,
    InstalledExtension,
    firefox_prefs_for_extensions,
)
from invisible_browser_studio.application.dto import RuntimeStartResult
from invisible_browser_studio.application.ports import BrowserRuntime, FramePublisher
from invisible_browser_studio.domain import BrowserMode, BrowserSession

logger = logging.getLogger("invisible_browser_studio.runtime")
_claimed_hwnds: set[int] = set()
_hwnd_lock = threading.Lock()


@dataclass(slots=True)
class _BrowserHandle:
    session: BrowserSession
    profile_dir: Path
    installed_extensions: list[InstalledExtension]
    seed: int
    manager: Any = None
    context: Any = None
    page: Any = None
    hwnd: int | None = None
    capture_task: asyncio.Task[None] | None = None


def _ensure_tiktok_english_url(url: str) -> str:
    parts = urlsplit(url)
    hostname = (parts.hostname or "").casefold()
    if hostname != "tiktok.com" and not hostname.endswith(".tiktok.com"):
        return url
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["lang"] = "en"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def _reap_session_tree(token: Any) -> int:
    """Terminate only processes positively owned by one invisible session."""
    try:
        from invisible_core.process import find_processes, terminate
    except ImportError:
        return 0
    try:
        processes = find_processes(token)
        return terminate(processes) if processes else 0
    except Exception as exc:
        logger.debug("Session-token cleanup failed: %s", exc)
        return 0


class InvisiblePlaywrightRuntime(BrowserRuntime):
    """The standalone runtime with the same launch/navigation contract as TD white."""

    def __init__(
        self,
        frame_publisher: FramePublisher,
        *,
        screenshot_interval_seconds: float = 0.08,
        jpeg_quality: int = 85,
        stream_max_width: int = 1280,
        navigation_timeout_ms: int = 60_000,
        extension_paths: tuple[Path, ...] = (),
        extensions_required: bool = True,
        omocaptcha_api_key: str = "",
        omocaptcha_extension_uuid: str = "d6105ea0-8d34-41ab-85a7-2eb0c66d55bb",
        profile_root: Path | None = None,
        launch_timeout_seconds: float = 45.0,
        launch_max_tries: int = 2,
        hwnd_detection_timeout_seconds: float = 6.0,
    ) -> None:
        self._frame_publisher = frame_publisher
        self._screenshot_interval = screenshot_interval_seconds
        self._jpeg_quality = jpeg_quality
        self._stream_max_width = stream_max_width
        self._navigation_timeout_ms = navigation_timeout_ms
        self._extension_paths = tuple(path.resolve() for path in extension_paths)
        self._extensions_required = extensions_required
        self._omocaptcha_api_key = omocaptcha_api_key
        self._omocaptcha_extension_uuid = omocaptcha_extension_uuid
        self._profile_root = profile_root or (
            Path(tempfile.gettempdir()) / "invisible_browser_studio_profiles"
        )
        self._launch_timeout = max(15.0, launch_timeout_seconds)
        self._launch_max_tries = max(1, launch_max_tries)
        self._hwnd_detection_timeout = max(0.0, hwnd_detection_timeout_seconds)
        self._handles: dict[str, _BrowserHandle] = {}
        self._lock = asyncio.Lock()

    async def start(self, session: BrowserSession) -> RuntimeStartResult:
        profile_dir = self._profile_root / f"{session.id}_{uuid.uuid4().hex}"
        installed: list[InstalledExtension]
        if session.extensions_enabled:
            extension_builder = ExtensionProfileBuilder(
                self._extension_paths,
                uuid_overrides={
                    "omocaptcha@gmail.com": self._omocaptcha_extension_uuid,
                },
                storage_local_seed_resources={
                    "omocaptcha@gmail.com": "configs.json",
                },
                storage_local_overrides={
                    "omocaptcha@gmail.com": {
                        "api_key": self._omocaptcha_api_key,
                        "initialized": True,
                    },
                },
                fail_if_empty=self._extensions_required,
            )
            try:
                installed = await asyncio.to_thread(
                    extension_builder.prepare_profile, profile_dir
                )
                await asyncio.to_thread(self._validate_extension_packages, installed)
            except BaseException:
                await asyncio.to_thread(shutil.rmtree, profile_dir, True)
                raise
        else:
            await asyncio.to_thread(profile_dir.mkdir, parents=True, exist_ok=False)
            installed = []

        handle = _BrowserHandle(
            session=session,
            profile_dir=profile_dir,
            installed_extensions=installed,
            seed=secrets.randbelow(0x7FFFFFFF) + 1,
        )
        try:
            await self._launch(handle)
            if session.start_url and session.start_url != "about:blank":
                await self._navigate_handle(handle, session.start_url)
            handle.capture_task = asyncio.create_task(
                self._capture_loop(session.id, handle),
                name=f"frame-capture-{session.id}",
            )
            async with self._lock:
                self._handles[session.id] = handle
            return RuntimeStartResult(current_url=str(handle.page.url))
        except BaseException:
            await self._close_handle(handle, delete_profile=True)
            raise

    async def close(self, session_id: str) -> None:
        async with self._lock:
            handle = self._handles.pop(session_id, None)
        if handle:
            await self._close_handle(handle, delete_profile=True)

    async def navigate(self, session_id: str, url: str) -> str:
        handle = await self._get(session_id)
        await self._navigate_handle(handle, url)
        return str(handle.page.url)

    async def upload(self, session_id: str, path: Path) -> None:
        handle = await self._get(session_id)
        page = await self._ensure_page(handle)
        if page is None:
            raise RuntimeError("No live page is available for upload")
        input_element = page.locator('input[type="file"]').first
        await input_element.wait_for(state="attached", timeout=self._navigation_timeout_ms)
        await input_element.set_input_files(str(path))

    async def page_for(self, session_id: str) -> Any:
        """Expose the live page only to colocated, purpose-built outbound drivers."""
        handle = await self._get(session_id)
        page = await self._ensure_page(handle)
        if page is None:
            raise RuntimeError("No live page is available")
        return page

    async def shutdown(self) -> None:
        async with self._lock:
            session_ids = tuple(self._handles)
        await asyncio.gather(
            *(self.close(session_id) for session_id in session_ids),
            return_exceptions=True,
        )

    async def _get(self, session_id: str) -> _BrowserHandle:
        async with self._lock:
            handle = self._handles.get(session_id)
        if not handle:
            raise RuntimeError("browser session is not running")
        return handle

    def _wrapper_options(self, handle: _BrowserHandle) -> dict[str, object]:
        session = handle.session
        options: dict[str, object] = {
            "headless": session.mode is BrowserMode.HIDDEN,
            "humanize": session.humanize,
            "seed": handle.seed,
            "locale": session.locale,
            "timezone": session.timezone,
            "profile_dir": handle.profile_dir,
            "extra_prefs": self._firefox_prefs(handle.installed_extensions),
        }
        if session.proxy:
            proxy = {"server": session.proxy.server}
            if session.proxy.username is not None:
                proxy["username"] = session.proxy.username
            if session.proxy.password is not None:
                proxy["password"] = session.proxy.password
            options["proxy"] = proxy
        return options

    @staticmethod
    def _firefox_prefs(installed: list[InstalledExtension]) -> dict[str, object]:
        return {
            **firefox_prefs_for_extensions(installed),
            "intl.accept_languages": "en-US, en",
            "intl.locale.requested": "en-US",
            "widget.windows.window_occlusion_tracking.enabled": False,
            "browser.sessionstore.resume_from_crash": False,
            "toolkit.startup.max_resumed_crashes": -1,
            "browser.sessionstore.max_resumed_crashes": 0,
            "browser.startup.page": 0,
            "browser.sessionstore.resume_session_once": False,
            "browser.startup.homepage_override.mstone": "ignore",
            "browser.startup.firstrunSkipsHomepage": True,
            "browser.aboutwelcome.enabled": False,
            "browser.newtabpage.enabled": False,
            "browser.newtabpage.activity-stream.feeds.topsites": False,
            "browser.newtabpage.activity-stream.feeds.section.topstories": False,
            "extensions.pocket.enabled": False,
            "datareporting.policy.dataSubmissionEnabled": False,
            "datareporting.healthreport.uploadEnabled": False,
            "toolkit.telemetry.enabled": False,
            "toolkit.telemetry.unified": False,
            "browser.contentblocking.report.hide_vpn_banner": True,
            "browser.discovery.enabled": False,
            "app.normandy.enabled": False,
            "app.shield.optoutstudies.enabled": False,
            "browser.region.network.url": "",
            "browser.safebrowsing.downloads.remote.enabled": False,
            "zoom.stealth.canvas.substitute_pixels": False,
        }

    async def _launch(self, handle: _BrowserHandle) -> None:
        try:
            from invisible_playwright.async_api import InvisiblePlaywright
        except ImportError as exc:
            raise RuntimeError(
                "invisible_playwright is unavailable; install the vendored browser extra"
            ) from exc

        options = self._wrapper_options(handle)
        before_hwnds = (
            set()
            if handle.session.mode is BrowserMode.HIDDEN
            else await asyncio.to_thread(self._enum_moz_hwnds)
        )
        last_error: BaseException | None = None
        for attempt in range(1, self._launch_max_tries + 1):
            manager = InvisiblePlaywright(**options)
            if handle.installed_extensions:
                manager.set_firefox_extensions(
                    item.xpi_path for item in handle.installed_extensions
                )
            owner = context = None
            try:
                owner = await asyncio.wait_for(manager.__aenter__(), timeout=self._launch_timeout)
                new_context = getattr(owner, "new_context", None)
                context = await new_context() if callable(new_context) else owner
                handle.manager = manager
                handle.context = context
                break
            except BaseException as exc:
                last_error = exc
                token = getattr(manager, "_session_token", None)
                if context is not None:
                    try:
                        await asyncio.wait_for(context.close(), timeout=10)
                    except Exception:
                        pass
                try:
                    await asyncio.wait_for(manager.__aexit__(None, None, None), timeout=10)
                except Exception:
                    pass
                if token:
                    await asyncio.to_thread(_reap_session_tree, token)
                if attempt < self._launch_max_tries:
                    await asyncio.sleep(1.5)
        else:
            raise last_error or RuntimeError("Could not launch invisible browser")

        try:
            handle.hwnd = await self._detect_own_hwnd(handle.manager, before_hwnds)
            if handle.hwnd and handle.session.mode is BrowserMode.VISIBLE:
                from .windows_capture import show_window_foreground

                await asyncio.wait_for(
                    asyncio.to_thread(show_window_foreground, handle.hwnd), timeout=5
                )

            startup_pages = list(getattr(handle.context, "pages", ()) or ())
            handle.page = await handle.context.new_page()
            handle.page.set_default_navigation_timeout(self._navigation_timeout_ms)
            locale_state = await handle.page.evaluate(
                "() => ({ language: navigator.language, languages: navigator.languages })"
            )
            if not str(locale_state.get("language") or "").casefold().startswith("en"):
                raise RuntimeError(f"Browser locale was not applied: {locale_state!r}")
            for startup_page in startup_pages:
                if startup_page is handle.page:
                    continue
                try:
                    await startup_page.close()
                except Exception:
                    pass
            await self._verify_loaded_extensions(handle.profile_dir, handle.installed_extensions)
            recovered = await self._recover_hwnd(handle)
            if recovered and handle.session.mode is BrowserMode.VISIBLE:
                from .windows_capture import show_window_foreground

                await asyncio.wait_for(
                    asyncio.to_thread(show_window_foreground, recovered), timeout=5
                )
            logger.info(
                "Browser launched: session=%s mode=%s seed=%s proxy=%s",
                handle.session.id,
                handle.session.mode.value,
                handle.seed,
                handle.session.proxy.server if handle.session.proxy else "direct",
            )
        except BaseException:
            await self._close_engine(handle)
            raise

    async def _navigate_handle(self, handle: _BrowserHandle, url: str) -> None:
        target = _ensure_tiktok_english_url(url)
        target_host = (urlsplit(target).hostname or "").casefold()
        last_error: BaseException | None = None
        relaunched = False

        for attempt in range(4):
            page = await self._ensure_page(handle)
            if page is None:
                last_error = RuntimeError("No page is available for navigation")
                if not relaunched:
                    relaunched = True
                    await self._relaunch(handle)
                    continue
                await asyncio.sleep(1)
                continue
            try:
                logger.info("Navigating to %s (attempt %s/4, wait=commit)", target, attempt + 1)
                await page.goto(target, wait_until="commit", timeout=30_000)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=12_000)
                except Exception:
                    pass
                return
            except BaseException as exc:
                last_error = exc
                message = str(exc)
                dead = (
                    "browsingContext" in message
                    or "Connection closed" in message
                    or "closed" in message
                    or "Target page" in message
                    or "crash" in message.casefold()
                )
                if dead:
                    if not relaunched and attempt >= 1:
                        relaunched = True
                        await self._relaunch(handle)
                        continue
                    handle.page = None
                    await asyncio.sleep(1)
                    continue
                try:
                    current_url = str(page.url or "")
                except Exception:
                    current_url = ""
                if target_host and target_host == (urlsplit(current_url).hostname or "").casefold():
                    logger.info("Navigation raised after reaching the target host: %s", current_url)
                    return
                await asyncio.sleep(1)

        raise last_error or RuntimeError(f"Could not navigate to {target}")

    async def _ensure_page(self, handle: _BrowserHandle) -> Any:
        try:
            if handle.page is not None and not handle.page.is_closed():
                return handle.page
        except Exception:
            pass
        for _ in range(5):
            context = handle.context
            if context is not None:
                try:
                    pages = [page for page in context.pages if not page.is_closed()]
                    if pages:
                        handle.page = pages[-1]
                        return handle.page
                    handle.page = await context.new_page()
                    handle.page.set_default_navigation_timeout(self._navigation_timeout_ms)
                    return handle.page
                except Exception:
                    pass
            await asyncio.sleep(0.6)
        return handle.page

    async def _relaunch(self, handle: _BrowserHandle) -> None:
        had_capture = handle.capture_task is not None
        if handle.capture_task:
            handle.capture_task.cancel()
            await asyncio.gather(handle.capture_task, return_exceptions=True)
            handle.capture_task = None
        await self._close_engine(handle)
        await asyncio.sleep(0.5)
        await self._launch(handle)
        if had_capture:
            handle.capture_task = asyncio.create_task(
                self._capture_loop(handle.session.id, handle),
                name=f"frame-capture-{handle.session.id}",
            )

    async def _close_handle(self, handle: _BrowserHandle, *, delete_profile: bool) -> None:
        if handle.capture_task:
            handle.capture_task.cancel()
            await asyncio.gather(handle.capture_task, return_exceptions=True)
            handle.capture_task = None
        await self._close_engine(handle)
        if delete_profile:
            await asyncio.to_thread(shutil.rmtree, handle.profile_dir, True)

    async def _close_engine(self, handle: _BrowserHandle) -> None:
        manager, context = handle.manager, handle.context
        token = getattr(manager, "_session_token", None) if manager else None
        if handle.hwnd is not None:
            with _hwnd_lock:
                _claimed_hwnds.discard(handle.hwnd)
        handle.manager = handle.context = handle.page = None
        handle.hwnd = None
        if context is not None:
            try:
                await asyncio.wait_for(context.close(), timeout=10)
            except Exception:
                pass
        if manager is not None:
            try:
                await asyncio.wait_for(manager.__aexit__(None, None, None), timeout=10)
            except Exception:
                pass
        if token:
            await asyncio.to_thread(_reap_session_tree, token)

    async def _capture_loop(self, session_id: str, handle: _BrowserHandle) -> None:
        try:
            loop = asyncio.get_running_loop()
            next_capture_at = loop.time()
            while True:
                frame: bytes | None = None
                if handle.hwnd:
                    from .windows_capture import capture_hwnd_jpeg

                    frame = await asyncio.to_thread(
                        capture_hwnd_jpeg,
                        handle.hwnd,
                        self._stream_max_width,
                        self._jpeg_quality,
                    )
                    if frame is None:
                        await self._recover_hwnd(handle)
                elif handle.session.mode is BrowserMode.VISIBLE:
                    await self._recover_hwnd(handle)

                if frame is None and not handle.hwnd and handle.page is not None:
                    try:
                        frame = await handle.page.screenshot(
                            type="jpeg",
                            quality=self._jpeg_quality,
                            animations="allow",
                            caret="hide",
                            timeout=5_000,
                        )
                    except Exception:
                        frame = None
                if frame:
                    await self._frame_publisher.publish_frame(session_id, frame)
                next_capture_at += self._screenshot_interval
                now = loop.time()
                if next_capture_at <= now:
                    # Encoding took longer than the frame budget. Skip missed
                    # slots and continue immediately; never add another full
                    # interval after an already-slow capture.
                    next_capture_at = now
                await asyncio.sleep(max(0.0, next_capture_at - now))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("Live capture stopped for %s: %s", session_id, exc)

    @staticmethod
    def _enum_moz_hwnds() -> set[int]:
        from .windows_capture import enum_moz_hwnds

        return enum_moz_hwnds()

    async def _detect_own_hwnd(self, manager: Any, before_hwnds: set[int]) -> int | None:
        if os.name != "nt" or self._hwnd_detection_timeout <= 0:
            return None
        from .windows_capture import enum_moz_hwnds, find_session_moz_hwnd

        token = getattr(manager, "_session_token", None)
        deadline = time.monotonic() + self._hwnd_detection_timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(0.25)
            if token:
                hwnd = await asyncio.to_thread(find_session_moz_hwnd, token)
                if hwnd:
                    with _hwnd_lock:
                        _claimed_hwnds.add(hwnd)
                    return hwnd
                continue
            current = await asyncio.to_thread(enum_moz_hwnds)
            with _hwnd_lock:
                candidates = current - before_hwnds - _claimed_hwnds
                if candidates:
                    hwnd = max(candidates)
                    _claimed_hwnds.add(hwnd)
                    return hwnd
        return None

    async def _recover_hwnd(self, handle: _BrowserHandle) -> int | None:
        if os.name != "nt" or handle.manager is None:
            return None
        token = getattr(handle.manager, "_session_token", None)
        if not token:
            return handle.hwnd
        from .windows_capture import find_session_moz_hwnd

        hwnd = await asyncio.to_thread(find_session_moz_hwnd, token)
        if not hwnd:
            return None
        if hwnd != handle.hwnd:
            with _hwnd_lock:
                if handle.hwnd is not None:
                    _claimed_hwnds.discard(handle.hwnd)
                _claimed_hwnds.add(hwnd)
            handle.hwnd = hwnd
        return hwnd

    def _validate_extension_packages(self, installed_extensions: list[InstalledExtension]) -> None:
        for item in installed_extensions:
            if item.addon_id != "omocaptcha@gmail.com":
                continue
            try:
                with zipfile.ZipFile(item.xpi_path) as archive:
                    json.loads(archive.read("configs.json").decode("utf-8-sig"))
                    signed = any(
                        name.casefold().startswith("meta-inf/") for name in archive.namelist()
                    )
            except (
                KeyError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                zipfile.BadZipFile,
            ) as exc:
                raise RuntimeError("OmoCaptcha 1.7.7 package/config is invalid") from exc
            if not signed:
                raise RuntimeError("OmoCaptcha XPI signature was not preserved")
            storage_path = (
                item.xpi_path.parents[1] / "browser-extension-data" / item.addon_id / "storage.js"
            )
            try:
                storage = json.loads(storage_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError("OmoCaptcha storage seed is invalid") from exc
            if storage.get("initialized") is not True or (
                self._omocaptcha_api_key and storage.get("api_key") != self._omocaptcha_api_key
            ):
                raise RuntimeError("OmoCaptcha storage has an unexpected API key")

    async def _verify_loaded_extensions(
        self,
        profile_dir: Path,
        installed: list[InstalledExtension],
    ) -> None:
        if not installed:
            return
        registry_path = profile_dir / "extensions.json"
        expected = {item.addon_id: item for item in installed}
        loaded: dict[str, dict[str, Any]] = {}
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                registry = await asyncio.to_thread(
                    lambda: json.loads(registry_path.read_text(encoding="utf-8"))
                )
                loaded = {
                    str(addon.get("id")): addon
                    for addon in registry.get("addons", [])
                    if addon.get("id") in expected
                }
            except (OSError, json.JSONDecodeError):
                loaded = {}
            if len(loaded) == len(expected):
                break
            await asyncio.sleep(0.2)

        for addon_id, item in expected.items():
            state = loaded.get(addon_id)
            if not state:
                raise RuntimeError(f"Firefox did not register extension {addon_id}")
            if (
                not state.get("active")
                or state.get("appDisabled")
                or state.get("userDisabled")
                or str(state.get("version", "")) != item.version
            ):
                raise RuntimeError(f"Firefox did not activate extension {addon_id}")
            if addon_id == "omocaptcha@gmail.com" and int(state.get("signedState") or 0) <= 0:
                raise RuntimeError("Firefox did not accept the OmoCaptcha signature")
            if addon_id == "omocaptcha@gmail.com":
                storage_path = profile_dir / "browser-extension-data" / addon_id / "storage.js"
                try:
                    storage_json = await asyncio.to_thread(
                        storage_path.read_text, encoding="utf-8"
                    )
                    storage = json.loads(storage_json)
                except (OSError, json.JSONDecodeError) as exc:
                    raise RuntimeError("OmoCaptcha storage was not initialized") from exc
                if self._omocaptcha_api_key and (
                    storage.get("api_key") != self._omocaptcha_api_key
                ):
                    raise RuntimeError("OmoCaptcha loaded an unexpected API key")
            logger.info("Extension active: %s@%s", item.addon_id, item.version)
