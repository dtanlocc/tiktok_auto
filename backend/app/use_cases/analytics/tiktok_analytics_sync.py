"""Collect first-party TikTok Studio responses and persist per-video metrics."""

import logging
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlmodel import select

from app.domain.account_rules import is_sold_account
from app.infrastructure.database.schemas import TikTokVideoMetricDbTable


logger = logging.getLogger("TikTokAnalyticsSync")

_ID_KEYS = ("video_id", "videoId", "item_id", "itemId", "aweme_id", "awemeId", "id")
_VIEW_KEYS = ("view_count", "viewCount", "play_count", "playCount")
_LIKE_KEYS = ("like_count", "likeCount", "digg_count", "diggCount")
_COMMENT_KEYS = ("comment_count", "commentCount")
_SHARE_KEYS = ("share_count", "shareCount")


def _first(mapping: Dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _integer(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))
    text = str(value).strip().replace(",", "")
    if text.isdigit():
        return int(text)
    return None


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def extract_studio_video_metrics(payloads: Iterable[Any]) -> Tuple[List[Dict[str, Any]], bool]:
    """Extract video rows without depending on one unstable internal endpoint.

    Only structured JSON objects containing a video id and at least one genuine
    engagement field are accepted. This intentionally refuses to guess metrics
    from rendered text such as "1.2K".
    """
    by_id: Dict[str, Dict[str, Any]] = {}
    saw_last_page = False
    for payload in payloads:
        payload_has_video = False
        payload_is_terminal = False
        for item in _walk(payload):
            for key in ("has_more", "hasMore"):
                if key in item and item[key] in (False, 0, "0"):
                    payload_is_terminal = True

            stats: Dict[str, Any] = {}
            for stats_key in ("stats", "statsV2", "statistics", "metrics"):
                nested = item.get(stats_key)
                if isinstance(nested, dict):
                    stats.update(nested)
            merged = {**item, **stats}
            metric_values = {
                "view_count": _integer(_first(merged, _VIEW_KEYS)),
                "like_count": _integer(_first(merged, _LIKE_KEYS)),
                "comment_count": _integer(_first(merged, _COMMENT_KEYS)),
                "share_count": _integer(_first(merged, _SHARE_KEYS)),
            }
            if all(value is None for value in metric_values.values()):
                continue
            explicit_video_id = _first(merged, _ID_KEYS[:-1])
            raw_id = explicit_video_id if explicit_video_id is not None else merged.get("id")
            if raw_id is None:
                continue
            # Generic user/profile objects can also contain `id` + `likeCount`.
            # Accept a generic id only when the same object has unmistakable
            # video evidence; explicit video/item/aweme ids remain sufficient.
            if explicit_video_id is None:
                has_video_evidence = any(metric_values[key] is not None for key in (
                    "view_count", "comment_count", "share_count"
                )) or any(key in merged for key in (
                    "video", "video_description", "videoDescription", "desc",
                    "caption", "create_time", "createTime", "duration",
                ))
                if not has_video_evidence:
                    continue
            video_id = str(raw_id).strip()
            if not video_id or len(video_id) < 6:
                continue
            payload_has_video = True
            row = {
                "video_id": video_id,
                "title": str(_first(merged, ("title", "video_description", "videoDescription", "desc", "caption")) or ""),
                "create_time": _integer(_first(merged, ("create_time", "createTime", "publish_time", "publishTime"))),
                "view_count": metric_values["view_count"] or 0,
                "like_count": metric_values["like_count"] or 0,
                "comment_count": metric_values["comment_count"] or 0,
                "share_count": metric_values["share_count"] or 0,
                "cover_url": str(_first(merged, ("cover_url", "coverUrl", "cover_image_url", "coverImageUrl")) or ""),
                "share_url": str(_first(merged, ("share_url", "shareUrl", "web_url", "webUrl")) or ""),
            }
            old = by_id.get(video_id)
            if old:
                # Duplicate responses are common. Keep the newest/highest counters
                # instead of depending on response arrival order.
                for metric in ("view_count", "like_count", "comment_count", "share_count"):
                    row[metric] = max(old[metric], row[metric])
                for field in ("title", "create_time", "cover_url", "share_url"):
                    row[field] = row[field] or old[field]
            by_id[video_id] = row
        # A generic TikTok page can emit unrelated paginated responses. Only a
        # terminal marker from a payload that also contained video metrics is
        # allowed to authorize deletion of older stored rows.
        if payload_has_video and payload_is_terminal:
            saw_last_page = True
    return sorted(by_id.values(), key=lambda row: row.get("create_time") or 0, reverse=True), saw_last_page


class TikTokAnalyticsSyncUseCase:
    def __init__(self, account_repo, browser_service, login_strategy, email_service, step_logger=None):
        self.account_repo = account_repo
        self.browser_service = browser_service
        self.login_strategy = login_strategy
        self.email_service = email_service
        self.step_logger = step_logger

    async def _log(self, message: str) -> None:
        if self.step_logger:
            await self.step_logger(message)

    async def execute(self, account_id: str) -> bool:
        account = self.account_repo.get_by_id(account_id)
        if not account:
            raise ValueError("Không tìm thấy tài khoản.")
        if is_sold_account(account):
            raise ValueError("Tài khoản thuộc mục ĐÃ BÁN; hệ thống không được phép soi hoặc đồng bộ.")

        account.analytics_sync_status = "SYNCING"
        account.analytics_sync_error = ""
        self.account_repo.save(account)
        try:
            await self._log("Đang đăng nhập để đồng bộ hiệu suất TikTok Studio...")
            logged_in = await self.login_strategy.login(
                self.browser_service,
                account,
                step_logger=self.step_logger,
                email_service=self.email_service,
            )
            if not logged_in:
                raise RuntimeError("Đăng nhập thất bại; không thể đọc TikTok Studio.")
            if not await self.browser_service.prepare_foryou_home(step_logger=self.step_logger):
                raise RuntimeError("Trang For You chưa ổn định sau đăng nhập.")

            await self._log("Đang đọc dữ liệu video trực tiếp từ TikTok Studio...")
            result = await self.browser_service.collect_studio_analytics(step_logger=self.step_logger)
            videos = list(result.get("videos") or [])
            complete = bool(result.get("complete"))
            if account.video_count is not None and len(videos) == account.video_count:
                complete = True
            if not videos and not complete:
                raise RuntimeError(result.get("error") or "TikTok Studio không trả dữ liệu video có cấu trúc.")

            synced_at = datetime.now().isoformat(timespec="seconds")
            session = self.account_repo.session
            existing = session.exec(
                select(TikTokVideoMetricDbTable)
                .where(TikTokVideoMetricDbTable.account_email == account_id)
            ).all()
            existing_by_id = {row.video_id: row for row in existing}
            received_ids = set()
            for video in videos:
                video_id = str(video["video_id"])
                received_ids.add(video_id)
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
            if complete:
                for row in existing:
                    if row.video_id not in received_ids:
                        session.delete(row)
            session.commit()

            all_rows = session.exec(
                select(TikTokVideoMetricDbTable)
                .where(TikTokVideoMetricDbTable.account_email == account_id)
            ).all()
            account = self.account_repo.get_by_id(account_id)
            account.collected_video_count = len(all_rows)
            if complete:
                account.video_count = len(all_rows)
            account.total_views = sum(row.view_count for row in all_rows)
            account.total_video_likes = sum(row.like_count for row in all_rows)
            account.total_comments = sum(row.comment_count for row in all_rows)
            account.total_shares = sum(row.share_count for row in all_rows)
            account.analytics_sync_status = "SUCCESS" if complete else "PARTIAL"
            account.analytics_sync_source = "TIKTOK_STUDIO_BROWSER"
            account.analytics_sync_error = "" if complete else str(
                result.get("partial_reason") or "Đã lưu dữ liệu tìm thấy nhưng TikTok chưa xác nhận hết phân trang."
            )[:500]
            account.metrics_updated_at = synced_at
            try:
                fresh_cookies = await self.browser_service.extract_cookies()
                if fresh_cookies:
                    account.cookies = fresh_cookies
            except Exception:
                pass
            self.account_repo.save(account)
            await self._log(
                f"Đã đồng bộ {len(all_rows)} video · {account.total_views or 0} view"
                + ("." if complete else " (dữ liệu một phần).")
            )
            return True
        except Exception as exc:
            account = self.account_repo.get_by_id(account_id)
            if account:
                account.analytics_sync_status = "FAILED"
                account.analytics_sync_error = (str(exc).strip() or type(exc).__name__)[:500]
                self.account_repo.save(account)
            raise
