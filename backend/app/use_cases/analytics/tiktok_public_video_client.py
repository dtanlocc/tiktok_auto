"""Collect public TikTok video metrics without logging into an account.

A second hidden tab stays on TikTok and calls its item_list API, which gives an
account's counts and every video's item without loading the profile. When that
is not possible, one tab renders the profile and the items come from its grid.
A video's own page is opened only for what neither carries (region,
shadow-ban) and only when that is missing or stale - over HTTP first, the
browser for what HTTP misses.
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import random
import re
import shutil
import tempfile
import uuid
import weakref
from typing import Any, Dict, Iterable, Optional
from urllib.parse import parse_qsl, unquote, urlparse

import httpx
from invisible_playwright.async_api import InvisiblePlaywright

from app.core.config import settings
from app.core.tiktok_urls import ensure_tiktok_english_url
from app.infrastructure.automation.configured_extensions import (
    configured_extension_builder,
    validate_configured_extensions,
)
from app.infrastructure.automation.extension_profile_builder import (
    firefox_prefs_for_extensions,
)
from app.use_cases.health_check.quick_check_use_case import (
    QuickCheckResult,
    _HTTP_HEADERS,
    _TikTokJsonScriptParser,
    _classify_profile_response,
    _extract_profile_metrics,
    _extract_public_profile_data,
    _profile_classification,
)


logger = logging.getLogger("TikTokPublicVideoClient")

_BROWSER_LAUNCH_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = (
    weakref.WeakKeyDictionary()
)

#: A page TikTok actually rendered carries its server state in this script.
#: Its "Please wait..." challenge interstitial does not.
_PAGE_DATA_READY_JS = (
    "() => !!document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__')"
)
#: The profile grid is rendered client-side; the data script alone is not enough.
_PROFILE_GRID_READY_JS = (
    "() => document.querySelectorAll('a[href*=\"/video/\"]').length > 0"
)


def _prepare_extension_profile() -> tuple[str, list[Any], set[str]]:
    """A fresh profile carrying the configured extensions, OmoCaptcha keyed.

    Built exactly as the account sessions build theirs. Kept outside the
    project tree, like theirs, so a dev-server file watcher never trips on it.
    """
    temp_root = os.path.join(tempfile.gettempdir(), "tiktok_auto_profiles")
    os.makedirs(temp_root, exist_ok=True)
    profile_dir = os.path.join(temp_root, f"analytics_{uuid.uuid4()}")
    builder, excluded = configured_extension_builder()
    try:
        installed = builder.prepare_profile(profile_dir)
        validate_configured_extensions(installed)
    except BaseException:
        shutil.rmtree(profile_dir, ignore_errors=True)
        raise
    return profile_dir, installed, excluded


#: Each grid card's full item struct, read from React's hook state.
#: ⛔ READ-ONLY ON PURPOSE. The item_list responses cannot be read here -
#: Juggler's Network.getResponseBody fails on them (NS_ERROR_FAILURE) - and
#: wrapping window.fetch to copy them would be a patch TikTok can detect. The
#: page already holds every item it rendered; reading what is there changes
#: nothing on the page. Measured: 17/17, 15/15 and 3/3 cards read, and 13 row
#: fields identical to each video's own page across 33 videos.
_READ_GRID_ITEMS_JS = r"""() => {
  const idOf = h => (String(h).match(/\/video\/(\d+)/) || [])[1];
  const anchors = [...document.querySelectorAll('a[href*="/video/"]')];
  const want = new Set(anchors.map(a => idOf(a.href)).filter(Boolean));
  const items = {};
  const seen = new Set();
  const visit = (v, depth) => {
    if (!v || typeof v !== 'object' || depth > 6 || seen.has(v)) return;
    seen.add(v);
    if (!Array.isArray(v) && want.has(String(v.id)) && (v.stats || v.statsV2) && v.video) {
      items[String(v.id)] = v; return;
    }
    if (v.$$typeof || (typeof Node !== 'undefined' && v instanceof Node)) return;
    let keys; try { keys = Object.keys(v); } catch (e) { return; }
    if (keys.length > 500) return;
    for (const k of keys) {
      let c; try { c = v[k]; } catch (e) { continue; }
      if (c && typeof c === 'object') visit(c, depth + 1);
    }
  };
  for (const a of anchors) {
    if (Object.keys(items).length >= want.size) break;
    const key = Object.keys(a).find(k => k.startsWith('__reactFiber$'));
    let f = key && a[key];
    for (let d = 0; d < 20 && f; d++, f = f.return) {
      let h = f.memoizedState, hi = 0;
      while (h && hi < 30) { visit(h.memoizedState, 0); h = h.next; hi++; }
    }
  }
  return {hrefs: anchors.map(a => a.href), items: JSON.parse(JSON.stringify(items))};
}"""

#: A grid read by fetch_profile is reused by the fetch_videos call that follows
#: for the same account, instead of loading the same profile a second time.
_GRID_CACHE_SECONDS = 120.0

#: TikTok's own item_list endpoint: the data behind the profile grid.
_ITEM_LIST_PATH = "/api/post/item_list/"
#: Tokens TikTok's script adds to each request itself. A captured copy made
#: TikTok answer with an empty body (2026-09-16), so they are never replayed.
_API_VOLATILE_PARAMS = frozenset({"X-Bogus", "X-Gnarly", "X-Dynosaur", "msToken"})
#: Per-profile query fields, set on every call.
_ITEM_LIST_OWN_PARAMS = frozenset({
    "secUid", "cursor", "count", "coverFormat",
    "post_item_list_request_type", "needPinnedItemIds",
})
_API_PAGE_SIZE = 35
_API_TIMEOUT_SECONDS = 15.0
#: Warm-ups (one profile load in the API tab) allowed per browser session.
_API_MAX_WARMUPS = 5
#: Transport failures in a row before the API tab is loaded again.
_API_FAILURES_BEFORE_REWARM = 3
#: ⛔ evaluate() has no timeout of its own: a fetch TikTok holds open hung a
#: probe for 16 minutes. The page aborts it, and the caller bounds evaluate.
_API_FETCH_JS = r"""async ({path, params, timeoutMs}) => {
  const url = new URL(path, location.origin);
  for (const [key, value] of Object.entries(params || {})) url.searchParams.set(key, value);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url.toString(), {credentials: 'include', signal: controller.signal});
    return {status: response.status, text: await response.text()};
  } catch (error) {
    return {status: 0, text: '', error: String(error)};
  } finally {
    clearTimeout(timer);
  }
}"""

#: Plain HTTP video reads are paused for a while when at most one of the last
#: few got a page: in 2026-09-17 runs it read 21 of ~596, each a request and
#: a retry that only delayed the browser read. A rare hit must not reset it.
_HTTP_DETAIL_WINDOW = 12
_HTTP_DETAIL_MAX_HITS_TO_PAUSE = 1
_HTTP_DETAIL_SKIP_SECONDS = 600.0
#: A read that did not happen because plain HTTP is paused.
_HTTP_SKIPPED = object()

#: How long to stay on a page while TikTok's challenge resolves itself.
#: Measured 2026-09-16 over 16 loads: the slowest resolved in 3.6s.
_CHALLENGE_SETTLE_SECONDS = 12.0


async def _wait_in_place(page, ready_js: str, timeout_seconds: float) -> bool:
    """Poll the SAME tab until ``ready_js`` holds, tolerating its navigations.

    ⛔ NOT page.goto AGAIN, AND NOT locator.wait_for. TikTok answers a real
    browser with a "Please wait..." interstitial that runs a script and then
    navigates the tab to the real page. Re-opening the URL starts that script
    over - measured: it recovered 1 of 3 grids and 2 of 3 detail pages -
    while waiting in place recovered 4 of 4 and 11 of 12. `wait_for` is no
    better: the challenge's own navigation destroys the execution context, the
    call throws at once, and the old `except Exception: pass` then read an
    empty grid 1.5s later and reported "profile_video_links_missing".
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.5, float(timeout_seconds))
    while True:
        try:
            if await page.evaluate(ready_js):
                return True
        except Exception:
            # The challenge navigates the tab; evaluate can race that. It is
            # a reason to look again, not a verdict.
            pass
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(0.5)


def _browser_launch_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _BROWSER_LAUNCH_LOCKS.get(loop)
    if lock is None:
        lock = asyncio.Lock()
        _BROWSER_LAUNCH_LOCKS[loop] = lock
    return lock

_VIDEO_STATUS_MEANINGS = {
    10202: ("UNKNOWN", "Video riêng tư"),
    10203: ("UNKNOWN", "Video chỉ dành cho bạn bè"),
    10204: ("YES", "TikTok chỉ cho tác giả xem (statusCode 10204)"),
    10216: ("UNKNOWN", "Video đang được xét duyệt"),
    10217: ("UNKNOWN", "Video đã bị xóa hoặc gỡ"),
    10231: ("YES", "Video không vượt qua kiểm duyệt (statusCode 10231)"),
}


def _nonnegative_int(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return max(0, int(float(str(value).replace(",", "").strip())))
    except (TypeError, ValueError):
        return None


def _first_media_url(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return next((str(item) for item in value if item), "")
    if isinstance(value, dict):
        for key in ("UrlList", "urlList", "urls", "url"):
            found = _first_media_url(value.get(key))
            if found:
                return found
    return ""


def _normalized_fps(value: Any) -> Optional[int]:
    fps = _nonnegative_int(value)
    if fps in {29, 30}:
        return 30
    if fps in {59, 60}:
        return 60
    return fps or None


def _max_video_quality(video: Dict[str, Any]) -> str:
    """Return the largest real TikTok rendition as e.g. ``1080p60``."""
    candidates: list[tuple[int, int, int, int]] = []
    for rendition in video.get("bitrateInfo") or []:
        if not isinstance(rendition, dict):
            continue
        address = rendition.get("PlayAddr") or rendition.get("playAddr") or {}
        if not isinstance(address, dict):
            continue
        width = _nonnegative_int(address.get("Width") or address.get("width")) or 0
        height = _nonnegative_int(address.get("Height") or address.get("height")) or 0
        if not width or not height:
            continue
        fps = _normalized_fps(
            rendition.get("BitrateFPS") or rendition.get("bitrateFPS")
        ) or 0
        bitrate = _nonnegative_int(rendition.get("Bitrate") or rendition.get("bitrate")) or 0
        candidates.append((min(width, height), max(width, height), fps, bitrate))

    if not candidates:
        width = _nonnegative_int(video.get("width")) or 0
        height = _nonnegative_int(video.get("height")) or 0
        if width and height:
            candidates.append((min(width, height), max(width, height), 0, 0))
    if not candidates:
        return ""
    short_edge, _long_edge, fps, _bitrate = max(candidates)
    return f"{short_edge}p{fps}" if fps else f"{short_edge}p"


def _restriction_summary(item: Dict[str, Any]) -> str:
    reasons: list[str] = []
    if item.get("isReviewing") is True:
        reasons.append("đang xét duyệt")
    if item.get("takeDown") not in (None, False, 0, "0"):
        reasons.append("đã bị gỡ")
    if item.get("privateItem") is True or item.get("secret") is True:
        reasons.append("riêng tư/ẩn")
    if item.get("forFriend") is True:
        reasons.append("chỉ bạn bè")
    if item.get("warnInfo"):
        reasons.append("có cảnh báo TikTok")
    return ", ".join(reasons)


def _video_detail_nodes(document: Any) -> list[tuple[Dict[str, Any], int]]:
    nodes: list[tuple[Dict[str, Any], int]] = []
    if not isinstance(document, dict):
        return nodes
    scope = document.get("__DEFAULT_SCOPE__")
    if isinstance(scope, dict):
        detail = scope.get("webapp.video-detail")
        if isinstance(detail, dict):
            info = detail.get("itemInfo")
            item = info.get("itemStruct") if isinstance(info, dict) else None
            if isinstance(item, dict):
                nodes.append((item, _nonnegative_int(detail.get("statusCode")) or 0))
            elif detail.get("statusCode") is not None:
                nodes.append(({}, _nonnegative_int(detail.get("statusCode")) or 0))
    item_module = document.get("ItemModule")
    if isinstance(item_module, dict):
        nodes.extend((item, 0) for item in item_module.values() if isinstance(item, dict))
    return nodes


def extract_public_video_detail_html(
    html: str, expected_video_id: str, share_url: str = ""
) -> Optional[Dict[str, Any]]:
    """Extract quality, moderation and engagement from one exact public URL."""
    parser = _TikTokJsonScriptParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return None

    restricted_status = 0
    for document in parser.documents:
        for item, status_code in _video_detail_nodes(document):
            if not item:
                restricted_status = restricted_status or status_code
                continue
            video_id = str(item.get("id") or item.get("itemId") or "")
            if video_id != str(expected_video_id):
                continue
            return public_video_row_from_item(item, share_url)

    if restricted_status in _VIDEO_STATUS_MEANINGS:
        shadow_ban, reason = _VIDEO_STATUS_MEANINGS[restricted_status]
        return {
            "video_id": str(expected_video_id),
            "share_url": share_url,
            "detail_source": "Browser",
            "shadow_ban": shadow_ban,
            "shadow_ban_reason": reason,
            "is_reviewing": restricted_status == 10216,
            "is_private": restricted_status in {10202, 10203},
            "is_taken_down": restricted_status == 10217,
            "detail_available": False,
        }
    return None


#: Row fields that only a video's OWN page carries. The profile grid's item has
#: every count, the quality ladder and the moderation flags, but not
#: ``locationCreated`` or ``indexEnabled`` - measured across 33 videos, 13
#: fields identical to the page, these absent.
PAGE_ONLY_ROW_FIELDS = ("region", "shadow_ban", "shadow_ban_reason", "index_enabled")


def grid_video_row_from_item(item: Dict[str, Any], share_url: str = "") -> Dict[str, Any]:
    """A row from a profile-grid item, WITHOUT the fields the grid cannot know.

    Leaving them out - rather than writing "" / None / "YES" - is what keeps
    the values last read from the video's page in the database: the sync's
    writer only sets fields a row actually carries.
    """
    row = public_video_row_from_item(item, share_url)
    for name in PAGE_ONLY_ROW_FIELDS:
        row.pop(name, None)
    row.pop("detail_source", None)
    return row


def public_video_row_from_item(item: Dict[str, Any], share_url: str = "") -> Dict[str, Any]:
    """The stored row for one TikTok item struct (video page shape)."""
    video_id = str(item.get("id") or item.get("itemId") or "")
    stats: Dict[str, Any] = {}
    for key in ("stats", "statsV2"):
        value = item.get(key)
        if isinstance(value, dict):
            stats.update(value)
    video = item.get("video") if isinstance(item.get("video"), dict) else {}
    if "indexEnabled" not in item:
        shadow_ban = "YES"
        shadow_reason = "Thiếu indexEnabled: video không được TikTok lập chỉ mục"
        index_enabled = None
    elif item.get("indexEnabled") is False:
        shadow_ban = "YES"
        shadow_reason = "indexEnabled=false: video không được TikTok lập chỉ mục"
        index_enabled = False
    else:
        shadow_ban = "NO"
        shadow_reason = "indexEnabled=true"
        index_enabled = True
    restrictions = _restriction_summary(item)
    if restrictions:
        shadow_reason = f"{shadow_reason}; {restrictions}"
    cover_url = ""
    for cover_key in ("dynamicCover", "cover", "originCover"):
        cover_url = _first_media_url(video.get(cover_key))
        if cover_url:
            break
    return {
        "video_id": video_id,
        "title": str(item.get("desc") or item.get("title") or ""),
        "create_time": _nonnegative_int(item.get("createTime")),
        "view_count": _nonnegative_int(stats.get("playCount")),
        "like_count": _nonnegative_int(stats.get("diggCount")),
        "comment_count": _nonnegative_int(stats.get("commentCount")),
        "share_count": _nonnegative_int(stats.get("shareCount")),
        "favorite_count": _nonnegative_int(stats.get("collectCount")),
        "repost_count": _nonnegative_int(stats.get("repostCount")),
        "download_count": _nonnegative_int(stats.get("downloadCount")),
        "cover_url": cover_url,
        "share_url": share_url,
        "duration_seconds": _nonnegative_int(video.get("duration")),
        "max_quality": _max_video_quality(video),
        "detail_source": "Browser",
        "region": str(item.get("locationCreated") or "").upper(),
        "shadow_ban": shadow_ban,
        "shadow_ban_reason": shadow_reason,
        "index_enabled": index_enabled,
        "is_reviewing": bool(item.get("isReviewing") is True),
        "is_private": bool(
            item.get("privateItem") is True or item.get("secret") is True
        ),
        "is_taken_down": bool(
            item.get("takeDown") not in (None, False, 0, "0")
        ),
        "detail_available": True,
    }


_REQUIRED_API_METRICS = ("video_count", "follower_count", "following_count", "likes_count")


def api_profile_result_from_items(
    items: Iterable[Dict[str, Any]], username: str
) -> Optional[QuickCheckResult]:
    """The profile an item_list answer describes - only if it is clearly this user's.

    Every item carries its author and the author's counts. None when the list
    is empty, any item belongs to someone else (a renamed account keeps its
    secUid), or a count is missing, so the caller loads the profile instead.
    """
    target = username.lstrip("@").casefold()
    author: Optional[Dict[str, Any]] = None
    stats: Optional[Dict[str, Any]] = None
    for item in items:
        item_author = item.get("author") if isinstance(item, dict) else None
        if not isinstance(item_author, dict):
            return None
        if str(item_author.get("uniqueId") or "").lstrip("@").casefold() != target:
            return None
        if author is None:
            author = item_author
            stats = item.get("authorStats") if isinstance(item.get("authorStats"), dict) else None
    if author is None or stats is None:
        return None
    metrics = _extract_profile_metrics(stats)
    if any(name not in metrics for name in _REQUIRED_API_METRICS):
        return None
    return QuickCheckResult(
        _profile_classification(author, stats),
        "tiktok_item_list_api",
        http_status=200,
        profile_metrics=metrics,
        profile_identity={
            "user_id": str(author.get("id") or ""),
            "sec_uid": str(author.get("secUid") or ""),
            "username": str(author.get("uniqueId") or ""),
        },
        profile_data=_extract_public_profile_data(author, stats),
    )


def _playwright_proxy_options(proxy_url: Optional[str]) -> Optional[Dict[str, str]]:
    if not proxy_url:
        return None
    parsed = urlparse(proxy_url)
    if not parsed.scheme or not parsed.hostname or not parsed.port:
        return None
    options = {"server": f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        options["username"] = unquote(parsed.username)
    if parsed.password:
        options["password"] = unquote(parsed.password)
    return options


def extract_public_user_identity(html: str, username: str) -> Optional[Dict[str, str]]:
    """Extract the exact public user identity; never trust a suggested profile."""
    parser = _TikTokJsonScriptParser()
    parser.feed(html or "")
    wanted = username.lstrip("@").casefold()
    for document in parser.documents:
        candidates = []
        if isinstance(document, dict):
            scope = document.get("__DEFAULT_SCOPE__")
            if isinstance(scope, dict) and isinstance(scope.get("webapp.user-detail"), dict):
                candidates.append(scope["webapp.user-detail"])
            elif "userInfo" in document:
                candidates.append(document)
        for item in candidates:
            info = item.get("userInfo")
            if not isinstance(info, dict):
                continue
            user = info.get("user")
            if not isinstance(user, dict):
                continue
            unique_id = str(user.get("uniqueId") or "").lstrip("@").casefold()
            sec_uid = str(user.get("secUid") or "")
            user_id = str(user.get("id") or "")
            if unique_id == wanted and sec_uid:
                return {"username": unique_id, "sec_uid": sec_uid, "user_id": user_id}
    return None


def normalize_profile_video_links(
    hrefs: list[str], username: str, max_videos: int
) -> list[str]:
    """Keep exact video links owned by the requested username, deduplicated."""
    wanted = username.lstrip("@").casefold()
    by_id: Dict[str, str] = {}
    for href in hrefs:
        try:
            parsed = urlparse(str(href or ""))
            if parsed.netloc.casefold() not in {"tiktok.com", "www.tiktok.com"}:
                continue
            match = re.fullmatch(
                r"/@([^/]+)/video/(\d+)", unquote(parsed.path).rstrip("/"), re.I
            )
            if not match or match.group(1).lstrip("@").casefold() != wanted:
                continue
            video_id = match.group(2)
            by_id.setdefault(
                video_id,
                ensure_tiktok_english_url(
                    f"https://www.tiktok.com/@{match.group(1)}/video/{video_id}"
                ),
            )
        except Exception:
            continue
        if len(by_id) >= max(0, max_videos):
            break
    return list(by_id.values())


def resolve_profile_video_links(
    rendered_hrefs: list[str],
    known_video_urls: list[str],
    username: str,
    max_videos: int,
) -> list[str]:
    """Prefer the live profile grid, then retain known public video URLs.

    TikTok can report a non-zero profile video count while omitting every video
    card from the guest DOM (for example while a post is under review).  A
    previously verified direct video URL can still expose the public metrics in
    that state, so do not discard it merely because the grid is temporarily
    empty.  ``normalize_profile_video_links`` still enforces the exact owner and
    numeric video ID for both sources.
    """
    return normalize_profile_video_links(
        [*rendered_hrefs, *known_video_urls], username, max_videos
    )


def extract_video_detail_html(
    html: str, expected_video_id: str, share_url: str = ""
) -> Optional[Dict[str, Any]]:
    """Return only the requested video's structured metrics from its HTML."""
    row = extract_public_video_detail_html(html, expected_video_id, share_url)
    return row if row and row.get("detail_available") else None


class TikTokPublicVideoClient:
    """A shared hidden profile renderer plus concurrent HTTP detail reader."""

    def __init__(
        self,
        detail_request_concurrency: Optional[int] = None,
        *,
        detail_global_gate: Optional[asyncio.Semaphore] = None,
        detail_account_gate: Optional[asyncio.Semaphore] = None,
        detail_route_gates: Optional[Dict[str, asyncio.Semaphore]] = None,
    ) -> None:
        self._invisible_pw: Optional[InvisiblePlaywright] = None
        self._browser = None
        self._page = None
        self._route_key: Optional[str] = None
        self._profile_dir: Optional[str] = None
        # username -> (read at, grid video ids in order, item structs by id)
        self._grid_cache: Dict[str, tuple[float, list[str], Dict[str, Any]]] = {}
        self._lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()
        configured_detail_concurrency = (
            detail_request_concurrency
            if detail_request_concurrency is not None
            else getattr(settings, "FAST_ANALYTICS_DETAIL_REQUEST_CONCURRENCY", 6)
        )
        detail_concurrency = max(
            1,
            min(
                int(configured_detail_concurrency),
                12,
            ),
        )
        self._detail_global_gate = detail_global_gate or asyncio.Semaphore(
            detail_concurrency
        )
        self._detail_account_gate = detail_account_gate or asyncio.Semaphore(
            detail_concurrency
        )
        self._detail_route_gates = detail_route_gates if detail_route_gates is not None else {}
        self._account_cookies_seeded = False
        # Tabs that read video pages side by side, separate from the profile tab.
        self._idle_detail_tabs: list[Any] = []
        self._detail_tab_gate = asyncio.Semaphore(max(1, min(
            int(getattr(settings, "FAST_ANALYTICS_DETAIL_TABS_PER_BROWSER", 3)), 6
        )))
        # A second tab that stays on TikTok and calls its item_list API; the
        # query template is copied from the page's own item_list request.
        self._api_page = None
        self._api_params: Optional[Dict[str, str]] = None
        self._api_gate = asyncio.Semaphore(2)
        self._api_warm_lock = asyncio.Lock()
        self._api_warmups = 0
        self._api_failures = 0
        # Plain HTTP detail reads mostly get TikTok's challenge page; when
        # almost none of the recent ones worked they pause (loop time, seconds).
        self._http_detail_outcomes: "collections.deque[bool]" = collections.deque(
            maxlen=_HTTP_DETAIL_WINDOW
        )
        self._http_detail_skip_until = 0.0
        self.http_detail_requests = 0
        self.http_detail_retries = 0
        self.browser_detail_fallbacks = 0
        self.api_account_reads = 0

    def reset_stats(self) -> None:
        self.http_detail_requests = 0
        self.http_detail_retries = 0
        self.browser_detail_fallbacks = 0
        self.api_account_reads = 0

    def get_stats(self) -> Dict[str, int]:
        return {
            "video_http_requests": self.http_detail_requests,
            "video_http_retries": self.http_detail_retries,
            "video_browser_fallbacks": self.browser_detail_fallbacks,
            "api_account_reads": self.api_account_reads,
        }

    async def _fetch_video_details_http(
        self,
        links: list[str],
        *,
        profile_url: str,
        proxy_url: Optional[str],
        cookie_header: str = "",
    ) -> list[Optional[Dict[str, Any]]]:
        """Read direct video pages with bounded, route-aware concurrency.

        Known URLs do not need a browser just to be rediscovered. Retry only
        transient failures, respect a short Retry-After, and leave unresolved
        pages for the browser fallback instead of multiplying HTTP requests.
        """
        if not links:
            return []
        loop = asyncio.get_running_loop()
        if loop.time() < self._http_detail_skip_until:
            # TikTok has been answering plain HTTP with its challenge page;
            # more tries only cost retries and raise this IP's risk score.
            return [None] * len(links)
        route_key = proxy_url or "__DIRECT__"
        route_gate = self._detail_route_gates.setdefault(
            route_key, asyncio.Semaphore(2 if proxy_url else 3)
        )
        timeout = httpx.Timeout(connect=8.0, read=12.0, write=5.0, pool=6.0)
        limits = httpx.Limits(max_connections=3, max_keepalive_connections=3)
        headers = {**_HTTP_HEADERS, "Referer": profile_url}

        async with self._detail_account_gate:
            async with httpx.AsyncClient(
                proxy=proxy_url,
                headers=headers,
                follow_redirects=True,
                timeout=timeout,
                limits=limits,
                trust_env=False,
            ) as client:
                async def fetch_detail(url: str) -> Optional[Dict[str, Any]]:
                    row = await read_detail(url)
                    if row is _HTTP_SKIPPED:
                        return None
                    self._note_http_detail(row is not None)
                    return row

                async def read_detail(url: str) -> Any:
                    url = ensure_tiktok_english_url(url)
                    video_id = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
                    for attempt in range(2):
                        response: Optional[httpx.Response] = None
                        try:
                            async with self._detail_global_gate:
                                async with route_gate:
                                    if loop.time() < self._http_detail_skip_until:
                                        # Paused while this read waited its turn.
                                        return _HTTP_SKIPPED
                                    self.http_detail_requests += 1
                                    response = await client.get(
                                        url,
                                        headers={
                                            "Cookie": cookie_header
                                            if attempt > 0 and cookie_header
                                            else ""
                                        },
                                    )
                            if response.text:
                                row = extract_public_video_detail_html(
                                    response.text, video_id, share_url=url
                                )
                                if row is not None:
                                    row = dict(row)
                                    row["detail_source"] = "HTTP"
                                    return row
                            if response.status_code != 200 and response.status_code not in {
                                403, 408, 412, 425, 429, 500, 502, 503, 504
                            }:
                                break
                        except (
                            httpx.TimeoutException,
                            httpx.NetworkError,
                            httpx.ProxyError,
                        ) as exc:
                            logger.debug(
                                "Public video HTTP attempt %s failed for %s: %s",
                                attempt + 1,
                                video_id,
                                type(exc).__name__,
                            )
                        except Exception as exc:
                            logger.debug(
                                "Public video page failed for %s: %s",
                                video_id,
                                type(exc).__name__,
                            )
                            break
                        if attempt == 0:
                            self.http_detail_retries += 1
                            retry_after = 0.0
                            if response is not None and response.status_code == 429:
                                try:
                                    retry_after = min(
                                        3.0,
                                        max(0.0, float(response.headers.get("Retry-After", "0"))),
                                    )
                                except (TypeError, ValueError):
                                    retry_after = 0.0
                            await asyncio.sleep(
                                max(retry_after, random.uniform(0.55, 0.95))
                            )
                    return None

                return await asyncio.gather(*(fetch_detail(url) for url in links))

    def _note_http_detail(self, got_page: bool) -> None:
        self._http_detail_outcomes.append(got_page)
        if (
            len(self._http_detail_outcomes) == self._http_detail_outcomes.maxlen
            and sum(self._http_detail_outcomes) <= _HTTP_DETAIL_MAX_HITS_TO_PAUSE
        ):
            self._http_detail_outcomes.clear()
            self._http_detail_skip_until = (
                asyncio.get_running_loop().time() + _HTTP_DETAIL_SKIP_SECONDS
            )

    @staticmethod
    def _finish_video_results(
        links: list[str],
        results: list[Optional[Dict[str, Any]]],
        *,
        max_videos: int,
        expected_video_count: Optional[int],
    ) -> tuple[list[Dict[str, Any]], bool]:
        rows = [row for row in results if row is not None]
        if not rows:
            raise RuntimeError("video_detail_pages_empty")
        rows.sort(key=lambda row: row.get("create_time") or 0, reverse=True)
        expected = (
            min(max_videos, max(0, expected_video_count))
            if expected_video_count is not None
            else len(links)
        )
        complete_details = sum(
            1 for row in rows if row.get("detail_available") is True
        )
        complete = (
            len(links) >= expected
            and len(rows) == len(links)
            and complete_details == len(links)
        )
        return rows[:max_videos], complete

    @classmethod
    async def _open_until_ready(
        cls,
        page,
        url: str,
        ready_js: str,
        *,
        navigations: int = 2,
        settle_seconds: float = _CHALLENGE_SETTLE_SECONDS,
    ) -> tuple[str, bool]:
        """Open ``url`` and return its HTML once TikTok really rendered it.

        Waits in place through the challenge first; only if the page still is
        not ready does it open the URL again. Returns the last HTML and whether
        ``ready_js`` ever held, so callers keep their own verdict on a page
        that never finished.
        """
        html = ""
        last_error: Optional[BaseException] = None
        for attempt in range(max(1, navigations)):
            try:
                html = await cls._navigate_html(page, url, attempts=1)
            except Exception as exc:
                last_error = exc
            if await _wait_in_place(page, ready_js, settle_seconds):
                try:
                    return await page.content(), True
                except Exception as exc:
                    last_error = exc
            if attempt + 1 < navigations:
                await asyncio.sleep(1.0)
        try:
            html = await page.content()
        except Exception:
            pass
        if not html and last_error is not None:
            raise last_error
        return html, False

    async def _fill_missing_details_with_browser(
        self,
        page,
        links: list[str],
        results: list[Optional[Dict[str, Any]]],
    ) -> None:
        missing_count = sum(row is None for row in results)
        if missing_count:
            self.browser_detail_fallbacks += missing_count
        for index, row in enumerate(results):
            if row is not None:
                continue
            url = links[index]
            video_id = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
            try:
                # ≥300 characters used to count as loaded, and TikTok's
                # "Please wait..." interstitial is 1.6KB: the challenge page
                # was extracted, yielded nothing, and was never retried.
                html, _ready = await self._open_until_ready(
                    page, url, _PAGE_DATA_READY_JS
                )
                results[index] = extract_public_video_detail_html(
                    html, video_id, share_url=url
                )
            except Exception as exc:
                logger.warning(
                    "Browser video fallback failed for %s: %s",
                    video_id,
                    type(exc).__name__,
                )

    async def _fill_missing_details_in_tabs(
        self,
        links: list[str],
        results: list[Optional[Dict[str, Any]]],
    ) -> None:
        """Read the missing video pages in the browser's detail tab(s).

        ⛔ WHY NOT THE PROFILE TAB. Video pages queued behind the profile tab's
        lock: in the 132-account THAITEST run, 369 page reads at 4.95s each were
        15 of its 17 minutes. In their own tab the same reads took 1.7s each.
        ⛔ AND WHY ONE TAB. More tabs loading at once made TikTok slow every
        load (3 tabs: 8.1s a page, 23 of 348 never ready) - see
        FAST_ANALYTICS_DETAIL_TABS_PER_BROWSER.
        """
        missing = [index for index, row in enumerate(results) if row is None]
        self.browser_detail_fallbacks += len(missing)

        async def read(index: int) -> None:
            url = links[index]
            video_id = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
            page = await self._borrow_detail_tab()
            try:
                html, _ready = await self._open_until_ready(page, url, _PAGE_DATA_READY_JS)
                results[index] = extract_public_video_detail_html(html, video_id, share_url=url)
            except Exception as exc:
                logger.warning(
                    "Browser video fallback failed for %s: %s", video_id, type(exc).__name__
                )
            finally:
                self._return_detail_tab(page)

        await asyncio.gather(*(read(index) for index in missing))

    async def _borrow_detail_tab(self):
        await self._detail_tab_gate.acquire()
        try:
            while self._idle_detail_tabs:
                page = self._idle_detail_tabs.pop()
                if not page.is_closed():
                    return page
            return await self._browser.new_page()
        except BaseException:
            self._detail_tab_gate.release()
            raise

    def _return_detail_tab(self, page) -> None:
        try:
            if page is not None and not page.is_closed():
                self._idle_detail_tabs.append(page)
        finally:
            self._detail_tab_gate.release()

    async def _close_session_unlocked(self) -> None:
        try:
            if self._invisible_pw is not None:
                await self._invisible_pw.__aexit__(None, None, None)
        except Exception:
            pass
        finally:
            self._page = None
            self._browser = None
            self._invisible_pw = None
            self._route_key = None
            self._idle_detail_tabs = []
            self._api_page = None
            self._api_params = None
            self._api_warmups = 0
            self._api_failures = 0
            self._account_cookies_seeded = False
            profile_dir, self._profile_dir = self._profile_dir, None
            # Deliberately NOT persist_external_storage: this browser only reads
            # the shared extension state. Writing its guest session back would
            # overwrite what the account sessions saved (NordVPN login, etc).
            if profile_dir:
                await asyncio.to_thread(shutil.rmtree, profile_dir, ignore_errors=True)

    async def _ensure_page(self, proxy_url: Optional[str] = None):
        route_key = proxy_url or "__DIRECT__"
        if (
            self._route_key == route_key
            and self._page is not None
            and not self._page.is_closed()
        ):
            return self._page
        async with self._start_lock:
            if (
                self._route_key == route_key
                and self._page is not None
                and not self._page.is_closed()
            ):
                return self._page
            async with _browser_launch_lock():
                if self._invisible_pw is not None:
                    await self._close_session_unlocked()
                # Same extension profile as the account sessions, so OmoCaptcha
                # starts with its API key instead of "Invalid or missing API KEY".
                profile_dir, installed, excluded = await asyncio.to_thread(
                    _prepare_extension_profile
                )
                self._profile_dir = profile_dir
                os.environ["INVPW_TRUE_HEADLESS"] = (
                    "1" if getattr(settings, "BROWSER_TRUE_HEADLESS", True) else "0"
                )
                self._invisible_pw = InvisiblePlaywright(
                    proxy=_playwright_proxy_options(proxy_url),
                    headless=True,
                    humanize=True,
                    seed=731,
                    locale=getattr(settings, "TIKTOK_WEB_LOCALE", "en-US"),
                    timezone="auto",
                    profile_dir=profile_dir,
                    extra_prefs={
                        **firefox_prefs_for_extensions(installed),
                        "dom.webdriver.enabled": False,
                        "intl.accept_languages": "en-US, en",
                        "intl.locale.requested": "en-US",
                        "media.autoplay.default": 0,
                    },
                )
                self._invisible_pw.set_firefox_extensions(
                    item.xpi_path for item in installed
                )
                self._invisible_pw.set_firefox_extension_exclusions(excluded)
                try:
                    # A persistent profile can hang Firefox at startup; the
                    # account sessions bound it the same way.
                    self._browser = await asyncio.wait_for(
                        self._invisible_pw.__aenter__(),
                        timeout=max(15, int(getattr(settings, "BROWSER_LAUNCH_TIMEOUT", 45))),
                    )
                    self._page = await self._browser.new_page()
                    self._watch_for_api_template(self._page)
                    self._route_key = route_key
                    return self._page
                except BaseException:
                    await self._close_session_unlocked()
                    raise

    async def _apply_cookie_header(self, page, cookie_header: str) -> None:
        """Isolate the shared fallback browser to one account's TikTok cookies.

        ⛔ A GUEST READ CLEARS NOTHING. Clearing before every guest load threw
        away the cookies TikTok sets once its "Please wait..." challenge is
        passed, so the next load met the challenge again, and it wiped the
        session the item_list tab signs its calls with. Cookies are cleared
        only to remove an account's seeded session.
        """
        if not cookie_header and not self._account_cookies_seeded:
            return
        try:
            await page.context.clear_cookies()
        except Exception:
            pass
        self._account_cookies_seeded = False
        if not cookie_header:
            return
        self._account_cookies_seeded = True
        cookies = []
        for raw_part in cookie_header.split(";"):
            name, separator, value = raw_part.strip().partition("=")
            if not separator or not name:
                continue
            cookies.append({
                "name": name,
                "value": value,
                "url": "https://www.tiktok.com/",
            })
        if cookies:
            try:
                await page.context.add_cookies(cookies)
            except Exception as exc:
                logger.debug(
                    "Could not seed public-browser TikTok cookies: %s",
                    type(exc).__name__,
                )

    @staticmethod
    async def _navigate_html(page, url: str, attempts: int = 3) -> str:
        """Open a public TikTok URL with bounded retries and keep partial loads."""
        last_error: Optional[BaseException] = None
        for attempt in range(max(1, attempts)):
            try:
                await page.goto(
                    ensure_tiktok_english_url(url),
                    wait_until="domcontentloaded",
                    timeout=45000,
                )
            except Exception as exc:
                last_error = exc
            try:
                html = await page.content()
                if len(html or "") >= 300:
                    return html
            except Exception as exc:
                last_error = exc
            if attempt + 1 < attempts:
                await asyncio.sleep(0.8 * (attempt + 1))
        raise RuntimeError(
            "browser_public_page_unavailable"
            + (f": {type(last_error).__name__}" if last_error else "")
        )

    @staticmethod
    def _grid_key(username: str) -> str:
        return username.lstrip("@").casefold()

    async def _read_profile_grid(
        self, page, username: str, max_videos: int
    ) -> tuple[list[str], Dict[str, Any]]:
        """Scroll the open profile until its cards stop growing, then read them.

        Returns the owner's video ids in grid order and each card's item struct.
        """
        previous = -1
        stable = 0
        for _ in range(10):
            try:
                count = await page.evaluate(
                    "() => document.querySelectorAll('a[href*=\"/video/\"]').length"
                )
            except Exception:
                count = previous
            if count >= max_videos:
                break
            stable = stable + 1 if count == previous else 0
            if stable >= 2:
                break
            previous = count
            try:
                await page.evaluate(
                    "() => window.scrollTo(0, document.documentElement.scrollHeight)"
                )
            except Exception:
                break
            await asyncio.sleep(0.6)
        data = await page.evaluate(_READ_GRID_ITEMS_JS)
        links = normalize_profile_video_links(
            list(data.get("hrefs") or []), username, max_videos
        )
        ids = [urlparse(link).path.rstrip("/").rsplit("/", 1)[-1] for link in links]
        raw_items = data.get("items") or {}
        items = {vid: raw_items[vid] for vid in ids if isinstance(raw_items.get(vid), dict)}
        return ids, items

    async def _cache_grid_from_open_page(
        self, page, username: str, profile_metrics: Dict[str, Any]
    ) -> None:
        """Best effort: never turns a good profile read into a failure."""
        if int(profile_metrics.get("video_count") or 0) <= 0:
            return
        try:
            if not await _wait_in_place(page, _PROFILE_GRID_READY_JS, 8.0):
                return
            max_videos = max(1, int(getattr(settings, "FAST_ANALYTICS_MAX_VIDEOS_PER_ACCOUNT", 60)))
            ids, items = await self._read_profile_grid(page, username, max_videos)
            if items:
                self._grid_cache[self._grid_key(username)] = (
                    asyncio.get_running_loop().time(), ids, items
                )
        except Exception as exc:
            logger.debug("Grid read after profile failed for @%s: %s", username, type(exc).__name__)

    async def fetch_profile(
        self,
        username: str,
        proxy_url: Optional[str] = None,
        cookie_header: str = "",
    ) -> QuickCheckResult:
        """Browser fallback for HTTP timeout/WAF, scoped to the exact profile URL."""
        async with self._lock:
            try:
                page = await self._ensure_page(proxy_url)
            except Exception as exc:
                return QuickCheckResult(
                    None,
                    f"browser_profile_launch_{type(exc).__name__}",
                    retryable=True,
                )
            await self._apply_cookie_header(page, cookie_header)
            profile_url = ensure_tiktok_english_url(
                f"https://www.tiktok.com/@{username.lstrip('@')}"
            )
            last = QuickCheckResult(None, "browser_profile_unavailable", retryable=True)
            for attempt in range(2):
                if attempt == 1 and cookie_header:
                    # A stale authenticated session can itself trigger a WAF
                    # shell. The second browser read is deliberately guest-only.
                    await self._apply_cookie_header(page, "")
                try:
                    # The same challenge interstitial reached this step too and
                    # was classified as "browser_tiktok_challenge".
                    html, _ready = await self._open_until_ready(
                        page, profile_url, _PAGE_DATA_READY_JS
                    )
                except Exception as exc:
                    last = QuickCheckResult(
                        None,
                        f"browser_profile_network_{type(exc).__name__}",
                        retryable=True,
                    )
                else:
                    result = _classify_profile_response(html, username, 200)
                    if result.profile_metrics:
                        # The page that answered the profile also renders the
                        # video grid; read it now rather than load it again.
                        await self._cache_grid_from_open_page(
                            page, username, result.profile_metrics
                        )
                    if result.classification is not None or result.profile_metrics:
                        return result
                    last = QuickCheckResult(
                        result.classification,
                        f"browser_{result.reason}",
                        result.retryable,
                        result.http_status,
                        result.profile_metrics,
                        result.profile_identity,
                        result.profile_data,
                    )
                if attempt == 0:
                    await asyncio.sleep(1.0)
            return last

    def _watch_for_api_template(self, page) -> None:
        """Copy the query of the first item_list request any of our tabs makes."""

        def capture(request) -> None:
            if self._api_params is not None:
                return
            url = str(getattr(request, "url", "") or "")
            if _ITEM_LIST_PATH not in url:
                return
            params = {
                key: value
                for key, value in parse_qsl(urlparse(url).query, keep_blank_values=True)
                if key not in _API_VOLATILE_PARAMS and key not in _ITEM_LIST_OWN_PARAMS
            }
            if params:
                self._api_params = params

        try:
            page.on("request", capture)
        except Exception as exc:
            logger.debug("Cannot watch requests for the API template: %s", type(exc).__name__)

    def _api_tab_ready(self) -> bool:
        return (
            self._api_params is not None
            and self._api_page is not None
            and not self._api_page.is_closed()
        )

    async def _ensure_api_page(
        self, username: str, proxy_url: Optional[str]
    ) -> tuple[Any, Optional[QuickCheckResult]]:
        """The API tab, warmed by loading this account's own profile in it.

        The warm-up load is never wasted: it is this account's profile read,
        returned as the second value, and its grid is cached like fetch_profile
        does. Accounts arriving meanwhile wait here, then use the API.
        """
        await self._ensure_page(proxy_url)
        if self._api_tab_ready():
            return self._api_page, None
        async with self._api_warm_lock:
            if self._api_tab_ready():
                return self._api_page, None
            if self._api_warmups >= _API_MAX_WARMUPS:
                return None, None
            self._api_warmups += 1
            page = self._api_page
            if page is None or page.is_closed():
                page = await self._browser.new_page()
                self._watch_for_api_template(page)
                self._api_page = page
            profile_url = ensure_tiktok_english_url(
                f"https://www.tiktok.com/@{username.lstrip('@')}"
            )
            try:
                html, _ready = await self._open_until_ready(
                    page, profile_url, _PAGE_DATA_READY_JS
                )
            except Exception:
                return None, None
            result = _classify_profile_response(html, username, 200)
            if result.profile_metrics:
                # Rendering the grid is also what sends item_list, i.e. the template.
                await self._cache_grid_from_open_page(page, username, result.profile_metrics)
            self._api_failures = 0
            usable_page = page if self._api_tab_ready() else None
            if result.classification is not None or result.profile_metrics:
                return usable_page, result
            return usable_page, None

    async def _api_get(self, page, path: str, params: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """One signed call from inside the API tab; None on a transport failure."""
        async with self._api_gate:
            await asyncio.sleep(random.uniform(0.15, 0.4))
            try:
                response = await asyncio.wait_for(
                    page.evaluate(
                        _API_FETCH_JS,
                        {
                            "path": path,
                            "params": {**(self._api_params or {}), **params},
                            "timeoutMs": int(_API_TIMEOUT_SECONDS * 1000),
                        },
                    ),
                    timeout=_API_TIMEOUT_SECONDS + 10,
                )
            except Exception:
                response = None
        body: Any = None
        if isinstance(response, dict) and response.get("status") == 200 and response.get("text"):
            try:
                body = json.loads(response["text"])
            except ValueError:
                body = None
        if not isinstance(body, dict):
            self._api_failures += 1
            if self._api_failures >= _API_FAILURES_BEFORE_REWARM and self._api_page is page:
                # The tab's session went bad (challenge, expired token): load it again.
                self._api_page = None
                self._api_failures = 0
                try:
                    await page.close()
                except Exception:
                    pass
            return None
        self._api_failures = 0
        return body

    async def fetch_profile_via_api(
        self,
        username: str,
        sec_uid: str,
        proxy_url: Optional[str] = None,
        max_videos: int = 60,
    ) -> Optional[QuickCheckResult]:
        """Profile counts and every video item from item_list - no page load.

        ⛔ WHY. Loading the profile page was ~6s of every account even when
        nothing else was needed; the same data from TikTok's item_list API,
        called inside a tab that stays on TikTok, took 0.6s (20 of 20 read,
        counts equal to the profile's). Returns None whenever the answer is
        not clearly this user's complete list, and the caller loads the
        profile exactly as before. The videos go to the grid cache, so
        fetch_videos reads them from there.
        """
        username = username.lstrip("@")
        if not username or not sec_uid:
            return None
        try:
            page, warm_result = await self._ensure_api_page(username, proxy_url)
        except Exception as exc:
            logger.debug("API tab unavailable for @%s: %s", username, type(exc).__name__)
            return None
        if warm_result is not None:
            return warm_result
        if page is None:
            return None
        ids: list[str] = []
        items: Dict[str, Any] = {}
        cursor = "0"
        while len(ids) < max_videos:
            body = await self._api_get(page, _ITEM_LIST_PATH, {
                "secUid": sec_uid,
                "cursor": cursor,
                "count": str(_API_PAGE_SIZE),
                "coverFormat": "2",
            })
            if body is None or body.get("statusCode") not in (0, None):
                return None
            batch = body.get("itemList") or []
            if not isinstance(batch, list):
                return None
            for item in batch:
                video_id = str((item or {}).get("id") or "") if isinstance(item, dict) else ""
                if video_id.isdigit() and video_id not in items:
                    ids.append(video_id)
                    items[video_id] = item
            next_cursor = str(body.get("cursor") or "")
            if not body.get("hasMore") or not batch or not next_cursor or next_cursor == cursor:
                break
            cursor = next_cursor
        result = api_profile_result_from_items((items[vid] for vid in ids), username)
        if result is None:
            return None
        self._grid_cache[self._grid_key(username)] = (
            asyncio.get_running_loop().time(), ids[:max_videos], items
        )
        self.api_account_reads += 1
        return result

    async def _fetch_videos_via_grid(
        self,
        username: str,
        max_videos: int,
        expected_video_count: Optional[int],
        known_video_urls: list[str],
        proxy_url: Optional[str],
        page_fresh_video_ids: set[str],
    ) -> Optional[tuple[list[Dict[str, Any]], bool]]:
        """Every video's counts from ONE profile load; pages only where needed.

        ⛔ WHY. The page-by-page crawl opened one browser page per video, in
        series, behind a browser shared by the whole batch: 37 pages x 4.4s
        were 162s of a 198s run for 6 accounts - 33s an account, ~80 minutes
        for 144. The profile grid already holds each video's full item, so a
        page is opened only for what the grid cannot say (region, shadow-ban)
        and only when that is missing or stale. Returns None when the grid
        could not be read, so the caller falls back to the page crawl.
        """
        profile_url = ensure_tiktok_english_url(
            f"https://www.tiktok.com/@{username.lstrip('@')}"
        )
        loop = asyncio.get_running_loop()
        cached = self._grid_cache.pop(self._grid_key(username), None)
        if cached and loop.time() - cached[0] <= _GRID_CACHE_SECONDS:
            ids, items = cached[1], cached[2]
        else:
            async with self._lock:
                page = await self._ensure_page(proxy_url)
                await self._apply_cookie_header(page, "")
                try:
                    _html, ready = await self._open_until_ready(
                        page, profile_url, _PROFILE_GRID_READY_JS
                    )
                except Exception:
                    ready = False
                if not ready:
                    return None
                try:
                    ids, items = await self._read_profile_grid(page, username, max_videos)
                except Exception as exc:
                    logger.warning("Grid read failed for @%s: %s", username, type(exc).__name__)
                    return None
        if not items:
            return None

        def video_url(video_id: str) -> str:
            return ensure_tiktok_english_url(
                f"https://www.tiktok.com/@{username.lstrip('@')}/video/{video_id}"
            )

        rows: Dict[str, Dict[str, Any]] = {
            vid: grid_video_row_from_item(items[vid], share_url=video_url(vid))
            for vid in ids
            if vid in items
        }
        known_ids = [
            urlparse(link).path.rstrip("/").rsplit("/", 1)[-1]
            for link in resolve_profile_video_links([], known_video_urls, username, max_videos)
        ]
        need = [vid for vid in ids if vid not in rows or vid not in page_fresh_video_ids]
        # Known videos the grid no longer shows (hidden, under review): their
        # own page is the only place that can say what happened to them.
        need += [vid for vid in known_ids if vid not in ids and vid not in need]
        if need:
            links = [video_url(vid) for vid in need]
            results = await self._fetch_video_details_http(
                links, profile_url=profile_url, proxy_url=proxy_url, cookie_header=""
            )
            if any(row is None for row in results):
                await self._ensure_page(proxy_url)
                await self._fill_missing_details_in_tabs(links, results)
            for vid, page_row in zip(need, results):
                if page_row is not None:
                    rows[vid] = page_row   # the full page row supersedes the grid row

        expected = (
            min(max_videos, max(0, expected_video_count))
            if expected_video_count is not None
            else len(ids)
        )
        complete = len(ids) >= expected and all(
            rows.get(vid, {}).get("detail_available") is True for vid in ids
        )
        ordered = sorted(rows.values(), key=lambda row: row.get("create_time") or 0, reverse=True)
        return ordered[:max_videos], complete

    async def fetch_videos(
        self,
        username: str,
        sec_uid: str,
        max_videos: int = 30,
        expected_video_count: Optional[int] = None,
        known_video_urls: Optional[list[str]] = None,
        proxy_url: Optional[str] = None,
        cookie_header: str = "",
        page_fresh_video_ids: Optional[Iterable[str]] = None,
    ) -> tuple[list[Dict[str, Any]], bool]:
        # sec_uid is retained for API compatibility but the robust source is the
        # exact /@username URL and its direct /video/{id} links.
        if not username or max_videos <= 0:
            return [], False
        if expected_video_count is not None and expected_video_count <= 0:
            return [], True
        if page_fresh_video_ids is not None:
            via_grid = await self._fetch_videos_via_grid(
                username,
                max_videos,
                expected_video_count,
                list(known_video_urls or []),
                proxy_url,
                {str(value) for value in page_fresh_video_ids},
            )
            if via_grid is not None:
                return via_grid
            logger.warning("Grid unreadable for @%s; falling back to per-video pages", username)
        profile_url = ensure_tiktok_english_url(
            f"https://www.tiktok.com/@{username.lstrip('@')}"
        )
        expected = (
            min(max_videos, max(0, expected_video_count))
            if expected_video_count is not None
            else None
        )
        known_links = resolve_profile_video_links(
            [], list(known_video_urls or []), username, max_videos
        )

        # Most repeat syncs already know every numeric video ID. Refresh those
        # exact URLs concurrently and avoid serializing behind the shared
        # profile browser. If HTTP misses only a subset, open the browser for
        # that subset instead of re-crawling every video.
        known_set_is_current = (
            expected is not None
            and expected > 0
            and (
                len(known_links) == expected
                if expected_video_count is not None
                and expected_video_count <= max_videos
                else len(known_links) >= expected
            )
        )
        if known_set_is_current:
            links = known_links[:expected]
            results = await self._fetch_video_details_http(
                links,
                profile_url=profile_url,
                proxy_url=proxy_url,
                cookie_header=cookie_header,
            )
            if any(row is None for row in results):
                async with self._lock:
                    page = await self._ensure_page(proxy_url)
                    await self._apply_cookie_header(page, cookie_header)
                    await self._fill_missing_details_with_browser(
                        page, links, results
                    )
            return self._finish_video_results(
                links,
                results,
                max_videos=max_videos,
                expected_video_count=expected_video_count,
            )

        async with self._lock:
            page = await self._ensure_page(proxy_url)
            await self._apply_cookie_header(page, cookie_header)
            try:
                _html, grid_ready = await self._open_until_ready(
                    page, profile_url, _PROFILE_GRID_READY_JS
                )
                if not grid_ready:
                    logger.warning(
                        "Profile grid for @%s never rendered a video link", username
                    )
            except Exception:
                # Known direct video URLs remain useful if the profile grid is
                # temporarily withheld by TikTok or a route-specific WAF.
                logger.warning("Browser profile grid unavailable for @%s", username)

            collected_hrefs: list[str] = []
            stable_rounds = 0
            previous_count = -1
            for _ in range(10):
                try:
                    hrefs = await page.locator('a[href*="/video/"]').evaluate_all(
                        "elements => elements.map(element => element.href || element.getAttribute('href') || '')"
                    )
                except Exception:
                    hrefs = []
                collected_hrefs.extend(str(value or "") for value in hrefs)
                links = resolve_profile_video_links(
                    collected_hrefs,
                    list(known_video_urls or []),
                    username,
                    max_videos,
                )
                if len(links) >= max_videos:
                    break
                stable_rounds = stable_rounds + 1 if len(links) == previous_count else 0
                if stable_rounds >= 2:
                    break
                previous_count = len(links)
                try:
                    await page.evaluate(
                        "() => window.scrollTo(0, document.documentElement.scrollHeight)"
                    )
                except Exception:
                    break
                await asyncio.sleep(0.75)

            links = resolve_profile_video_links(
                collected_hrefs,
                list(known_video_urls or []),
                username,
                max_videos,
            )
            if not links:
                raise RuntimeError("profile_video_links_missing")

            cookie_values = {
                str(cookie.get("name") or ""): str(cookie.get("value") or "")
                for cookie in await page.context.cookies("https://www.tiktok.com/?lang=en")
                if cookie.get("name")
            }
            browser_cookie_header = cookie_header or "; ".join(
                f"{name}={value}" for name, value in cookie_values.items()
            )
            results = await self._fetch_video_details_http(
                links,
                profile_url=profile_url,
                proxy_url=proxy_url,
                cookie_header=browser_cookie_header,
            )

            # Plain HTTP is fastest, but TikTok sometimes returns a WAF stub to
            # it while the anonymous browser on the same route loads normally.
            # Retry only the missing detail pages through that browser.
            await self._fill_missing_details_with_browser(page, links, results)
            return self._finish_video_results(
                links,
                results,
                max_videos=max_videos,
                expected_video_count=expected_video_count,
            )

    async def close(self) -> None:
        async with self._start_lock:
            await self._close_session_unlocked()
