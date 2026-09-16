"""Fast public TikTok profile/video metrics sync.

The profile pass is plain HTTP. Known video URLs also stay on bounded HTTP;
only unresolved profiles/pages enter a two-slot invisible_playwright fallback.
It never opens TikTok Studio or authorizes a Developer application. Unavailable
metrics remain untouched instead of being guessed as zero.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional
from urllib.parse import quote, urlparse

import httpx
from sqlmodel import Session, select

from app.core.config import settings
from app.domain.account_rules import is_sold_account
from app.core.tiktok_urls import ensure_tiktok_english_url
from app.infrastructure.database.connection import engine
from app.infrastructure.database.sqlite_repository import (
    SQLiteAccountRepository,
    SQLiteProxyRepository,
)
from app.infrastructure.websocket.socket_manager import ws_manager
from app.infrastructure.database.schemas import TikTokVideoMetricDbTable
from app.use_cases.analytics.tiktok_public_video_client import TikTokPublicVideoClient
from app.use_cases.health_check.quick_check_use_case import (
    QuickCheckResult,
    _HTTP_HEADERS,
    _classify_profile_response,
)


logger = logging.getLogger("TikTokFastAnalyticsSync")

PUBLIC_PROFILE_SOURCE = "TIKTOK_PUBLIC_PROFILE"
_REQUIRED_PROFILE_METRICS = (
    "video_count",
    "follower_count",
    "following_count",
    "likes_count",
)


def profile_metric_sync_result(metrics: Dict[str, int]) -> tuple[str, str]:
    """Return an honest sync status without treating unavailable data as zero."""
    missing = [name for name in _REQUIRED_PROFILE_METRICS if name not in metrics]
    if not missing:
        return "SUCCESS", ""
    if metrics:
        return (
            "PARTIAL",
            "TikTok không trả đủ chỉ số profile công khai: " + ", ".join(missing),
        )
    return "FAILED", "TikTok không trả dữ liệu profile công khai có cấu trúc."


def _is_cache_fresh(updated_at: str, ttl_seconds: int) -> bool:
    if ttl_seconds <= 0 or not updated_at:
        return False
    try:
        timestamp = datetime.fromisoformat(updated_at)
    except (TypeError, ValueError):
        return False
    return datetime.now() - timestamp < timedelta(seconds=ttl_seconds)


def _is_sync_cache_usable(
    status: str,
    source: str,
    updated_at: str,
    ttl_seconds: int,
) -> bool:
    """Only successful profile reads may suppress a later sync attempt."""
    return (
        status in {"SUCCESS", "PARTIAL"}
        and source in {PUBLIC_PROFILE_SOURCE, "TIKTOK_PUBLIC_WEB"}
        and _is_cache_fresh(updated_at, ttl_seconds)
    )


def _is_video_cache_usable(
    rows: list[Any],
    expected: int,
    ttl_seconds: int,
) -> bool:
    """Reuse a complete, recent detail snapshot without issuing video requests."""
    if expected <= 0 or len(rows) != expected:
        return False
    return all(
        _is_cache_fresh(str(getattr(row, "synced_at", "") or ""), ttl_seconds)
        for row in rows[:expected]
    )


def _page_fresh_video_ids(rows: Iterable[Any], ttl_seconds: int) -> set[str]:
    """Videos whose page-only fields (region, shadow-ban) are known and recent.

    A row predating detail_synced_at is dated by synced_at: before the grid
    sync existed, every stored row was written from the video's own page.
    """
    fresh: set[str] = set()
    for row in rows:
        video_id = str(getattr(row, "video_id", "") or "")
        if not video_id or not str(getattr(row, "region", "") or ""):
            continue
        stamp = str(getattr(row, "detail_synced_at", "") or "") or str(
            getattr(row, "synced_at", "") or ""
        )
        if _is_cache_fresh(stamp, ttl_seconds):
            fresh.add(video_id)
    return fresh


def _stale_video_ids_to_remove(
    existing_ids: set[str],
    current_ids: set[str],
    *,
    complete: bool,
    profile_video_count: int,
    max_videos: int,
) -> set[str]:
    """Delete old rows only when the crawl covered the entire public profile."""
    if not complete or profile_video_count > max(1, max_videos):
        return set()
    return existing_ids - current_ids


def _merge_video_completeness(
    status: str,
    error: str,
    expected: int,
    collected: int,
    complete: bool,
    rows: Optional[int] = None,
    restricted: Optional[Iterable[str]] = None,
) -> tuple[str, str]:
    """Do not report SUCCESS when only part of the public video list was read.

    ``rows`` is how many videos the crawl found on the profile. When every one
    of them was read in full and the total is still short of TikTok's own
    count, nothing failed: TikTok counts videos the public profile does not
    show - private, under review, restricted. Measured on the 'test 16/9'
    batch: two accounts counted 4, showed 3 after scrolling, and all 3 read
    cleanly, on every one of four runs. Saying "chưa đủ" sent the reader after
    the crawler. It stays PARTIAL - a counted video nobody can see is worth
    noticing - but the message says what it is.
    """
    if status != "SUCCESS" or expected <= 0 or complete:
        return status, error
    restricted = [reason for reason in (restricted or []) if reason]
    if rows is not None and restricted and rows == collected + len(restricted):
        # Every row was either read in full or refused by TikTok with a stated
        # reason on the video's own page. Measured on the full 'reg web' batch:
        # all six "chưa đủ (n-1/n)" accounts were a video answered with
        # statusCode 10231, "không vượt qua kiểm duyệt" - nothing left to read.
        counts: Dict[str, int] = {}
        for reason in restricted:
            counts[reason] = counts.get(reason, 0) + 1
        summary = "; ".join(f"{n} video: {reason}" for reason, n in counts.items())
        message = (
            f"Đã đọc đủ {collected} video công khai; {len(restricted)} video bị "
            f"TikTok hạn chế ({summary})."
        )
        if expected > rows:
            message += (
                f" TikTok còn đếm thêm {expected - rows} video không hiện công khai."
            )
        return "PARTIAL", message[:500]
    if rows is not None and 0 < rows == collected < expected:
        hidden = expected - collected
        return (
            "PARTIAL",
            f"Đã đọc đủ {collected} video profile đang hiển thị; TikTok đếm "
            f"{expected} — {hidden} video không hiện công khai (riêng tư / "
            "đang xét duyệt / bị hạn chế) hoặc chưa tải được.",
        )
    return (
        "PARTIAL",
        f"Profile đã đồng bộ; chi tiết video chưa đủ ({collected}/{expected}).",
    )


class TikTokFastAnalyticsSyncService:
    """Bounded-concurrency public sync, independent from the browser dispatcher."""

    def __init__(self) -> None:
        self.is_running = False
        self.total = 0
        self.completed = 0
        self.updated = 0
        self.failed = 0
        self.cached = 0
        self.video_cached = 0
        self.skipped_sold = 0
        self.browser_profile_fallbacks = 0
        self.reason_counts: Dict[str, int] = {}
        self._task: Optional[asyncio.Task] = None
        browser_concurrency = max(
            1,
            min(
                int(getattr(settings, "FAST_ANALYTICS_BROWSER_CONCURRENCY", 2)),
                4,
            ),
        )
        detail_concurrency = max(
            1,
            int(getattr(settings, "FAST_ANALYTICS_DETAIL_REQUEST_CONCURRENCY", 6)),
        )
        detail_global_gate = asyncio.Semaphore(detail_concurrency)
        detail_account_gate = asyncio.Semaphore(detail_concurrency)
        detail_route_gates: Dict[str, asyncio.Semaphore] = {}
        self._video_clients = [
            TikTokPublicVideoClient(
                detail_request_concurrency=detail_concurrency,
                detail_global_gate=detail_global_gate,
                detail_account_gate=detail_account_gate,
                detail_route_gates=detail_route_gates,
            )
            for _ in range(browser_concurrency)
        ]
        # Compatibility alias for integrations which inspect the original
        # single-client attribute. Runtime work is sharded deterministically.
        self._video_client = self._video_clients[0]
        self._route_reachability: Dict[str, tuple[float, bool]] = {}

    def _video_client_for(self, account_id: str) -> TikTokPublicVideoClient:
        slot = sum(account_id.encode("utf-8")) % len(self._video_clients)
        return self._video_clients[slot]

    def _video_client_stats(self) -> Dict[str, int]:
        totals = {
            "video_http_requests": 0,
            "video_http_retries": 0,
            "video_browser_fallbacks": 0,
        }
        for client in self._video_clients:
            for name, value in client.get_stats().items():
                totals[name] = totals.get(name, 0) + int(value)
        return totals

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_running": self.is_running,
            "total": self.total,
            "completed": self.completed,
            "updated": self.updated,
            "failed": self.failed,
            "cached": self.cached,
            "video_cached": self.video_cached,
            "skipped_sold": self.skipped_sold,
            "browser_profile_fallbacks": self.browser_profile_fallbacks,
            "reason_counts": dict(self.reason_counts),
            **self._video_client_stats(),
        }

    async def shutdown(self) -> None:
        """Close the shared signer once, when the backend itself stops."""
        await asyncio.gather(
            *(client.close() for client in self._video_clients),
            return_exceptions=True,
        )

    def start_batch(
        self,
        account_ids: Iterable[str],
        concurrency_limit: int = 4,
        force: bool = False,
    ) -> bool:
        if self.is_running:
            return False
        ids = list(dict.fromkeys(str(value).strip().lower() for value in account_ids if value))
        if not ids:
            return False
        # Set synchronously so two API requests cannot both pass the guard before
        # the background coroutine gets its first event-loop turn.
        self.is_running = True
        self._task = asyncio.create_task(
            self.run_batch(ids, concurrency_limit=concurrency_limit, force=force)
        )
        return True

    @staticmethod
    def _build_proxy_url(session: Session, proxy_id: Optional[str]) -> Optional[str]:
        if not settings.USE_PROXY or not proxy_id:
            return None
        proxy = SQLiteProxyRepository(session).get_by_id(proxy_id)
        if not proxy or not proxy.host:
            return None
        scheme = (proxy.protocol or "http").strip()
        auth = ""
        if proxy.username:
            auth = (
                f"{quote(str(proxy.username), safe='')}:"
                f"{quote(str(proxy.password or ''), safe='')}@"
            )
        return f"{scheme}://{auth}{proxy.host}:{proxy.port}"

    @classmethod
    def _build_public_route_candidates(
        cls,
        session: Session,
        assigned_proxy_id: Optional[str],
    ) -> list[Optional[str]]:
        """Assigned proxy first, then bounded guest-only proxy fallbacks."""
        if not settings.USE_PROXY:
            return [None]
        proxy_repo = SQLiteProxyRepository(session)
        proxies = proxy_repo.get_all()
        ordered = sorted(
            proxies,
            key=lambda proxy: 0 if proxy.id == assigned_proxy_id else 1,
        )
        routes: list[Optional[str]] = []
        for proxy in ordered:
            route = cls._build_proxy_url(session, proxy.id)
            if route and route not in routes:
                routes.append(route)
        limit = max(
            1,
            int(getattr(settings, "FAST_ANALYTICS_PROXY_ROUTE_ATTEMPTS", 3)),
        )
        return routes[:limit] or [None]

    async def _proxy_endpoint_reachable(self, proxy_url: Optional[str]) -> bool:
        """Skip an offline proxy port before paying several HTTP/browser timeouts."""
        if not proxy_url:
            return True
        cached = self._route_reachability.get(proxy_url)
        now = time.monotonic()
        if cached and now - cached[0] < 60:
            return cached[1]
        parsed = urlparse(proxy_url)
        if not parsed.hostname or not parsed.port:
            self._route_reachability[proxy_url] = (now, False)
            return False
        writer = None
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(parsed.hostname, parsed.port),
                timeout=3.0,
            )
            reachable = True
        except Exception:
            reachable = False
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
        self._route_reachability[proxy_url] = (time.monotonic(), reachable)
        return reachable

    @staticmethod
    async def _fetch_profile(
        client: httpx.AsyncClient,
        username: str,
        cookie_header: str = "",
    ) -> QuickCheckResult:
        url = ensure_tiktok_english_url(
            f"https://www.tiktok.com/@{quote(username, safe='')}"
        )
        try:
            response = await client.get(url, headers={"Cookie": cookie_header})
        except (httpx.TimeoutException, httpx.NetworkError, httpx.ProxyError) as exc:
            return QuickCheckResult(
                None,
                f"profile_network_{type(exc).__name__}",
                retryable=True,
            )
        except Exception as exc:
            logger.warning("Fast profile request failed: %s", type(exc).__name__)
            return QuickCheckResult(None, f"profile_unexpected_{type(exc).__name__}")
        return _classify_profile_response(response.text or "", username, response.status_code)

    async def _fetch_with_fallback(
        self,
        client: httpx.AsyncClient,
        username: str,
        cookie_header: str,
        run_limited: Callable[
            [Callable[[], Awaitable[QuickCheckResult]]], Awaitable[QuickCheckResult]
        ],
    ) -> QuickCheckResult:
        # Guest first: no account authorization and no stale session state. A
        # stored account cookie is only a fallback for profiles hidden by guest WAF.
        result = await run_limited(lambda: self._fetch_profile(client, username, ""))
        if result.profile_metrics or result.classification == "DIE" or not cookie_header:
            return result
        await asyncio.sleep(random.uniform(0.15, 0.35))
        cookie_result = await run_limited(
            lambda: self._fetch_profile(client, username, cookie_header)
        )
        return cookie_result if cookie_result.profile_metrics else result

    async def _process_one(
        self,
        account_id: str,
        clients: Dict[Optional[str], httpx.AsyncClient],
        global_gate: asyncio.Semaphore,
        proxy_gates: Dict[str, asyncio.Semaphore],
        cache_ttl_seconds: int,
        force: bool,
    ) -> None:
        video_client = self._video_client_for(account_id)
        try:
            with Session(engine) as session:
                repo = SQLiteAccountRepository(session)
                account = repo.get_by_id(account_id)
                if not account or not account.username:
                    self.failed += 1
                    return
                if is_sold_account(account):
                    self.skipped_sold += 1
                    return
                if (
                    not force
                    and _is_sync_cache_usable(
                        account.analytics_sync_status,
                        account.analytics_sync_source,
                        account.metrics_updated_at,
                        cache_ttl_seconds,
                    )
                ):
                    self.cached += 1
                    return
                username = account.username.lstrip("@")
                previous_video_sync_success = (
                    account.analytics_sync_status == "SUCCESS"
                    and account.analytics_sync_source == "TIKTOK_PUBLIC_WEB"
                )
                route_candidates = self._build_public_route_candidates(
                    session, account.proxy_id
                )
                # ⛔ GUEST ONLY. Everything this sync reads is public, and the
                # account's own session is actively harmful here: with it,
                # TikTok serves the owner's "Edit profile" view, whose video
                # grid rendered 0 links where the guest view of the same
                # profile rendered all 3 (@dar_8101_ciip, 2026-09-16) - the
                # source of "profile_video_links_missing". It also sent a live
                # session cookie through plain HTTP with a non-browser
                # fingerprint, which risks the session for no gain.
                known_video_rows = session.exec(
                    select(TikTokVideoMetricDbTable).where(
                        TikTokVideoMetricDbTable.account_email == account_id
                    )
                ).all()
                known_video_rows.sort(
                    key=lambda row: int(row.create_time or 0), reverse=True
                )
                # Rebuild the URL with the current username. TikTok usernames can
                # change while the stable numeric video ID remains the same.
                known_video_urls = [
                    ensure_tiktok_english_url(
                        f"https://www.tiktok.com/@{username}/video/{row.video_id}"
                    )
                    for row in known_video_rows
                    if str(row.video_id or "").isdigit()
                ]
                page_fresh_video_ids = _page_fresh_video_ids(
                    known_video_rows,
                    max(0, int(getattr(settings, "FAST_ANALYTICS_PAGE_DETAIL_TTL_HOURS", 24)))
                    * 3600,
                )

            result = QuickCheckResult(None, "public_routes_unavailable", retryable=True)
            proxy_url = route_candidates[0]
            reachable_routes: list[Optional[str]] = []
            for candidate_index, candidate_proxy_url in enumerate(route_candidates):
                proxy_url = candidate_proxy_url
                if not await self._proxy_endpoint_reachable(proxy_url):
                    result = QuickCheckResult(
                        None,
                        "proxy_endpoint_unreachable",
                        retryable=True,
                    )
                    if candidate_index + 1 < len(route_candidates):
                        logger.info(
                            "Assigned public proxy is offline for @%s; trying fallback %s/%s",
                            username,
                            candidate_index + 2,
                            len(route_candidates),
                        )
                    continue
                reachable_routes.append(proxy_url)
                client = clients.get(proxy_url)
                if client is None:
                    client = httpx.AsyncClient(
                        proxy=proxy_url,
                        headers=_HTTP_HEADERS,
                        timeout=httpx.Timeout(
                            connect=10.0, read=15.0, write=5.0, pool=8.0
                        ),
                        limits=httpx.Limits(
                            max_connections=2, max_keepalive_connections=2
                        ),
                        follow_redirects=True,
                        trust_env=False,
                    )
                    clients[proxy_url] = client

                proxy_key = proxy_url or "__DIRECT__"
                per_route_limit = 1 if proxy_url else 2
                proxy_gate = proxy_gates.setdefault(
                    proxy_key, asyncio.Semaphore(per_route_limit)
                )

                async def run_limited(
                    factory: Callable[[], Awaitable[QuickCheckResult]],
                ) -> QuickCheckResult:
                    async with global_gate:
                        async with proxy_gate:
                            await asyncio.sleep(random.uniform(0.10, 0.30))
                            return await factory()

                route_result = await self._fetch_with_fallback(
                    client, username, "", run_limited
                )
                result = route_result
                if route_result.classification == "DIE":
                    break
                if route_result.profile_metrics and route_result.profile_identity:
                    break
                if candidate_index + 1 < len(route_candidates):
                    logger.info(
                        "Public HTTP route failed for @%s (%s); trying route %s/%s before browser",
                        username,
                        result.reason,
                        candidate_index + 2,
                        len(route_candidates),
                    )

            # Opening Firefox is the expensive fallback. Try every bounded HTTP
            # route first, then use the browser only for accounts whose public
            # structured profile still could not be read. This avoids repeated
            # browser restarts when several proxies are configured.
            if (
                result.classification != "DIE"
                and not (result.profile_metrics and result.profile_identity)
                and reachable_routes
            ):
                browser_route_limit = max(
                    1,
                    min(
                        int(getattr(
                            settings,
                            "FAST_ANALYTICS_BROWSER_ROUTE_ATTEMPTS",
                            1,
                        )),
                        len(reachable_routes),
                    ),
                )
                for browser_proxy_url in reachable_routes[:browser_route_limit]:
                    self.browser_profile_fallbacks += 1
                    browser_result = await video_client.fetch_profile(
                        username,
                        proxy_url=browser_proxy_url,
                    )
                    result = browser_result
                    proxy_url = browser_proxy_url
                    if browser_result.classification == "DIE":
                        break
                    if (
                        browser_result.profile_metrics
                        and browser_result.profile_identity
                    ):
                        break
            self.reason_counts[result.reason] = self.reason_counts.get(result.reason, 0) + 1
            metrics = result.profile_metrics or {}
            status, error = profile_metric_sync_result(metrics)
            videos: list[Dict[str, Any]] = []
            profile_video_count = max(0, int(metrics.get("video_count") or 0))
            videos_complete = profile_video_count == 0
            video_cache_used = False
            video_error = ""
            if (
                metrics
                and result.profile_identity
                and settings.FAST_ANALYTICS_FETCH_VIDEOS
                and profile_video_count > 0
            ):
                expected_video_details = min(
                    max(1, settings.FAST_ANALYTICS_MAX_VIDEOS_PER_ACCOUNT),
                    profile_video_count,
                )
                video_cache_used = (
                    not force
                    and previous_video_sync_success
                    and _is_video_cache_usable(
                        known_video_rows,
                        expected_video_details,
                        max(
                            0,
                            int(getattr(
                                settings,
                                "FAST_ANALYTICS_VIDEO_CACHE_TTL_SECONDS",
                                300,
                            )),
                        ),
                    )
                )
                if video_cache_used:
                    videos_complete = True
                    self.video_cached += 1
                else:
                    try:
                        videos, videos_complete = await video_client.fetch_videos(
                            username,
                            result.profile_identity.get("sec_uid", ""),
                            max_videos=max(1, settings.FAST_ANALYTICS_MAX_VIDEOS_PER_ACCOUNT),
                            expected_video_count=profile_video_count,
                            known_video_urls=known_video_urls,
                            proxy_url=proxy_url,
                            page_fresh_video_ids=page_fresh_video_ids,
                        )
                    except Exception as exc:
                        video_error = f"video_detail_{type(exc).__name__}: {str(exc)[:160]}"
                        logger.warning("Public video detail failed for %s: %s", username, video_error)
            if video_error and status == "SUCCESS":
                status = "PARTIAL"
                error = "Profile đã đồng bộ; chi tiết video chưa lấy được (" + video_error + ")"
            elif metrics and settings.FAST_ANALYTICS_FETCH_VIDEOS:
                expected_video_details = min(
                    max(1, settings.FAST_ANALYTICS_MAX_VIDEOS_PER_ACCOUNT),
                    profile_video_count,
                )
                status, error = _merge_video_completeness(
                    status,
                    error,
                    expected_video_details,
                    sum(
                        1
                        for video in videos
                        if video.get("detail_available") is True
                    ),
                    videos_complete,
                    rows=len(videos),
                    restricted=[
                        str(video.get("shadow_ban_reason") or "")
                        for video in videos
                        if video.get("detail_available") is False
                    ],
                )

            with Session(engine) as session:
                repo = SQLiteAccountRepository(session)
                account = repo.get_by_id(account_id)
                if account is None or is_sold_account(account):
                    self.skipped_sold += 1
                    return
                if metrics:
                    for field_name, value in metrics.items():
                        setattr(account, field_name, value)
                    for field_name, value in (result.profile_data or {}).items():
                        if hasattr(account, field_name):
                            setattr(account, field_name, value)
                    account.metrics_updated_at = datetime.now().isoformat(timespec="seconds")
                    account.analytics_sync_source = PUBLIC_PROFILE_SOURCE
                    if videos or video_cache_used:
                        account.analytics_sync_source = "TIKTOK_PUBLIC_WEB"
                    account.analytics_sync_status = status
                    account.analytics_sync_error = error[:500]
                    self.updated += 1
                else:
                    account.analytics_sync_status = "FAILED"
                    account.analytics_sync_error = (
                        "Đồng bộ nhanh chưa lấy được dữ liệu: " + result.reason
                    )[:500]
                    self.failed += 1
                if videos:
                    existing = session.exec(
                        select(TikTokVideoMetricDbTable).where(
                            TikTokVideoMetricDbTable.account_email == account_id
                        )
                    ).all()
                    existing_by_id = {row.video_id: row for row in existing}
                    current_video_ids = {
                        str(video.get("video_id") or "")
                        for video in videos
                        if str(video.get("video_id") or "")
                    }
                    synced_at = datetime.now().isoformat(timespec="seconds")
                    for video in videos:
                        video_id = str(video.get("video_id") or "")
                        if not video_id:
                            continue
                        row = existing_by_id.get(video_id) or TikTokVideoMetricDbTable(
                            account_email=account_id, video_id=video_id
                        )
                        if video.get("title") is not None:
                            row.title = str(video.get("title") or "")
                        if video.get("create_time") is not None:
                            row.create_time = int(video["create_time"])
                        for field_name in (
                            "view_count",
                            "like_count",
                            "comment_count",
                            "share_count",
                            "favorite_count",
                            "repost_count",
                            "download_count",
                            "duration_seconds",
                        ):
                            if video.get(field_name) is not None:
                                setattr(row, field_name, int(video[field_name]))
                        for field_name in (
                            "cover_url",
                            "share_url",
                            "max_quality",
                            "detail_source",
                            "region",
                            "shadow_ban",
                            "shadow_ban_reason",
                        ):
                            if video.get(field_name) is not None:
                                setattr(row, field_name, str(video[field_name]))
                        for field_name in (
                            "index_enabled",
                            "is_reviewing",
                            "is_private",
                            "is_taken_down",
                        ):
                            if field_name in video:
                                setattr(row, field_name, video[field_name])
                        if "region" in video:
                            # This row came from the video's own page.
                            row.detail_synced_at = synced_at
                        elif not row.detail_synced_at and row.synced_at:
                            # Rows written before detail_synced_at existed were
                            # page reads; date them before synced_at moves on,
                            # or a grid sync would make them look fresh forever.
                            row.detail_synced_at = row.synced_at
                        row.synced_at = synced_at
                        session.add(row)
                    stale_video_ids = _stale_video_ids_to_remove(
                        set(existing_by_id),
                        current_video_ids,
                        complete=videos_complete,
                        profile_video_count=profile_video_count,
                        max_videos=settings.FAST_ANALYTICS_MAX_VIDEOS_PER_ACCOUNT,
                    )
                    if stale_video_ids:
                        for stale_row in existing:
                            if stale_row.video_id in stale_video_ids:
                                session.delete(stale_row)
                    session.flush()
                    all_rows = session.exec(
                        select(TikTokVideoMetricDbTable).where(
                            TikTokVideoMetricDbTable.account_email == account_id
                        )
                    ).all()
                    account.collected_video_count = len(all_rows)
                    if videos_complete and all_rows:
                        account.total_views = sum(row.view_count for row in all_rows)
                        account.total_video_likes = sum(row.like_count for row in all_rows)
                        account.total_comments = sum(row.comment_count for row in all_rows)
                        account.total_shares = sum(row.share_count for row in all_rows)
                elif (
                    metrics
                    and settings.FAST_ANALYTICS_FETCH_VIDEOS
                    and profile_video_count == 0
                ):
                    empty_profile_rows = session.exec(
                        select(TikTokVideoMetricDbTable).where(
                            TikTokVideoMetricDbTable.account_email == account_id
                        )
                    ).all()
                    for stale_row in empty_profile_rows:
                        session.delete(stale_row)
                    account.collected_video_count = 0
                    account.total_views = 0
                    account.total_video_likes = 0
                    account.total_comments = 0
                    account.total_shares = 0
                repo.save(account)
                event_data = {
                    "id": account.id,
                    "video_count": account.video_count,
                    "follower_count": account.follower_count,
                    "following_count": account.following_count,
                    "likes_count": account.likes_count,
                    "tiktok_user_id": account.tiktok_user_id,
                    "tiktok_sec_uid": account.tiktok_sec_uid,
                    "display_name": account.display_name,
                    "bio": account.bio,
                    "avatar_url": account.avatar_url,
                    "verified": account.verified,
                    "private_account": account.private_account,
                    "website_url": account.website_url,
                    "total_views": account.total_views,
                    "total_video_likes": account.total_video_likes,
                    "total_comments": account.total_comments,
                    "total_shares": account.total_shares,
                    "analytics_sync_status": account.analytics_sync_status,
                    "analytics_sync_source": account.analytics_sync_source,
                    "analytics_sync_error": account.analytics_sync_error,
                    "metrics_updated_at": account.metrics_updated_at,
                }
            await ws_manager.broadcast({"event": "ACCOUNT_STATUS_CHANGED", "data": event_data})
        finally:
            self.completed += 1

    async def run_batch(
        self,
        account_ids: Iterable[str],
        concurrency_limit: int = 4,
        force: bool = False,
    ) -> None:
        ids = list(dict.fromkeys(str(value).strip().lower() for value in account_ids if value))
        self.total = len(ids)
        self.completed = 0
        self.updated = 0
        self.failed = 0
        self.cached = 0
        self.video_cached = 0
        self.skipped_sold = 0
        self.browser_profile_fallbacks = 0
        self.reason_counts = {}
        for client in self._video_clients:
            client.reset_stats()
        clients: Dict[Optional[str], httpx.AsyncClient] = {}
        proxy_gates: Dict[str, asyncio.Semaphore] = {}
        global_gate = asyncio.Semaphore(max(1, min(concurrency_limit, 24)))
        cache_ttl_seconds = max(0, settings.FAST_ANALYTICS_CACHE_TTL_SECONDS)
        try:
            results = await asyncio.gather(
                *(
                    self._process_one(
                        account_id,
                        clients,
                        global_gate,
                        proxy_gates,
                        cache_ttl_seconds,
                        force,
                    )
                    for account_id in ids
                ),
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    self.failed += 1
                    logger.error("Fast analytics worker failed: %s", result)
        finally:
            for client in clients.values():
                try:
                    await client.aclose()
                except Exception:
                    pass
            self.is_running = False
            await ws_manager.broadcast(
                {"event": "FAST_ANALYTICS_FINISHED", "data": self.get_status()}
            )


fast_analytics_sync_service = TikTokFastAnalyticsSyncService()
