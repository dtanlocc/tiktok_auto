"""Collect public TikTok video metrics without logging into an account.

TikTok currently renders public video cards on the profile while its old
``/api/post/item_list/`` request can return HTTP 200 with an empty body.  Keep
one hidden page only to render/cursor the profile, then read each public video
page concurrently through ordinary HTTP.  This avoids treating the unstable
internal list endpoint as the source of truth.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import unquote, urlparse

import httpx
from invisible_playwright.async_api import InvisiblePlaywright

from app.core.config import settings
from app.core.tiktok_urls import ensure_tiktok_english_url
from app.use_cases.analytics.tiktok_analytics_sync import extract_studio_video_metrics
from app.use_cases.health_check.quick_check_use_case import (
    _HTTP_HEADERS,
    _TikTokJsonScriptParser,
)


logger = logging.getLogger("TikTokPublicVideoClient")


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
    parser = _TikTokJsonScriptParser()
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return None
    rows, _ = extract_studio_video_metrics(parser.documents)
    for row in rows:
        if str(row.get("video_id") or "") == str(expected_video_id):
            row["share_url"] = str(row.get("share_url") or share_url)
            return row
    return None


class TikTokPublicVideoClient:
    """A shared hidden profile renderer plus concurrent HTTP detail reader."""

    def __init__(self) -> None:
        self._invisible_pw: Optional[InvisiblePlaywright] = None
        self._browser = None
        self._page = None
        self._lock = asyncio.Lock()
        self._start_lock = asyncio.Lock()

    async def _ensure_page(self):
        if self._page is not None and not self._page.is_closed():
            return self._page
        async with self._start_lock:
            if self._page is not None and not self._page.is_closed():
                return self._page
            self._invisible_pw = InvisiblePlaywright(
                headless=True,
                humanize=True,
                seed=731,
                locale=getattr(settings, "TIKTOK_WEB_LOCALE", "en-US"),
                timezone="auto",
                extra_prefs={
                    "dom.webdriver.enabled": False,
                    "intl.accept_languages": "en-US, en",
                    "intl.locale.requested": "en-US",
                    "media.autoplay.default": 0,
                },
            )
            try:
                self._browser = await self._invisible_pw.__aenter__()
                self._page = await self._browser.new_page()
                return self._page
            except Exception:
                try:
                    await self._invisible_pw.__aexit__(None, None, None)
                except Exception:
                    pass
                self._page = None
                self._browser = None
                self._invisible_pw = None
                raise

    async def fetch_videos(
        self,
        username: str,
        sec_uid: str,
        max_videos: int = 30,
        expected_video_count: Optional[int] = None,
        known_video_urls: Optional[list[str]] = None,
    ) -> tuple[list[Dict[str, Any]], bool]:
        if not sec_uid or max_videos <= 0:
            return [], False
        if expected_video_count is not None and expected_video_count <= 0:
            return [], True
        page = await self._ensure_page()
        async with self._lock:
            profile_url = ensure_tiktok_english_url(
                f"https://www.tiktok.com/@{username.lstrip('@')}"
            )
            await page.goto(
                profile_url,
                wait_until="domcontentloaded",
                timeout=30000,
            )
            try:
                await page.locator('a[href*="/video/"]').first.wait_for(
                    state="attached", timeout=15000
                )
            except Exception:
                pass

            collected_hrefs: list[str] = []
            stable_rounds = 0
            previous_count = -1
            for _ in range(10):
                hrefs = await page.locator('a[href*="/video/"]').evaluate_all(
                    "elements => elements.map(element => element.href || element.getAttribute('href') || '')"
                )
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
                await page.evaluate(
                    "() => window.scrollTo(0, document.documentElement.scrollHeight)"
                )
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
            gate = asyncio.Semaphore(6)
            timeout = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)
            limits = httpx.Limits(max_connections=6, max_keepalive_connections=6)
            async with httpx.AsyncClient(
                headers={**_HTTP_HEADERS, "Referer": profile_url},
                cookies=cookie_values,
                follow_redirects=True,
                timeout=timeout,
                limits=limits,
                trust_env=False,
            ) as client:
                async def fetch_detail(url: str) -> Optional[Dict[str, Any]]:
                    async with gate:
                        url = ensure_tiktok_english_url(url)
                        video_id = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
                        try:
                            response = await client.get(url)
                            if response.status_code != 200 or not response.text:
                                return None
                            return extract_video_detail_html(
                                response.text, video_id, share_url=url
                            )
                        except Exception as exc:
                            logger.debug(
                                "Public video page failed for %s: %s",
                                video_id,
                                type(exc).__name__,
                            )
                            return None

                results = await asyncio.gather(
                    *(fetch_detail(url) for url in links)
                )

            rows = [row for row in results if row is not None]
            if not rows:
                raise RuntimeError("video_detail_pages_empty")
            rows.sort(
                key=lambda row: row.get("create_time") or 0, reverse=True
            )
            expected = (
                min(max_videos, max(0, expected_video_count))
                if expected_video_count is not None
                else len(links)
            )
            complete = len(links) >= expected and len(rows) == len(links)
            return rows[:max_videos], complete

    async def close(self) -> None:
        async with self._start_lock:
            try:
                if self._invisible_pw is not None:
                    await self._invisible_pw.__aexit__(None, None, None)
            finally:
                self._page = None
                self._browser = None
                self._invisible_pw = None
