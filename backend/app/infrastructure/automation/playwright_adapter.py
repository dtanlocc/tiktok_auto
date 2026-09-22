import asyncio
import logging
import random
import time
import re
import shutil
import os
import uuid
import tempfile
import json
import unicodedata
import weakref
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from urllib.parse import urlsplit

# =============================================================================
# CHE DO AN VA STREAM CHI XEM
# -----------------------------------------------------------------------------
# Task nen dung true-headless cua invisible_playwright. Dashboard chi xem bang
# page.screenshot; khong co kenh gui chuot/ban phim nguoc vao Playwright page.
# =============================================================================
from invisible_playwright.async_api import InvisiblePlaywright
from invisible_playwright import (
    merge_faithful_canvas_readback,
    set_input_files_native,
)
from app.domain.ports.browser import IBrowserService
from app.core.config import settings
from app.infrastructure.automation.configured_extensions import (
    configured_extension_builder,
    validate_configured_extensions,
)
from app.core.exceptions import (
    AccountBannedException,
    AuthenticationPageNotReady,
    StudioReauthenticationRequired,
)
from app.core.tiktok_urls import ensure_tiktok_english_url
from app.infrastructure.automation.extension_profile_builder import (
    ExtensionProfileBuilder,
    InstalledExtension,
    firefox_prefs_for_extensions,
)
from app.use_cases.upload.caption_hashtags import (
    choose_stable_hashtag_suggestion,
    hashtag_query_candidates,
)

logger = logging.getLogger("PlaywrightAdapter")


# Firefox/Juggler occasionally loses its launch pipe when two sessions start at
# exactly the same time.  Serialize only the short startup/cleanup phase; once
# a context is ready, all account sessions still run concurrently.
_BROWSER_LAUNCH_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
    weakref.WeakKeyDictionary()
)


def _browser_launch_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _BROWSER_LAUNCH_LOCKS.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _BROWSER_LAUNCH_LOCKS[loop] = lock
    return lock


_PLAYWRIGHT_COOKIE_FIELDS = {
    "name",
    "value",
    "url",
    "domain",
    "path",
    "expires",
    "httpOnly",
    "secure",
    "sameSite",
}


def _sanitize_browser_cookies(cookies: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drop export metadata and repair cookies before browser import.

    Firefox does not reliably send a cookie declared as ``SameSite=None``
    without ``Secure``. A cookie imported from a plain ``name=value`` file
    starts without attributes, but ``context.cookies()`` later serializes that
    default as ``sameSite=None, secure=false``. Persisting that snapshot after
    an upload made the current tab stay logged in while the next fresh browser
    silently rejected the restored TikTok session.
    """
    deduped: Dict[tuple, Dict[str, Any]] = {}
    for raw in cookies or []:
        if not isinstance(raw, dict) or not raw.get("name"):
            continue
        cookie = {
            key: value
            for key, value in raw.items()
            if key in _PLAYWRIGHT_COOKIE_FIELDS and value is not None
        }
        same_site = cookie.get("sameSite")
        if same_site not in {None, "Strict", "Lax", "None"}:
            cookie.pop("sameSite", None)
        # Playwright exports session cookies with ``expires=-1``. Feeding that
        # value back into Firefox does not recreate a session cookie: Firefox
        # treats it as an already-expired Unix timestamp and silently drops it
        # during add_cookies(). Omit non-positive/invalid expiry values so the
        # cookie is imported as a real browser-session cookie again.
        if "expires" in cookie:
            try:
                expires = float(cookie["expires"])
            except (TypeError, ValueError):
                cookie.pop("expires", None)
            else:
                if expires <= 0:
                    cookie.pop("expires", None)
                else:
                    cookie["expires"] = expires
        domain = str(cookie.get("domain") or "").lstrip(".").casefold()
        is_tiktok_cookie = domain == "tiktok.com" or domain.endswith(".tiktok.com")
        if is_tiktok_cookie and (
            cookie.get("sameSite") == "None"
            or str(cookie.get("name") or "") in {
                "sessionid",
                "sessionid_ss",
                "sid_guard",
                "sid_tt",
                "uid_tt",
                "uid_tt_ss",
            }
        ):
            cookie["secure"] = True
        key = (
            cookie.get("name"),
            cookie.get("domain", ""),
            cookie.get("path", "/"),
        )
        deduped[key] = cookie
    return list(deduped.values())


async def _locator_has_visible(locator, max_items: int = 20) -> bool:
    """Require a rendered match; hidden SPA templates are not UI evidence."""
    try:
        count = min(await locator.count(), max_items)
        for index in range(count):
            item = locator.nth(index) if hasattr(locator, "nth") else locator.first
            if await item.is_visible():
                return True
    except Exception:
        return False
    return False


def _normalize_caption_text(value: str) -> str:
    """Normalize visible Draft.js text without its invisible entity markers."""
    without_format_markers = "".join(
        character
        for character in (value or "")
        if unicodedata.category(character) != "Cf"
    )
    return unicodedata.normalize(
        "NFKC", " ".join(without_format_markers.split())
    ).strip()


def _foryou_state_ready(state: Dict[str, Any], network_idle: bool) -> bool:
    """Return True only for a fully rendered, signed-in For You observation."""
    # TikTok's virtualized infinite feed commonly keeps one or two in-viewport
    # skeleton slots mounted below already playable posts. They are prefetch
    # placeholders, not a blocking page loader. Network quiet + decoded media +
    # five sustained observations in ``prepare_foryou_home`` are the guard; a
    # small residual count must not turn a healthy feed into a 90-second timeout.
    residual_busy = int(state.get("busy") or 0)
    return bool(
        network_idle
        and state.get("ready") == "complete"
        and state.get("loggedIn")
        and not state.get("login")
        and int(state.get("feedItems") or 0) > 0
        and int(state.get("mediaReady") or 0) > 0
        and int(state.get("pendingImages") or 0) == 0
        and residual_busy <= 2
        and state.get("fontsLoaded")
    )


#: Per-query budget while scanning Studio Posts. It was 0.35s, which only made
#: sense while the whole verification lasted five seconds: on a loaded Studio
#: page a single `locator.count()` can take longer than that, and the timeout
#: was swallowed by the loop's `except Exception: pass` - so a post that WAS
#: on screen could be missed and reported as swallowed.
_STUDIO_OP_TIMEOUT = 2.0

#: How long a For You page verified by ``check_login_status`` may be reused by
#: the upload gate instead of being loaded again. Long enough to cover the hand
#: -off, short enough that a page left sitting is reloaded rather than trusted.
_FORYOU_REUSE_SECONDS = 25.0

#: Hosts that serve the script bundles and static assets the SPA needs. The
#: document itself comes from ``www.tiktok.com`` and can arrive perfectly while
#: every one of these is refused.
_TIKTOK_ASSET_HOST_MARKS = ("tiktokcdn", "ibytedtos", "byteoversea")

#: How many refused asset requests are enough to say the page will never paint.
#: A healthy load refuses none; the measured broken case refused 69 in 20s.
_BLOCKED_ASSET_VERDICT = 5


def _is_tiktok_asset_host(url: str) -> bool:
    try:
        host = urlsplit(url).netloc.casefold()
    except Exception:
        return False
    return any(mark in host for mark in _TIKTOK_ASSET_HOST_MARKS)


def _auth_shell_state_ready(state: Dict[str, Any]) -> bool:
    """Require TikTok's document and navigation shell to finish rendering.

    This is intentionally lighter than the upload gate because a logged-out
    page has no usable For You media. It is still strict enough that a temporary
    guest navbar shown during SPA hydration cannot invalidate a live cookie.

    ``textLen`` is part of "rendered" and not a nicety. ``rootReady`` only asks
    whether ``document.body`` has a child, which a page that rendered NOTHING
    still satisfies - a stylesheet link and an empty mount div are children.
    Behind a proxy that accepts the connection and then delivers no content,
    TikTok reached ``readyState=complete`` with ``busy=0`` on a blank body:
    the shell was declared settled, neither the Log in control nor the
    signed-in marker could exist to be found, and the caller spent 45s
    concluding nothing before reporting an unstable page. Measured on the
    'reg web' batch, 2026-09-16: 34 accounts, all on one broken proxy, zero
    successes ever, every one of them this shape.
    """
    return bool(
        state.get("ready") == "complete"
        and state.get("rootReady")
        and state.get("fontsLoaded")
        and int(state.get("busy") or 0) <= 2
        and int(state.get("textLen") or 0) > 0
    )


def _classify_distribution_text(value: str) -> str:
    """Classify only explicit TikTok review/FYF labels, never infer from views."""
    text = " ".join((value or "").split()).casefold()
    if re.search(
        r"not eligible (?:for|to appear in) (?:the )?for you|"
        r"ineligible (?:for|to appear in) (?:the )?for you|"
        r"kh[oô]ng (?:đủ|du) điều kiện.*(?:dành cho bạn|for you)",
        text,
    ):
        return "FYF_INELIGIBLE"
    if re.search(
        r"under review|being reviewed|processing review|"
        r"đang (?:được )?(?:xem xét|xét duyệt|kiểm duyệt)",
        text,
    ):
        return "UNDER_REVIEW"
    return "PUBLISHED"


def _normalize_studio_post_text(value: str) -> str:
    """Normalize filename/caption text across Studio's punctuation variants."""
    text = unicodedata.normalize("NFKC", value or "").casefold()
    text = re.sub(
        r"\.(?:mp4|mov|m4v|webm|avi|mkv)\s*$",
        "",
        text,
        flags=re.I,
    )
    return " ".join(re.sub(r"[\W_]+", " ", text, flags=re.UNICODE).split())


def _studio_post_text_matches(expected: str, observed: str) -> bool:
    """Accept a full, extended, or visibly truncated Studio post title."""
    wanted = _normalize_studio_post_text(expected)
    page_text = _normalize_studio_post_text(observed)
    if not wanted or not page_text:
        return False
    if wanted in page_text:
        return True

    # Studio commonly renders only the first part of a long caption followed
    # by an ellipsis. Twenty-four normalized characters retain the video's
    # distinctive title for our filename convention while tolerating that UI
    # truncation. Try longer prefixes first to minimise accidental matches.
    for length in (48, 40, 32, 28, 24):
        if len(wanted) >= length and wanted[:length].rstrip() in page_text:
            return True
    return False


def _is_studio_posts_url(value: str) -> bool:
    """Accept current and legacy TikTok routes for the published-content list."""
    normalized = str(value or "").casefold()
    return any(
        route in normalized
        for route in (
            "/tiktokstudio/content",
            "/tiktokstudio/posts",
            "/creator-center/content",
            "/creator-center/manage",
        )
    )


def _studio_posts_body_ready(body_text: str, expected_values: List[str]) -> bool:
    """Recognize the Posts list when SPA/page URL bookkeeping is stale."""
    text = body_text or ""
    lines = {
        " ".join(line.split()).casefold()
        for line in text.splitlines()
        if line.strip()
    }
    has_posts_heading = bool(
        lines.intersection({
            "posts",
            "manage posts",
            "content",
            "bài đăng",
            "nội dung",
        })
    )
    if not has_posts_heading:
        return False
    has_expected_video = any(
        _studio_post_text_matches(expected, text)
        for expected in expected_values
        if expected
    )
    has_posts_columns = bool(
        lines.intersection({
            "views",
            "likes",
            "comments",
            "status",
            "visibility",
            "date posted",
            "posted",
        })
    )
    return has_expected_video or has_posts_columns


def _caption_hashtags(value: str) -> list[str]:
    """Return hashtags already present in TikTok's filename caption.

    TikTok treats every non-whitespace character after ``#`` as part of the
    token. Preserve that exact boundary so positioning the caret does not
    silently change a filename-provided hashtag.
    """
    return [match.group(0) for match in re.finditer(r"#[^\s]+", value or "")]


def _upload_progress_percent(
    raw_value: Optional[str],
    raw_max: Optional[str] = None,
    label: str = "",
) -> Optional[float]:
    """Normalize the progress representations used by Studio to 0..100."""
    percent_match = re.search(r"(?<!\d)(\d{1,3}(?:[.,]\d+)?)\s*%", label or "")
    if percent_match:
        try:
            return max(0.0, min(100.0, float(percent_match.group(1).replace(",", "."))))
        except ValueError:
            pass
    try:
        value = float(raw_value) if raw_value not in (None, "") else None
    except (TypeError, ValueError):
        value = None
    try:
        maximum = float(raw_max) if raw_max not in (None, "") else None
    except (TypeError, ValueError):
        maximum = None
    if value is None:
        return None
    if maximum and maximum > 0:
        value = value / maximum * 100.0
    elif 0.0 <= value <= 1.0:
        value *= 100.0
    return max(0.0, min(100.0, value))


def _video_upload_finished(
    state: Dict[str, Any],
    *,
    reached_high: bool = False,
    reached_100: bool = False,
) -> bool:
    """Return whether Studio has finished the file-upload lifecycle.

    Some Studio builds remove their short-lived progress node before a 500 ms
    poll observes it. Once the generated video preview exposes ``Edit cover``,
    no progress node remains and no uploading copy is visible, the file is
    already available to the publish editor. Content checks can still run
    independently and are deliberately not treated as file-upload progress.
    """
    no_upload_activity = not state.get("has_progress") and not state.get("uploading")
    return bool(
        reached_100
        or state.get("complete")
        or (no_upload_activity and (reached_high or state.get("preview_ready")))
    )

# =============================================================================
# THEO DOI CUA SO (HWND) DE STREAM BANG PrintWindow
# =============================================================================
# Moi adapter nhan cua so MozillaWindowClass co process mang DUNG session-token
# cua invisible_playwright. _claimed_hwnds chi la lop bao ve phu, khong con dung
# phep doan "cua so nao xuat hien sau" khi nhieu browser khoi dong cung luc.
import threading as _threading
_claimed_hwnds: set = set()
_hwnd_lock = _threading.Lock()

def _reap_session_tree(token) -> int:
    """Giet DUNG cay tien trinh cua 1 phien, nhan dien bang SessionToken cua
    invisible_playwright (moi process cua phien mang bien moi truong
    INVPW_SESSION_TOKEN = token do).

    Dung thay cho cach cu "chup snapshot PID roi giet moi firefox.exe la": khop
    DUONG theo token nen chay da luong khong bao gio dung nham phien khac.
    Tra ve so tien trinh da gui lenh dung. Chay trong thread (psutil la blocking).
    """
    try:
        from invisible_core.process import find_processes, terminate
    except Exception:
        return 0
    try:
        procs = find_processes(token)
        if not procs:
            return 0
        n = terminate(procs)
        if n:
            logger.info(f"[CLEANUP] Da don {n} tien trinh cua rieng phien nay (theo token).")
        return n
    except Exception as e:
        logger.debug(f"_reap_session_tree loi: {e}")
        return 0


async def _launch_invisible_context(instance: InvisiblePlaywright, timeout: int):
    """Launch one context without overlapping another Firefox/Juggler startup."""
    async with _browser_launch_lock():
        try:
            return await asyncio.wait_for(instance.__aenter__(), timeout=timeout)
        except BaseException:
            # Keep cleanup inside the same gate.  Starting another session while
            # Juggler is still closing the failed pipe is what created orphaned
            # Firefox trees and made the following launch hang as well.
            token = getattr(instance, "_session_token", None)
            try:
                await asyncio.wait_for(instance.__aexit__(None, None, None), timeout=10)
            except BaseException:
                pass
            if token:
                try:
                    await asyncio.to_thread(_reap_session_tree, token)
                except BaseException:
                    pass
            raise


class InvisiblePlaywrightAdapter(IBrowserService):
    def __init__(self):
        self._invisible_pw: Optional[InvisiblePlaywright] = None
        self._browser = None
        self._page = None
        self._temp_profile_path: Optional[str] = None
        #: Refused CDN requests for the tab in ``_blocked_assets_page``.
        self._blocked_assets: Dict[str, int] = {}
        self._blocked_assets_page = None
        #: The @username the signed-in page showed at the last identity check.
        self.last_observed_identity: str = ""
        #: When and where check_login_status last saw a signed-in For You.
        self._foryou_verified_at: Optional[float] = None
        self._foryou_verified_url: Optional[str] = None
        self._extension_profile_builder: Optional[ExtensionProfileBuilder] = None
        self._native_upload_staging_dirs: set[str] = set()
        self._last_native_upload_error: Optional[str] = None
        self._last_attached_media_names: List[str] = []
        # HWND cua so Firefox cua rieng phien nay (dung cho PrintWindow stream).
        self._hwnd: Optional[int] = None
        self._window_visible: bool = False
        #: The private Win32 desktop this session's browser lives on
        #: (invisible_playwright >= 0.24). None = the ordinary desktop.
        self._browser_desktop: Optional[str] = None
        self._launch_headless: bool = True
        self._automation_gate: Optional[asyncio.Event] = None
        self._stream_suspended: bool = False
        self.last_publish_distribution_status: str = "UNKNOWN"
        # True only after TikTok has accepted a publish action far enough to
        # redirect, show an explicit success message, or accept ``Post now``.
        # The upload use case uses this to decide whether a read-only public
        # profile check is safe after Studio itself produces a false negative.
        self.last_publish_acknowledged: bool = False
        # Exact observation which set ``last_publish_acknowledged``. Keep this
        # separate from the boolean because an editor disappearing is weaker
        # evidence than clicking Post now or reaching Studio Posts.
        self.last_publish_ack_source: str = ""
        self.last_publish_diagnostics: List[Dict[str, Any]] = []
        # Machine-readable result for the queue. Duplicate rejection happens
        # before Post now; swallowed means Post now was accepted but the video
        # did not appear on the redirected Studio Posts page.
        self.last_publish_failure_code: str = ""
        self.last_publish_failure_detail: str = ""
        # A successful For You readiness check issues one short-lived ticket.
        # Upload consumes it before navigating to Studio, preventing callers
        # from bypassing the mandatory home-load gate.
        self._foryou_ready_at: Optional[float] = None

    @property
    def stream_suspended(self) -> bool:
        return self._stream_suspended

    def _set_native_dialog_active(self, active: bool) -> None:
        """Pause capture only while this account owns the OS file chooser."""
        self._stream_suspended = bool(active)

    def bind_automation_gate(self, gate: asyncio.Event) -> None:
        self._automation_gate = gate

    async def _wait_automation_gate(self) -> None:
        gate = self._automation_gate
        if gate is None or gate.is_set():
            return
        await gate.wait()

    async def initialize(self, proxy_config: Optional[Dict[str, Any]] = None, seed: Optional[int] = None, force_visible: bool = False) -> None:
        # force_visible=True (che do DEBUG): dua cua so ra HIEN + foreground de user
        # thao tac tay, KHONG day off-screen. Mac dinh False -> theo cau hinh (an off-screen).
        # Luu tham so launch -> co the RE-LAUNCH khi browser CHET giua chung (navigate_to
        # phat hien 'browsingContext undefined' = tab/browser chet -> mo lai).
        self._foryou_ready_at = None
        self._init_proxy_config = proxy_config
        self._init_seed = seed
        self._init_force_visible = force_visible
        try:
            # SUA LOI BAO MAT: Bo loc lam sach Proxy (Proxy Sanitization)
            proxy_opts = None
            if proxy_config and proxy_config.get("server"):
                proxy_opts = {
                    "server": proxy_config.get("server")
                }
                if proxy_config.get("username"):
                    proxy_opts["username"] = proxy_config.get("username")
                if proxy_config.get("password"):
                    proxy_opts["password"] = proxy_config.get("password")

            # QUAN TRONG: tao profile tam O NGOAI thu muc project (trong %TEMP%),
            # KHONG dat trong ./profiles/ nua. Ly do: uvicorn --reload quet de quy
            # thu muc project tim file .py; profile tam 147MB (hang nghin file) tao
            # roi xoa lien tuc khi mo/dong browser khien watcher sap
            # (FileNotFoundError khi 1 temp bi xoa dung luc dang quet) -> restart/kill
            # server ngay giua chung -> task "chuyen RUNNING roi ERROR ngay". Dat o
            # %TEMP% (ngoai cay project) thi watcher khong bao gio dung toi.
            temp_root = os.path.join(tempfile.gettempdir(), "tiktok_auto_profiles")
            os.makedirs(temp_root, exist_ok=True)
            self._temp_profile_path = os.path.join(temp_root, f"temp_{uuid.uuid4()}")
            # Build profile MOI tu cac extension ngoai. Khong copy profile master,
            # khong mang cookie/cache/site-storage cua phien khac sang phien nay.
            # Cung mot cach dung voi trinh duyet dong bo nhanh (gan key OmoCaptcha).
            extension_builder, excluded_addon_ids = configured_extension_builder()
            self._extension_profile_builder = extension_builder
            installed_extensions = await asyncio.to_thread(
                extension_builder.prepare_profile, self._temp_profile_path
            )
            await asyncio.to_thread(
                self._validate_extension_packages, installed_extensions
            )
            logger.info(
                "[*] Da tao profile moi va gan %d extension: %s",
                len(installed_extensions),
                ", ".join(
                    f"{item.addon_id}@{item.version}" for item in installed_extensions
                ) or "khong co",
            )

            configured_headless = bool(getattr(settings, "BROWSER_HEADLESS", True))
            # Task nen true-headless; force_visible chi dung cho luong debug/login.
            self._launch_headless = bool((not force_visible) and configured_headless)
            true_headless_enabled = bool(
                getattr(settings, "BROWSER_TRUE_HEADLESS", True)
            )
            use_true_headless = bool(self._launch_headless and true_headless_enabled)
            # _resolve_headless() cua invisible_playwright doc bien nay. Giu o cap
            # process; force_visible truyen headless=False nen wrapper tu bo qua.
            os.environ["INVPW_TRUE_HEADLESS"] = "1" if true_headless_enabled else "0"
            hide_offscreen = bool(
                (not force_visible)
                and (not self._launch_headless)
                and getattr(settings, "HIDE_BROWSER_OFFSCREEN", True)
            )

            # =================================================================
            # PREFS BO SUNG (extra_prefs) - duoc invisible_playwright overlay
            # SAU CUNG nen override duoc moi thu (xem prefs.translate_profile_to_prefs).
            # =================================================================
            firefox_prefs = {
                # Keep the profile's pre-start UUID mapping identical to the
                # prefs passed through the invisible_playwright launcher.
                **firefox_prefs_for_extensions(installed_extensions),

                # TikTok Studio ignores ?lang=en on some routes and resolves
                # translations from the browser context instead. Keep the
                # Firefox prefs aligned with InvisiblePlaywright(locale=...) so
                # navigator.language, Intl and Accept-Language all say en-US.
                "intl.accept_languages": "en-US, en",
                "intl.locale.requested": "en-US",

                # =============================================================
                # SUA LOI "TRINH DUYET 2 DUNG TRANG KHI BI CUA SO 1 CHE":
                # Mac dinh tren Windows, Firefox bat occlusion tracking -> khi 1
                # cua so bi cua so khac che (chay da luong nhieu cua so chong len
                # nhau), no coi cua so do la "bi che khuat" va NGUNG VE/compositor
                # -> man hinh trang, treo khong chay tiep. invisible_playwright chi
                # tat pref nay o CHE DO AN (headless cloak), con che do HIEN thi
                # khong -> phai tu tat o day de moi cua so van ve/chay binh thuong
                # du bi che. (Nguon: _headless.py: "window_occlusion_tracking is
                # disabled so a hidden window keeps painting.")
                "widget.windows.window_occlusion_tracking.enabled": False,

                # LUU Y: TUNG thu ep software-render (layers.gpu-process.enabled=False,
                # gfx.webrender.software=True, ...) de sua GPU crash luc debug tren
                # RDP mat display. NHUNG sau khi cai lai firefox + reboot, software-
                # render lai gay loi khac: cua so OFF-SCREEN khong duoc VE -> phai
                # "re chuot vao icon de active" moi len + firefox de crash
                # ("Connection closed while reading from the driver"). => BO software-
                # render, de firefox dung hardware/compositor mac dinh (cua so an van
                # ve binh thuong nho pref occlusion o tren).

                # =============================================================
                # CHONG "PROFILE KHONG MO DUOC" / KET O MAN "RESTORE SESSION":
                # Neu profile bi dong khong sach (mo tay, bi kill, crash) -> lan
                # sau Firefox co the hien man khoi phuc phien / bao profile dang
                # dung -> treo khong chay tiep. Cac pref nay tat han co che do:
                # KHONG hoi khoi phuc, KHONG dem so lan crash, bo qua session cu.
                # (Doc invisible_playwright canh bao: mo profile bang browser khac
                #  khi automation dang dung se lam HONG profile - phong ngua o day.)
                # =============================================================
                "browser.sessionstore.resume_from_crash": False,
                "toolkit.startup.max_resumed_crashes": -1,
                "browser.sessionstore.max_resumed_crashes": 0,
                "browser.startup.page": 0,               # khong mo tab phien truoc
                "browser.sessionstore.resume_session_once": False,

                # =============================================================
                # FIX GOC "TRINH DUYET MO LEN NHUNG KHONG CHAY" (13/08/2026)
                # Log firefox luc treo:
                #   Juggler listening to the pipe
                #   [FrameTree] removeListeners(_eventListeners) failed
                #               (half-destroyed webProgress)     <-- HONG O DAY
                #   ... roi treo den timeout 180s / goto bao browsingContext undefined
                # => Luc khoi dong, Firefox tu mo/dong cac tab noi bo (first-run,
                #    welcome, newtab, pocket, remote-settings). Tab bi HUY ngay khi
                #    juggler dang gan listener -> FrameTree vo -> browsingContext
                #    khong bao gio dang ky -> treo. Tat het cac thu tu-mo do:
                # =============================================================
                "browser.startup.homepage_override.mstone": "ignore",  # khong mo trang "What's new"
                "browser.startup.firstrunSkipsHomepage": True,
                "browser.aboutwelcome.enabled": False,                 # tat man Welcome
                "browser.newtabpage.enabled": False,                   # newtab = trang trong
                "browser.newtabpage.activity-stream.feeds.topsites": False,
                "browser.newtabpage.activity-stream.feeds.section.topstories": False,
                "extensions.pocket.enabled": False,                    # tat Pocket (spocs 403)
                "datareporting.policy.dataSubmissionEnabled": False,
                "datareporting.healthreport.uploadEnabled": False,
                "toolkit.telemetry.enabled": False,
                "toolkit.telemetry.unified": False,
                "browser.contentblocking.report.hide_vpn_banner": True,
                "browser.discovery.enabled": False,
                "app.normandy.enabled": False,                         # tat thi nghiem tu dong
                "app.shield.optoutstudies.enabled": False,
                "browser.region.network.url": "",                      # bo goi mang luc khoi dong
                "browser.safebrowsing.downloads.remote.enabled": False,

                # Không override process count, BFCache, disk/image/media cache.
                # Nhóm cũ chỉ tiết kiệm ~3% RAM nhưng làm video giật và tạo hành vi
                # khác Firefox stock. Để engine invisible quản lý các bề mặt này.
            }

            # TikTok Studio reads Canvas2D pixels while preparing crops and
            # thumbnails. The default anti-fingerprint substitution would
            # otherwise become visible speckle in the resulting media. Apply
            # this consistently to every product session for the identity.
            firefox_prefs = merge_faithful_canvas_readback(
                firefox_prefs, True
            ) or firefox_prefs

            # =================================================================
            # KHOI CHAY DUNG WRAPPER CU: headless=True => binary self-cloak;
            # force_visible/debug => headed-visible.
            # =================================================================
            from app.infrastructure.streaming.win_capture import enum_moz_hwnds
            # True-headless khong co HWND. Neu tat tuy chon nay thi wrapper quay ve
            # DWM self-cloak va ta van theo doi HWND cho PrintWindow.
            _before_hwnds = set() if use_true_headless else enum_moz_hwnds()
            # (the session's own desktop does not exist yet; this snapshot is
            # only used by the pre-token fallback below)
            # =============================================================
            # invisible-core hien tai + firefox_prefs o tren deu tat Windows
            # occlusion tracking, nen headed-offscreen van tiep tuc render.
            # =============================================================
            self._invisible_pw = InvisiblePlaywright(
                proxy=proxy_opts,
                headless=self._launch_headless,
                humanize=True,
                seed=seed,
                # Language preference is English (US), but geography remains
                # automatic from the real/proxy egress (for example Indonesia).
                locale=getattr(settings, "TIKTOK_WEB_LOCALE", "en-US"),
                timezone="auto",
                profile_dir=self._temp_profile_path,
                extra_prefs=firefox_prefs,
                # TikTok Studio reads Canvas2D pixels while preparing crops and
                # thumbnails. The default anti-fingerprint substitution would
                # otherwise become visible speckle in the resulting media.
                # Keep this stable for every task using this account identity,
                # rather than changing canvas behaviour only on upload pages.
            )
            self._invisible_pw.set_firefox_extensions(
                item.xpi_path for item in installed_extensions
            )
            self._invisible_pw.set_firefox_extension_exclusions(excluded_addon_ids)
            # =============================================================
            # LUOI AN TOAN (khong phai cach chua chinh).
            # Phong truong hop hi huu launch bi treo (vd may qua tai):
            # cat o BROWSER_LAUNCH_TIMEOUT giay, giet tien trinh firefox cua lan do
            # (tranh ro ri) roi mo lai 1 lan. KHONG con la co che "hen xui" nua.
            # =============================================================
            _lt = max(15, int(getattr(settings, "BROWSER_LAUNCH_TIMEOUT", 25)))
            _tries = max(1, int(getattr(settings, "BROWSER_LAUNCH_MAX_TRIES", 2)))
            self._browser = None
            _err = None
            _att = 0
            _max_tries = _tries
            while _att < _max_tries:
                _att += 1
                _t0 = time.monotonic()
                try:
                    self._browser = await _launch_invisible_context(
                        self._invisible_pw, _lt
                    )
                    logger.info(f"[LAUNCH] OK sau {time.monotonic()-_t0:.1f}s (lan {_att}/{_max_tries}).")
                    break
                except Exception as e_l:
                    _err = e_l
                    _kind = "treo qua %ss" % _lt if isinstance(e_l, asyncio.TimeoutError) else str(e_l)[:70]
                    # ⛔ A PROXY THAT DID NOT ANSWER THE EGRESS-IP LOOKUP IS
                    # SLOW, NOT BROKEN. The lookup has a 15s budget; through
                    # 209.145.57.39 it ran out on the 1st launch and passed on
                    # the 2nd (adanavid168), and ran out twice for
                    # mo91trow4_spau, failing the task (2026-09-18). One more
                    # try, for this cause only.
                    if "egress ip" in str(e_l).lower() and _max_tries == _tries:
                        _max_tries += 1
                    logger.warning(f"[LAUNCH] Lan {_att}/{_max_tries} hong ({_kind}) -> don + mo lai.")
                    # _launch_invisible_context already closes/reaps the failed
                    # attempt before releasing the global startup gate.
                    if _att < _max_tries:
                        await asyncio.sleep(1.5)
                        self._invisible_pw = InvisiblePlaywright(
                            proxy=proxy_opts,
                            headless=self._launch_headless,
                            humanize=True,
                            seed=seed,
                            locale=getattr(settings, "TIKTOK_WEB_LOCALE", "en-US"),
                            timezone="auto",
                            profile_dir=self._temp_profile_path,
                            extra_prefs=firefox_prefs,
                        )
                        self._invisible_pw.set_firefox_extensions(
                            item.xpi_path for item in installed_extensions
                        )
                        self._invisible_pw.set_firefox_extension_exclusions(
                            excluded_addon_ids
                        )
            if self._browser is None:
                raise _err or RuntimeError(f"Khong mo duoc trinh duyet sau {_max_tries} lan.")

            # AN CUA SO NGAY SAU __aenter__, TRUOC moi thao tac page. Ban vua roi
            # tao/doi tab truoc khi an nen cua so lo ra lau va co the can focus.
            # ⛔ FROM 0.24 THE WINDOW IS NOT ON THIS DESKTOP. `headless=True`
            # creates a private Win32 desktop per session and builds the browser
            # there, so EnumWindows/PrintWindow from an ordinary thread see
            # nothing at all (measured 2026-09-22: 0 windows). The name of that
            # desktop is the only thing needed to look again from a thread
            # attached to it. On 0.16.2 there is no such object and this stays
            # None, which every helper reads as "the window is on this desktop".
            self._browser_desktop = getattr(
                getattr(self._invisible_pw, "_virtual_display", None), "name", None
            )
            if self._browser_desktop:
                logger.info(
                    "[WINDOW] Browser song tren desktop rieng %s.",
                    self._browser_desktop,
                )
            self._hwnd = None
            self._window_visible = False
            try:
                self._hwnd = (
                    None
                    if use_true_headless
                    else await self._detect_own_hwnd(_before_hwnds)
                )
                if self._hwnd:
                    from app.infrastructure.streaming.win_capture import (
                        move_window_offscreen,
                        show_window_foreground,
                    )
                    try:
                        if self._launch_headless or self._browser_desktop:
                            # Nothing to hide: the binary cloaks itself (<=0.23)
                            # or the whole desktop is private (>=0.24). The HWND
                            # is kept only so PrintWindow can stream it.
                            self._window_visible = False
                        elif hide_offscreen:
                            ok = await asyncio.wait_for(
                                asyncio.to_thread(
                                    move_window_offscreen, self._hwnd, -3200, -3200,
                                    self._browser_desktop,
                                ), timeout=5
                            )
                            self._window_visible = not bool(ok)
                        else:
                            ok = await asyncio.wait_for(
                                asyncio.to_thread(
                                    show_window_foreground, self._hwnd,
                                    self._browser_desktop,
                                ), timeout=5
                            )
                            self._window_visible = bool(ok)
                    except Exception:
                        logger.warning("[WINDOW] Khong dat duoc trang thai cua so (bo qua).")
                    logger.info(
                        f"[WINDOW] HWND={self._hwnd} "
                        f"{'dang HIEN' if self._window_visible else 'dang CLOAK/OFF-SCREEN'}."
                    )
            except Exception as e_v:
                logger.warning(f"[WINDOW] Loi: {str(e_v)}")

            # Tao page sach sau khi persistent context san sang. Tab khoi dong cua
            # patched Firefox co luc chua co browsingContext; tai su dung no se lam
            # task dung ngay lan goto dau tien.
            _default_pages = list(getattr(self._browser, "pages", None) or [])
            self._page = await self._browser.new_page()
            locale_state = await self._page.evaluate(
                "() => ({ language: navigator.language, languages: navigator.languages })"
            )
            if not str(locale_state.get("language") or "").casefold().startswith("en"):
                raise RuntimeError(
                    f"TikTok browser locale was not applied: {locale_state!r}"
                )
            logger.info(
                "[LOCALE] TikTok browser fixed to %s (%s).",
                locale_state.get("language"),
                locale_state.get("languages"),
            )
            for _old_page in _default_pages:
                if _old_page is self._page:
                    continue
                try:
                    await _old_page.close()
                except Exception:
                    pass
            await self._verify_loaded_extensions(installed_extensions)
            # Firefox can replace its startup top-level HWND when the initial
            # tab is closed. Re-resolve after the clean page exists so the
            # streamer starts with the stable, token-owned window.
            if not use_true_headless:
                await self.recover_stream_hwnd()
            logger.info("[*] Khoi tao tab moi sach.")

            _display_mode = (
                ("true-headless" if use_true_headless else "headed-cloaked")
                if self._launch_headless
                else ("headed-visible" if self._window_visible else "headed-offscreen")
            )
            logger.info(f"[+] Khoi tao browser session ({_display_mode}). Seed: {seed} | Proxy: {proxy_opts.get('server') if proxy_opts else 'Direct NET'}")
        except Exception as e:
            logger.error(f"[-] Khong the khoi tao trinh duyet: {str(e)}")
            await self.close()
            raise e

    @staticmethod
    def _validate_extension_packages(
        installed_extensions: List[InstalledExtension],
    ) -> None:
        """Validate sensitive bundled config without changing signed XPIs."""
        validate_configured_extensions(installed_extensions)

    async def _verify_loaded_extensions(
        self, installed_extensions: List[InstalledExtension]
    ) -> None:
        """Verify Firefox activated each requested extension before use.

        Playwright's Firefox transport can hang when navigating directly to a
        ``moz-extension://`` JSON resource.  Firefox's own extension registry
        is the authoritative source and also exposes signature/disabled state.
        """

        if not installed_extensions:
            return
        registry_path = Path(self._temp_profile_path or "") / "extensions.json"
        expected = {item.addon_id: item for item in installed_extensions}
        loaded: Dict[str, Dict[str, Any]] = {}
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
            if addon_id == "omocaptcha@gmail.com" and int(
                state.get("signedState") or 0
            ) <= 0:
                raise RuntimeError("Firefox did not accept the OmoCaptcha signature")
            if addon_id == "omocaptcha@gmail.com":
                storage_path = (
                    Path(self._temp_profile_path or "")
                    / "browser-extension-data"
                    / addon_id
                    / "storage.js"
                )
                try:
                    storage = await asyncio.to_thread(
                        lambda: json.loads(storage_path.read_text(encoding="utf-8"))
                    )
                except (OSError, json.JSONDecodeError) as exc:
                    raise RuntimeError("OmoCaptcha storage was not initialized") from exc
                expected_key = getattr(settings, "OMOCAPTCHA_KEY", "")
                if expected_key and storage.get("api_key") != expected_key:
                    raise RuntimeError("OmoCaptcha loaded an unexpected API key")
            logger.info(
                "[EXTENSION] Active: %s@%s (signed/configuration verified).",
                item.addon_id,
                item.version,
            )

    async def _detect_own_hwnd(self, before_hwnds: set) -> Optional[int]:
        """Find this session's Firefox HWND by its process session-token."""
        if os.name != "nt":
            return None
        try:
            from app.infrastructure.streaming.win_capture import (
                enum_moz_hwnds,
                find_session_moz_hwnd,
            )
        except Exception:
            return None
        session_token = getattr(self._invisible_pw, "_session_token", None)
        # Poll toi da ~6s cho cua so hien ra (browser vua launch).
        for _ in range(24):
            await asyncio.sleep(0.25)
            if session_token:
                try:
                    owned_hwnd = await asyncio.to_thread(
                        find_session_moz_hwnd, session_token, self._browser_desktop
                    )
                except Exception:
                    owned_hwnd = None
                if owned_hwnd:
                    with _hwnd_lock:
                        _claimed_hwnds.add(owned_hwnd)
                    logger.info(
                        "[HWND] Detected token-owned window HWND=%s for this session.",
                        owned_hwnd,
                    )
                    return owned_hwnd
                # A minted token is authoritative. Never guess another
                # concurrently launching session's window.
                continue
            try:
                now = enum_moz_hwnds(self._browser_desktop)
            except Exception:
                continue
            with _hwnd_lock:
                candidates = now - before_hwnds - _claimed_hwnds
                if candidates:
                    hwnd = max(candidates)  # cua so moi nhat
                    _claimed_hwnds.add(hwnd)
                    logger.info(f"[HWND] Detected window HWND={hwnd} for this session.")
                    return hwnd
        logger.warning("[HWND] Could NOT detect this session's window within timeout.")
        return None

    async def recover_stream_hwnd(self) -> Optional[int]:
        """Reacquire only this session's HWND without touching Playwright.

        The stream calls this after an OS capture failure.  It is safe during
        captcha/upload waits because the lookup runs in a worker thread and is
        keyed by the browser process token rather than the Playwright channel.
        """
        if os.name != "nt" or self._invisible_pw is None:
            return None
        session_token = getattr(self._invisible_pw, "_session_token", None)
        if not session_token:
            return None
        try:
            from app.infrastructure.streaming.win_capture import find_session_moz_hwnd

            owned_hwnd = await asyncio.to_thread(
                find_session_moz_hwnd, session_token, self._browser_desktop
            )
        except Exception:
            return None
        if not owned_hwnd:
            return None
        old_hwnd = self._hwnd
        if old_hwnd != owned_hwnd:
            with _hwnd_lock:
                if old_hwnd is not None:
                    _claimed_hwnds.discard(old_hwnd)
                _claimed_hwnds.add(owned_hwnd)
            self._hwnd = owned_hwnd
            logger.info("[HWND] Stream recovered token-owned HWND=%s.", owned_hwnd)
        return owned_hwnd

    @property
    def window_is_visible(self) -> bool:
        return bool(self._window_visible and self._hwnd)

    async def show_window(self) -> bool:
        """Restore cua so cua session hien tai cho che do debug truc tiep.

        Khong tao browser/page moi, vi vay URL, cookies, local storage va moi state
        trong tab deu duoc giu nguyen. True-headless khong co HWND va se tra False.
        """
        if self._launch_headless or not self._hwnd:
            return False
        from app.infrastructure.streaming.win_capture import show_window_foreground

        try:
            shown = await asyncio.wait_for(
                asyncio.to_thread(
                    show_window_foreground, self._hwnd, self._browser_desktop
                ), timeout=5
            )
        except Exception:
            shown = False
        self._window_visible = bool(shown)
        return self._window_visible

    async def hide_window(self) -> bool:
        """Move the current headed window back off-screen without minimizing it."""
        if self._launch_headless or not self._hwnd:
            return False
        from app.infrastructure.streaming.win_capture import move_window_offscreen

        try:
            hidden = await asyncio.wait_for(
                asyncio.to_thread(
                    move_window_offscreen, self._hwnd, -3200, -3200,
                    self._browser_desktop,
                ), timeout=5
            )
        except Exception:
            hidden = False
        if hidden:
            self._window_visible = False
        return bool(hidden)

    async def _ensure_page(self):
        """Tra ve 1 page HOP LE dang mo. Neu self._page da bi dong/detach (nguyen
        nhan loi 'browsingContext is undefined' khi goto), lay lai page moi nhat tu
        context (hoac tao page moi). Fix truong hop browser mo len nhung goto tren
        page cu bi treo/loi."""
        try:
            if self._page and not self._page.is_closed():
                return self._page
        except Exception:
            pass
        # Tim context: uu tien context cua page cu; neu khong co, lay tu browser
        # (persistent context: self._browser CHINH LA context, co .pages truc tiep;
        #  browser thuong: co .contexts[0]). Thu ca 2 + cho toi 5 lan cho page ready.
        for _ in range(5):
            ctx = None
            try:
                ctx = self._page.context if self._page else None
            except Exception:
                ctx = None
            if ctx is None and self._browser is not None:
                if getattr(self._browser, "contexts", None):
                    ctx = self._browser.contexts[0]
                elif hasattr(self._browser, "pages"):
                    ctx = self._browser          # persistent context = chinh no
            if ctx is not None:
                try:
                    pages = [p for p in ctx.pages if not p.is_closed()]
                    if pages:
                        self._page = pages[-1]
                        return self._page
                    self._page = await ctx.new_page()
                    return self._page
                except Exception:
                    pass
            await asyncio.sleep(0.6)
        return self._page

    async def navigate_to(self, url: str) -> None:
        await self._wait_automation_gate()
        if not self._page and not self._browser:
            raise RuntimeError("Trinh duyet chua duoc khoi tao.")

        from urllib.parse import urlparse
        url = ensure_tiktok_english_url(url)
        target_host = urlparse(url).netloc.split(":")[0]
        last_err = None

        # THU TOI 4 LAN. Dung wait_until="commit" (GIONG go tay URL: commit xong tra
        # ve NGAY, KHONG cho load event cua SPA nang nhu TikTok). Neu context CHET
        # ('browsingContext undefined' / 'Connection closed' = tab/browser chet) va
        # doi page van khong cuu duoc -> RE-LAUNCH ca browser 1 lan roi thu tiep.
        relaunched = False
        for attempt in range(4):
            page = await self._ensure_page()
            # Arm the refused-asset listener BEFORE the navigation, and start
            # it from zero: the requests that decide whether this page can
            # paint all fail during the goto, long before anyone asks how the
            # login check went. Arming it inside that check counted nothing.
            self._blocked_asset_counter().clear()
            if page is None:
                last_err = RuntimeError("Khong lay duoc page de dieu huong.")
                if not relaunched and hasattr(self, "_init_seed"):
                    relaunched = True
                    logger.warning("[!] Khong co page -> RE-LAUNCH lai browser...")
                    try: await self._relaunch()
                    except Exception as e_re: logger.warning(f"[!] re-launch loi: {e_re}")
                    continue
                await asyncio.sleep(1)
                continue
            try:
                logger.info(f"[*] Dieu huong toi {url} (lan {attempt+1}, wait=commit)...")
                await page.goto(url, wait_until="commit", timeout=30000)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=12000)
                except Exception:
                    pass
                return
            except Exception as e:
                last_err = e
                msg = str(e)
                logger.warning(f"[!] goto loi (lan {attempt+1}): {msg[:90]}")
                dead = ("browsingContext" in msg or "Connection closed" in msg
                        or "closed" in msg or "Target page" in msg or "crash" in msg.lower())
                if dead:
                    # Lan dau doi page; van chet & chua re-launch -> MO LAI ca browser.
                    if not relaunched and hasattr(self, "_init_seed") and attempt >= 1:
                        relaunched = True
                        logger.warning("[!] Context CHET -> RE-LAUNCH lai browser (cung proxy/seed)...")
                        try: await self._relaunch()
                        except Exception as e_re: logger.warning(f"[!] re-launch loi: {e_re}")
                        continue
                    self._page = None
                    await asyncio.sleep(1.0)
                    continue
                # Loi khac (vd timeout commit): neu THUC TE da toi dung host -> coi nhu OK.
                try:
                    cur = page.url or ""
                except Exception:
                    cur = ""
                if target_host and target_host in cur:
                    logger.info(f"[*] goto bao loi nhung da toi {cur} -> coi nhu OK.")
                    return
                await asyncio.sleep(1.0)

        logger.error(f"[-] That bai dieu huong toi {url} sau nhieu lan: {last_err}")
        raise last_err if last_err else RuntimeError(f"Khong dieu huong duoc toi {url}")

    async def _relaunch(self) -> None:
        """Dong browser hien tai + MO LAI voi cung proxy/seed (khi tab/browser chet
        giua chung). Dung 1 lan trong navigate_to de cuu phien thay vi bao loi ngay."""
        try:
            await self.close()
        except Exception:
            pass
        await self.initialize(
            proxy_config=getattr(self, "_init_proxy_config", None),
            seed=getattr(self, "_init_seed", None),
            force_visible=getattr(self, "_init_force_visible", False),
        )

    async def inject_cookies(self, cookies: List[Dict[str, Any]]) -> None:
        await self._wait_automation_gate()
        if not self._browser:
            raise RuntimeError("Trinh duyet chua duoc khoi tao.")

        # KHU TRUNG luc inject (giu ban CUOI cung theo name+domain+path): cookies
        # luu/xuat GIU DAY DU (co the co ten trung nhu msToken bi refresh), nhung
        # add_cookies khong nen nhan 2 cookie trung name+domain+path -> loc o day.
        clean = _sanitize_browser_cookies(cookies)

        contexts = getattr(self._browser, "contexts", [])
        if contexts:
            await contexts[0].add_cookies(clean)
        else:
            await self._browser.add_cookies(clean)

    async def extract_cookies(self) -> List[Dict[str, Any]]:
        await self._wait_automation_gate()
        if not self._browser:
            return []

        contexts = getattr(self._browser, "contexts", [])
        if contexts:
            return _sanitize_browser_cookies(await contexts[0].cookies())
        else:
            return _sanitize_browser_cookies(await self._browser.cookies())

    async def validate_authenticated_identity(self, expected_username: str) -> bool:
        """Require the signed-in nav identity to match the requested account."""
        await self._wait_automation_gate()
        if not self._page:
            return False

        expected = str(expected_username or "").strip().lstrip("@").casefold()
        if not expected:
            return False

        for _ in range(12):
            await self._wait_automation_gate()
            try:
                observed = await self._page.evaluate(
                    r"""() => {
                      const normalize = value => {
                        try { value = decodeURIComponent(String(value || '')); }
                        catch (_) { value = String(value || ''); }
                        return value.trim().replace(/^@/, '').toLowerCase();
                      };
                      const nodes = Array.from(document.querySelectorAll(
                        '[data-e2e="nav-profile"], [data-e2e="profile-icon"]'
                      ));
                      for (const node of nodes) {
                        const anchor = node.matches('a')
                          ? node : (node.closest('a') || node.querySelector('a'));
                        const href = anchor && anchor.getAttribute('href');
                        const match = String(href || '').match(
                          /^\/@([^/?#]+)(?:[/?#]|$)/
                        );
                        if (match && normalize(match[1])) return normalize(match[1]);
                      }

                      const scope = window.__UNIVERSAL_DATA_FOR_REHYDRATION__
                        && window.__UNIVERSAL_DATA_FOR_REHYDRATION__.__DEFAULT_SCOPE__;
                      const context = scope && scope['webapp.app-context'];
                      const username = context && (
                        context.user?.uniqueId
                        || context.userInfo?.user?.uniqueId
                        || context.user?.unique_id
                      );
                      return normalize(username) || null;
                    }"""
                )
                if observed:
                    self.last_observed_identity = str(observed)
                    matched = str(observed).casefold() == expected
                    if matched:
                        logger.info(
                            "[COOKIE] Da xac minh dung identity @%s.",
                            expected_username,
                        )
                    else:
                        logger.warning(
                            "[COOKIE] Identity sai: can @%s nhung browser dang @%s.",
                            expected_username,
                            observed,
                        )
                    return matched
            except Exception as exc:
                logger.debug("[COOKIE] Chua doc duoc identity: %s", exc)
            await asyncio.sleep(0.5)

        logger.warning(
            "[COOKIE] Khong xac minh duoc identity @%s; khong chap nhan cookie login.",
            expected_username,
        )
        return False

    _SESSION_ACCOUNT_JS = r"""async () => {
      if (location.hostname !== 'www.tiktok.com')
        return {state: 'unknown', detail: 'page is not on www.tiktok.com'};
      try {
        // Absolute: TikTok wraps window.fetch to sign calls, and the wrapper
        // rejected a relative path once ("is not a valid URL", 2026-09-18).
        const r = await fetch(location.origin
                              + '/passport/web/account/info/?aid=1459&app_name=tiktok_web&lang=en',
                              {credentials: 'include'});
        const j = await r.json();
        const d = (j && j.data) || {};
        if (String((j && j.message) || '').toLowerCase() === 'success'
            && (d.user_id || d.user_id_str || d.username))
          return {state: 'alive', username: String(d.username || ''),
                  detail: String(d.user_id_str || d.user_id || '')};
        const why = String(d.description || (j && j.message) || ('HTTP ' + r.status));
        if (d.error_code === 13 || /session expired|sign in again|log ?in/i.test(why))
          return {state: 'signed_out', detail: why};
        return {state: 'unknown', detail: why};
      } catch (e) {
        return {state: 'unknown', detail: String(e).slice(0, 160)};
      }
    }"""

    async def read_session_account(self) -> Dict[str, str]:
        """What TikTok's server says about this browser's session.

        ⛔ THE SERVER DECIDES, NOT THE PAINT. A For You whose scripts were
        refused by the proxy, or a Studio tab that bounced through /login,
        looks signed out while TikTok still honours the session - and treating
        that as a dead cookie cleared it and forced an OTP login (the "logged
        out when upload starts" of batch 1k, 2026-09-18). passport account/info
        is answered by www.tiktok.com itself, so it works even when the CDN
        is refused. Returns {"state": "alive"|"signed_out"|"unknown",
        "username", "detail"}.
        """
        await self._wait_automation_gate()
        if not self._page:
            return {"state": "unknown", "username": "", "detail": "no page"}
        try:
            result = await asyncio.wait_for(
                self._page.evaluate(self._SESSION_ACCOUNT_JS), timeout=30
            )
        except Exception as exc:
            return {"state": "unknown", "username": "", "detail": str(exc)[:160]}
        if not isinstance(result, dict):
            return {"state": "unknown", "username": "", "detail": "no answer"}
        return {
            "state": str(result.get("state") or "unknown"),
            "username": str(result.get("username") or "").lstrip("@"),
            "detail": str(result.get("detail") or ""),
        }

    async def sample_egress_ips(self, samples: int = 5) -> List[str]:
        """Public address of `samples` fresh connections on THIS session's route
        (its proxy, or the machine's own network). See egress_stability."""
        from app.infrastructure.automation.egress_stability import (
            proxy_url_from_config,
            sample_egress_ips,
        )

        return await sample_egress_ips(
            proxy_url_from_config(getattr(self, "_init_proxy_config", None)),
            samples=samples,
        )

    async def clear_auth_session(self) -> None:
        """Clear the partial web session before a forced Studio re-login."""
        await self._wait_automation_gate()
        if not self._page:
            raise RuntimeError("Trinh duyet chua khoi tao.")
        try:
            await self._page.evaluate(
                "() => { try { localStorage.clear(); } catch (_) {} "
                "try { sessionStorage.clear(); } catch (_) {} }"
            )
        except Exception:
            pass
        await self._page.context.clear_cookies()

    def _blocked_asset_counter(self) -> Dict[str, int]:
        """Refused CDN requests for the CURRENT tab, by host.

        ⛔ WHY A NETWORK LISTENER AND NOT JUST A TIMER. The document comes from
        `www.tiktok.com`; every script that paints it comes from the CDN. A
        proxy can serve the first and refuse the second, and the page then
        reaches readyState=complete with 317KB of markup, 69 script tags and
        not one character of text - forever, through reloads. Measured
        2026-09-16: 67 refusals of `lf16-tiktok-web.tiktokcdn-us.com` in 20s
        while `www.tiktok.com` answered 200. Waiting does not change that and
        the DOM never says why, so the refusals themselves are the evidence.

        The listener is attached ONCE PER TAB. Attaching it per call would pile
        a new one on the same page for every login check of the session.
        """
        page = self._page
        if page is None:
            return {}
        if getattr(self, "_blocked_assets_page", None) is page:
            return self._blocked_assets
        counter: Dict[str, int] = {}

        def _note(request) -> None:
            try:
                if not _is_tiktok_asset_host(request.url):
                    return
                host = urlsplit(request.url).netloc
                counter[host] = counter.get(host, 0) + 1
            except Exception:
                pass

        try:
            page.on("requestfailed", _note)
        except Exception:
            return {}
        self._blocked_assets_page = page
        self._blocked_assets = counter
        return counter

    async def check_login_status(self) -> bool:
        await self._wait_automation_gate()
        if not self._page:
            raise AuthenticationPageNotReady("Trình duyệt chưa có trang TikTok để xác minh.")

        logger.info("[*] Dang cho TikTok tai day du va on dinh truoc khi xac minh phien...")

        # navigate_to() deliberately returns after DOMContentLoaded so heavy
        # TikTok pages do not block every navigation. Authentication is a place
        # where an early verdict is dangerous, however: wait for the full load
        # event here, then also require several stable render observations.
        try:
            await self._page.wait_for_load_state("load", timeout=30000)
        except Exception as exc:
            logger.warning(
                "[COOKIE] Chua nhan load event; tiep tuc quan sat document: %s",
                str(exc)[:120],
            )

        ban_dialog_locator = self._page.locator(
            '.tux-dialog__content-title:has-text("Your account was banned"), '
            '.tux-dialog__content-title:has-text("banned"), '
            '.tux-dialog__content-title:has-text("cam"), '
            '.tux-dialog__content-message:has-text("submit an appeal"), '
            '.tux-dialog__content-message:has-text("appeal"), '
            '.tux-dialog__content-message:has-text("khang nghi")'
        )

        # Chỉ dùng marker dành riêng cho account đã đăng nhập. TikTok vẫn hiện
        # nút/link Upload cho guest rồi redirect sang /login, nên nav-upload và
        # link Studio tuyệt đối không được xem là bằng chứng đăng nhập.
        profile_link_locator = self._page.locator(
            '[data-e2e="profile-icon"], [data-e2e="nav-profile"], '
            '[data-e2e="messages-icon"], [data-e2e="inbox-icon"], '
            'a[href*="/messages"]'
        )
        login_locator = self._page.locator(
            '[data-e2e="nav-login-button"], button:has-text("Log in"), '
            'button:has-text("Dang nhap"), button:has-text("\u0110\u0103ng nh\u1eadp")'
        )

        visible_login_streak = 0
        visible_account_streak = 0
        stable_shell_streak = 0
        blank_streak = 0
        last_url = ""

        # Counted since the navigation that produced this page, which is where
        # the refusals happen; navigate_to resets it.
        blocked_assets = self._blocked_asset_counter()
        last_state: Dict[str, Any] = {}
        saw_settled_shell = False
        for i in range(45):
            await self._wait_automation_gate()
            try:
                if await ban_dialog_locator.count() > 0 and await ban_dialog_locator.first.is_visible():
                    dialog_title = await ban_dialog_locator.first.inner_text()
                    logger.error(f"[!] PHAT HIEN TAI KHOAN BI BANNED QUA DIALOG: '{dialog_title}'")
                    raise AccountBannedException(f"Tai khoan bi cam vinh vien: {dialog_title}")

                # CHONG FALSE-POSITIVE: khi con modal CAPTCHA (chua giai) thi feed For You
                # mo phia sau co the khien cac dau hieu "da login" khop nham -> CHUA duoc
                # coi la dang nhap. Bo qua vong nay, cho captcha giai xong (hoac timeout).
                if await self.is_captcha_present():
                    stable_shell_streak = 0
                    visible_login_streak = 0
                    visible_account_streak = 0
                    await asyncio.sleep(1)
                    continue

                state = await self._page.evaluate(r"""() => {
                  const visible = el => !!(el && (
                    el.offsetParent !== null || el.getClientRects().length
                  ));
                  const busySelectors = [
                    '[aria-busy="true"]',
                    '[data-e2e="loading"]', '[data-e2e*="skeleton"]',
                    '.TUXLoading', '[class*="Skeleton"]'
                  ];
                  return {
                    ready: document.readyState,
                    rootReady: !!document.body && document.body.childElementCount > 0,
                    fontsLoaded: !document.fonts || document.fonts.status === 'loaded',
                    busy: Array.from(document.querySelectorAll(
                      busySelectors.join(',')
                    )).filter(visible).length,
                    // A body that renders no text at all has not finished,
                    // whatever readyState says.
                    textLen: (document.body && document.body.innerText
                              ? document.body.innerText.trim().length : 0),
                    // How much markup arrived. Separates "React has not
                    // painted yet" (lots of markup, no text) from "nothing
                    // was fetched" (no markup either).
                    htmlLen: (document.body && document.body.innerHTML
                              ? document.body.innerHTML.length : 0),
                    href: location.href
                  };
                }""")
                if not isinstance(state, dict):
                    state = {}
                current_url = str(state.get("href") or self._page.url or "")
                last_state = state
                if current_url and current_url == last_url:
                    stable_shell_streak += 1
                else:
                    last_url = current_url
                    stable_shell_streak = 1
                    visible_login_streak = 0
                    visible_account_streak = 0

                # ⛔ TEXT ALONE DOES NOT MEAN EMPTY. TikTok is a single-page
                # app: `readyState=complete` fires when the document is done,
                # while React has not painted, so a body with no text for the
                # first seconds is the ORDINARY shape of a healthy load. A
                # 6-observation verdict on text alone stopped accounts 8s in,
                # before they had a chance to render, and reported a working
                # proxy as a dead one. Measured on a live batch, 2026-09-16.
                #
                # The document TikTok serves - even mid-hydration - carries its
                # shell and script tags, tens of kilobytes of it. A page that
                # failed to fetch anything has almost no markup at all, so the
                # two cases separate on HTML SIZE, which is present from the
                # first observation, rather than on time.
                blocked_total = sum(blocked_assets.values())
                if (
                    state.get("ready") == "complete"
                    and int(state.get("textLen") or 0) == 0
                    and blocked_total >= _BLOCKED_ASSET_VERDICT
                ):
                    blank_streak += 1
                    # Two observations, because one can land in the instant
                    # between a refusal and the retry that succeeds.
                    if blank_streak >= 2:
                        worst = sorted(
                            blocked_assets.items(), key=lambda kv: -kv[1]
                        )[:2]
                        named = ", ".join(f"{h} x{n}" for h, n in worst)
                        logger.warning(
                            "[-] TikTok khong render duoc: %d request CDN bi tu "
                            "choi sau %ds (%s)",
                            blocked_total,
                            i + 1,
                            named,
                        )
                        raise AuthenticationPageNotReady(
                            "Proxy của account này vào được www.tiktok.com "
                            f"nhưng bị từ chối {blocked_total} request tới CDN "
                            f"({named}) — không tải được JS nên trang không "
                            "render. Cookies không liên quan. Lỗi này đến "
                            "theo đợt: thử lại sau hoặc đổi proxy."
                        )
                else:
                    blank_streak = 0

                shell_settled = bool(
                    stable_shell_streak >= 3 and _auth_shell_state_ready(state)
                )
                if not shell_settled:
                    visible_login_streak = 0
                    visible_account_streak = 0
                    await asyncio.sleep(1)
                    continue
                saw_settled_shell = True

                # Guest HTML can contain hidden profile templates and nested
                # hydration records with isLogin=true for unrelated objects. It
                # can also render a *visible* nav-profile control whose href is
                # merely "/@?lang=en". Therefore a visible Log in control must
                # always take precedence over generic profile/messages markers.
                login_visible = await _locator_has_visible(login_locator)
                on_login_route = "/login" in current_url.casefold()
                if login_visible or on_login_route:
                    visible_login_streak += 1
                    visible_account_streak = 0
                    # A temporary guest navbar commonly survives several
                    # seconds after document.complete. Require eight settled
                    # observations before declaring the cookie invalid.
                    if visible_login_streak >= 8:
                        logger.warning(
                            f"[-] Xac minh THAT BAI sau {i+1} giay "
                            "(Trang da tai on dinh va giao dien Log in van hien lien tuc)."
                        )
                        return False
                    await asyncio.sleep(1)
                    continue
                else:
                    visible_login_streak = 0

                # Require the authenticated marker to remain visible for three
                # observations. This avoids accepting a transient guest shell
                # while TikTok hydrates the actual navigation state.
                if await _locator_has_visible(profile_link_locator):
                    visible_account_streak += 1
                    if visible_account_streak >= 3:
                        logger.info(
                            f"[+] Xac minh THANH CONG sau {i+1} giay "
                            "(Phat hien profile/messages cua account va khong co nut Log in)."
                        )
                        # This page IS a settled, signed-in For You. Record it
                        # so the upload gate can finish its own checks on it
                        # instead of throwing it away and loading the same URL
                        # again - measured at 10s per account.
                        self._foryou_verified_at = time.monotonic()
                        self._foryou_verified_url = current_url
                        return True
                else:
                    visible_account_streak = 0

            except AccountBannedException as e_ban:
                raise e_ban
            except AuthenticationPageNotReady:
                # The blank-page verdict above is a conclusion, not a hiccup
                # this loop should absorb and retry.
                raise
            except Exception:
                pass

            await asyncio.sleep(1)

        # The loop can also exhaust on a page that settled perfectly: neither
        # the Log in control nor the signed-in marker was ever visible, so
        # there was nothing to conclude from. That is a different fact from an
        # unstable page, and reporting it as instability sent every reader
        # after the network instead of the markup. Record which one it was.
        nav = {}
        try:
            nav = await self._page.evaluate(
                """() => {
                  const vis = el => !!(el && (
                    el.offsetParent !== null || el.getClientRects().length));
                  return {
                    e2e: [...new Set(Array.from(
                      document.querySelectorAll('[data-e2e]'))
                      .filter(vis).map(e => e.getAttribute('data-e2e')))]
                      .slice(0, 40),
                    profile_hrefs: Array.from(
                      document.querySelectorAll('a[href^="/@"]'))
                      .filter(vis).slice(0, 3).map(a => a.getAttribute('href')),
                    text: document.body.innerText.slice(0, 200)
                      .replace(/\\s+/g, ' '),
                  };
                }"""
            )
        except Exception:
            pass
        logger.warning(
            "[-] Khong ket luan duoc dang nhap; settled=%s, url=%s, state=%s, "
            "nav=%s, cdn_refused=%s",
            saw_settled_shell,
            last_url,
            last_state,
            nav,
            {h: n for h, n in sorted(
                blocked_assets.items(), key=lambda kv: -kv[1])[:3]},
        )
        if saw_settled_shell:
            raise AuthenticationPageNotReady(
                "Trang TikTok đã tải xong nhưng không thấy cả nút Đăng nhập "
                "lẫn dấu hiệu đã đăng nhập; không kết luận được cookies. "
                f"nav={nav.get('e2e')}"
            )
        raise AuthenticationPageNotReady(
            "Trang TikTok chưa tải ổn định nên chưa thể kết luận cookies hết hạn."
        )

    async def prepare_foryou_home(self, step_logger=None) -> bool:
        """Open For You and require a fully loaded, stable signed-in feed.

        URL/``interactive``/a generic ``main`` element are deliberately
        insufficient. Studio remains blocked until the load event and critical
        render requests are quiet, feed media is usable, visible images/fonts
        are ready, and loading overlays are gone for several consecutive
        observations. Feed media URLs and item order are intentionally allowed
        to rotate because For You is a live feed. Continuous video streaming and
        telemetry are not treated as unfinished page rendering.
        """
        async def log(message):
            if step_logger:
                await step_logger(message)

        self._foryou_ready_at = None
        # ⛔ THE GATE BELOW STILL RUNS IN FULL. What is skipped is only the
        # RELOAD: check_login_status just verified a signed-in For You on this
        # very URL, and navigating to it again threw that page away and paid
        # for it twice - 13s to verify, then 10s to load the same thing,
        # measured on a live batch 2026-09-16. If anything about that is not
        # true any more - different tab, different URL, or long enough ago
        # that the feed may have moved - load it properly.
        current = ""
        try:
            current = str(self._page.url or "")
        except Exception:
            current = ""
        verified_at = getattr(self, "_foryou_verified_at", None)
        reuse = (
            verified_at is not None
            and (time.monotonic() - verified_at) <= _FORYOU_REUSE_SECONDS
            and "/foryou" in current
            and current == getattr(self, "_foryou_verified_url", None)
        )
        if reuse:
            await log("Đang dùng lại trang For You vừa xác minh đăng nhập...")
        else:
            await log("Đang mở trang For You và chờ tải hoàn toàn...")
            await self.navigate_to("https://www.tiktok.com/foryou?lang=en")
        try:
            await self._page.wait_for_load_state("load", timeout=30000)
        except Exception as exc:
            logger.warning("[UPLOAD] For You không phát load event: %s", str(exc)[:120])
            await log("Trang For You chưa hoàn tất tải tài liệu; chưa được chuyển sang đăng bài.")
            return False

        page = self._page
        critical_types = {"document", "script", "stylesheet", "font", "image"}
        critical_inflight: set[int] = set()
        last_critical_activity = [time.monotonic()]

        def on_request(request) -> None:
            try:
                if request.resource_type in critical_types:
                    critical_inflight.add(id(request))
                    last_critical_activity[0] = time.monotonic()
            except Exception:
                pass

        def on_request_done(request) -> None:
            key = id(request)
            if key in critical_inflight:
                critical_inflight.discard(key)
                last_critical_activity[0] = time.monotonic()

        page.on("request", on_request)
        page.on("requestfinished", on_request_done)
        page.on("requestfailed", on_request_done)
        stable = 0
        last_state: Dict[str, Any] = {}
        try:
            for _ in range(90):
                await self._wait_automation_gate()
                if await self.is_captcha_present():
                    stable = 0
                    await asyncio.sleep(1)
                    continue
                try:
                    state = await self._page.evaluate(r"""() => {
                  const visible = el => !!(el && (el.offsetParent !== null || el.getClientRects().length));
                  const inViewport = el => {
                    if (!visible(el)) return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0 && rect.bottom > 0 &&
                      rect.right > 0 && rect.top < window.innerHeight && rect.left < window.innerWidth;
                  };
                  const loggedIn = Array.from(document.querySelectorAll(
                    '[data-e2e="profile-icon"],[data-e2e="nav-profile"],'
                    + '[data-e2e="messages-icon"],[data-e2e="inbox-icon"],a[href*="/messages"]'
                  )).some(visible);
                  const feedSelectors = [
                    '[data-e2e="recommend-list-item-container"]',
                    '[data-e2e*="recommend-list-item"]',
                    '[data-e2e="browse-video"]',
                    '[data-e2e="feed-video"]'
                  ];
                  let feedItems = Array.from(document.querySelectorAll(feedSelectors.join(','))).filter(visible);
                  if (!feedItems.length) {
                    feedItems = Array.from(document.querySelectorAll('main video')).filter(visible)
                      .map(video => video.closest('article, section, div') || video);
                  }
                  feedItems = Array.from(new Set(feedItems));
                  const feedRoot = feedItems[0]?.parentElement || document.querySelector('main');
                  const videos = Array.from((feedRoot || document).querySelectorAll('video')).filter(inViewport);
                  const playableVideos = videos.filter(video =>
                    video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA &&
                    (video.videoWidth > 0 || !!video.poster || !!video.currentSrc)
                  );
                  const images = Array.from((feedRoot || document).querySelectorAll('img')).filter(inViewport);
                  const pendingImages = images.filter(image => !image.complete || image.naturalWidth <= 0).length;
                  const loadedImages = images.length - pendingImages;
                  const busySelectors = [
                    '[aria-busy="true"]',
                    '[data-e2e="loading"]', '[data-e2e*="skeleton"]',
                    '.TUXLoading', '[class*="Skeleton"]'
                  ];
                  const busy = Array.from(document.querySelectorAll(busySelectors.join(','))).filter(inViewport).length;
                  const login = Array.from(document.querySelectorAll('[data-e2e="nav-login-button"]')).some(visible);
                  const mediaReady = playableVideos.length + loadedImages;
                  const mediaKey = videos.slice(0, 3).map(video =>
                    `${video.currentSrc || video.poster || ''}:${video.readyState}`
                  ).join('|') || images.slice(0, 3).map(image => image.currentSrc || image.src || '').join('|');
                  return {
                    ready: document.readyState,
                    loggedIn,
                    login,
                    feedItems: feedItems.length,
                    mediaReady,
                    pendingImages,
                    busy,
                    fontsLoaded: !document.fonts || document.fonts.status === 'loaded',
                    fingerprint: `${location.pathname}|${feedItems.length}|${mediaKey}|${document.documentElement.scrollHeight}`
                  };
                    }""")
                    last_state = state
                    critical_quiet = bool(
                        not critical_inflight
                        and time.monotonic() - last_critical_activity[0] >= 2.0
                    )
                    good = bool(
                        "/foryou" in (self._page.url or "").lower()
                        and _foryou_state_ready(state, critical_quiet)
                    )
                    # The feed legitimately rotates videos, signed CDN URLs and
                    # scroll height while remaining fully usable. Requiring an
                    # identical content fingerprint made healthy signed-in feeds
                    # time out forever. Five consecutive structural-ready states
                    # still reject transient or partially rendered pages.
                    stable = stable + 1 if good else 0
                    if stable >= 5:
                        self._foryou_ready_at = time.monotonic()
                        await log("✅ Trang For You đã tải hoàn toàn và ổn định; bắt đầu chuyển sang màn đăng bài.")
                        return True
                except Exception as exc:
                    logger.debug("[UPLOAD] Quan sát For You chưa sẵn sàng: %s", exc)
                    stable = 0
                await asyncio.sleep(1)
            logger.warning(
                "[UPLOAD] For You timeout, critical_inflight=%d, trạng thái cuối: %s",
                len(critical_inflight),
                last_state,
            )
            await log("Trang For You chưa tải hoàn toàn sau 90 giây; dừng trước khi mở trang đăng bài.")
            return False
        finally:
            try:
                page.remove_listener("request", on_request)
                page.remove_listener("requestfinished", on_request_done)
                page.remove_listener("requestfailed", on_request_done)
            except Exception:
                pass

    def _consume_foryou_upload_ticket(self) -> None:
        ready_at = self._foryou_ready_at
        self._foryou_ready_at = None
        current_url = (getattr(self._page, "url", "") or "").lower()
        if (
            ready_at is None
            or time.monotonic() - ready_at > 90
            or "/foryou" not in current_url
        ):
            raise RuntimeError(
                "Chưa có xác nhận For You tải hoàn toàn; không được mở TikTok Studio Upload."
            )

    async def is_captcha_present(self) -> bool:
        """True neu dang co hop captcha (geetest/slider/puzzle) hien tren trang."""
        await self._wait_automation_gate()
        if not self._page:
            return False
        try:
            # A stalled browsing context must never freeze the whole upload.
            # asyncio.wait_for is required because page.evaluate has no timeout
            # argument in Playwright and can otherwise wait indefinitely.
            return bool(await asyncio.wait_for(
                self._page.evaluate("""() => {
                  // Cac container CHI ton tai khi modal captcha TikTok dang bat (dac hieu,
                  // khong false-positive). Bao gom cac bien the da biet: web-v2, secsdk,
                  // geetest slider/rotate/puzzle.
                  const sels = ['.captcha_verify_container','[class*="captcha_verify"]',
                    '#captcha-verify-container','#captcha-verify-container-web-v2','[id*="captcha-verify"]',
                    '#captcha_container','[id="captcha_container"]','div[class*="captcha-verify"]',
                    '.captcha-disable-scroll','[class*="secsdk-captcha"]',
                    '.geetest_captcha','[class*="geetest_"]'];
                  for (const s of sels){
                    const el=document.querySelector(s);
                    if(el && (el.offsetParent!==null || (el.getClientRects && el.getClientRects().length>0))) return true;
                  }
                  for (const f of document.querySelectorAll('iframe')){ if(/captcha/i.test(f.getAttribute('src')||'')) return true; }
                  const t = (document.body.innerText||'');
                  return /drag the slider|kéo thanh trượt|ghép hình|verify to continue|xác minh để tiếp tục|slide to complete|rotate the shapes|xoay hình/i.test(t);
                }"""),
                timeout=2.5,
            ))
        except Exception:
            return False

    async def is_account_banned(self) -> bool:
        """Kiem tra NHANH (1 lan, khong loop) xem trang co dang hien thong bao BAN
        khong. Dung de thoat som trong luong login (tiet kiem thoi gian) thay vi cho
        het cac buoc OTP. Khong dua ket luan sai cho nick binh thuong (chi True khi
        that su thay dau hieu ban)."""
        await self._wait_automation_gate()
        if not self._page:
            return False
        try:
            return await self._page.evaluate("""() => {
              const t=(document.body.innerText||'').toLowerCase();
              if(/your account was banned|submit an appeal|permanently banned|account was suspended|tài khoản của bạn đã bị cấm|đã bị cấm vĩnh viễn|vi phạm nguyên tắc cộng đồng/i.test(t)) return true;
              const dt=document.querySelector('.tux-dialog__content-title');
              if(dt && /ban|cấm|suspend/i.test(dt.innerText||'')) return true;
              return false;
            }""")
        except Exception:
            return False

    async def wait_captcha_cleared(self, timeout: float = 120.0, step_logger=None) -> bool:
        """Neu co CAPTCHA -> DUNG cho extension solver da cau hinh tu xu ly; chi tiep tuc khi
        captcha BIEN MAT. Tra True neu khong co captcha / da giai xong; False neu het
        gio ma captcha van con. Cho captcha xuat hien tre toi 3s truoc khi ket luan."""
        appeared = False
        for _ in range(4):
            await self._wait_automation_gate()
            if await self.is_captcha_present():
                appeared = True
                break
            await asyncio.sleep(0.8)
        if not appeared:
            return True
        if step_logger:
            await step_logger("Phát hiện CAPTCHA -> chờ extension giải xong (không tiếp tục cho tới khi xong)...")
        waited = 0.0
        while waited < timeout:
            await self._wait_automation_gate()
            if not await self.is_captcha_present():
                if step_logger:
                    await step_logger(f"[+] Captcha đã được giải sau ~{int(waited)}s -> tiếp tục.")
                await asyncio.sleep(1.5)   # on dinh sau khi captcha bien mat
                return True
            await asyncio.sleep(2)
            waited += 2
        if step_logger:
            await step_logger("[!] Captcha vẫn chưa giải xong sau thời gian chờ.")
        return False

    async def wait_first_visible(self, selectors: List[str], timeout: float = 12.0):
        """Cho phan tu DAU TIEN trong danh sach selector hien ra (state-based, thay cho
        sleep() cung). Tra ve locator dau tien thay duoc, hoac None neu het gio."""
        if not self._page:
            return None
        waited = 0.0
        while waited < timeout:
            await self._wait_automation_gate()
            for sel in selectors:
                try:
                    loc = self._page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        return loc
                except Exception:
                    pass
            await asyncio.sleep(0.5)
            waited += 0.5
        return None

    async def _retype_username_input(self, uname_input, value: str) -> None:
        """Xoa sach o username va go lai 'value'."""
        await uname_input.click()
        await self._page.keyboard.press("Control+A")
        await self._page.keyboard.press("Backspace")
        await asyncio.sleep(0.4)
        await uname_input.press_sequentially(value, delay=random.randint(60, 140))
        await asyncio.sleep(0.5)

    async def _type_username_until_valid(
        self, uname_input, base_name: str, step_logger: Optional[Any] = None,
        max_len: int = 18, max_tries: int = 6,
    ) -> Tuple[bool, str]:
        """Go username va cho TikTok validate:
          - Neu co TICH XANH (svg fill #0BE09B trong o username) -> HOP LE -> tra (True, ten).
          - Neu KHONG (dau X / 'isn't available') -> them 1 chu so vao cuoi va thu lai,
            gioi han toi da max_len (18) ky tu.
        Tra ve (is_valid, final_name)."""
        tick = self._page.locator(
            '[data-e2e="edit-profile-username-input"] svg[fill="#0BE09B"], '
            '[data-e2e="edit-profile-username-input"] svg[class*="Tick" i]'
        )
        candidate = (base_name or "").strip()[:max_len]
        if not candidate:
            return False, candidate

        for attempt in range(max_tries):
            await self._retype_username_input(uname_input, candidate)
            await asyncio.sleep(2.8)  # cho TikTok kiem tra tinh kha dung
            try:
                if await tick.count() > 0:
                    logger.info(f"[Username] '{candidate}' HOP LE (tich xanh) sau {attempt+1} lan.")
                    return True, candidate
            except Exception:
                pass

            # Chua hop le -> them 1 chu so (ton trong gioi han 18 ky tu).
            if step_logger:
                await step_logger(f"Username '{candidate}' chua hop le (dau X) -> them so, thu lai...")
            digit = str(random.randint(0, 9))
            if len(candidate) + len(digit) > max_len:
                candidate = candidate[: max_len - len(digit)] + digit
            else:
                candidate = candidate + digit

        return False, candidate

    async def _wait_first_visible_locator(
        self,
        locator,
        *,
        timeout_seconds: float,
    ):
        """Return the first visible match instead of trusting DOM order."""
        deadline = time.monotonic() + max(0.1, float(timeout_seconds))
        while time.monotonic() < deadline:
            await self._wait_automation_gate()
            try:
                count = await locator.count()
            except Exception:
                count = 0
            for index in range(min(count, 24)):
                candidate = locator.nth(index)
                try:
                    if await candidate.is_visible():
                        return candidate
                except Exception:
                    continue
            await asyncio.sleep(0.25)
        return None

    async def _open_own_profile_page(
        self,
        db_username: Optional[str],
    ):
        """Open the signed-in user's profile across desktop/nav variants."""
        edit_selector = (
            '[data-e2e="edit-profile-entrance"], '
            'button:has-text("Edit profile"), '
            'button:has-text("Chỉnh sửa hồ sơ")'
        )

        # UPDATE_PROFILE may already start on the account's own page.
        edit_button = await self._wait_first_visible_locator(
            self._page.locator(edit_selector),
            timeout_seconds=1.0,
        )
        if edit_button is not None:
            return edit_button

        # TikTok often keeps the real nav-profile anchor in the DOM while a
        # responsive duplicate before it is hidden. Read its own href instead
        # of waiting for ``locator(...).first`` to become visible.
        profile_href = None
        try:
            profile_href = await self._page.evaluate(
                r"""() => {
                  const visible = node => !!(node && (
                    node.offsetParent !== null || node.getClientRects().length
                  ));
                  const nodes = Array.from(document.querySelectorAll(
                    '[data-e2e="nav-profile"], [data-e2e="profile-icon"]'
                  ));
                  const paths = [];
                  for (const node of nodes) {
                    const anchor = node.matches('a')
                      ? node : (node.closest('a') || node.querySelector('a'));
                    const href = anchor && anchor.getAttribute('href');
                    if (href && /^\/@[A-Za-z0-9._]+(?:[/?#]|$)/.test(href)) {
                      paths.push({href, visible: visible(anchor)});
                    }
                  }
                  paths.sort((left, right) => Number(right.visible) - Number(left.visible));
                  if (paths.length) return paths[0].href;

                  const scope = window.__UNIVERSAL_DATA_FOR_REHYDRATION__
                    && window.__UNIVERSAL_DATA_FOR_REHYDRATION__.__DEFAULT_SCOPE__;
                  const context = scope && scope['webapp.app-context'];
                  const username = context && (
                    context.user?.uniqueId
                    || context.userInfo?.user?.uniqueId
                    || context.user?.unique_id
                  );
                  return username ? `/@${encodeURIComponent(username)}` : null;
                }"""
            )
        except Exception:
            profile_href = None

        attempted_urls: list[str] = []

        async def navigate_profile(path: str) -> Optional[Any]:
            target = str(path or "").strip()
            if not target:
                return None
            if target.startswith("/@"):
                target = f"https://www.tiktok.com{target}"
            if not target.lower().startswith("https://www.tiktok.com/@"):
                return None
            if target in attempted_urls:
                return None
            attempted_urls.append(target)
            await self.navigate_to(target)
            return await self._wait_first_visible_locator(
                self._page.locator(edit_selector),
                timeout_seconds=25.0,
            )

        # ⛔ CLICK THE PROFILE BUTTON FIRST. It opens whoever is actually signed
        # in; a URL built from the saved username opens that name's page, which
        # is someone else's when the saved name is out of date (mo91trow4_spau
        # vs @maryannfranze, 2026-09-18) - and reading the username there is
        # the whole point of the check.
        profile_controls = self._page.locator(
            '[data-e2e="nav-profile"], [data-e2e="profile-icon"]'
        )
        visible_profile = await self._wait_first_visible_locator(
            profile_controls,
            timeout_seconds=5.0,
        )
        if visible_profile is not None:
            try:
                await visible_profile.click(timeout=5000, no_wait_after=True)
                edit_button = await self._wait_first_visible_locator(
                    self._page.locator(edit_selector),
                    timeout_seconds=20.0,
                )
                if edit_button is not None:
                    return edit_button
            except Exception:
                pass

        # The same button's own link, when the button could not be clicked
        # (a hidden responsive duplicate is common).
        if profile_href:
            edit_button = await navigate_profile(str(profile_href))
            if edit_button is not None:
                return edit_button

        # Last resort only. "Edit profile" appears on one's own page alone, so
        # a wrong saved name fails here instead of editing someone else.
        safe_username = re.sub(
            r"[^A-Za-z0-9._]", "", (db_username or "").strip().lstrip("@")
        )
        if safe_username:
            edit_button = await navigate_profile(f"/@{safe_username}")
            if edit_button is not None:
                return edit_button

        current_url = str(getattr(self._page, "url", "") or "")
        raise RuntimeError(
            "Không mở được profile của account đã đăng nhập hoặc không thấy "
            f"nút Edit profile (URL cuối: {current_url[:180]})."
        )

    async def update_profile(
        self,
        avatar_path: Optional[str] = None,
        bio: Optional[str] = None,
        step_logger: Optional[Any] = None,
        db_username: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Tra ve (success, username_for_db). username_for_db != None nghia la
        can CAP NHAT username trong DB thanh gia tri do (Rule B: web = db + duoi)."""
        await self._wait_automation_gate()
        if not self._page:
            raise RuntimeError("Trinh duyet chua duoc khoi tao.")

        username_for_db: Optional[str] = None
        username_needs_confirm = False  # True neu vua doi username -> can bam Confirm sau Save
        try:
            if step_logger:
                await step_logger("Dang di chuyen toi trang ca nhan TikTok...")

            edit_btn = await self._open_own_profile_page(db_username)

            if step_logger:
                await step_logger("Dang mo Modal chinh sua thong tin tai khoan...")
            await edit_btn.click(timeout=10000, no_wait_after=True)

            # Cho Modal chinh sua ho so mo len. KHONG dung class roi '.e17raual2'
            # (TikTok doi ten class lien tuc -> selector chet -> Timeout). Cho cac
            # selector BEN theo data-e2e / role=dialog / input[type=file] (chinh la
            # noi se inject avatar ben duoi), co nhieu phuong an du phong.
            avatar_wrapper = self._page.locator(
                '[data-e2e="edit-profile-avatar-edit-icon"], '
                'div[role="dialog"] input[type="file"], '
                '[data-e2e="edit-profile-avatar"], '
                'div[role="dialog"] [class*="avatar" i]'
            )
            await avatar_wrapper.first.wait_for(state="attached", timeout=15000)
            await asyncio.sleep(2)

            if avatar_path:
                try:
                    abs_origin_path = os.path.abspath(os.path.expanduser(avatar_path))
                    if not os.path.exists(abs_origin_path):
                        raise FileNotFoundError(f"Khong tim thay file: {abs_origin_path}")

                    if step_logger:
                        await step_logger("Dang nap anh avatar...")

                    avatar_input = self._page.locator(
                        '[data-e2e="edit-profile-avatar-edit-icon"] input[type="file"], '
                        'div[role="dialog"] input[type="file"][accept*="image"]'
                    ).first
                    await avatar_input.wait_for(state="attached", timeout=15000)
                    await self._attach_avatar_file(avatar_input, abs_origin_path)

                    if step_logger:
                        await step_logger("Doi khung cat anh (Crop Modal) on dinh...")
                    await asyncio.sleep(4)

                    if step_logger:
                        await step_logger("Dang tim nut bam xac nhan cat anh...")

                    candidate_selectors = [
                        'button:has-text("Apply")',
                        'button:has-text("Ap dung")',
                        'div[role="dialog"] button:has-text("Apply")',
                        'div[role="dialog"] button:has-text("Ap dung")',
                        '[class*="tux-button"]:has-text("Apply")',
                        '[class*="tux-button"]:has-text("Ap dung")',
                        'button.ef1kawg9',
                        'button:has-text("Save")',
                        'button:has-text("Luu")'
                    ]

                    apply_btn = None
                    for selector in candidate_selectors:
                        try:
                            loc = self._page.locator(selector)
                            count = await loc.count()
                            for idx in range(count):
                                candidate = loc.nth(idx)
                                if await candidate.is_visible() and await candidate.is_enabled():
                                    apply_btn = candidate
                                    logger.info(f"[+] Tim thay nut xac nhan hop le: '{selector}'")
                                    break
                            if apply_btn:
                                break
                        except Exception as sel_err:
                            logger.debug(f"Bo qua selector '{selector}': {str(sel_err)}")

                    if not apply_btn:
                        logger.warning("[-] Khong dinh vi duoc Apply bang phuong phap loc dong. Dung bo gop du phong...")
                        apply_btn = self._page.locator(
                            'button.ef1kawg9, button:has-text("Apply"), button:has-text("Ap dung"), [class*="tux-button"]:has-text("Apply")'
                        ).first

                    await apply_btn.wait_for(state="visible", timeout=15000)

                    if step_logger:
                        await step_logger("Dang nhan nut Apply...")

                    clicked = False
                    for attempt in range(3):
                        try:
                            await apply_btn.click(force=True, timeout=4000)
                            logger.info(f"[+] Da nhan Apply thanh cong o lan thu {attempt+1} bang Click gia lap.")
                            clicked = True
                            break
                        except Exception as e_click:
                            logger.warning(f"[-] Click gia lap that bai o lan thu {attempt+1}: {str(e_click)}. Dang thu Dispatch Event...")
                            try:
                                await apply_btn.dispatch_event("click")
                                logger.info(f"[+] Da nhan Apply thanh cong o lan thu {attempt+1} bang Dispatch Event.")
                                clicked = True
                                break
                            except Exception as e_disp:
                                logger.warning(f"[-] Dispatch Event that bai: {str(e_disp)}. Dang thu Direct JS Click...")
                                try:
                                    await apply_btn.evaluate("node => node.click()")
                                    logger.info(f"[+] Da nhan Apply thanh cong o lan thu {attempt+1} bang Direct JS Click.")
                                    clicked = True
                                    break
                                except Exception as e_js:
                                    logger.error(f"[-] Direct JS Click that bai: {str(e_js)}")
                        await asyncio.sleep(1.5)

                    if not clicked:
                        raise Exception("Toan bo cac no luc nhan nut Apply cat anh deu that bai.")

                    await asyncio.sleep(4)
                    logger.info("[+] Cap nhat avatar vao form thanh cong.")

                except Exception as e3:
                    logger.error(f"[-] Loi Buoc 3 (Avatar): {str(e3)}")
                    if step_logger:
                        await step_logger(f"[-] Loi thay avatar: {str(e3)}")
                    raise e3

            # =================================================================
            # XU LY USERNAME (4 quy tac):
            #  A. web == db            -> khong lam gi.
            #  B. web bat dau bang db nhung co them duoi (web = db + "xxx")
            #                          -> CAP NHAT DB thanh web (tra ve username_for_db).
            #  C. web la username mac dinh cua TikTok (userXXXXX / bat dau "user")
            #                          -> DOI username tren WEB thanh db (go vao o input).
            #  D. web la username that nhung khac db
            #                          -> CAP NHAT DB thanh web.
            # =================================================================
            if db_username:
                try:
                    # CHI lay dung phan tu INPUT (o username that su co placeholder
                    # "Username", KHONG co data-e2e). Truoc day them
                    # [data-e2e="edit-profile-username-input"] khop nham 1 DIV wrapper
                    # -> input_value() loi "Node is not an input".
                    uname_input = self._page.locator(
                        'div[role="dialog"] input[placeholder="Username" i], '
                        'div[role="dialog"] input[name="username"], '
                        'input[placeholder="Username" i]'
                    )
                    await uname_input.first.wait_for(state="visible", timeout=8000)
                    web_username = (await uname_input.first.input_value() or "").strip()
                    dbu = db_username.strip()
                    logger.info(f"[Username] web='{web_username}' | db='{dbu}'")

                    if not web_username:
                        pass  # khong doc duoc -> bo qua
                    elif web_username == dbu:
                        # Rule A
                        if step_logger:
                            await step_logger(f"Username web '{web_username}' == DB -> giu nguyen.")
                    elif web_username.startswith(dbu):
                        # Rule B: web = db + duoi -> DB se cap nhat thanh web
                        username_for_db = web_username
                        if step_logger:
                            await step_logger(f"Username web '{web_username}' = DB + duoi -> se cap nhat DB.")
                    elif re.match(r'^user\d', web_username, re.IGNORECASE) or web_username.lower().startswith("user"):
                        # Rule C: username mac dinh (userXXXX) -> doi web thanh db.
                        # Go db username, doi tich xanh (hop le); neu dau X (da co
                        # nguoi/khong hop le) thi them so, thu lai, toi da 18 ky tu.
                        if step_logger:
                            await step_logger(f"Username web '{web_username}' la mac dinh -> dat username '{dbu}' (co kiem tra hop le)...")
                        ok_valid, final_name = await self._type_username_until_valid(
                            uname_input.first, dbu, step_logger=step_logger
                        )
                        if ok_valid:
                            username_needs_confirm = True   # sau Save se co dialog "Set your username?"
                            username_for_db = final_name     # cap nhat DB thanh ten cuoi cung (co the da +so)
                            if step_logger:
                                await step_logger(f"[+] Username hop le: '{final_name}' (tich xanh) -> se luu.")
                        else:
                            # Khong dat duoc ten hop le -> revert ve ten web goc de Save
                            # KHONG bi chan (o username sai lam Save disabled).
                            await self._retype_username_input(uname_input.first, web_username)
                            if step_logger:
                                await step_logger(f"[!] Khong tim duoc username hop le trong 18 ky tu -> giu nguyen '{web_username}'.")
                    else:
                        # Rule D: username dang hien tren profile TikTok la nguon
                        # su that. Khong bo qua ten that chi vi no khong con lien
                        # quan theo tien to voi username cu da import vao DB.
                        username_for_db = web_username
                        if step_logger:
                            await step_logger(
                                f"Username web '{web_username}' khac DB '{dbu}' "
                                "-> se cap nhat DB theo username web."
                            )
                except Exception as e_un:
                    logger.warning(f"[Username] Bo qua xu ly username do loi: {str(e_un)}")

            if bio is not None:
                if step_logger:
                    await step_logger(f"Dang cap nhat Bio: '{bio}'...")
                # Bio: uu tien data-e2e, fallback ve textarea trong modal (bền hơn).
                bio_input = self._page.locator(
                    '[data-e2e="edit-profile-bio-input"], '
                    'div[role="dialog"] textarea, '
                    'textarea[placeholder*="bio" i]'
                )
                await bio_input.first.wait_for(state="visible", timeout=10000)
                await bio_input.first.click()
                await self._page.keyboard.press("Control+A")
                await self._page.keyboard.press("Backspace")
                await bio_input.first.press_sequentially(bio, delay=random.randint(100, 200))
                await asyncio.sleep(2)

            if not avatar_path and bio is None and not username_needs_confirm:
                # Nothing was changed - the username sync after login only
                # READ the name. Save is disabled then, and pressing it would
                # leave the dialog open, which _confirm_profile_saved rightly
                # calls a refused save. Close the dialog instead.
                await self._close_edit_profile_dialog()
                return (True, username_for_db)

            if step_logger:
                await step_logger("Dang nhan Save luu toan bo thay doi...")

            save_btn = self._page.locator(
                '[data-e2e="edit-profile-save"], '
                'button:has-text("Save"), '
                'button:has-text("Luu")'
            )

            await save_btn.first.wait_for(state="visible", timeout=10000)

            try:
                await save_btn.first.click(force=True, timeout=4000)
                logger.info("[+] Da nhan Save thanh cong bang click gia lap.")
            except Exception:
                await save_btn.first.dispatch_event("click")
                logger.info("[+] Da nhan Save thanh cong bang dispatch_event.")

            # =================================================================
            # XAC NHAN DOI USERNAME: neu vua doi username, TikTok hien dialog
            # "Set your username? You can change your username once every 30 days."
            # -> phai bam CONFIRM (set-username-popup-confirm) thi moi thuc su luu.
            # =================================================================
            if username_needs_confirm:
                try:
                    if step_logger:
                        await step_logger("Dang xac nhan doi username (dialog Set your username)...")
                    # Cho dialog confirm hien ra va animate xong (neu bam ngay se
                    # gap "Element is not visible").
                    await asyncio.sleep(2.0)
                    confirm_btn = self._page.locator('[data-e2e="set-username-popup-confirm"]')
                    await confirm_btn.first.wait_for(state="visible", timeout=10000)
                    await asyncio.sleep(0.6)

                    # Thu bam theo nhieu cach (giong nut Apply) cho chac.
                    cf_clicked = False
                    for cf_attempt in range(3):
                        try:
                            await confirm_btn.first.click(timeout=4000)
                            cf_clicked = True
                            break
                        except Exception:
                            try:
                                await confirm_btn.first.click(force=True, timeout=4000)
                                cf_clicked = True
                                break
                            except Exception:
                                try:
                                    await confirm_btn.first.dispatch_event("click")
                                    cf_clicked = True
                                    break
                                except Exception:
                                    await asyncio.sleep(1.0)
                    if cf_clicked:
                        logger.info("[+] Da bam Confirm xac nhan doi username.")
                        # CHO dialog confirm DONG HAN (toi 25s). Doi username la hanh
                        # dong nhay cam -> TikTok co the hien captcha sau Confirm;
                        # extension solver can thoi gian tu xu ly. Neu dong browser
                        # ngay (chi sleep 3s) thi captcha chua giai xong -> KHONG luu.
                        if step_logger:
                            await step_logger("Da bam Confirm, dang cho xu ly (co the co captcha)...")
                        try:
                            await confirm_btn.first.wait_for(state="detached", timeout=25000)
                            if step_logger:
                                await step_logger("[+] Da xac nhan doi username thanh cong.")
                        except Exception:
                            # Dialog van con sau 25s -> co the captcha chua giai / bi tu choi.
                            logger.warning("[Username] Dialog confirm van chua dong sau 25s (co the captcha/rate-limit).")
                            if step_logger:
                                await step_logger("[!] Doi username co the chua hoan tat (captcha/gioi han 30 ngay).")
                    else:
                        logger.warning("[Username] Khong bam duoc nut Confirm sau nhieu lan thu.")
                    await asyncio.sleep(2)
                except Exception as e_cf:
                    logger.warning(f"[Username] Khong thay/khong bam duoc dialog Confirm: {str(e_cf)}")

            await self._confirm_profile_saved(
                save_btn, retry_save=not username_needs_confirm, step_logger=step_logger
            )
            if step_logger:
                await step_logger("Da luu thay doi ho so thanh cong!")
            await asyncio.sleep(5)
            return (True, username_for_db)

        except Exception as e:
            if step_logger:
                await step_logger(f"Loi thao tac sua ho so: {str(e)}")
            logger.error(f"[-] Gap loi khi thao tac cap nhat thong tin ho so: {str(e)}")
            return (False, None)

    # =====================================================================
    # UPLOAD VIDEO (da verify end-to-end)
    # =====================================================================
    # Diem mau chot: input file cua TikTok KHONG set duoc bang set_input_files
    # / file_chooser tren Firefox-va (bi chan) VA inject synthetic DataTransfer
    # thi ket voi file LON (nhanh VOD/Vmok). GIAI PHAP: tu dong hoa HOP THOAI
    # CHON FILE cua Windows (click that vao "Chon video" -> dialog #32770 ->
    # dien duong dan bang win32 -> Open) => dua FILE THAT tren dia vao, chay
    # cho MOI kich thuoc y het lam tay. Captcha luc vao trang do extension
    # extension solver tu xu ly.
    async def _click_by_texts(self, texts, timeout=8000, no_wait_after=True):
        """Click phan tu dau tien khop 1 trong cac chuoi text (da/anh)."""
        for t in texts:
            try:
                loc = self._page.get_by_text(t, exact=False).first
                await loc.click(timeout=timeout, no_wait_after=no_wait_after)
                return True
            except Exception:
                continue
        return False

    _EDIT_PROFILE_DIALOG_PARTS = (
        '[data-e2e="edit-profile-save"], [data-e2e="edit-profile-avatar"]'
    )

    async def _edit_profile_dialog_closed(self, timeout_seconds: float) -> bool:
        parts = self._page.locator(self._EDIT_PROFILE_DIALOG_PARTS)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            try:
                count = await asyncio.wait_for(parts.count(), timeout=2.0)
                if not count or not await asyncio.wait_for(parts.first.is_visible(), timeout=2.0):
                    return True
            except Exception:
                pass
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(0.5)

    async def _close_edit_profile_dialog(self) -> None:
        """Leave the edit dialog without saving (Cancel, else Escape)."""
        try:
            cancel = self._page.locator(
                'div[role="dialog"] button:has-text("Cancel"), '
                'div[role="dialog"] button:has-text("Hủy")'
            )
            if await cancel.count() and await cancel.first.is_visible():
                await cancel.first.click(timeout=4000)
            else:
                await self._page.keyboard.press("Escape")
        except Exception:
            try:
                await self._page.keyboard.press("Escape")
            except Exception:
                pass
        await self._edit_profile_dialog_closed(timeout_seconds=5.0)

    async def _confirm_profile_saved(self, save_btn, *, retry_save: bool, step_logger=None) -> None:
        """Raise unless TikTok closed the edit dialog, i.e. accepted the save.

        ⛔ A CLICK ON SAVE IS NOT A SAVE. TikTok answers every Save with HTTP
        200 on /api/update/profile/ and only closes the dialog when it kept
        the change. On lexie_39_lipton (2026-09-17) one run left the dialog
        open - new photo and bio still in it, "No bio yet." on the profile -
        and the task was recorded SUCCESS/COMPLETED; the next run closed it
        and the bio appeared publicly.
        """
        if await self._edit_profile_dialog_closed(timeout_seconds=12.0):
            return
        if retry_save:
            for attempt in range(1, 3):
                logger.warning("[-] Hop thoai Edit profile van mo sau Save; bam lai Save (lan %d).", attempt)
                if step_logger:
                    await step_logger(f"[!] TikTok chua dong hop thoai sau Save, bam lai Save (lan {attempt})...")
                try:
                    await save_btn.first.click(timeout=4000)
                except Exception:
                    try:
                        await save_btn.first.dispatch_event("click")
                    except Exception:
                        pass
                if await self._edit_profile_dialog_closed(timeout_seconds=12.0):
                    return
        raise RuntimeError(
            "TikTok chua luu ho so: hop thoai Edit profile van mo sau khi bam Save "
            "(TikTok tu choi thay doi - thu lai sau hoac doi proxy)."
        )

    _AVATAR_EDIT_ICON = (
        'div[role="dialog"] [data-e2e="edit-profile-avatar-edit-icon"], '
        '[data-e2e="edit-profile-avatar-edit-icon"]'
    )

    async def _avatar_crop_dialog_visible(self, timeout_seconds: float) -> bool:
        """TikTok answers an accepted photo with its "Edit photo" crop dialog."""
        apply = self._page.get_by_role("button", name=re.compile(r"^\s*(Apply|Áp dụng)\s*$", re.I))
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            try:
                if (
                    await asyncio.wait_for(apply.count(), timeout=2.0)
                    and await asyncio.wait_for(apply.first.is_visible(), timeout=2.0)
                ):
                    return True
            except Exception:
                pass
            if loop.time() >= deadline:
                return False
            await asyncio.sleep(0.5)

    async def _attach_avatar_file(self, avatar_input, path: str) -> None:
        """Give the edit-profile dialog its new photo.

        ⛔ DIRECT INPUT FIRST, AND NEVER A GUESSED TRIGGER. The native chooser
        used to be opened by whatever `_resolve_native_upload_trigger` found,
        and on the profile page that was the sidebar's "Upload" nav button -
        a VIDEO upload link lying under the edit dialog. The click waited on
        an element the dialog covers ("Windows file chooser did not appear
        (click command remained pending)", lexie_39_lipton 2026-09-17); had
        it landed it would have left the page. `set_input_files` on the hidden
        input attached the photo in 0.0s and TikTok opened its crop dialog.
        The native chooser stays as a fallback, opened only by the avatar's own
        edit icon inside the dialog.
        """
        try:
            handle = await asyncio.wait_for(
                avatar_input.element_handle(timeout=5000), timeout=5.5
            )
            if handle is None:
                raise RuntimeError("Input anh dai dien da bien mat.")
            await handle.set_input_files([path], timeout=15000)
            if await self._avatar_crop_dialog_visible(timeout_seconds=10.0):
                logger.info("[+] Da gan file avatar qua input truc tiep.")
                return
            logger.warning("[-] Da gan avatar truc tiep nhung khung cat anh chua hien; thu hop thoai native.")
        except Exception as exc:
            logger.warning("[-] Gan avatar qua input truc tiep that bai (%s); thu hop thoai native.", exc)

        trigger = self._page.locator(self._AVATAR_EDIT_ICON).first
        await trigger.wait_for(state="visible", timeout=5000)
        owner_process_ids = await asyncio.to_thread(self._native_upload_process_ids)
        await set_input_files_native(
            avatar_input,
            [path],
            trigger=trigger,
            owner_process_ids=owner_process_ids or None,
            owner_session_token=getattr(self._invisible_pw, "_session_token", None),
            on_dialog_active=self._set_native_dialog_active,
            trigger_dwell_ms=random.randint(160, 420),
            trigger_click_delay_ms=random.randint(70, 160),
            timeout_ms=15000,
        )
        logger.info("[+] Da gan file avatar bang native chooser an.")

    @staticmethod
    async def _is_unobstructed(candidate) -> bool:
        """False when something else (a dialog, an overlay) covers its centre.

        A click on a covered element never completes - it waits for the
        element to receive pointer events. Unknown stays True, as before.
        """
        try:
            return bool(await asyncio.wait_for(
                candidate.evaluate(
                    """el => {
                        const r = el.getBoundingClientRect();
                        if (!r.width || !r.height) return false;
                        const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                        return !!top && (top === el || el.contains(top));
                    }"""
                ),
                timeout=2.0,
            ))
        except Exception:
            return True

    async def _resolve_native_upload_trigger(self, target, media_kind: str):
        """Find the visible control a person clicks to open ``target``."""
        semantic_pattern = re.compile(
            (
                r"^\s*(Select video|Choose video|Upload video|Chọn video|Tải video lên)\s*$"
                if media_kind == "video"
                else r"^\s*(Select photos?|Choose photos?|Upload photos?|Chọn ảnh|Tải ảnh lên)\s*$"
            ),
            re.I,
        )
        semantic_candidates = []
        try:
            semantic_candidates.append(
                self._page.get_by_role("button", name=semantic_pattern).first
            )
        except Exception:
            pass
        try:
            semantic_candidates.append(self._page.get_by_text(semantic_pattern).first)
        except Exception:
            pass
        for candidate in semantic_candidates:
            try:
                if (
                    await asyncio.wait_for(candidate.count(), timeout=2.0)
                    and await asyncio.wait_for(candidate.is_visible(), timeout=2.0)
                    and await asyncio.wait_for(candidate.is_enabled(), timeout=2.0)
                    and await self._is_unobstructed(candidate)
                ):
                    return candidate
            except Exception:
                continue

        # Compatibility fallback: derive the control linked to the file input
        # only when Studio exposes no stable role/text for its visible button.
        try:
            handle = await asyncio.wait_for(
                target.element_handle(timeout=3000), timeout=3.5
            )
            if handle is not None:
                candidate_handle = await asyncio.wait_for(
                    handle.evaluate_handle(
                        """
                        input => {
                            const linked = input.labels && input.labels.length
                                ? input.labels[0]
                                : input.closest('label');
                            if (linked) return linked;
                            let node = input.parentElement;
                            while (node && node !== document.body) {
                                const rect = node.getBoundingClientRect();
                                const style = getComputedStyle(node);
                                const role = node.getAttribute('role');
                                const clickable = node.tagName === 'BUTTON'
                                    || role === 'button'
                                    || node.tabIndex >= 0
                                    || style.cursor === 'pointer';
                                const visible = style.display !== 'none'
                                    && style.visibility !== 'hidden'
                                    && rect.width >= 8 && rect.height >= 8;
                                if (clickable && visible) return node;
                                node = node.parentElement;
                            }
                            return null;
                        }
                        """
                    ),
                    timeout=3.0,
                )
                candidate = candidate_handle.as_element()
                if (
                    candidate is not None
                    and await asyncio.wait_for(candidate.is_visible(), timeout=2.0)
                ):
                    return candidate
        except Exception:
            pass

        text = (
            r"select\s+(video|file)|choose\s+(video|file)|upload|"
            r"ch[oọ]n\s+(video|t[eệ]p|[aả]nh)|t[aả]i\s+l[eê]n"
        )
        for selector in ("label", "button", '[role="button"]'):
            try:
                candidates = self._page.locator(selector).filter(
                    has_text=re.compile(text, re.I)
                )
                count = await asyncio.wait_for(candidates.count(), timeout=2.0)
                for index in range(min(count, 12)):
                    candidate = candidates.nth(index)
                    if (
                        await asyncio.wait_for(candidate.is_visible(), timeout=2.0)
                        and await asyncio.wait_for(candidate.is_enabled(), timeout=2.0)
                        # "upload" also matches the sidebar nav link; under a
                        # dialog it is covered and a click on it never returns.
                        and await self._is_unobstructed(candidate)
                    ):
                        return candidate
            except Exception:
                continue
        raise RuntimeError(
            f"Khong tim thay nut chon {media_kind} dang hien thi cho file input."
        )

    async def _bridge_native_upload_trigger(self, target, trigger) -> None:
        """Make a trusted trigger synchronously open the exact hidden input.

        TikTok Studio can render its Select video button before its React click
        handler is attached. A Playwright click then succeeds but performs no
        action. Bind one capture listener to the already-resolved button. The
        real pointer click remains the user-activation source; the listener
        only connects that activation to the exact file input synchronously.
        """
        input_handle = await target.element_handle(timeout=5_000)
        trigger_handle = await trigger.element_handle(timeout=5_000)
        if input_handle is None or trigger_handle is None:
            raise RuntimeError("Upload input or trigger disappeared before click.")
        bridge_id = f"tkauto-{uuid.uuid4().hex}"
        await trigger_handle.evaluate(
            "(element, value) => element.setAttribute('data-tkauto-native-trigger', value)",
            bridge_id,
        )

        await input_handle.evaluate(
            """(input, bridgeId) => {
                const trigger = input.ownerDocument.querySelector(
                    `[data-tkauto-native-trigger="${bridgeId}"]`
                );
                if (!trigger) throw new Error('Native upload trigger is not in the input document');
                if (input === trigger) return;
                trigger.addEventListener('click', event => {
                    trigger.removeAttribute('data-tkauto-native-trigger');
                    event.preventDefault();
                    event.stopImmediatePropagation();
                    if (input.isConnected && !input.disabled) input.click();
                }, {capture: true, once: true});
            }""",
            bridge_id,
        )

    def _native_upload_process_ids(self) -> list[int]:
        """Return every process stamped with this browser session token."""
        token = getattr(self._invisible_pw, "_session_token", None)
        if not token:
            return []
        try:
            from invisible_core.process import find_processes

            return [int(process.pid) for process in find_processes(token)]
        except Exception as exc:
            logger.debug(
                "Khong doc duoc PID cay Firefox de khoa native dialog: %s", exc
            )
            return []

    async def _wait_media_input_accepted(
        self,
        target,
        expected_count: int,
        timeout_seconds: float = 15.0,
    ) -> bool:
        """Verify a native selection even when TikTok replaces its React input."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                actual = await target.evaluate(
                    "element => element.files.length",
                    timeout=500,
                )
                if int(actual) == expected_count:
                    return True
            except Exception:
                pass
            try:
                editor = self._page.locator(
                    '.public-DraftEditor-content, [contenteditable="true"]'
                ).first
                progress = self._page.locator('[role="progressbar"]').first
                uploading = self._page.get_by_text(
                    re.compile(r"uploading|đang tải lên", re.I)
                ).first
                if (
                    (
                        await asyncio.wait_for(editor.count(), timeout=2.0)
                        and await asyncio.wait_for(editor.is_visible(), timeout=2.0)
                    )
                    or (
                        await asyncio.wait_for(progress.count(), timeout=2.0)
                        and await asyncio.wait_for(progress.is_visible(), timeout=2.0)
                    )
                    or (
                        await asyncio.wait_for(uploading.count(), timeout=2.0)
                        and await asyncio.wait_for(uploading.is_visible(), timeout=2.0)
                    )
                ):
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.25)
        return False

    async def _set_files_via_native_dialog(self, paths: List[str], media_kind: str) -> bool:
        """Attach media the way a person does: click Select video, use the chooser.

        ⛔ THE BUTTON IS THE PATH, NOT set_input_files. Handing the file
        straight to the hidden input skips everything TikTok can see a person
        doing - the pointer arriving at the button, the trusted click, the
        Windows chooser opening and closing. The operator posts by hand
        through this same browser and sees those posts distributed normally,
        so the automated run must take the same route (2026-09-22). Attaching
        to the input is kept only for the case where two button clicks cannot
        open the chooser at all, because a post that did not happen helps
        nobody; it is logged loudly when it happens.
        """
        self._last_native_upload_error = None
        self._last_attached_media_names = []
        abs_paths = [os.path.abspath(os.path.expanduser(path)) for path in paths]
        if not abs_paths:
            raise ValueError("Khong co file de tai len.")
        missing = [path for path in abs_paths if not os.path.isfile(path)]
        if missing:
            raise FileNotFoundError(f"Khong tim thay file: {missing[0]}")

        last_error = None
        for attempt in (1, 2):
            try:
                if await self._attach_media_by_clicking_button(abs_paths, media_kind):
                    return True
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "[Upload] Lan %d bam nut chon %s khong mo duoc hop thoai: %s",
                    attempt,
                    media_kind,
                    exc,
                )
            await asyncio.sleep(0.6)

        logger.warning(
            "[Upload] Hai lan bam nut chon %s deu hong; gan thang file vao input "
            "(du phong, khong giong thao tac nguoi). Loi cuoi: %s",
            media_kind,
            last_error,
        )
        if await self._attach_media_via_input_channel(abs_paths, media_kind):
            return True
        if last_error is not None:
            self._last_native_upload_error = f"{type(last_error).__name__}: {last_error}"
        return False

    async def _attach_media_via_input_channel(self, abs_paths: List[str], media_kind: str) -> bool:
        """Fallback only: hand the files to the hidden input (no button click)."""
        last_error = None
        for attempt in range(1, 2):
            try:
                inputs = self._page.locator('input[type="file"]')
                count = await asyncio.wait_for(inputs.count(), timeout=3.0)
                if not count:
                    await inputs.first.wait_for(state="attached", timeout=15000)
                    count = await asyncio.wait_for(inputs.count(), timeout=3.0)
                target = None
                for index in range(count):
                    candidate = inputs.nth(index)
                    accept = ((await candidate.get_attribute(
                        "accept", timeout=2000
                    )) or "").lower()
                    matches = (
                        media_kind == "photo"
                        and ("image" in accept or ".jpg" in accept or ".png" in accept)
                    ) or (
                        media_kind == "video"
                        and ("video" in accept or ".mp4" in accept or ".mov" in accept)
                    )
                    if matches:
                        target = candidate
                        break
                target = target or inputs.first
                handle = await asyncio.wait_for(
                    target.element_handle(timeout=5000), timeout=5.5
                )
                if handle is None:
                    raise RuntimeError("Input file da bien mat.")
                await handle.set_input_files(abs_paths, timeout=15000)
                self._last_attached_media_names = [
                    os.path.basename(path) for path in abs_paths
                ]
                logger.info(
                    "[Upload] Da gan %s qua Playwright input channel (lan %d).",
                    media_kind,
                    attempt,
                )
                return True
            except Exception as exc:
                last_error = exc
                # TikTok React may replace the input after accepting the file.
                # Check the editor/progress state before resolving and retrying.
                for _ in range(5):
                    await asyncio.sleep(0.6)
                    try:
                        editor = self._page.locator(
                            '.public-DraftEditor-content, [contenteditable="true"]'
                        ).first
                        progress = self._page.locator('[role="progressbar"]').first
                        uploading = self._page.get_by_text(
                            re.compile(r"uploading|đang tải lên", re.I)
                        ).first
                        accepted = (
                            (
                                await asyncio.wait_for(editor.count(), timeout=2.0)
                                and await asyncio.wait_for(editor.is_visible(), timeout=2.0)
                            )
                            or (
                                await asyncio.wait_for(progress.count(), timeout=2.0)
                                and await asyncio.wait_for(progress.is_visible(), timeout=2.0)
                            )
                            or (
                                await asyncio.wait_for(uploading.count(), timeout=2.0)
                                and await asyncio.wait_for(uploading.is_visible(), timeout=2.0)
                            )
                        )
                        if accepted:
                            self._last_attached_media_names = [
                                os.path.basename(path) for path in abs_paths
                            ]
                            logger.info("[Upload] TikTok da nhan file sau khi thay input React.")
                            return True
                    except Exception:
                        pass

        return False

    async def _attach_media_by_clicking_button(
        self, abs_paths: List[str], media_kind: str
    ) -> bool:
        """Click TikTok's own Select button and fill the Windows chooser."""
        # B178: the real-path protocol command is still broken in firefox-21.
        # Use the helper kept in our vendored invisible_playwright build
        # source. It opens the real Windows chooser, DWM-cloaks it immediately,
        # keeps the chooser offscreen/cloaked, and preserves trusted
        # input/change events for files of any size.
        try:
            # The native helper writes and verifies UTF-16 text directly in
            # control 1148. Keep the original paths: copying Unicode-named
            # videos to an ASCII alias added disk I/O and made chooser timing
            # less deterministic, especially when media and profile are on
            # different drives.
            native_paths = self._stage_native_upload_paths(abs_paths)
            inputs = self._page.locator('input[type="file"]')
            count = await asyncio.wait_for(inputs.count(), timeout=3.0)
            target = inputs.first
            for index in range(count):
                candidate = inputs.nth(index)
                accept = ((await candidate.get_attribute(
                    "accept", timeout=2000
                )) or "").lower()
                matches = (
                    media_kind == "photo"
                    and ("image" in accept or ".jpg" in accept or ".png" in accept)
                ) or (
                    media_kind == "video"
                    and ("video" in accept or ".mp4" in accept or ".mov" in accept)
                )
                if matches:
                    target = candidate
                    break
            trigger = await self._resolve_native_upload_trigger(target, media_kind)
            owner_process_ids = await asyncio.to_thread(
                self._native_upload_process_ids
            )
            owner_session_token = getattr(
                self._invisible_pw, "_session_token", None
            )
            # The chooser opens on the browser's own desktop from
            # invisible_playwright 0.24; older builds have no such desktop and
            # no such argument, so it is only passed when there is one.
            desktop_kwargs = (
                {"desktop": self._browser_desktop} if self._browser_desktop else {}
            )

            async def open_native_chooser() -> None:
                await set_input_files_native(
                    target,
                    native_paths,
                    trigger=trigger,
                    allow_input_replacement=True,
                    owner_process_ids=owner_process_ids or None,
                    owner_session_token=owner_session_token,
                    on_dialog_active=self._set_native_dialog_active,
                    trigger_dwell_ms=random.randint(160, 420),
                    trigger_click_delay_ms=random.randint(70, 160),
                    timeout_ms=15000,
                    **desktop_kwargs,
                )

            try:
                # Let TikTok's own visible Select video button open the native
                # chooser. The helper performs the curved pointer approach,
                # dwell and held click only after this account owns the global
                # chooser lock, so parallel account sessions cannot interleave.
                try:
                    await open_native_chooser()
                except Exception as direct_trigger_error:
                    if (
                        "windows file chooser did not appear"
                        not in str(direct_trigger_error).casefold()
                    ):
                        raise
                    # A few Studio builds paint the button before React binds
                    # its handler. Preserve the manual direct click as the
                    # primary path and install the one-shot bridge only for
                    # that proven missing-handler case.
                    logger.info(
                        "[Upload] Nut Select video chua mo dialog; "
                        "gan cau noi mot lan roi click lai."
                    )
                    await self._bridge_native_upload_trigger(target, trigger)
                    await open_native_chooser()
            except Exception as native_error:
                # TikTok sometimes replaces the accepted file input with a new
                # empty input before the helper reads ``files.length``. In that
                # case the old locator reports 0/1 even though the editor or
                # progress bar is already mounting. Check page state before
                # turning the helper's diagnostic into a task failure.
                if (
                    "input contains" not in str(native_error).casefold()
                    or not await self._wait_media_input_accepted(
                        target, len(abs_paths), timeout_seconds=15.0
                    )
                ):
                    raise
                logger.info(
                    "[Upload] TikTok da thay input sau native chooser; "
                    "chap nhan editor/progress lam xac nhan."
                )
            if not await self._wait_media_input_accepted(target, len(abs_paths)):
                raise RuntimeError(
                    "Native chooser da dong nhung TikTok khong hien editor/progress."
                )
            self._last_attached_media_names = [
                os.path.basename(path) for path in native_paths
            ]
            logger.info(
                "[Upload] Da gan %d file %s qua native chooser DWM-cloaked.",
                len(abs_paths),
                media_kind,
            )
            return True
        except Exception as exc:
            self._last_native_upload_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "[Upload] Native chooser that bai: %s (%r)", exc, exc,
                exc_info=True,
            )
            # The caller decides whether to press the button again or fall
            # back; it cannot do that from a bare False.
            raise

    def _stage_native_upload_paths(self, paths: List[str]) -> List[str]:
        """Keep original media paths, including supplementary Unicode.

        ``native_upload`` now uses Unicode-safe Win32 messages and confirms
        the File name control before accepting the chooser. This compatibility
        hook deliberately performs no hard-link or cross-drive copy.
        """
        return list(paths)

    async def _set_file_via_native_dialog(self, video_path: str) -> bool:
        """Attach one video. The retries live in _set_files_via_native_dialog:
        the Select video button twice, then the input channel as a last resort."""
        return await self._set_files_via_native_dialog([video_path], "video")

    async def _human_click(self, locator, timeout: int = 5000) -> None:
        """Approach a control with the pointer, pause briefly, then click it."""
        try:
            box = await asyncio.wait_for(locator.bounding_box(), timeout=2.5)
        except Exception:
            box = None
        if box:
            x = box["x"] + box["width"] * random.uniform(0.38, 0.62)
            y = box["y"] + box["height"] * random.uniform(0.35, 0.65)
            await self._page.mouse.move(x, y, steps=random.randint(6, 14))
            await asyncio.sleep(random.uniform(0.12, 0.32))
        await locator.click(
            timeout=timeout,
            no_wait_after=True,
            delay=random.randint(70, 160),
        )
        await asyncio.sleep(random.uniform(0.35, 0.75))

    async def _read_visible_page_text(self, timeout_ms: int = 1500) -> Optional[str]:
        """Read rendered text with Playwright's own protocol timeout.

        Do not wrap an unbounded DOM command in asyncio.wait_for: some driver
        calls do not finish cancellation and can retain the Playwright channel.
        ``inner_text(timeout=...)`` times out inside Playwright instead.
        """
        if not self._page:
            return None
        try:
            return await self._page.locator("body").inner_text(timeout=timeout_ms)
        except Exception:
            return None

    async def _dismiss_upload_popups(self, max_actions: int = 8) -> int:
        """Accept safe Studio upload coachmarks until none remain.

        TikTok can render ``Turn on`` and then ``Got it`` as consecutive
        overlays. Only these affirmative, non-publishing actions (and their
        translations) are accepted here. Generic Cancel/Continue buttons are
        excluded so this helper cannot cancel or publish content by itself.
        """
        action_patterns = (
            # ``visible_text`` is the entire BODY innerText. MULTILINE keeps
            # the exact-label safety boundary while allowing a button label
            # to match one line inside the rest of Studio's page copy.
            ("Turn on", re.compile(r"^\s*(Turn on|Enable|Bật|Bật lên)\s*$", re.I | re.M)),
            ("Got it", re.compile(r"^\s*(Got it|I understand|OK|Okay|Đã hiểu|Tôi hiểu)\s*$", re.I | re.M)),
        )
        actions = 0
        actionable_deadline = time.monotonic() + 20.0
        while actions < max(1, max_actions):
            visible_text = await self._read_visible_page_text()
            if visible_text is None:
                return actions
            selected = next(
                (
                    (action_name, pattern)
                    for action_name, pattern in action_patterns
                    if pattern.search(visible_text)
                ),
                None,
            )
            if selected is None:
                return actions

            action_name, pattern = selected
            clicked = False
            candidates = []
            try:
                candidates.append(self._page.get_by_role("button", name=pattern).first)
            except Exception:
                pass
            # Exact-text BUTTON fallback only. Never click an arbitrary parent
            # element or a generated TikTok class.
            candidates.append(
                self._page.locator("button:visible").filter(has_text=pattern).first
            )
            for button in candidates:
                try:
                    await button.click(
                        timeout=2500,
                        no_wait_after=True,
                        delay=random.randint(70, 160),
                    )
                    actions += 1
                    clicked = True
                    logger.info("[UPLOAD] Đã chấp nhận popup: %s", action_name)
                    await asyncio.sleep(random.uniform(0.35, 0.75))
                    break
                except Exception:
                    continue
            if clicked:
                continue
            if time.monotonic() >= actionable_deadline:
                raise RuntimeError(
                    "Popup Turn on/Got it đang hiện nhưng nút không bấm được sau 20 giây."
                )
            await asyncio.sleep(0.4)
        return actions

    async def _handle_upload_interruptions(
        self,
        step_logger=None,
        captcha_timeout: float = 120.0,
    ) -> int:
        """Resolve optional upload interruptions without delaying the happy path.

        Turn on/Got it are always accepted when present. CAPTCHA is only
        waited on when it is actually visible; otherwise this returns
        immediately. The integer result is non-zero when an overlay or CAPTCHA
        interrupted the current action, allowing the caller to retry safely.
        """
        async def log(message: str) -> None:
            if step_logger:
                await step_logger(message)

        interruptions = await self._dismiss_upload_popups()
        visible_text = await self._read_visible_page_text()
        captcha_pattern = re.compile(
            r"drag the slider|kéo thanh trượt|ghép hình|verify to continue|"
            r"xác minh để tiếp tục|slide to complete|rotate the shapes|xoay hình",
            re.I,
        )
        if visible_text is None or not captcha_pattern.search(visible_text):
            return interruptions

        interruptions = max(1, interruptions)
        await log(
            "Phát hiện CAPTCHA trong lúc upload -> tạm dừng và chờ extension xử lý..."
        )
        deadline = time.monotonic() + max(1.0, captcha_timeout)
        while time.monotonic() < deadline:
            await self._wait_automation_gate()
            await asyncio.sleep(1.0)
            current_text = await self._read_visible_page_text()
            if current_text is not None and not captcha_pattern.search(current_text):
                await log("[+] CAPTCHA đã được xử lý -> tiếp tục đúng bước đang làm.")
                await asyncio.sleep(0.7)
                # CAPTCHA can reveal a queued coachmark immediately after it
                # disappears. Clear that prerequisite before returning.
                interruptions += await self._dismiss_upload_popups()
                return interruptions
        raise RuntimeError(
            f"CAPTCHA vẫn còn sau {int(captcha_timeout)} giây; không tiếp tục thao tác phía sau."
        )

    async def _confirm_post_now_popup(self) -> Optional[bool]:
        """Confirm TikTok's final immediate-publish dialog, if it is visible.

        This publishing action is deliberately separate from the coachmark
        helper and is only called after the primary Post button was clicked.
        Returns True when clicked, False when absent, and None when the popup
        is visible but its button is not actionable yet.
        """
        pattern = re.compile(
            r"^\s*(Post now|Publish now|Đăng ngay)\s*$", re.I | re.M
        )
        visible_text = await self._read_visible_page_text()
        if visible_text is None or not pattern.search(visible_text):
            return False
        candidates = []
        try:
            candidates.append(self._page.get_by_role("button", name=pattern).first)
        except Exception:
            pass
        candidates.append(self._page.locator("button:visible").filter(has_text=pattern).first)
        popup_visible = False
        for button in candidates:
            try:
                await button.wait_for(state="visible", timeout=700)
                popup_visible = True
                await button.click(
                    timeout=2500,
                    no_wait_after=True,
                )
                logger.info("[UPLOAD] Đã xác nhận popup Post now.")
                return True
            except Exception:
                continue
        return None if popup_visible else False

    async def _read_publish_blocking_failure(self) -> Optional[Tuple[str, str]]:
        """Return an explicit publish rejection shown after the primary Post click."""
        visible_text = await self._read_visible_page_text()
        if not visible_text:
            return None
        duplicate_pattern = re.compile(
            r"duplicate (?:video|content)|(?:video|content) (?:is |was )?duplicate|"
            r"(?:you(?:'ve| have) )?already (?:posted|uploaded) (?:this|the) video|"
            r"this video (?:has )?already been (?:posted|uploaded)|"
            r"previously (?:posted|uploaded) video|"
            r"(?:video|nội dung) (?:bị |là )?trùng(?: lặp)?|"
            r"(?:bạn )?đã đăng video này|video này đã (?:được )?đăng|"
            r"(?:video|konten) duplikat|video ini sudah pernah (?:diposting|diunggah)|"
            r"vídeo duplicado|você já publicou (?:este|esse) vídeo|"
            r"(?:este|esse) vídeo já foi publicado",
            re.I,
        )
        match = duplicate_pattern.search(visible_text)
        if not match:
            return None
        matching_line = next(
            (
                " ".join(line.split())
                for line in visible_text.splitlines()
                if duplicate_pattern.search(line)
            ),
            " ".join(match.group(0).split()),
        )
        return "VIDEO_DUPLICATE", matching_line[:300]

    async def _read_publish_notice_texts(self) -> List[str]:
        """Return visible modal/toast text without treating page copy as a notice."""
        selector = (
            '[role="dialog"]:visible, [role="alert"]:visible, '
            '[data-e2e*="toast" i]:visible, [class*="toast" i]:visible, '
            '[class*="notification" i]:visible'
        )
        notices: List[str] = []
        try:
            candidates = self._page.locator(selector)
            count = min(await candidates.count(), 12)
            for index in range(count):
                candidate = candidates.nth(index)
                try:
                    if not await candidate.is_visible():
                        continue
                    raw_text = await candidate.inner_text(timeout=500)
                except Exception:
                    continue
                text = " ".join((raw_text or "").split()).strip()
                if text and text.casefold() not in {
                    value.casefold() for value in notices
                }:
                    notices.append(text[:500])
        except Exception:
            pass
        return notices

    async def _read_unexpected_notice_after_post(
        self,
        notices_before_post: set[str],
    ) -> Optional[str]:
        """Treat a new unknown notice without Post now as duplicate feedback.

        The queue contract supplied by the operator is UI-based: after the
        primary Post click, the normal immediate flow exposes Post now. A new
        modal/toast instead means this media must be replaced. Known transient
        prerequisites are excluded because they are handled independently.
        """
        ignored_pattern = re.compile(
            r"\b(?:post now|publish now|đăng ngay|turn on|got it|"
            r"verify to continue|slide to complete|drag the slider|captcha|"
            r"uploading|content check|checking your video)\b",
            re.I,
        )
        for notice in await self._read_publish_notice_texts():
            normalized = notice.casefold()
            if normalized in notices_before_post or ignored_pattern.search(notice):
                continue
            return notice
        return None

    async def _publish_success_visible(self) -> bool:
        """Accept only an explicit completed-publish message.

        TikTok's confirmation dialog can say that a video *will be published*.
        A loose ``published`` match treats that future-tense dialog copy as a
        success and leaves for Studio Posts before clicking ``Post now``.
        """
        visible_text = await self._read_visible_page_text()
        if visible_text is None:
            return False
        return bool(re.search(
            r"^\s*(?:"
            r"your (?:video|post) (?:has been|was) (?:posted|published)|"
            r"(?:video|post) (?:posted|published) successfully|"
            r"(?:post|publish|schedule) successful(?:ly)?|"
            r"your (?:video|post) is (?:being processed|processing)|"
            r"video submitted for review|"
            r"đã (?:đăng|xuất bản|lên lịch) thành công|"
            r"(?:video|bài đăng) đang được xử lý"
            r")\s*[.!]?\s*$",
            visible_text,
            re.I | re.M,
        ))

    #: The controls a person clicks to reach the upload screen: the sidebar
    #: entry on For You, Studio's own Upload button, or a link to either page.
    _UPLOAD_ENTRY_SELECTOR = (
        '[data-e2e="nav-upload"], [data-e2e="upload-icon"], '
        'a[href*="/tiktokstudio/upload"], a[href="/upload"], a[href^="/upload?"], '
        'button:has-text("Upload"), div[role="button"]:has-text("Upload")'
    )

    async def _wait_upload_page_open(self, timeout_seconds: float = 25.0) -> bool:
        """The upload screen is open when its own file entry is mounted."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            url = str(getattr(self._page, "url", "") or "").casefold()
            if "/login" in url and "redirect_url" in url:
                return False
            if "/upload" in url and await self._video_upload_entry_ready():
                return True
            await asyncio.sleep(0.5)
        return False

    async def _open_studio_upload_page(self, step_logger=None) -> None:
        """Reach the upload screen by clicking Upload, as a person does.

        ⛔ TYPING THE URL IS NOT A VISIT. A person arrives at the upload
        screen from the page they were on, by pointing at Upload and
        clicking it; the app used to jump straight to
        /tiktokstudio/upload, which no session of a real account ever does.
        The URL stays as the fallback, because an account that cannot find
        the button must still be able to post.
        """
        async def log(message):
            if step_logger:
                await step_logger(message)

        candidates = self._page.locator(self._UPLOAD_ENTRY_SELECTOR)
        try:
            count = await asyncio.wait_for(candidates.count(), timeout=3.0)
        except Exception:
            count = 0
        for index in range(min(count, 6)):
            candidate = candidates.nth(index)
            try:
                if not await asyncio.wait_for(candidate.is_visible(), timeout=2.0):
                    continue
                if not await self._is_unobstructed(candidate):
                    continue
                await log("Đang bấm nút Upload trên TikTok để mở màn đăng bài...")
                await self._human_click(candidate, timeout=10000)
            except Exception as exc:
                logger.debug("[Upload] Nut Upload thu %d khong bam duoc: %s", index, exc)
                continue
            if await self._wait_upload_page_open():
                logger.info("[Upload] Da vao man dang bai bang nut Upload.")
                return
        logger.warning(
            "[Upload] Khong bam duoc nut Upload nao; mo thang URL man dang bai."
        )
        await log("Không thấy nút Upload trên trang; mở thẳng màn đăng bài...")
        await self.navigate_to("https://www.tiktok.com/tiktokstudio/upload?lang=en")

    async def _video_upload_entry_ready(self) -> bool:
        """Detect Studio's upload entry by semantics, then its real file input."""
        visible_text = await self._read_visible_page_text()
        if visible_text is not None and re.search(
            r"\b(Select video to upload|Select video|Choose (?:a )?video(?: to upload)?|"
            r"Chọn video(?: để tải lên)?|Tải video lên)\b",
            visible_text,
            re.I,
        ):
            return True

        # Some Studio variants attach the real input without the old prompt.
        # This is a read-only check; it never clicks a probe element.
        try:
            inputs = self._page.locator('input[type="file"]')
            count = await asyncio.wait_for(inputs.count(), timeout=2.0)
            for index in range(min(count, 8)):
                accept = ((await inputs.nth(index).get_attribute(
                    "accept", timeout=1500
                )) or "").lower()
                if "video" in accept or ".mp4" in accept or ".mov" in accept:
                    return True
        except Exception:
            pass
        return False

    def _publish_button(self, scheduled: bool = False):
        """Resolve the currently rendered primary action, never a hidden clone."""
        labels = (r"Post|Publish|Đăng|Schedule|Lên lịch" if scheduled
                  else r"Post|Publish|Đăng")
        return self._page.locator("button:visible").filter(
            has_text=re.compile(rf"^\s*({labels})\s*$", re.IGNORECASE)
        ).first

    async def _upload_editor_is_active(self) -> bool:
        """Return whether the current page still shows the pre-publish editor."""
        try:
            editor = self._page.locator(
                '.public-DraftEditor-content, [contenteditable="true"]'
            ).first
            return bool(
                await editor.count()
                and await editor.is_visible()
            )
        except Exception:
            return False

    def _session_page_candidates(self) -> List[Any]:
        """Return every live tab, newest first, without duplicating self._page."""
        candidates: List[Any] = []
        try:
            browser_pages = list(getattr(self._browser, "pages", None) or [])
        except Exception:
            browser_pages = []
        for page in reversed(browser_pages):
            if page is not None and all(page is not item for item in candidates):
                candidates.append(page)
        if self._page is not None and all(
            self._page is not item for item in candidates
        ):
            candidates.append(self._page)
        return candidates

    def _adopt_studio_posts_page_by_url(self):
        """Track TikTok's Posts tab even when publishing opened a new page."""
        for page in self._session_page_candidates():
            try:
                if _is_studio_posts_url(getattr(page, "url", "")):
                    self._page = page
                    return page
            except Exception:
                continue
        return None

    async def _adopt_studio_posts_page_by_ui(
        self,
        expected_values: List[str],
    ):
        """Use rendered Posts UI when Firefox reports a stale SPA URL."""
        for page in self._session_page_candidates():
            try:
                body_text = await asyncio.wait_for(
                    page.locator("body").inner_text(timeout=500),
                    timeout=0.7,
                )
                if not _studio_posts_body_ready(body_text, expected_values):
                    continue
                editor = page.locator(
                    '.public-DraftEditor-content, [contenteditable="true"]'
                ).first
                editor_count = await asyncio.wait_for(editor.count(), timeout=0.35)
                if editor_count and await asyncio.wait_for(
                    editor.is_visible(), timeout=0.35
                ):
                    continue
                self._page = page
                return page
            except Exception:
                continue
        return None

    async def _collect_post_submit_diagnostics(
        self,
        expected_values: List[str],
    ) -> List[Dict[str, Any]]:
        """Capture a small, read-only snapshot when Studio never redirects.

        This is deliberately collected only on the failure path. It records
        enough rendered state to distinguish an unclicked confirmation dialog,
        a still-active editor, and a stale URL without dumping the whole page.
        """
        snapshots: List[Dict[str, Any]] = []
        for index, page in enumerate(self._session_page_candidates(), start=1):
            snapshot: Dict[str, Any] = {
                "tab": index,
                "url": str(getattr(page, "url", "") or "")[:240],
                "editor_visible": False,
                "dialog": "",
                "buttons": [],
                "expected_text_visible": False,
                "body": "",
            }
            body_text = ""
            try:
                body_text = await asyncio.wait_for(
                    page.locator("body").inner_text(timeout=800),
                    timeout=1.0,
                )
            except Exception:
                pass
            normalized_body = " ".join((body_text or "").split())
            snapshot["body"] = normalized_body[:900]
            snapshot["expected_text_visible"] = any(
                _studio_post_text_matches(expected, body_text)
                for expected in expected_values
                if expected
            )
            try:
                editor = page.locator(
                    '.public-DraftEditor-content, [contenteditable="true"]'
                ).first
                snapshot["editor_visible"] = bool(
                    await asyncio.wait_for(editor.count(), timeout=0.4)
                    and await asyncio.wait_for(editor.is_visible(), timeout=0.4)
                )
            except Exception:
                pass
            try:
                dialog = page.locator('[role="dialog"]:visible').first
                if (
                    await asyncio.wait_for(dialog.count(), timeout=0.4)
                    and await asyncio.wait_for(dialog.is_visible(), timeout=0.4)
                ):
                    dialog_text = await asyncio.wait_for(
                        dialog.inner_text(timeout=500), timeout=0.7
                    )
                    snapshot["dialog"] = " ".join(dialog_text.split())[:500]
            except Exception:
                pass
            try:
                buttons = page.locator("button:visible")
                button_count = min(
                    await asyncio.wait_for(buttons.count(), timeout=0.5), 20
                )
                labels: List[str] = []
                for button_index in range(button_count):
                    try:
                        label = await asyncio.wait_for(
                            buttons.nth(button_index).inner_text(timeout=350),
                            timeout=0.5,
                        )
                    except Exception:
                        continue
                    label = " ".join((label or "").split())
                    if label and label not in labels:
                        labels.append(label[:100])
                snapshot["buttons"] = labels
            except Exception:
                pass
            snapshots.append(snapshot)
        self.last_publish_diagnostics = snapshots
        return snapshots

    async def _select_existing_hashtag_suggestion(
        self,
        token: str,
        *,
        excluded_tokens: Optional[List[str]] = None,
        timeout_seconds: float = 4.0,
    ) -> Optional[str]:
        """Click a live Studio suggestion without typing or changing caption text."""
        candidates = self._page.locator(
            "[role='listbox']:visible [role='option']:visible, "
            "[role='menu']:visible [role='menuitem']:visible, "
            "[role='option']:visible, "
            "[data-e2e*='hashtag' i]:visible, "
            "[data-e2e*='search' i]:visible [role='button']:visible"
        )
        deadline = time.monotonic() + max(0.2, timeout_seconds)
        while time.monotonic() < deadline:
            try:
                count = await candidates.count()
            except Exception:
                count = 0
            visible_indexes: List[int] = []
            suggestion_texts: List[str] = []
            for index in range(min(count, 40)):
                candidate = candidates.nth(index)
                try:
                    if not await candidate.is_visible():
                        continue
                    text_value = " ".join(
                        (await candidate.inner_text(timeout=800)).split()
                    )
                    if text_value:
                        visible_indexes.append(index)
                        suggestion_texts.append(text_value)
                except Exception:
                    continue
            choice = choose_stable_hashtag_suggestion(
                token,
                suggestion_texts,
                excluded_tokens=excluded_tokens or (),
            )
            if choice is not None:
                try:
                    await candidates.nth(
                        visible_indexes[choice.source_index]
                    ).click(timeout=4000, no_wait_after=True)
                    logger.info(
                        "[UPLOAD] Hashtag %s -> %s (usage=%s)",
                        token,
                        choice.token,
                        choice.usage_count or "not-shown",
                    )
                    return choice.token
                except Exception:
                    pass
            await asyncio.sleep(0.2)
        return None

    async def _activate_filename_hashtags(
        self,
        media_name: str = "",
        step_logger=None,
    ) -> str:
        """Activate only hashtags already present in TikTok's auto-caption.

        Selecting a video makes Studio populate Description from the filename.
        Keep that text untouched. For each existing ``#token``, perform exactly
        one physical click inside its final character so Studio opens the native
        suggestion popup, then select the matching suggestion. This enhancement
        is best-effort: a missing popup leaves plain hashtag text intact and
        never blocks publishing. With no hashtag this method performs no editor
        input at all.
        """
        async def log(message: str) -> None:
            if step_logger:
                await step_logger(message)

        editor = self._page.locator(
            '.public-DraftEditor-content, [contenteditable="true"]'
        ).first
        await editor.wait_for(state="visible", timeout=30000)
        expected = Path(media_name).stem.strip() if media_name else ""
        caption = ""
        hydration_deadline = time.monotonic() + 12.0
        while time.monotonic() < hydration_deadline:
            caption = " ".join((await editor.inner_text(timeout=5000)).split())
            if caption and (
                not expected or _studio_post_text_matches(expected, caption)
            ):
                break
            await asyncio.sleep(0.25)
        if not caption:
            raise RuntimeError("TikTok chưa tự điền tên video vào caption.")
        if expected and not _studio_post_text_matches(expected, caption):
            raise RuntimeError(
                "Caption TikTok tự điền không khớp tên video; không đăng để tránh sai nội dung."
            )
        hashtags = _caption_hashtags(caption)
        if not hashtags:
            await log("Caption đã có sẵn từ tên video; không có hashtag cần kích hoạt.")
            return caption

        occurrences: list[tuple[str, int]] = []
        seen: dict[str, int] = {}
        for token in hashtags:
            occurrence = seen.get(token, 0)
            seen[token] = occurrence + 1
            occurrences.append((token, occurrence))

        selected: List[str] = []
        for token, occurrence in reversed(occurrences):
            await self._handle_upload_interruptions(step_logger=step_logger)
            try:
                point = await editor.evaluate(
                    r"""(element, target) => {
                      element.scrollIntoView({block: 'center', inline: 'nearest'});
                      const text = element.textContent || '';
                      let start = -1;
                      let from = 0;
                      for (let index = 0; index <= target.occurrence; index++) {
                        start = text.indexOf(target.token, from);
                        if (start < 0) return null;
                        from = start + target.token.length;
                      }
                      const end = start + target.token.length;
                      const walker = document.createTreeWalker(
                        element, NodeFilter.SHOW_TEXT
                      );
                      let consumed = 0;
                      let node = walker.nextNode();
                      while (node) {
                        const length = (node.nodeValue || '').length;
                        if (end <= consumed + length) {
                          const offset = Math.max(0, end - consumed);
                          const glyph = document.createRange();
                          glyph.setStart(node, Math.max(0, offset - 1));
                          glyph.setEnd(node, offset);
                          const rect = glyph.getBoundingClientRect();
                          const editorRect = element.getBoundingClientRect();
                          if (!rect || (!rect.width && !rect.height)) return null;
                          return {
                            // One real click inside the right half of the last
                            // hashtag glyph. Never click the blank area after it:
                            // that moves the caret past the token like a trailing
                            // space and closes TikTok's suggestion popup.
                            x: Math.max(
                              editorRect.left + 2,
                              Math.min(
                                editorRect.right - 2,
                                rect.right - Math.max(0.5, Math.min(2, rect.width * 0.2))
                              )
                            ),
                            y: rect.top + Math.max(1, rect.height / 2)
                          };
                        }
                        consumed += length;
                        node = walker.nextNode();
                      }
                      return null;
                    }""",
                    {"token": token, "occurrence": occurrence},
                )
                if not point:
                    logger.warning(
                        "[UPLOAD] Không xác định được cuối hashtag %s; giữ nguyên.",
                        token,
                    )
                    continue
                await self._page.mouse.move(point["x"], point["y"])
                await self._page.mouse.click(point["x"], point["y"])
                selected_token = await self._select_existing_hashtag_suggestion(
                    token,
                )
                if selected_token:
                    selected.append(selected_token)
                    await log(f"Đã kích hoạt hashtag TikTok: {selected_token}")
                else:
                    logger.warning(
                        "[UPLOAD] TikTok không hiện gợi ý cho %s; giữ nguyên caption.",
                        token,
                    )
            except Exception as exc:
                logger.warning(
                    "[UPLOAD] Không kích hoạt được hashtag %s; giữ nguyên: %s",
                    token,
                    exc,
                )

        if len(selected) != len(occurrences):
            missing_count = len(occurrences) - len(selected)
            logger.warning(
                "[UPLOAD] Khong kich hoat duoc %d/%d hashtag; "
                "giu nguyen caption va tiep tuc dang.",
                missing_count,
                len(occurrences),
            )
            await log(
                f"⚠️ Không làm đậm được {missing_count}/{len(occurrences)} hashtag; "
                "giữ nguyên caption và tiếp tục đăng."
            )

        try:
            final_caption = " ".join(
                (await editor.inner_text(timeout=5000)).split()
            )
        except Exception as exc:
            logger.warning(
                "[UPLOAD] Khong doc lai duoc caption sau buoc hashtag; "
                "dung caption da xac minh truoc do: %s",
                exc,
            )
            final_caption = caption
        await log(
            "Caption tên video đã sẵn sàng; tiếp tục đăng."
            if len(selected) != len(occurrences)
            else "Caption tên video và hashtag đã sẵn sàng."
        )
        return final_caption or caption

    async def _prepare_video_caption(
        self,
        caption: str,
        media_name: str,
        step_logger=None,
    ) -> str:
        """Keep Studio's filename caption and activate its existing hashtags."""
        async def log(message: str) -> None:
            if step_logger:
                await step_logger(message)

        expected = Path(media_name).stem.strip()
        if not expected:
            raise RuntimeError("Tên video trống; không thể kiểm tra caption tự điền.")

        # Studio has already copied the filename into Description. Preserve it
        # byte-for-byte: click once inside each existing hashtag and select
        # TikTok's suggestion. Do not click the trailing blank area afterward.
        await log(
            "Giữ nguyên caption TikTok tự điền; bấm trực tiếp từng hashtag và chọn gợi ý..."
        )
        final_value = await self._activate_filename_hashtags(
            media_name=media_name,
            step_logger=step_logger,
        )
        if not _studio_post_text_matches(expected, final_value):
            raise RuntimeError(
                "Caption tu dien khong con khop ten video sau khi chon hashtag "
                f"(hien tai: {final_value[:100]})."
            )
        requested = (caption or "").strip()
        if requested and not _studio_post_text_matches(requested, final_value):
            logger.info(
                "[UPLOAD] Bo qua caption truyen vao de giu nguyen caption ten file: %s",
                final_value,
            )
        logger.info("[UPLOAD] Caption filename hashtags verified: %s", final_value)
        return final_value

    async def _publish_button_in_viewport(
        self,
        scheduled: bool = False,
        timeout_seconds: int = 20,
        step_logger=None,
    ):
        """Human-scroll Studio's nested content pane until Post is in viewport."""
        deadline = time.monotonic() + timeout_seconds
        last_error = None
        while time.monotonic() < deadline:
            await self._handle_upload_interruptions(step_logger=step_logger)
            button = self._publish_button(scheduled=scheduled)
            try:
                if not await button.count() or not await button.is_visible():
                    await asyncio.sleep(0.5)
                    continue
                box = await button.bounding_box()
                viewport = self._page.viewport_size or await self._page.evaluate(
                    "() => ({width: window.innerWidth, height: window.innerHeight})"
                )
                viewport_height = viewport["height"]
                if (box and box["y"] >= 0 and box["y"] + box["height"] <= viewport_height
                        and await button.is_enabled()):
                    return button

                # TikTok Studio uses a nested DIV scroller. Point the mouse inside
                # that pane and wheel down, instead of Playwright's unreliable
                # scrollIntoView on the off-screen button.
                scroll_point = await button.evaluate("""element => {
                  let node = element.parentElement;
                  while (node) {
                    const style = getComputedStyle(node);
                    if (/(auto|scroll)/.test(style.overflowY) && node.scrollHeight > node.clientHeight) {
                      const rect = node.getBoundingClientRect();
                      return {x: rect.left + rect.width * 0.72, y: rect.top + rect.height * 0.72};
                    }
                    node = node.parentElement;
                  }
                  return null;
                }""")
                x = scroll_point["x"] if scroll_point else viewport["width"] * 0.7
                y = scroll_point["y"] if scroll_point else viewport_height * 0.7
                x = max(5, min(x, viewport["width"] - 5))
                y = max(5, min(y, viewport_height - 5))
                await self._page.mouse.move(x, y, steps=random.randint(5, 9))
                await self._page.mouse.wheel(0, random.randint(420, 680))
                await asyncio.sleep(random.uniform(0.35, 0.65))
            except Exception as exc:
                last_error = exc
            await asyncio.sleep(0.5)
        raise RuntimeError(f"Nut Post khong vao duoc viewport: {last_error}")

    async def _fill_publish_caption(self, caption: str, step_logger=None) -> None:
        if not caption:
            return

        async def log(message: str) -> None:
            if step_logger:
                await step_logger(message)

        editor = self._page.locator('.public-DraftEditor-content, [contenteditable="true"]').first
        await editor.wait_for(state="visible", timeout=30000)
        last_error = None
        for _ in range(5):
            # Studio may open an automatic-content-check coachmark immediately
            # after upload. It overlays the editor even though the editor still
            # reports visible/enabled.
            await self._handle_upload_interruptions(step_logger=step_logger)
            try:
                await editor.click(timeout=5000)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.7)
        if last_error:
            raise RuntimeError(f"Khong focus duoc o caption sau khi dong popup: {last_error}")
        # Wait for Studio's filename auto-caption hydration to settle before
        # clearing. If we clear too early, React repopulates the filename while
        # the real caption is being typed and the two strings get interleaved.
        await asyncio.sleep(0.8)
        # TikTok only turns a hashtag into a clickable entity after the user
        # types the token and selects its suggestion. Inserting the whole
        # caption in one protocol command leaves plain text instead of a tag.
        # Type ordinary text in chunks, type each hashtag sequentially to open
        # the suggestion list, then click the matching visible option/link.
        async def read_caption() -> str:
            try:
                return _normalize_caption_text(await editor.inner_text(timeout=5000))
            except Exception:
                return ""

        async def clear_caption() -> str:
            for _ in range(2):
                await editor.click(timeout=5000)
                await self._page.keyboard.press("Control+A")
                await self._page.keyboard.press("Backspace")
                await self._page.keyboard.press("Delete")
                await asyncio.sleep(0.2)
            return await read_caption()

        async def type_chunk(value: str, *, hashtag: bool = False) -> None:
            if not value:
                return
            delay = random.randint(48, 92) if hashtag else random.randint(32, 68)
            # A fixed 20-second timeout aborts legitimate long filenames and
            # captions mid-entry. Budget for the chosen per-character delay,
            # protocol overhead and a detached/re-rendered Draft.js frame.
            timeout_ms = max(20_000, min(180_000, 10_000 + len(value) * (delay + 25)))
            # Playwright's key model has no physical US-layout key for emoji
            # and many non-Latin glyphs. Send those runs through insertText,
            # while retaining real sequential key events for ASCII hashtags so
            # TikTok opens its native hashtag suggestion popup.
            runs = re.findall(r"[\x20-\x7e]+|[^\x20-\x7e]+", value)
            for run in runs:
                if run.isascii() and all(0x20 <= ord(char) <= 0x7E for char in run):
                    await editor.press_sequentially(
                        run,
                        delay=delay,
                        timeout=timeout_ms,
                    )
                else:
                    await self._page.keyboard.insert_text(run)
                    await asyncio.sleep(random.uniform(0.04, 0.12))

        expected = _normalize_caption_text(caption)

        async def restore_plain_caption() -> bool:
            """Recover from optional rich-hashtag UI failures without losing the post."""
            for _ in range(2):
                try:
                    remaining = await clear_caption()
                    if remaining:
                        continue
                    await editor.click(timeout=5000)
                    await self._page.keyboard.insert_text(caption)
                    await asyncio.sleep(0.35)
                    if expected in await read_caption():
                        return True
                except Exception:
                    await asyncio.sleep(0.35)
            return False

        remaining = await clear_caption()
        if remaining:
            raise RuntimeError(f"Khong the xoa caption tu dong truoc khi nhap (con lai: {remaining[:100]})")
        # TikTok xác định ranh giới hashtag bằng khoảng trắng: mọi ký tự nối
        # liền sau dấu # (kể cả _, -, ., chữ có dấu...) thuộc cùng một tag.
        # Không dùng \w vì sẽ cắt sai các tag dạng ``#lol_-5I8h``.
        hashtag_re = re.compile(r"#[^\s]+", re.UNICODE)
        cursor = 0
        selected_hashtags: List[str] = []
        auto_selected_hashtags: List[str] = []
        auto_queries = []
        if (
            getattr(settings, "AUTO_HASHTAGS_ENABLED", True)
            and not hashtag_re.search(caption)
        ):
            auto_queries = hashtag_query_candidates(
                caption,
                limit=max(0, int(getattr(settings, "AUTO_HASHTAG_QUERY_LIMIT", 6))),
            )

        async def select_hashtag(
            token: str,
            *,
            allow_keyboard_fallback: bool = True,
            excluded_tokens: Optional[List[str]] = None,
        ) -> Optional[str]:
            # The upload editor exposes hashtag suggestions as a transient
            # popup.  They are not guaranteed to be links (and often have no
            # href at all), so identify the suggestion by its visible text and
            # popup semantics instead of relying on a generated URL.
            candidates = self._page.locator(
                "[role='listbox']:visible [role='option']:visible, "
                "[role='menu']:visible [role='menuitem']:visible, "
                "[role='option']:visible, "
                "[data-e2e*='hashtag' i]:visible, "
                "[data-e2e*='search' i]:visible [role='button']:visible"
            )
            # Suggestions are loaded asynchronously after the last character;
            # a single immediate DOM query races the network and misses them.
            for _ in range(12):
                try:
                    count = await candidates.count()
                except Exception:
                    count = 0
                visible_indexes: List[int] = []
                suggestion_texts: List[str] = []
                for index in range(min(count, 40)):
                    candidate = candidates.nth(index)
                    try:
                        if not await candidate.is_visible():
                            continue
                        text_value = " ".join(
                            (await candidate.inner_text(timeout=800)).split()
                        )
                        if text_value:
                            visible_indexes.append(index)
                            suggestion_texts.append(text_value)
                    except Exception:
                        continue
                choice = choose_stable_hashtag_suggestion(
                    token,
                    suggestion_texts,
                    excluded_tokens=excluded_tokens or (),
                )
                if choice is not None:
                    try:
                        candidate_index = visible_indexes[choice.source_index]
                        await candidates.nth(candidate_index).click(
                            timeout=4000,
                            no_wait_after=True,
                        )
                        logger.info(
                            "[UPLOAD] Hashtag %s -> %s (usage=%s)",
                            token,
                            choice.token,
                            choice.usage_count or "not-shown",
                        )
                        return choice.token
                    except Exception:
                        pass
                await asyncio.sleep(0.3)
            # Some Studio builds expose the popup only through keyboard
            # navigation.  Enter selects the highlighted suggestion and keeps
            # the hashtag as a rich entity in the editor.
            if allow_keyboard_fallback:
                try:
                    await self._page.keyboard.press("ArrowDown")
                    await self._page.keyboard.press("Enter")
                    await asyncio.sleep(0.25)
                    return token
                except Exception:
                    pass
            return None

        async def refocus_caption_end() -> None:
            # Clicking a suggestion moves focus into the popup. Explicitly
            # restore the Draft.js editor before inserting the next chunk;
            # otherwise subsequent text can be appended without the space
            # that TikTok inserted after the selected hashtag.
            await self._handle_upload_interruptions(step_logger=step_logger)
            await editor.click(timeout=5000)
            await self._page.keyboard.press("Control+End")

        try:
            for match in hashtag_re.finditer(caption):
                before = caption[cursor:match.start()]
                if before:
                    # Keep ordinary chunks on the Draft.js editor as real key
                    # events too. keyboard.insert_text() can restore the caret at
                    # the start of the block after a mention selection, which
                    # reverses text around the hashtag (e.g. #tagPrefix).
                    await refocus_caption_end()
                    await type_chunk(before)
                    await asyncio.sleep(random.uniform(0.08, 0.28))
                token = match.group(0)
                # TikTok opens the hashtag suggestion menu from the literal '#'
                # typed in Description. Keep the whole token as real key events;
                # filling the editor in one operation does not trigger suggestions.
                await type_chunk(token, hashtag=True)
                await asyncio.sleep(random.uniform(0.38, 0.72))
                selected_token = await select_hashtag(token)
                if selected_token:
                    selected_hashtags.append(selected_token)
                    await log(f"Đã chọn hashtag TikTok: {selected_token}")
                    await refocus_caption_end()
                else:
                    logger.warning("[UPLOAD] TikTok không hiện gợi ý hashtag cho %s; giữ nguyên text.", token)
                    await refocus_caption_end()
                cursor = match.end()
            tail = caption[cursor:]
            if tail:
                await refocus_caption_end()
                await type_chunk(tail)

            # No explicit hashtag: query TikTok Studio with conservative title
            # keywords and keep only options that its live suggestion menu returns.
            # A failed query is deleted immediately; generic #fyp/#viral tags are
            # never invented by the application.
            max_auto_hashtags = max(0, int(getattr(settings, "AUTO_HASHTAGS_MAX", 3)))
            if auto_queries and max_auto_hashtags:
                await log("Caption đã nhập xong. Đang tìm hashtag phù hợp từ TikTok...")
            for slug in auto_queries:
                if len(selected_hashtags) >= max_auto_hashtags:
                    break
                token = f"#{slug}"
                await refocus_caption_end()
                typed_query = f" {token}"
                await type_chunk(typed_query, hashtag=True)
                await asyncio.sleep(random.uniform(0.38, 0.72))
                selected_token = await select_hashtag(
                    token,
                    allow_keyboard_fallback=False,
                    excluded_tokens=selected_hashtags,
                )
                if selected_token:
                    selected_hashtags.append(selected_token)
                    auto_selected_hashtags.append(selected_token)
                    await log(f"Đã chọn hashtag TikTok: {selected_token}")
                    await refocus_caption_end()
                    continue
                # No TikTok suggestion means this is only a guessed keyword. Remove
                # the exact query instead of publishing a low-confidence hashtag.
                await refocus_caption_end()
                for _ in range(len(typed_query)):
                    await self._page.keyboard.press("Backspace")
                await asyncio.sleep(0.15)
        except Exception as exc:
            # Hashtag suggestions are optional UI. A transient popup/editor
            # re-render must not cancel a video that already uploaded to 100%.
            logger.warning("[UPLOAD] Hashtag UI failed; restoring plain caption: %s", exc)
            await log(
                "⚠️ Gợi ý hashtag bị gián đoạn; đã khôi phục caption gốc và tiếp tục đăng."
            )
            if not await restore_plain_caption():
                raise RuntimeError(f"Khong khoi phuc duoc caption sau loi hashtag: {exc}") from exc
            selected_hashtags.clear()
            auto_selected_hashtags.clear()

        last_value = ""
        for _ in range(2):
            await asyncio.sleep(0.35)
            try:
                last_value = await read_caption()
                if expected in last_value:
                    if auto_selected_hashtags and step_logger:
                        await step_logger(
                            "Đã tự chọn hashtag ổn định từ gợi ý TikTok: "
                            + " ".join(auto_selected_hashtags)
                        )
                    if selected_hashtags:
                        logger.info("[UPLOAD] Đã kích hoạt hashtag TikTok: %s", ", ".join(selected_hashtags))
                    await log(
                        "Caption và hashtag đã sẵn sàng."
                        if selected_hashtags
                        else "Caption đã sẵn sàng."
                    )
                    return
            except Exception:
                pass
            await editor.click(timeout=5000)
        if selected_hashtags:
            logger.warning(
                "[UPLOAD] Caption rich chưa khớp sau khi chọn hashtag; "
                "expected=%r observed=%r",
                expected,
                last_value,
            )
        if await restore_plain_caption():
            await log("⚠️ Đã nhập lại caption bằng chế độ dự phòng; tiếp tục đăng.")
            return
        raise RuntimeError(
            f"Caption khong duoc ghi nhan day du (gia tri hien tai: {last_value[:80]})"
        )

    async def _read_video_upload_state(self) -> Dict[str, Any]:
        """Read Studio upload progress without trusting the enabled Post button."""
        progress_nodes = self._page.locator(
            "[role='progressbar']:visible, "
            "[aria-valuenow]:visible, "
            "[data-e2e*='progress' i]:visible"
        )
        percentages: list[float] = []
        try:
            count = await progress_nodes.count()
        except Exception:
            count = 0
        for index in range(min(count, 12)):
            node = progress_nodes.nth(index)
            try:
                raw_value = await node.get_attribute("aria-valuenow", timeout=700)
                raw_max = await node.get_attribute("aria-valuemax", timeout=700)
                aria_text = await node.get_attribute("aria-valuetext", timeout=700)
                node_text = await node.inner_text(timeout=700)
                percent = _upload_progress_percent(
                    raw_value,
                    raw_max,
                    f"{aria_text or ''} {node_text or ''}",
                )
                if percent is not None:
                    percentages.append(percent)
            except Exception:
                continue

        async def first_visible_text(pattern: re.Pattern) -> Optional[str]:
            """Return a short visible match, not an arbitrary first DOM match."""
            try:
                locator = self._page.get_by_text(pattern)
                count = await locator.count()
                for index in range(min(count, 12)):
                    candidate = locator.nth(index)
                    if not await candidate.is_visible():
                        continue
                    try:
                        text = " ".join(
                            (await candidate.inner_text(timeout=700)).split()
                        )
                    except Exception:
                        text = ""
                    # Large ancestor containers can contain an unrelated error
                    # elsewhere on the page. Only a compact status/toast label
                    # is valid evidence for the file-upload lifecycle.
                    if text and len(text) <= 320:
                        return text
            except Exception:
                pass
            return None

        uploading_text = await first_visible_text(re.compile(
            r"uploading|đang tải lên", re.I
        ))
        complete_text = await first_visible_text(re.compile(
            r"(?:^|\s)100(?:[.,]0+)?\s*%|upload complete|uploaded successfully|"
            r"video uploaded|ready to post|tải lên hoàn tất|đã tải lên thành công",
            re.I,
        ))
        preview_text = await first_visible_text(
            re.compile(r"^\s*edit cover\s*$", re.I)
        )
        # Abort only for language that explicitly ties the failure to this
        # video/upload. Generic page-level copy such as "Please try again" can
        # come from recommendations, content checks, extensions, or a stale
        # toast while the video upload itself continues normally.
        failure_text = await first_visible_text(re.compile(
            r"upload(?:ing)? (?:the )?video failed|video upload failed|"
            r"upload failed|failed to upload(?: (?:the )?video)?|"
            r"(?:could not|couldn't|unable to) upload(?: (?:the )?video)?|"
            r"tải (?:video )?lên thất bại|không thể tải (?:video )?lên",
            re.I,
        ))
        warning_text = await first_visible_text(re.compile(
            r"network error|please try again|something went wrong|đã xảy ra lỗi",
            re.I,
        ))

        return {
            "has_progress": bool(percentages),
            "percent": max(percentages) if percentages else None,
            "uploading": bool(uploading_text),
            "uploading_text": uploading_text,
            "complete": bool(complete_text),
            "complete_text": complete_text,
            # This action appears only after Studio has decoded the selected
            # video into its generated preview. It is independent of the
            # optional Content check lite that may continue in the background.
            "preview_ready": bool(preview_text),
            "failed": bool(failure_text),
            "failure_text": failure_text,
            "warning_text": warning_text,
        }

    async def _wait_video_upload_completion(
        self,
        *,
        timeout_seconds: float = 420.0,
        step_logger=None,
    ) -> Dict[str, Any]:
        """Wait for upload completion and confirm explicit failures three times."""
        page = self._page
        if page is None:
            raise RuntimeError("Trang upload không còn khả dụng.")
        editor = page.locator(
            '.public-DraftEditor-content, [contenteditable="true"]'
        ).first
        post_button = self._publish_button()
        reached_high = False
        reached_100 = False
        failure_streak = 0
        last_failure_text: Optional[str] = None
        logged_warnings: set[str] = set()
        deadline = time.monotonic() + max(1.0, float(timeout_seconds))

        while time.monotonic() < deadline:
            await self._wait_automation_gate()
            await self._handle_upload_interruptions(step_logger=step_logger)
            state = await self._read_video_upload_state()

            warning_text = str(state.get("warning_text") or "").strip()
            if warning_text and warning_text not in logged_warnings:
                logged_warnings.add(warning_text)
                logger.info(
                    "[Upload] Bo qua thong bao chung, tiep tuc doi upload: %s",
                    warning_text[:240],
                )

            failure_text = str(state.get("failure_text") or "").strip()
            if state.get("failed") and failure_text:
                if failure_text == last_failure_text:
                    failure_streak += 1
                else:
                    last_failure_text = failure_text
                    failure_streak = 1
                # A single toast sample can race a React replacement or belong
                # to a previous request. Three consecutive 500 ms observations
                # are required before cancelling this upload.
                if failure_streak >= 3:
                    return {
                        "ready": False,
                        "failure_text": failure_text,
                        "timed_out": False,
                        "state": state,
                    }
                await asyncio.sleep(0.5)
                continue
            else:
                failure_streak = 0
                last_failure_text = None

            percent = state.get("percent")
            if percent is not None and percent >= 95.0:
                reached_high = True
            if percent is not None and percent >= 100.0:
                reached_100 = True
            editor_ready = bool(
                await editor.count() > 0 and await editor.is_visible()
            )
            post_ready = bool(
                await post_button.count() > 0
                and await post_button.is_visible()
                and await post_button.is_enabled()
            )
            if (
                editor_ready
                and post_ready
                and _video_upload_finished(
                    state,
                    reached_high=reached_high,
                    reached_100=reached_100,
                )
            ):
                return {
                    "ready": True,
                    "failure_text": None,
                    "timed_out": False,
                    "state": state,
                }
            await asyncio.sleep(0.5)

        return {
            "ready": False,
            "failure_text": None,
            "timed_out": True,
            "state": None,
        }

    async def _wait_publish_ready(
        self,
        timeout_seconds: int = 180,
        step_logger=None,
    ) -> bool:
        """Wait for editor + enabled primary action, stable twice."""
        stable = 0
        editor = self._page.locator('.public-DraftEditor-content, [contenteditable="true"]').first
        post = self._publish_button(scheduled=False)
        for _ in range(timeout_seconds):
            await self._wait_automation_gate()
            if await self._handle_upload_interruptions(step_logger=step_logger):
                stable = 0
                continue
            try:
                good = (
                    await editor.count() > 0
                    and await editor.is_visible()
                    and await post.count() > 0
                    and await post.is_visible()
                    and await post.is_enabled()
                )
                stable = stable + 1 if good else 0
                if stable >= 2:
                    return True
            except Exception:
                stable = 0
            await asyncio.sleep(1)
        return False

    async def _click_publish_and_confirm(self, step_logger=None, scheduled: bool = False) -> bool:
        """Use invisible_playwright's locator click and require explicit success."""
        async def log(message):
            if step_logger:
                await step_logger(message)

        self.last_publish_acknowledged = False
        self.last_publish_ack_source = ""
        self.last_publish_diagnostics = []
        self.last_publish_failure_code = ""
        self.last_publish_failure_detail = ""
        await self._handle_upload_interruptions(step_logger=step_logger)
        notices_before_post = {
            notice.casefold()
            for notice in await self._read_publish_notice_texts()
        }
        button = await self._publish_button_in_viewport(
            scheduled=scheduled,
            timeout_seconds=60,
            step_logger=step_logger,
        )
        # Re-resolve once at the bottom and let the real locator click perform
        # the final actionability check through invisible_playwright.
        button = self._publish_button(scheduled=scheduled)
        await self._human_click(button, timeout=30000)
        await log("Đã bấm Đăng; đang chờ TikTok xác nhận...")

        reposts_after_popup = 0
        for _ in range(45):
            await self._wait_automation_gate()
            try:
                if self._adopt_studio_posts_page_by_url() is not None:
                    self.last_publish_acknowledged = True
                    self.last_publish_ack_source = "studio_posts_url"
                    return True
                # Turn on/Got it and CAPTCHA can appear after the primary Post
                # click as well. Resolve them before interpreting any success
                # copy or looking for the optional Post now confirmation.
                accepted = await self._handle_upload_interruptions(
                    step_logger=step_logger
                )
                blocking_failure = await self._read_publish_blocking_failure()
                if blocking_failure is not None:
                    code, detail = blocking_failure
                    self.last_publish_failure_code = code
                    self.last_publish_failure_detail = detail
                    await log(
                        "⚠️ VIDEO_TRUNG: TikTok báo video đã tồn tại nên không "
                        f"hiện Post now ({detail})."
                    )
                    return False
                # Immediate posts can require a second, explicit "Post now"
                # confirmation. Check it BEFORE generic success text: dialog
                # copy can itself contain "posted/published", which is not a
                # completed post until this button has actually been clicked.
                # Never accept it for scheduled posts because that would
                # bypass the requested schedule.
                if not scheduled:
                    post_now_result = await self._confirm_post_now_popup()
                    if post_now_result is True:
                        self.last_publish_acknowledged = True
                        self.last_publish_ack_source = "post_now_clicked"
                        await log("Đã xác nhận Post now; đang chờ TikTok đăng bài...")
                        # Post now is a one-shot external action. Return at once
                        # so the caller can inspect the filename receipt; never
                        # loop back and click a still-mounted dialog twice.
                        return True
                    if post_now_result is None:
                        # Popup exists but its action is still hydrating. Do not
                        # mistake dialog copy for a publish-success message.
                        await asyncio.sleep(1)
                        continue
                if await self._publish_success_visible():
                    self.last_publish_acknowledged = True
                    self.last_publish_ack_source = "publish_success_message"
                    return True
                if not scheduled:
                    unexpected_notice = await self._read_unexpected_notice_after_post(
                        notices_before_post
                    )
                    if unexpected_notice:
                        self.last_publish_failure_code = "VIDEO_DUPLICATE"
                        self.last_publish_failure_detail = (
                            "TikTok hiện thông báo khác thay vì Post now: "
                            f"{unexpected_notice[:300]}"
                        )
                        await log(
                            "⚠️ VIDEO_TRUNG: Sau khi bấm Đăng, TikTok hiện thông báo "
                            f"khác và không có Post now ({unexpected_notice[:220]})."
                        )
                        return False
                if not scheduled and not await self._upload_editor_is_active():
                    # The submit replaced the editor. The filename receipt is
                    # checked by _finalize_immediate_media_publish next.
                    self.last_publish_acknowledged = True
                    self.last_publish_ack_source = "upload_editor_disappeared"
                    return True
                # A coachmark can be injected at the exact moment Post is
                # clicked. Retry Post only after accepting such a popup; never
                # repeatedly click Post while merely waiting for TikTok.
                if accepted and reposts_after_popup < 2:
                    retry_button = self._publish_button(scheduled=scheduled)
                    if (
                        await retry_button.count()
                        and await retry_button.is_visible()
                        and await retry_button.is_enabled()
                    ):
                        await asyncio.sleep(random.uniform(0.55, 1.10))
                        await self._human_click(retry_button, timeout=10000)
                        reposts_after_popup += 1
            except Exception:
                pass
            await asyncio.sleep(1)
        if self.last_publish_acknowledged:
            await log(
                "TikTok đã nhận thao tác đăng nhưng chưa hiện xác nhận hoàn tất; "
                "chuyển sang Studio Posts để kiểm tra tên bài."
            )
            return True
        await log(
            "TikTok chưa hiện toast hoặc tự chuyển trang; "
            "tiếp tục mở Studio Posts để kiểm tra tên bài."
        )
        return False

    async def _finalize_immediate_video_publish(
        self,
        acknowledged: bool,
        caption: str,
        video_path: str,
        step_logger=None,
    ) -> bool:
        if self.last_publish_failure_code == "VIDEO_DUPLICATE":
            return False

        # For video, only the redirected Studio Posts list is accepted. A
        # filename receipt/toast is not enough because TikTok can acknowledge
        # Post now and still swallow the video before it reaches Posts.
        #
        # ⛔ THE LIST DOES NOT CONTAIN THE POST THE INSTANT IT REDIRECTS, AND
        # IT DOES NOT FILL ITSELF IN. Studio renders Posts from what it fetched
        # when the page was entered; a video that finished processing after
        # that is simply not in the DOM, and no amount of polling the same DOM
        # will find it. Five seconds without a reload called two videos
        # swallowed at 10:22:41 that Studio itself lists as posted at 10:22,
        # with views on them - measured 2026-09-16 on @merced3_mint49.
        #
        # So the budget covers a couple of reload cycles (the loop reloads
        # every 18s) instead of one impatient look. A video TikTok really
        # swallowed still fails, it just takes longer to say so - which is the
        # right way round: a false "swallowed" throws away a published video
        # and re-posts it, a slow verdict costs seconds.
        verified = await self._verify_post_in_studio(
            caption,
            media_name=os.path.basename(video_path),
            step_logger=step_logger,
            timeout_seconds=45,
            require_auto_redirect=True,
            allow_reload=True,
            auto_redirect_timeout_seconds=20.0,
            poll_seconds=0.2,
        )
        if verified:
            self.last_publish_acknowledged = True
            return True

        reached_posts = self._adopt_studio_posts_page_by_url() is not None
        if not reached_posts:
            reached_posts = (
                await self._adopt_studio_posts_page_by_ui([
                    Path(video_path).stem,
                    caption,
                ])
            ) is not None
        if not reached_posts:
            self.last_publish_failure_code = "PUBLISH_NOT_CONFIRMED"
            ack_source = self.last_publish_ack_source or (
                "legacy_acknowledged" if acknowledged else "none"
            )
            self.last_publish_failure_detail = (
                "Không quan sát được trang Studio Posts sau thao tác đăng; "
                f"nguồn xác nhận trước đó: {ack_source}."
            )
            if step_logger:
                await step_logger(
                    "❌ KHONG_CHUYEN_SANG_POSTS: Không quan sát được trang Studio Posts; "
                    "chưa đủ căn cứ gọi video bị nuốt."
                )
            return False

        self.last_publish_failure_code = "VIDEO_SWALLOWED"
        self.last_publish_failure_detail = (
            "Post now đã được xử lý nhưng không thấy video trên Studio Posts."
        )
        if step_logger:
            await step_logger(
                "❌ VIDEO_BI_NUOT: Không thấy video trong Studio Posts sau "
                "khi chờ và tải lại danh sách."
            )
        return False

    async def _finalize_immediate_media_publish(
        self,
        acknowledged: bool,
        caption: str,
        media_path: str,
        step_logger=None,
    ) -> bool:
        """Use the filename receipt first, then Studio Posts as a fallback."""
        if not acknowledged and step_logger:
            await step_logger(
                "Chưa có toast xác nhận; vẫn kiểm tra tên file trên trang "
                "chuyển tiếp rồi mới dự phòng bằng Studio Posts."
            )
        if await self._wait_for_transition_publish_receipt(
            os.path.basename(media_path),
            step_logger=step_logger,
        ):
            self.last_publish_acknowledged = True
            return True
        verified = await self._verify_post_in_studio(
            caption,
            media_name=os.path.basename(media_path),
            step_logger=step_logger,
        )
        if verified:
            self.last_publish_acknowledged = True
        return verified

    async def _wait_for_transition_publish_receipt(
        self,
        media_name: str,
        step_logger=None,
        timeout_seconds: float = 20.0,
    ) -> bool:
        """Accept the post-submit page when it shows this video's filename.

        TikTok commonly replaces the editor with a short transition/receipt
        page after ``Post now``. That page is stronger and faster evidence than
        waiting for the eventually-consistent Studio Posts list. Do not accept
        the same filename while the pre-publish editor is still active.
        """
        async def log(message: str) -> None:
            if step_logger:
                await step_logger(message)

        expected = Path(media_name).stem.strip()
        if not expected:
            return False
        receipt_pattern = re.compile(
            r"your videos? (?:is|are) being uploaded to tiktok|"
            r"your (?:video|post) (?:has been|was) (?:posted|published)|"
            r"(?:video|post) (?:posted|published) successfully|"
            r"video submitted for review|upload another|"
            r"manage (?:your )?(?:posts|videos)|go to (?:your )?profile|"
            r"(?:video|bài đăng) đang được (?:tải lên|xử lý)|"
            r"đã (?:đăng|xuất bản) thành công|tải (?:lên )?video khác|"
            r"quản lý (?:bài đăng|video)",
            re.I,
        )
        deadline = time.monotonic() + max(0.5, timeout_seconds)
        while time.monotonic() < deadline:
            await self._wait_automation_gate()
            try:
                current_url = str(getattr(self._page, "url", "") or "").lower()
                visible_text = await self._read_visible_page_text()
                if visible_text and _studio_post_text_matches(expected, visible_text):
                    editor_active = await self._upload_editor_is_active()
                    if (
                        "/tiktokstudio/content" in current_url
                        or receipt_pattern.search(visible_text)
                        or not editor_active
                    ):
                        self.last_publish_distribution_status = "PUBLISHED"
                        await log(
                            f"Đã thấy tên video '{expected}' trên trang xác nhận sau Post now."
                        )
                        return True
                if "/tiktokstudio/content" in current_url:
                    return False
            except Exception:
                pass
            await asyncio.sleep(0.4)
        return False

    async def _verify_post_in_studio(
        self,
        caption: str,
        media_name: Optional[str] = None,
        step_logger=None,
        timeout_seconds: int = 75,
        require_auto_redirect: bool = False,
        allow_reload: bool = True,
        auto_redirect_timeout_seconds: float = 12.0,
        poll_seconds: float = 3.0,
    ) -> bool:
        """Accept the new caption or filename appearing in Studio Posts.

        Studio can add hashtags to the displayed text or truncate a long title.
        Matching therefore accepts a normalized full string or a distinctive
        leading prefix. The public profile is not required for this result.
        """
        async def log(message):
            if step_logger:
                await step_logger(message)

        expected_values: list[str] = []
        for raw_value in (
            Path(media_name).stem if media_name else "",
            caption,
        ):
            candidate = " ".join((raw_value or "").split()).strip()
            if candidate and candidate.casefold() not in {
                value.casefold() for value in expected_values
            }:
                expected_values.append(candidate)
        self.last_publish_distribution_status = "UNKNOWN"
        if not expected_values:
            await log("Không có caption hoặc tên media để đối chiếu trong Studio Posts.")
            return False
        locator_needles: list[str] = []
        for expected in expected_values:
            for length in (min(64, len(expected)), 48, 40, 32, 28, 24):
                needle = expected[:length].rstrip()
                minimum = min(24, len(expected))
                if len(needle) >= minimum and needle.casefold() not in {
                    item.casefold() for item in locator_needles
                }:
                    locator_needles.append(needle)
        await log("Đang chờ TikTok tự chuyển sang trang bài đăng...")
        auto_deadline = time.monotonic() + max(0.5, float(auto_redirect_timeout_seconds))
        auto_redirected = False
        redirect_detected_by_ui = False
        while time.monotonic() < auto_deadline:
            await self._wait_automation_gate()
            if self._adopt_studio_posts_page_by_url() is not None:
                auto_redirected = True
                break
            if await self._adopt_studio_posts_page_by_ui(expected_values) is not None:
                auto_redirected = True
                redirect_detected_by_ui = True
                break
            remaining = auto_deadline - time.monotonic()
            if remaining <= 0:
                break
            redirect_poll = (
                min(max(0.05, float(poll_seconds)), remaining)
                if require_auto_redirect
                else min(random.uniform(0.65, 1.05), remaining)
            )
            await asyncio.sleep(redirect_poll)

        if auto_redirected:
            if require_auto_redirect:
                await log(
                    "Đã nhận diện giao diện Studio Posts; kiểm tra video ngay..."
                    if redirect_detected_by_ui
                    else "TikTok đã tự chuyển sang Studio Posts; kiểm tra video ngay..."
                )
            else:
                await log(
                    "TikTok đã tự chuyển sang Studio Posts; chờ bài xuất hiện ổn định..."
                )
                await asyncio.sleep(random.uniform(4.0, 7.0))
        elif require_auto_redirect:
            observed_urls = [
                str(getattr(page, "url", "") or "")[:180]
                for page in self._session_page_candidates()
            ]
            diagnostics = await self._collect_post_submit_diagnostics(
                expected_values
            )
            for snapshot in diagnostics:
                logger.warning(
                    "[UPLOAD][POST_DIAG] ack_source=%s snapshot=%s",
                    self.last_publish_ack_source or "none",
                    snapshot,
                )
            await log(
                "Sau Post now TikTok không chuyển sang Studio Posts; "
                "không mở hoặc reload trang thay thế. "
                f"Nguồn xác nhận: {self.last_publish_ack_source or 'không có'}; "
                f"URL driver đang thấy: {observed_urls or ['không có page']}."
            )
            return False
        else:
            await log(
                "TikTok chưa tự chuyển trang; mở Studio Posts trong cùng phiên để xác minh..."
            )
            try:
                # Fallback only: the upload tab can become half-destroyed after
                # Post. Keep the normal auto-redirect page whenever it exists.
                old_page = self._page
                self._page = await asyncio.wait_for(self._browser.new_page(), timeout=20)
                if old_page is not None and old_page is not self._page:
                    try:
                        await asyncio.wait_for(old_page.close(), timeout=5)
                    except Exception:
                        pass
                await self.navigate_to("https://www.tiktok.com/tiktokstudio/content?lang=en")
            except Exception as exc:
                logger.warning("[Upload] Khong mo duoc Studio Posts: %s", exc)
                return False

        # The requested five seconds starts only after Studio Posts is present.
        # Redirect latency is not evidence that TikTok swallowed the video.
        deadline = time.monotonic() + (
            max(0.5, float(timeout_seconds))
            if require_auto_redirect
            else max(10, timeout_seconds)
        )
        next_reload = time.monotonic() + 18
        while time.monotonic() < deadline:
            await self._wait_automation_gate()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(max(0.05, float(poll_seconds)), remaining))
            try:
                matched = None
                for locator_needle in locator_needles:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    match = self._page.get_by_text(locator_needle, exact=False)
                    operation_timeout = (
                        min(_STUDIO_OP_TIMEOUT, remaining) if require_auto_redirect else 6
                    )
                    match_count = await asyncio.wait_for(
                        match.count(), timeout=max(0.05, operation_timeout)
                    )
                    for index in range(min(match_count, 5)):
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        candidate = match.nth(index)
                        operation_timeout = (
                            min(_STUDIO_OP_TIMEOUT, remaining) if require_auto_redirect else 6
                        )
                        if await asyncio.wait_for(
                            candidate.is_visible(),
                            timeout=max(0.05, operation_timeout),
                        ):
                            matched = candidate
                            break
                    if matched is not None:
                        break

                if matched is not None:
                    nearby_text = ""
                    try:
                        evaluate_operation = matched.evaluate(
                            r"""element => {
                              let node = element;
                              for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) {
                                const text = (node.innerText || '').replace(/\s+/g, ' ').trim();
                                if (text.length >= 20 && text.length <= 1600 &&
                                    /under review|being reviewed|not eligible|ineligible|xét duyệt|kiểm duyệt|đủ điều kiện/i.test(text)) {
                                  return text;
                                }
                              }
                              return '';
                            }"""
                        )
                        if require_auto_redirect:
                            remaining = deadline - time.monotonic()
                            if remaining > 0:
                                nearby_text = await asyncio.wait_for(
                                    evaluate_operation,
                                    timeout=max(0.05, min(_STUDIO_OP_TIMEOUT, remaining)),
                                )
                            else:
                                evaluate_operation.close()
                        else:
                            nearby_text = await evaluate_operation
                    except Exception:
                        nearby_text = ""
                    distribution = _classify_distribution_text(nearby_text)
                    self.last_publish_distribution_status = distribution
                    await log("Đã thấy tên bài/caption trong Studio Posts.")
                    if distribution == "FYF_INELIGIBLE":
                        await log("⚠ TikTok ghi rõ bài không đủ điều kiện xuất hiện trên For You; cần mở Analytics để xem lý do/kháng nghị.")
                    elif distribution == "UNDER_REVIEW":
                        await log("⏳ Bài đã đăng nhưng TikTok đang xét duyệt; chưa được kết luận là bị hạn chế phân phối.")
                    return True

                # Some Studio versions split captions across nested spans.
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                body_timeout = min(_STUDIO_OP_TIMEOUT, remaining) if require_auto_redirect else 7
                body_text = await asyncio.wait_for(
                    self._page.locator("body").inner_text(
                        timeout=max(50, int(body_timeout * 1000))
                        if require_auto_redirect
                        else 5000
                    ),
                    timeout=max(0.05, body_timeout),
                )
                if any(
                    _studio_post_text_matches(expected, body_text)
                    for expected in expected_values
                ):
                    self.last_publish_distribution_status = "PUBLISHED"
                    await log(
                        "Đã thấy tên bài/caption trong Studio Posts "
                        "(chấp nhận nội dung dài hơn hoặc bị rút gọn)."
                    )
                    return True
            except Exception:
                pass

            if allow_reload and time.monotonic() >= next_reload:
                try:
                    await self._page.reload(wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
                next_reload = time.monotonic() + 18

        await log(
            "Chưa thấy bài trong Studio Posts sau thời gian xác minh; "
            "đánh dấu thất bại để tránh báo thành công sai."
        )
        return False

    async def collect_studio_analytics(self, step_logger=None) -> Dict[str, Any]:
        """Capture structured TikTok Studio JSON and reject guessed DOM numbers."""
        from app.use_cases.analytics.tiktok_analytics_sync import extract_studio_video_metrics

        async def log(message: str) -> None:
            if step_logger:
                await step_logger(message)

        payloads: List[Any] = []
        capture_tasks: set = set()
        json_urls: List[str] = []
        capture_errors: List[str] = []

        async def capture_response(response) -> None:
            try:
                url = (response.url or "").lower()
                # TikTok regularly renames Studio's private URL paths. Capture
                # every first-party JSON response, then let the strict structured
                # extractor below decide whether an object is genuinely a video.
                if "tiktok.com" not in url:
                    return
                headers = await response.all_headers()
                content_type = str(headers.get("content-type", "")).lower()
                if "json" not in content_type:
                    return
                body = await asyncio.wait_for(response.json(), timeout=6)
                if isinstance(body, (dict, list)):
                    payloads.append(body)
                    if len(json_urls) < 20:
                        json_urls.append(url[:240])
            except Exception as exc:
                if len(capture_errors) < 5:
                    capture_errors.append(f"{type(exc).__name__}: {str(exc)[:120]}")
                return

        def on_response(response) -> None:
            task = asyncio.create_task(capture_response(response))
            capture_tasks.add(task)
            task.add_done_callback(capture_tasks.discard)

        async def read_semantic_studio_table() -> List[Dict[str, Any]]:
            """Read TikTok's Posts table by column names, never CSS classes."""
            try:
                rows = await self._page.evaluate("""() => {
                  const text = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                  const lower = (value) => text(value).toLowerCase();
                  const metric = (value) => {
                    const raw = text(value).replace(/,/g, '').toUpperCase();
                    const match = raw.match(/(\d+(?:\.\d+)?)\s*([KMB])?/);
                    if (!match) return null;
                    const scale = match[2] === 'K' ? 1e3 : match[2] === 'M' ? 1e6 : match[2] === 'B' ? 1e9 : 1;
                    return Math.max(0, Math.round(Number(match[1]) * scale));
                  };
                  const hash = (value) => {
                    let result = 2166136261;
                    for (let i = 0; i < value.length; i++) {
                      result ^= value.charCodeAt(i);
                      result = Math.imul(result, 16777619);
                    }
                    return (result >>> 0).toString(16).padStart(8, '0');
                  };
                  const output = [];
                  const containers = [...document.querySelectorAll('table, [role="table"], [role="grid"]')];
                  if (!containers.length && document.querySelector('[role="columnheader"]')) containers.push(document);
                  for (const table of containers) {
                    const headers = [...table.querySelectorAll('thead th, [role="columnheader"]')].map((node) => lower(node.innerText));
                    const find = (names) => headers.findIndex((header) => names.some((name) => header.includes(name)));
                    const postIndex = find(['post', 'video', 'content', 'bài đăng', 'nội dung']);
                    const viewIndex = find(['view', 'lượt xem']);
                    const likeIndex = find(['like', 'lượt thích']);
                    const commentIndex = find(['comment', 'bình luận']);
                    const shareIndex = find(['share', 'chia sẻ']);
                    if (postIndex < 0 || viewIndex < 0 || (likeIndex < 0 && commentIndex < 0)) continue;
                    const bodyRows = [...table.querySelectorAll('tbody tr, [role="row"]')]
                      .filter((row) => !row.querySelector('[role="columnheader"]'));
                    for (let rowIndex = 0; rowIndex < bodyRows.length; rowIndex++) {
                      const row = bodyRows[rowIndex];
                      let cells = [...row.querySelectorAll(':scope > td, :scope > [role="cell"], :scope > [role="gridcell"]')];
                      if (!cells.length) cells = [...row.querySelectorAll('[role="cell"], [role="gridcell"]')];
                      if (cells.length <= Math.max(postIndex, viewIndex)) continue;
                      const postCell = cells[postIndex];
                      const hrefs = [...row.querySelectorAll('a[href]')].map((node) => node.href);
                      const href = hrefs.find((value) => /\/video\/\d+/.test(value)) || hrefs[0] || '';
                      const idMatch = href.match(/\/video\/(\d+)/) || href.match(/[?&](?:item_id|video_id)=(\d+)/);
                      const lines = String(postCell.innerText || '').split(/\\r?\\n/).map(text).filter(Boolean);
                      const title = lines
                        .filter((line) => !/^\d{1,2}:\d{2}$/.test(line) && !/^(everyone|private|friends|only me)$/i.test(line))
                        .sort((a, b) => b.length - a.length)[0] || 'Không có caption';
                      const identity = idMatch ? idMatch[1] : `studio-${hash(`${title}|${rowIndex}`)}`;
                      const views = metric(cells[viewIndex]?.innerText);
                      const likes = likeIndex >= 0 ? metric(cells[likeIndex]?.innerText) : null;
                      const comments = commentIndex >= 0 ? metric(cells[commentIndex]?.innerText) : null;
                      const shares = shareIndex >= 0 ? metric(cells[shareIndex]?.innerText) : null;
                      if (views === null && likes === null && comments === null && shares === null) continue;
                      output.push({
                        video_id: identity,
                        title,
                        create_time: null,
                        view_count: views || 0,
                        like_count: likes || 0,
                        comment_count: comments || 0,
                        share_count: shares || 0,
                        cover_url: '',
                        share_url: href,
                      });
                    }
                  }

                  // TikTok Studio currently renders the Posts grid with plain
                  // divs instead of native/ARIA table elements. `data-tt` is
                  // the stable semantic contract used by the Studio bundle;
                  // class names are generated and must not be depended on.
                  const studioRoot = document.querySelector('[data-tt="components_PostTable_FlexColumn"]');
                  if (studioRoot) {
                    const postInfos = [...studioRoot.querySelectorAll('[data-tt="components_PostInfoCell_FlexRow"]')];
                    const seenRows = new Set();
                    for (let rowIndex = 0; rowIndex < postInfos.length; rowIndex++) {
                      const postInfo = postInfos[rowIndex];
                      let row = postInfo;
                      while (row && row !== studioRoot) {
                        if (row.getAttribute?.('data-tt') === 'components_RowLayout_FlexRow') break;
                        row = row.parentElement;
                      }
                      if (!row || row === studioRoot || seenRows.has(row)) continue;
                      seenRows.add(row);

                      let postBranch = postInfo;
                      while (postBranch.parentElement && postBranch.parentElement !== row) {
                        postBranch = postBranch.parentElement;
                      }
                      const metricBranch = [...row.children].find((child) => child !== postBranch) || row;
                      const metricValues = [...metricBranch.querySelectorAll('.TUXText, [data-tt*="TUXText"]')]
                        .map((node) => text(node.innerText))
                        .filter((value) => /^\d+(?:[.,]\d+)?\s*[KMB]?$/.test(value));
                      if (metricValues.length < 1) continue;

                      const titleNode = postInfo.querySelector('[data-tt="components_PostInfoCell_TruncateText"]');
                      const title = text(titleNode?.innerText) || 'Không có caption';
                      const postLines = String(postInfo.innerText || '').split(/\\r?\\n/).map(text).filter(Boolean);
                      const createdText = postLines.find((line) =>
                        /(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|tháng)\b/i.test(line)
                      ) || '';
                      const hrefs = [...postInfo.querySelectorAll('a[href]')].map((node) => node.href);
                      const href = hrefs.find((value) => /\/video\/\d+/.test(value)) || hrefs[0] || '';
                      const idMatch = href.match(/\/video\/(\d+)/) || href.match(/[?&](?:item_id|video_id)=(\d+)/);
                      const identitySeed = `${title}|${createdText}`;
                      const identity = idMatch ? idMatch[1] : `studio-${hash(identitySeed)}`;
                      let createTime = null;
                      if (/^\d{15,20}$/.test(identity)) {
                        try { createTime = Number(BigInt(identity) >> 32n); } catch (_) {}
                      }
                      if (output.some((item) => item.video_id === identity)) continue;
                      const cover = postInfo.querySelector('img[src]');
                      output.push({
                        video_id: identity,
                        title,
                        create_time: createTime,
                        view_count: metric(metricValues[0]) || 0,
                        like_count: metric(metricValues[1]) || 0,
                        comment_count: metric(metricValues[2]) || 0,
                        share_count: 0,
                        cover_url: cover?.src || '',
                        share_url: href,
                      });
                    }
                  }
                  return output;
                }""")
                return rows if isinstance(rows, list) else []
            except Exception as exc:
                logger.warning("[ANALYTICS] Khong doc duoc bang Studio semantic: %s: %s", type(exc).__name__, exc)
                return []

        try:
            # Invisible context is most stable with its existing page. Creating
            # a second page can block while the cloaked window is painting, so
            # reuse the authenticated tab and navigate it to Studio.
            if self._page is None or self._page.is_closed():
                self._page = await asyncio.wait_for(self._browser.new_page(), timeout=20)
            self._page.on("response", on_response)
            await self.navigate_to("https://www.tiktok.com/tiktokstudio/content?lang=en")
            await log("TikTok Studio đã mở; đang tải toàn bộ danh sách bài đăng...")

            stable_rounds = 0
            last_count = -1
            dom_by_id: Dict[str, Dict[str, Any]] = {}
            for _ in range(18):
                await self._wait_automation_gate()
                await asyncio.sleep(2)
                videos, saw_last_page = extract_studio_video_metrics(payloads)
                for dom_video in await read_semantic_studio_table():
                    video_id = str(dom_video["video_id"])
                    existing = dom_by_id.get(video_id)
                    if existing:
                        for key in ("view_count", "like_count", "comment_count", "share_count"):
                            dom_video[key] = max(int(existing.get(key) or 0), int(dom_video.get(key) or 0))
                    dom_by_id[video_id] = dom_video
                combined_count = len({str(row["video_id"]) for row in videos} | set(dom_by_id))
                if combined_count == last_count:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                    last_count = combined_count
                if saw_last_page and stable_rounds >= 2:
                    break
                try:
                    await self._page.evaluate("""() => {
                      window.scrollTo(0, document.documentElement.scrollHeight);
                      for (const el of document.querySelectorAll('*')) {
                        if (el.scrollHeight > el.clientHeight + 200) el.scrollTop = el.scrollHeight;
                      }
                    }""")
                except Exception:
                    pass
                try:
                    more = self._page.locator("button:visible").filter(
                        has_text=re.compile(r"load more|show more|xem thêm|next", re.IGNORECASE)
                    ).first
                    if await more.count() and await more.is_enabled():
                        await more.click(timeout=3000, no_wait_after=True)
                except Exception:
                    pass
                if stable_rounds >= 4:
                    break

            if capture_tasks:
                await asyncio.gather(*list(capture_tasks), return_exceptions=True)

            # Hydration JSON is a safe fallback because it remains structured.
            try:
                script_texts = await self._page.locator(
                    'script[type="application/json"], script#__UNIVERSAL_DATA_FOR_REHYDRATION__, script#__NEXT_DATA__'
                ).all_text_contents()
                for text_value in script_texts:
                    try:
                        parsed = json.loads(text_value)
                        if isinstance(parsed, (dict, list)):
                            payloads.append(parsed)
                    except Exception:
                        continue
            except Exception:
                pass

            videos, saw_last_page = extract_studio_video_metrics(payloads)
            used_dom_fallback = False
            if not videos and dom_by_id:
                videos = list(dom_by_id.values())
                used_dom_fallback = True
            body_text = ""
            try:
                body_text = (await self._page.locator("body").inner_text(timeout=5000)).casefold()
            except Exception:
                pass
            if any(token in body_text for token in ("captcha", "verify to continue", "xác minh")):
                return {"videos": videos, "complete": False, "error": "TikTok yêu cầu xác minh/captcha khi mở Studio."}
            empty_confirmed = not videos and any(token in body_text for token in (
                "no posts yet", "no content", "chưa có bài đăng", "không có nội dung",
            ))
            if not videos and not empty_confirmed:
                try:
                    title = await self._page.title()
                except Exception:
                    title = ""
                try:
                    dom_shape = await self._page.evaluate("""() => {
                      const nodes = [...document.querySelectorAll('body *')];
                      const header = nodes.find((node) => {
                        const own = String(node.innerText || '').replace(/\s+/g, ' ').trim().toLowerCase();
                        return own === 'posts (created on)' || own === 'bài đăng (được tạo vào)';
                      });
                      let root = header;
                      for (let i = 0; i < 3 && root?.parentElement; i++) root = root.parentElement;
                      return {
                        tables: document.querySelectorAll('table').length,
                        grids: document.querySelectorAll('[role="grid"], [role="table"]').length,
                        rows: document.querySelectorAll('[role="row"]').length,
                        dataE2e: document.querySelectorAll('[data-e2e]').length,
                        semanticPostCells: document.querySelectorAll('[data-tt="components_PostInfoCell_FlexRow"]').length,
                        snippet: String(root?.outerHTML || '').slice(0, 800),
                      };
                    }""")
                except Exception as exc:
                    dom_shape = {"error": f"{type(exc).__name__}: {exc}"}
                logger.warning(
                    "[ANALYTICS] No video JSON: url=%s title=%r payloads=%d json_urls=%s errors=%s body=%r dom=%s",
                    getattr(self._page, "url", ""), title, len(payloads), json_urls[-6:],
                    capture_errors, " ".join(body_text.split())[:350], dom_shape,
                )
            return {
                "videos": videos,
                "complete": bool((saw_last_page and not used_dom_fallback) or empty_confirmed),
                "partial_reason": (
                    "Đọc từ bảng Posts chính chủ TikTok Studio; bảng này chưa cung cấp đủ share/phân trang nên dữ liệu được đánh dấu một phần."
                    if used_dom_fallback else ""
                ),
                "error": "" if videos or empty_confirmed else "Không bắt được phản hồi JSON chứa chỉ số video.",
            }
        finally:
            try:
                if self._page:
                    self._page.remove_listener("response", on_response)
            except Exception:
                pass

    async def publish_media(
        self,
        image_paths: Optional[List[str]] = None,
        video_path: Optional[str] = None,
        caption: str = "",
        schedule_at: Optional[str] = None,
        step_logger=None,
        continue_session: bool = False,
    ) -> bool:
        """Publish photos when present; video is only a fallback."""
        if image_paths:
            return await self._upload_photos(image_paths, caption, schedule_at, step_logger)
        if not video_path:
            raise ValueError("Khong co anh hoac video de dang.")
        return await self.upload_video(
            video_path,
            caption,
            schedule_at,
            step_logger,
            continue_session=continue_session,
        )

    async def _upload_photos(
        self,
        image_paths: List[str],
        caption: str = "",
        schedule_at: Optional[str] = None,
        step_logger=None,
    ) -> bool:
        """Open the real Photos tab, upload 1-35 images, then publish."""
        async def log(message):
            if step_logger:
                await step_logger(message)

        if not 1 <= len(image_paths) <= 35:
            raise ValueError("TikTok cho phép từ 1 đến 35 ảnh mỗi bài.")

        self._consume_foryou_upload_ticket()
        await log(f"Mở TikTok Studio và chọn tab Photos ({len(image_paths)} ảnh)...")
        await self._open_studio_upload_page(step_logger=step_logger)
        photo_tab = self._page.get_by_role("tab", name=re.compile(r"^(Photos|Ảnh)$", re.I), exact=True).first
        tab_deadline = time.monotonic() + 45.0
        photo_tab_seen = False
        while time.monotonic() < tab_deadline:
            await self._handle_upload_interruptions(step_logger=step_logger)
            current_url = (str(getattr(self._page, "url", "") or "")).lower()
            if "/login" in current_url and "redirect_url" in current_url:
                raise StudioReauthenticationRequired(
                    "TikTok Studio yêu cầu đăng nhập lại; cookie hiện tại không có phiên Studio hợp lệ."
                )
            try:
                if await photo_tab.count() and await photo_tab.is_visible():
                    photo_tab_seen = True
                    await photo_tab.click(timeout=10000)
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)
        else:
            if photo_tab_seen:
                raise RuntimeError(
                    "Đã thấy tab Photos nhưng CAPTCHA/popup đang chặn thao tác; "
                    "hãy kiểm tra cấu hình extension giải CAPTCHA."
                )
            raise RuntimeError("Không thấy tab Photos sau khi đã xử lý CAPTCHA/popup.")

        photo_input = self._page.locator(
            'input[type="file"][accept*="image/jpeg"], input[type="file"][accept*="image/png"], input[type="file"][accept^="image/"]'
        ).first
        input_deadline = time.monotonic() + 20.0
        while time.monotonic() < input_deadline:
            await self._handle_upload_interruptions(step_logger=step_logger)
            current_url = (str(getattr(self._page, "url", "") or "")).lower()
            if "/login" in current_url and "redirect_url" in current_url:
                raise StudioReauthenticationRequired(
                    "TikTok Studio yêu cầu đăng nhập lại; cookie hiện tại không có phiên Studio hợp lệ."
                )
            if await photo_input.count():
                break
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("Ô upload ảnh không xuất hiện sau khi đã xử lý CAPTCHA/popup.")

        await self._handle_upload_interruptions(step_logger=step_logger)
        await log("Đang chọn ảnh bằng hộp thoại Windows...")
        if not await self._set_files_via_native_dialog(image_paths, "photo"):
            detail = self._last_native_upload_error or "không có chi tiết"
            raise Exception(
                "Không đưa được ảnh vào ô tải lên của TikTok Studio. "
                f"Chi tiết: {detail}"
            )
        if not await self._wait_publish_ready(
            timeout_seconds=180,
            step_logger=step_logger,
        ):
            raise Exception("Ảnh chưa xử lý xong hoặc màn chỉnh sửa chưa sẵn sàng.")

        await self._handle_upload_interruptions(step_logger=step_logger)
        await self._fill_publish_caption(caption, step_logger=step_logger)
        await self._handle_upload_interruptions(step_logger=step_logger)
        if schedule_at:
            await self._handle_upload_interruptions(step_logger=step_logger)
            scheduled = await self._set_tiktok_schedule(schedule_at)
            if not scheduled:
                raise Exception("Không đặt được lịch TikTok; không tự chuyển sang đăng ngay.")
        await log("Ảnh đã sẵn sàng. Đang đăng bài...")
        acknowledged = await self._click_publish_and_confirm(
            step_logger=step_logger,
            scheduled=bool(schedule_at),
        )
        if schedule_at:
            return acknowledged
        # Photo posts can succeed without a toast or redirect, just like
        # videos. Verify the caption in Studio Posts before returning failure.
        return await self._finalize_immediate_media_publish(
            acknowledged,
            caption,
            media_path=image_paths[0],
            step_logger=step_logger,
        )

    async def upload_video(self, video_path: str, caption: str = "",
                           schedule_at: Optional[str] = None, step_logger=None,
                           continue_session: bool = False) -> bool:
        """Dang 1 video len TikTok. schedule_at=None -> dang ngay; nguoc lai
        (chuoi 'YYYY-MM-DD HH:MM') -> dat lich dang qua tuy chon 'Len lich' cua
        TikTok. Tra ve True neu dang/len lich thanh cong."""
        await self._wait_automation_gate()
        page = self._page
        if not page:
            raise RuntimeError("Trinh duyet chua khoi tao.")
        if not continue_session:
            self._consume_foryou_upload_ticket()

        async def log(m):
            if step_logger:
                await step_logger(m)

        await log(
            "Quay lại trang Post video trong cùng phiên..."
            if continue_session
            else "Mở TikTok Studio Upload..."
        )
        await self._open_studio_upload_page(step_logger=step_logger)
        page = self._page
        if not page:
            raise RuntimeError("Trang upload không còn khả dụng sau khi điều hướng.")

        # 1) CAPTCHA/popup là tùy chọn: có thì xử lý, không có thì ô upload
        # được kiểm tra ngay và luồng tiếp tục, không chờ timeout cố định.
        await log("Kiểm tra CAPTCHA/popup và chờ ô upload...")
        file_ready = False
        file_started = time.monotonic()
        file_deadline = file_started + 135.0
        reload_after = file_started + 40.0
        reloads = 0
        while time.monotonic() < file_deadline:
            await self._handle_upload_interruptions(step_logger=step_logger)
            current_url = (str(getattr(self._page, "url", "") or "")).lower()
            if "/login" in current_url and "redirect_url" in current_url:
                raise StudioReauthenticationRequired(
                    "TikTok Studio yeu cau dang nhap lai; cookie hien tai khong co phien Studio hop le."
                )
            if await self._video_upload_entry_ready():
                file_ready = True
                break
            # A transient Studio response can leave a blank shell that never
            # hydrates. Preserve cookies/profile and retry only this navigation.
            if time.monotonic() >= reload_after and reloads < 2:
                reloads += 1
                await log(
                    f"Trang upload chưa sẵn sàng; tải lại trong cùng phiên "
                    f"(lần {reloads}/2)..."
                )
                await self.navigate_to(
                    "https://www.tiktok.com/tiktokstudio/upload?lang=en"
                )
                reload_after = time.monotonic() + 40.0
            await asyncio.sleep(1)
        if not file_ready:
            current_url = str(getattr(self._page, "url", "") or "")
            raise Exception(
                "Ô upload không xuất hiện sau 2 lần tải lại "
                f"(URL cuối: {current_url[:180]})."
            )

        # 2) Dua FILE THAT vao qua hop thoai Windows.
        await self._handle_upload_interruptions(step_logger=step_logger)
        await log("Chọn video (hộp thoại Windows)...")
        file_attached = await self._set_file_via_native_dialog(video_path)
        if not file_attached:
            detail = self._last_native_upload_error or "không có chi tiết"
            raise Exception(
                "Không đưa được video vào ô upload (native dialog). "
                f"Chi tiết: {detail}"
            )
        await self._handle_upload_interruptions(step_logger=step_logger)

        # 3) Cho video UPLOAD LEN SERVER XONG (progress ~100%) roi moi cho dang.
        #    ============================ QUAN TRONG ============================
        #    TikTok BAT nut 'Đăng' NGAY khi upload moi 0%. Neu bam Dang som (khi
        #    byte video chua len xong VOD/CDN), frontend hien toast 'Video
        #    published' + redirect NHUNG backend KHONG luu -> KHONG co video nao
        #    len account (profile & Studio Posts trong rong). Da kiem chung
        #    07/08/2026: progress chay 0%->99% mat ~25s cho file 19MB.
        #    => PHAI doi progress bar dat ~100% / bien mat truoc khi dang.
        await log("Đang tải video lên máy chủ TikTok...")
        upload_outcome = await self._wait_video_upload_completion(
            timeout_seconds=420.0,
            step_logger=step_logger,
        )
        failure_detail = str(upload_outcome.get("failure_text") or "").strip()

        if not upload_outcome["ready"] and failure_detail:
            # The file was selected correctly, but TikTok's media endpoint can
            # fail transiently through a proxy. Reload only the upload page and
            # make one clean re-selection in the same browser/account session.
            await log(
                "⚠️ TikTok xác nhận upload bị gián đoạn "
                f"({failure_detail[:160]}). Đang thử lại video một lần..."
            )
            await self.navigate_to(
                "https://www.tiktok.com/tiktokstudio/upload?lang=en"
            )
            retry_entry_ready = False
            retry_deadline = time.monotonic() + 90.0
            while time.monotonic() < retry_deadline:
                await self._handle_upload_interruptions(
                    step_logger=step_logger
                )
                current_url = str(
                    getattr(self._page, "url", "") or ""
                ).lower()
                if "/login" in current_url and "redirect_url" in current_url:
                    raise StudioReauthenticationRequired(
                        "TikTok Studio yeu cau dang nhap lai khi thu lai upload."
                    )
                if await self._video_upload_entry_ready():
                    retry_entry_ready = True
                    break
                await asyncio.sleep(0.5)
            if not retry_entry_ready:
                raise Exception(
                    "TikTok không mở lại được ô chọn video sau lỗi upload."
                )

            await log("Chọn lại video (lần thử cuối)...")
            if not await self._set_file_via_native_dialog(video_path):
                detail = self._last_native_upload_error or "không có chi tiết"
                raise Exception(
                    "Không chọn lại được video sau lỗi upload. "
                    f"Chi tiết: {detail}"
                )
            await log("Đang tải lại video lên máy chủ TikTok...")
            upload_outcome = await self._wait_video_upload_completion(
                timeout_seconds=420.0,
                step_logger=step_logger,
            )
            failure_detail = str(
                upload_outcome.get("failure_text") or ""
            ).strip()

        if not upload_outcome["ready"]:
            if failure_detail:
                raise Exception(
                    "TikTok xác nhận tải video thất bại sau lần thử lại: "
                    f"{failure_detail[:240]}"
                )
            raise Exception(
                "Video tải lên quá lâu / chưa đạt 100% -> "
                "hủy để tránh đăng rỗng."
            )
        await log("Video đã tải lên xong 100%.")
        await self._handle_upload_interruptions(step_logger=step_logger)

        # 4) Keep Studio's filename caption untouched. Click once inside each
        # existing hashtag and activate it through TikTok's suggestions.
        published_caption = await self._prepare_video_caption(
            caption,
            os.path.basename(video_path),
            step_logger=step_logger
        )
        await self._handle_upload_interruptions(step_logger=step_logger)

        # Keep account defaults for privacy/comments/reuse. Those controls are
        # only touched when they become explicit inputs in a future UI.

        # 5) Dat lich (neu co) qua tuy chon 'Len lich' cua TikTok.
        scheduled = False
        if schedule_at:
            await self._handle_upload_interruptions(step_logger=step_logger)
            await log(f"Đặt lịch đăng: {schedule_at}...")
            scheduled = await self._set_tiktok_schedule(schedule_at)
            if not scheduled:
                raise Exception("Không đặt được lịch TikTok; không tự chuyển sang đăng ngay.")

        # 6) Bam Dang/Len lich voi VONG LAP: popup phu (vd 'New editing features/
        #    Got it', 'Bật kiểm tra nội dung') hay chen vao DUNG luc bam -> chan
        #    Post. Nen: moi vong -> dismiss popup -> JS-click Post -> kiem tra da
        #    roi man upload chua; neu chua thi dismiss + click lai.
        await log("Bấm Đăng...")
        acknowledged = await self._click_publish_and_confirm(
            step_logger=step_logger, scheduled=bool(schedule_at)
        )
        if schedule_at:
            return acknowledged
        return await self._finalize_immediate_video_publish(
            acknowledged,
            published_caption,
            video_path,
            step_logger=step_logger,
        )

    async def _set_tiktok_schedule(self, schedule_at: str) -> bool:
        """Chon 'Len lich' cua TikTok + dien ngay/gio. schedule_at: 'YYYY-MM-DD HH:MM'.
        CANH BAO (da kiem chung 06/08/2026): tuy chon 'Len lich' cua TikTok bi KHOA
        voi nhieu nick (bot/moi) — radio value=schedule hien ra nhung KHONG tick duoc
        du click bang moi cach (toa do / label.click / get_by_text). Day KHONG phai
        bug code ma la han che phia TikTok. -> Dat lich TIN CAY dung ScheduledUploadService
        (hen gio phia app: toi gio thi dang ngay). Ham nay chi best-effort cho nick
        du dieu kien; that bai thi upload_video se fallback dang ngay."""
        page = self._page
        try:
            # Chon radio 'Len lich'
            await self._click_by_texts(["Lên lịch", "Schedule"], timeout=5000, no_wait_after=False)
            await asyncio.sleep(1.5)
            # Tach ngay + gio
            date_part, _, time_part = schedule_at.partition(" ")
            # Dien vao cac o input date/time neu co (TikTok dung input text tuy bien)
            filled = await page.evaluate("""(args) => {
              const [d, t] = args;
              let n = 0;
              const inputs = Array.from(document.querySelectorAll('input'));
              // heuristic: o co placeholder/aria ve gio va ngay
              inputs.forEach(i => {
                const k = ((i.placeholder||'')+' '+(i.getAttribute('aria-label')||'')).toLowerCase();
                if (/time|giờ/.test(k) && t) { i.value = t; i.dispatchEvent(new Event('input',{bubbles:true})); i.dispatchEvent(new Event('change',{bubbles:true})); n++; }
                else if (/date|ngày/.test(k) && d) { i.value = d; i.dispatchEvent(new Event('input',{bubbles:true})); i.dispatchEvent(new Event('change',{bubbles:true})); n++; }
              });
              return n;
            }""", [date_part, time_part])
            return filled > 0
        except Exception as e:
            logger.warning(f"[Upload] _set_tiktok_schedule loi: {e}")
            return False

    async def close(self) -> None:
        """Dong trinh duyet va xoa hoan toan thu muc ho so tam thoi ra khoi dia cung"""
        # Nha HWND da nhan de cua so khac co the tai su dung so hieu (khi Windows
        # cap phat lai) va tranh ro ri tap _claimed_hwnds.
        if self._hwnd is not None:
            with _hwnd_lock:
                _claimed_hwnds.discard(self._hwnd)
            self._hwnd = None
        self._window_visible = False
        try:
            if self._invisible_pw:
                instance = self._invisible_pw
                token = getattr(instance, "_session_token", None)
                close_timeout = max(
                    0.1,
                    float(getattr(settings, "BROWSER_CLOSE_TIMEOUT", 15.0)),
                )
                try:
                    await asyncio.wait_for(
                        instance.__aexit__(None, None, None),
                        timeout=close_timeout,
                    )
                    logger.info("[+] Da dong phien trinh duyet va giai phong tai nguyen.")
                except asyncio.TimeoutError:
                    logger.warning(
                        "[CLOSE] Browser khong tu dong sau %.1fs; reap rieng session tree.",
                        close_timeout,
                    )
                    if token:
                        try:
                            await asyncio.wait_for(
                                asyncio.to_thread(_reap_session_tree, token),
                                timeout=10,
                            )
                        except Exception as reap_exc:
                            logger.warning("[CLOSE] Khong reap duoc session tree: %s", reap_exc)
                except Exception as close_exc:
                    logger.warning("[CLOSE] Driver close loi: %s", close_exc)
                    if token:
                        try:
                            await asyncio.wait_for(
                                asyncio.to_thread(_reap_session_tree, token),
                                timeout=10,
                            )
                        except Exception:
                            pass
                finally:
                    self._invisible_pw = None
                    self._browser = None
                    self._page = None

            if self._temp_profile_path and os.path.exists(self._temp_profile_path):
                if self._extension_profile_builder is not None:
                    persisted = await asyncio.to_thread(
                        self._extension_profile_builder.persist_external_storage,
                        self._temp_profile_path,
                    )
                    if persisted:
                        logger.info(
                            "[+] Da luu trang thai extension moi nhat: %s",
                            ", ".join(persisted),
                        )
                logger.info(f"[*] Dang don dep ho so tam thoi: {self._temp_profile_path}")
                # Xoa trong THREAD -> khong dong bang event loop luc dong browser.
                _p = self._temp_profile_path
                self._temp_profile_path = None
                await asyncio.to_thread(shutil.rmtree, _p, ignore_errors=True)
            self._extension_profile_builder = None

            for staging_dir in list(self._native_upload_staging_dirs):
                await asyncio.to_thread(shutil.rmtree, staging_dir, ignore_errors=True)
            self._native_upload_staging_dirs.clear()
        except Exception as e:
            logger.error(f"[-] Loi phat sinh khi dong trinh duyet va don dep: {str(e)}")
