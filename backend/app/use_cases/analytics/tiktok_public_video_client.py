"""Collect public TikTok video metrics without logging into an account.

One hidden browser page renders the profile; every video's counts come from
the items that page's grid already holds. A video's own page is opened only for
what the grid does not carry (region, shadow-ban) and only when that is missing
or stale - over HTTP first, the browser for what HTTP misses.
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import shutil
import tempfile
import uuid
import weakref
from typing import Any, Dict, Iterable, Optional
from urllib.parse import unquote, urlparse

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
        self.http_detail_requests = 0
        self.http_detail_retries = 0
        self.browser_detail_fallbacks = 0

    def reset_stats(self) -> None:
        self.http_detail_requests = 0
        self.http_detail_retries = 0
        self.browser_detail_fallbacks = 0

    def get_stats(self) -> Dict[str, int]:
        return {
            "video_http_requests": self.http_detail_requests,
            "video_http_retries": self.http_detail_retries,
            "video_browser_fallbacks": self.browser_detail_fallbacks,
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
                    url = ensure_tiktok_english_url(url)
                    video_id = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
                    for attempt in range(2):
                        response: Optional[httpx.Response] = None
                        try:
                            async with self._detail_global_gate:
                                async with route_gate:
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
                    self._route_key = route_key
                    return self._page
                except BaseException:
                    await self._close_session_unlocked()
                    raise

    @staticmethod
    async def _apply_cookie_header(page, cookie_header: str) -> None:
        """Isolate the shared fallback browser to one account's TikTok cookies."""
        try:
            await page.context.clear_cookies()
        except Exception:
            pass
        if not cookie_header:
            return
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
                async with self._lock:
                    page = await self._ensure_page(proxy_url)
                    await self._apply_cookie_header(page, "")
                    await self._fill_missing_details_with_browser(page, links, results)
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
