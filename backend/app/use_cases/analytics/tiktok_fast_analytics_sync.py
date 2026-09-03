"""Fast public TikTok profile/video metrics sync.

The profile pass is plain HTTP. The optional public-video pass uses one shared
invisible_playwright signer, never logs into TikTok Studio or authorizes a
Developer application. Unavailable metrics remain untouched instead of being
guessed as zero.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional
from urllib.parse import quote

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
    _build_tiktok_cookie_header,
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


class TikTokFastAnalyticsSyncService:
    """Bounded-concurrency public sync, independent from the browser dispatcher."""

    def __init__(self) -> None:
        self.is_running = False
        self.total = 0
        self.completed = 0
        self.updated = 0
        self.failed = 0
        self.cached = 0
        self.skipped_sold = 0
        self.reason_counts: Dict[str, int] = {}
        self._task: Optional[asyncio.Task] = None
        self._video_client = TikTokPublicVideoClient()

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_running": self.is_running,
            "total": self.total,
            "completed": self.completed,
            "updated": self.updated,
            "failed": self.failed,
            "cached": self.cached,
            "skipped_sold": self.skipped_sold,
            "reason_counts": dict(self.reason_counts),
        }

    async def shutdown(self) -> None:
        """Close the shared signer once, when the backend itself stops."""
        await self._video_client.close()

    def start_batch(
        self,
        account_ids: Iterable[str],
        concurrency_limit: int = 12,
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
                    and account.analytics_sync_source in {
                        PUBLIC_PROFILE_SOURCE,
                        "TIKTOK_PUBLIC_WEB",
                    }
                    and _is_cache_fresh(account.metrics_updated_at, cache_ttl_seconds)
                ):
                    self.cached += 1
                    return
                username = account.username.lstrip("@")
                proxy_url = self._build_proxy_url(session, account.proxy_id)
                cookie_header = _build_tiktok_cookie_header(account.cookies)
                known_video_rows = session.exec(
                    select(TikTokVideoMetricDbTable).where(
                        TikTokVideoMetricDbTable.account_email == account_id
                    )
                ).all()
                # Rebuild the URL with the current username. TikTok usernames can
                # change while the stable numeric video ID remains the same.
                known_video_urls = [
                    ensure_tiktok_english_url(
                        f"https://www.tiktok.com/@{username}/video/{row.video_id}"
                    )
                    for row in known_video_rows
                    if str(row.video_id or "").isdigit()
                ]

            client = clients.get(proxy_url)
            if client is None:
                client = httpx.AsyncClient(
                    proxy=proxy_url,
                    headers=_HTTP_HEADERS,
                    timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0),
                    limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
                    follow_redirects=True,
                    trust_env=False,
                )
                clients[proxy_url] = client

            proxy_key = proxy_url or "__DIRECT__"
            per_route_limit = 2 if proxy_url else 4
            proxy_gate = proxy_gates.setdefault(
                proxy_key, asyncio.Semaphore(per_route_limit)
            )

            async def run_limited(
                factory: Callable[[], Awaitable[QuickCheckResult]],
            ) -> QuickCheckResult:
                async with global_gate:
                    async with proxy_gate:
                        await asyncio.sleep(random.uniform(0.02, 0.10))
                        return await factory()

            result = await self._fetch_with_fallback(
                client, username, cookie_header, run_limited
            )
            self.reason_counts[result.reason] = self.reason_counts.get(result.reason, 0) + 1
            metrics = result.profile_metrics or {}
            status, error = profile_metric_sync_result(metrics)
            videos: list[Dict[str, Any]] = []
            profile_video_count = max(0, int(metrics.get("video_count") or 0))
            videos_complete = profile_video_count == 0
            video_error = ""
            if (
                metrics
                and result.profile_identity
                and settings.FAST_ANALYTICS_FETCH_VIDEOS
                and profile_video_count > 0
            ):
                try:
                    videos, videos_complete = await self._video_client.fetch_videos(
                        username,
                        result.profile_identity.get("sec_uid", ""),
                        max_videos=max(1, settings.FAST_ANALYTICS_MAX_VIDEOS_PER_ACCOUNT),
                        expected_video_count=profile_video_count,
                        known_video_urls=known_video_urls,
                    )
                except Exception as exc:
                    video_error = f"video_detail_{type(exc).__name__}: {str(exc)[:160]}"
                    logger.warning("Public video detail failed for %s: %s", username, video_error)
            if video_error and status == "SUCCESS":
                status = "PARTIAL"
                error = "Profile đã đồng bộ; chi tiết video chưa lấy được (" + video_error + ")"

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
                    if videos:
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
                    synced_at = datetime.now().isoformat(timespec="seconds")
                    for video in videos:
                        video_id = str(video.get("video_id") or "")
                        if not video_id:
                            continue
                        row = existing_by_id.get(video_id) or TikTokVideoMetricDbTable(
                            account_email=account_id, video_id=video_id
                        )
                        row.title = str(video.get("title") or "")
                        row.create_time = video.get("create_time")
                        row.view_count = int(video.get("view_count") or 0)
                        row.like_count = int(video.get("like_count") or 0)
                        row.comment_count = int(video.get("comment_count") or 0)
                        row.share_count = int(video.get("share_count") or 0)
                        row.cover_url = str(video.get("cover_url") or "")
                        row.share_url = str(video.get("share_url") or "")
                        row.synced_at = synced_at
                        session.add(row)
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
        concurrency_limit: int = 12,
        force: bool = False,
    ) -> None:
        ids = list(dict.fromkeys(str(value).strip().lower() for value in account_ids if value))
        self.total = len(ids)
        self.completed = 0
        self.updated = 0
        self.failed = 0
        self.cached = 0
        self.skipped_sold = 0
        self.reason_counts = {}
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
