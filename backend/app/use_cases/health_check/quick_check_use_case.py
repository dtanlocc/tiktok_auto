# File: tiktok_auto/backend/app/use_cases/health_check/quick_check_use_case.py

"""Kiểm tra nhanh trạng thái tài khoản bằng phản hồi trực tiếp từ TikTok.

Không mở browser và không gọi dịch vụ kiểm tra bên thứ ba. Luồng kiểm tra ưu
tiên endpoint account-info nhẹ với cookie của chính account, sau đó chạy oEmbed
creator profile và trang ``/@username`` khi cần xác minh public/DIE.

Chỉ hai bằng chứng chắc chắn mới thay đổi health status:

* ``userInfo.user.uniqueId`` khớp username -> ALIVE.
* TikTok trả HTTP 404/410 hoặc statusCode 10221 -> BANNED/DIE.

WAF, CAPTCHA, 403, 429, 5xx và lỗi proxy đều là *chưa kết luận*; tuyệt đối không
được biến lỗi hạ tầng thành kết luận tài khoản đã chết.
"""
import asyncio
import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Awaitable, Callable, List, Dict, Any, Optional
from urllib.parse import quote, unquote, urlparse

import httpx
from sqlmodel import Session

from app.infrastructure.database.connection import engine
from app.infrastructure.database.sqlite_repository import SQLiteAccountRepository, SQLiteProxyRepository
from app.infrastructure.websocket.socket_manager import ws_manager
from app.domain.account_rules import is_sold_account
from app.core.tiktok_urls import ensure_tiktok_english_url

logger = logging.getLogger("QuickHealthCheck")

# Header cho trang public TikTok. Không giả thêm fingerprint JS vì đây là HTTP
# probe, không phải browser session.
_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.tiktok.com/?lang=en",
}

_TIKTOK_JSON_SCRIPT_IDS = {
    "__UNIVERSAL_DATA_FOR_REHYDRATION__",
    "SIGI_STATE",
}
_CHALLENGE_MARKERS = (
    "slardarwaf",
    "_wafchallengeid",
    "captcha_verify_action",
    "verifycenter",
    "drag the slider",
)
_NOT_FOUND_STATUS_CODES = {10221}
_ACCOUNT_INFO_URL = "https://www.tiktok.com/passport/web/account/info/?aid=1459&lang=en"


@dataclass(frozen=True)
class QuickCheckResult:
    classification: Optional[str]
    reason: str
    retryable: bool = False
    http_status: Optional[int] = None
    profile_metrics: Optional[Dict[str, int]] = None
    profile_identity: Optional[Dict[str, str]] = None
    profile_data: Optional[Dict[str, Any]] = None


def _extract_public_profile_data(user: Dict[str, Any], stats: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize only fields TikTok actually returned in public hydration."""
    bio_link = user.get("bioLink") or user.get("bio_link") or {}
    if isinstance(bio_link, dict):
        website_url = str(bio_link.get("link") or bio_link.get("url") or "")
    else:
        website_url = str(bio_link or "")
    data: Dict[str, Any] = {
        "tiktok_user_id": str(user.get("id") or ""),
        "tiktok_sec_uid": str(user.get("secUid") or ""),
        "display_name": str(user.get("nickname") or user.get("nickName") or ""),
        "bio": str(user.get("signature") or user.get("bioDescription") or ""),
        "avatar_url": str(
            user.get("avatarLarger") or user.get("avatarMedium") or user.get("avatarThumb") or ""
        ),
        "verified": bool(user.get("verified") is True or user.get("isVerified") is True),
        "private_account": bool(
            user.get("privateAccount") is True or user.get("isPrivateAccount") is True
        ),
        "website_url": website_url,
    }
    return {key: value for key, value in data.items() if value not in ("", None)}


class _TikTokJsonScriptParser(HTMLParser):
    """Chỉ thu nội dung các script JSON trạng thái chính thức của TikTok."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self._active_id: Optional[str] = None
        self._chunks: List[str] = []
        self.documents: List[Any] = []

    def handle_starttag(self, tag: str, attrs: List[tuple]) -> None:
        if tag.lower() != "script":
            return
        attributes = dict(attrs)
        script_id = attributes.get("id")
        if script_id in _TIKTOK_JSON_SCRIPT_IDS:
            self._active_id = script_id
            self._chunks = []

    def handle_data(self, data: str) -> None:
        if self._active_id is not None:
            self._chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "script" or self._active_id is None:
            return
        raw = "".join(self._chunks).strip()
        if raw:
            try:
                self.documents.append(json.loads(raw))
            except (TypeError, ValueError):
                pass
        self._active_id = None
        self._chunks = []


def _as_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_profile_metrics(stats: Dict[str, Any]) -> Dict[str, int]:
    """Chuẩn hóa các key public giữa Universal Data và SIGI_STATE."""
    key_map = {
        "video_count": ("videoCount", "video_count"),
        "follower_count": ("followerCount", "follower_count"),
        "following_count": ("followingCount", "following_count"),
        "likes_count": ("heartCount", "heart", "likesCount", "likes_count"),
        # Không phải profile nào cũng có tổng view. Chỉ ghi khi TikTok trả thật.
        "total_views": ("playCount", "viewCount", "totalViewCount", "total_views"),
    }
    result: Dict[str, int] = {}
    for output_key, source_keys in key_map.items():
        for source_key in source_keys:
            value = _as_int(stats.get(source_key))
            if value is not None:
                result[output_key] = max(0, value)
                break
    return result


def _profile_classification(user: Dict[str, Any], stats: Dict[str, Any]) -> str:
    video_count = _as_int(stats.get("videoCount")) or 0
    avatar = str(user.get("avatarLarger") or user.get("avatarMedium") or "")
    default_markers = ("1594805258216454", "default_avatar", "default-avatar")
    has_custom_avatar = bool(avatar) and not any(marker in avatar for marker in default_markers)
    return "SONG_DA_TUONG_TAC" if video_count > 0 or has_custom_avatar else "SONG_TRANG"


def _build_tiktok_cookie_header(cookies: List[Dict[str, Any]]) -> str:
    """Tạo Cookie header riêng cho một account, không trộn cookie giữa account.

    Chỉ gửi cookie thuộc tiktok.com, bỏ cookie hết hạn và giữ bản cuối nếu một
    tên cookie xuất hiện nhiều lần sau các lần refresh session.
    """
    now = time.time()
    values: Dict[str, str] = {}
    for item in cookies or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        value = str(item.get("value") or "")
        domain = str(item.get("domain") or ".tiktok.com").lower().lstrip(".")
        if not name or not (domain == "tiktok.com" or domain.endswith(".tiktok.com")):
            continue
        expires = item.get("expires")
        try:
            if expires not in (None, -1, 0) and float(expires) <= now:
                continue
        except (TypeError, ValueError):
            pass
        values[name] = value
    return "; ".join(f"{name}={value}" for name, value in values.items())


def _classify_account_info_response(
    body: str, username: str, http_status: int = 200
) -> QuickCheckResult:
    """Account-info chỉ được dùng làm bằng chứng ALIVE, không bao giờ kết luận DIE."""
    if http_status == 429:
        return QuickCheckResult(None, "account_info_rate_limited", http_status=http_status)
    if http_status in (403, 412):
        return QuickCheckResult(None, "account_info_challenge", http_status=http_status)
    if http_status >= 500:
        return QuickCheckResult(None, f"account_info_http_{http_status}", True, http_status)
    if http_status != 200 or not body:
        return QuickCheckResult(None, "account_info_unavailable", http_status=http_status)
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return QuickCheckResult(None, "account_info_invalid", http_status=http_status)
    if not isinstance(payload, dict) or str(payload.get("message") or "").casefold() != "success":
        return QuickCheckResult(None, "account_info_session_invalid", http_status=http_status)
    data = payload.get("data")
    if not isinstance(data, dict):
        return QuickCheckResult(None, "account_info_missing", http_status=http_status)
    returned_username = str(data.get("username") or "").lstrip("@").casefold()
    expected_username = username.lstrip("@").casefold()
    if returned_username != expected_username:
        return QuickCheckResult(None, "account_info_identity_mismatch", http_status=http_status)
    if not (data.get("user_id") or data.get("user_id_str") or data.get("sec_user_id")):
        return QuickCheckResult(None, "account_info_missing_identity", http_status=http_status)
    metrics = _extract_profile_metrics(data)
    return QuickCheckResult(
        "ALIVE", "tiktok_account_info", http_status=http_status,
        profile_metrics=metrics or None,
    )


def _classify_oembed_response(
    body: str, username: str, http_status: int = 200
) -> QuickCheckResult:
    """Phân loại Creator Profile oEmbed chính thức của TikTok.

    4xx không phải bằng chứng DIE: profile private/underage cũng có thể không
    được phép embed, nên các trường hợp đó phải rơi xuống profile HTML.
    """
    if http_status == 429:
        return QuickCheckResult(None, "oembed_rate_limited", http_status=http_status)
    if http_status >= 500:
        return QuickCheckResult(None, f"oembed_http_{http_status}", True, http_status)
    if http_status != 200 or not body:
        return QuickCheckResult(None, "oembed_unavailable", http_status=http_status)
    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return QuickCheckResult(None, "oembed_invalid", http_status=http_status)
    if not isinstance(payload, dict):
        return QuickCheckResult(None, "oembed_invalid", http_status=http_status)
    author_url = str(payload.get("author_url") or "")
    path = unquote(urlparse(author_url).path).rstrip("/")
    returned_username = path.rsplit("/@", 1)[-1].lstrip("@").casefold() if "/@" in path else ""
    expected_username = username.lstrip("@").casefold()
    if returned_username != expected_username:
        return QuickCheckResult(None, "oembed_identity_mismatch", http_status=http_status)
    return QuickCheckResult("ALIVE", "tiktok_oembed", http_status=http_status)


def _classify_tiktok_document(document: Any, username: str) -> QuickCheckResult:
    target = username.lstrip("@").casefold()

    # Universal hydration format: webapp.user-detail contains statusCode and
    # userInfo. Requiring both the container and exact uniqueId avoids matching
    # suggested accounts elsewhere in the page.
    detail_candidates: List[Dict[str, Any]] = []
    if isinstance(document, dict):
        scope = document.get("__DEFAULT_SCOPE__")
        if isinstance(scope, dict):
            detail = scope.get("webapp.user-detail")
            if isinstance(detail, dict):
                detail_candidates.append(detail)
        if "userInfo" in document or "statusCode" in document:
            detail_candidates.append(document)

    seen_nodes = set()
    for node in detail_candidates:
        node_id = id(node)
        if node_id in seen_nodes:
            continue
        seen_nodes.add(node_id)
        status = _as_int(node.get("statusCode"))
        if status in _NOT_FOUND_STATUS_CODES:
            return QuickCheckResult("DIE", f"tiktok_status_{status}", http_status=200)

        user_info = node.get("userInfo")
        if not isinstance(user_info, dict):
            continue
        user = user_info.get("user")
        stats = user_info.get("stats")
        if not isinstance(user, dict):
            continue
        unique_id = str(user.get("uniqueId") or "").lstrip("@").casefold()
        if unique_id != target:
            continue
        return QuickCheckResult(
            _profile_classification(user, stats if isinstance(stats, dict) else {}),
            "tiktok_user_info",
            http_status=200,
            profile_metrics=_extract_profile_metrics(stats) if isinstance(stats, dict) else None,
            profile_identity={
                "user_id": str(user.get("id") or ""),
                "sec_uid": str(user.get("secUid") or ""),
                "username": str(user.get("uniqueId") or ""),
            },
            profile_data=_extract_public_profile_data(user, stats if isinstance(stats, dict) else {}),
        )

    # Older SIGI_STATE format keeps users/stats under UserModule keyed by id or
    # username. This is still scoped to the exact requested uniqueId.
    if isinstance(document, dict):
        module = document.get("UserModule")
        if isinstance(module, dict):
            users = module.get("users") if isinstance(module.get("users"), dict) else {}
            stats_map = module.get("stats") if isinstance(module.get("stats"), dict) else {}
            for key, user in users.items():
                if not isinstance(user, dict):
                    continue
                unique_id = str(user.get("uniqueId") or "").lstrip("@").casefold()
                if unique_id != target:
                    continue
                stats = stats_map.get(key, {})
                return QuickCheckResult(
                    _profile_classification(user, stats if isinstance(stats, dict) else {}),
                    "tiktok_sigi_state",
                    http_status=200,
                    profile_metrics=_extract_profile_metrics(stats) if isinstance(stats, dict) else None,
                    profile_identity={
                        "user_id": str(user.get("id") or key or ""),
                        "sec_uid": str(user.get("secUid") or ""),
                        "username": str(user.get("uniqueId") or ""),
                    },
                    profile_data=_extract_public_profile_data(user, stats if isinstance(stats, dict) else {}),
                )

    return QuickCheckResult(None, "tiktok_state_missing")


def _classify_profile_response(html: str, username: str, http_status: int = 200) -> QuickCheckResult:
    if http_status in (404, 410):
        return QuickCheckResult("DIE", f"tiktok_http_{http_status}", http_status=http_status)
    if http_status == 429:
        return QuickCheckResult(None, "tiktok_rate_limited", http_status=http_status)
    if http_status in (403, 412):
        return QuickCheckResult(None, "tiktok_challenge", http_status=http_status)
    if http_status >= 500:
        return QuickCheckResult(None, f"tiktok_http_{http_status}", retryable=True, http_status=http_status)
    if http_status != 200:
        return QuickCheckResult(None, f"tiktok_http_{http_status}", http_status=http_status)
    if not html:
        return QuickCheckResult(None, "tiktok_empty_response", retryable=True, http_status=http_status)

    lowered = html.casefold()
    if any(marker in lowered for marker in _CHALLENGE_MARKERS):
        return QuickCheckResult(None, "tiktok_challenge", http_status=http_status)

    parser = _TikTokJsonScriptParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return QuickCheckResult(None, "tiktok_html_invalid", retryable=True, http_status=http_status)

    inconclusive = QuickCheckResult(None, "tiktok_state_missing", http_status=http_status)
    for document in parser.documents:
        result = _classify_tiktok_document(document, username)
        if result.classification is not None:
            return result
        inconclusive = result
    return inconclusive


def _classify_profile_html(html: str, username: str) -> Optional[str]:
    """Compatibility wrapper used by the profile-update verification flow."""
    return _classify_profile_response(html, username).classification


class QuickHealthCheckService:
    """Singleton doc lap - khong lien quan gi toi ConcurrentTaskDispatcher."""

    def __init__(self):
        self.is_running: bool = False
        self.total: int = 0
        self.completed: int = 0
        self.alive: int = 0
        self.dead: int = 0
        self.inconclusive: int = 0
        self.reason_counts: Dict[str, int] = {}

        # =================================================================
        # CHE DO LIEN TUC (Continuous Mode): tu dong lap lai quet CHI cho
        # danh sach account_ids duoc chi dinh luc bat (do nguoi dung tu chon,
        # KHONG con quet toan bo DB nua), hoan toan tach biet, khong dinh gi
        # toi ConcurrentTaskDispatcher/InteractionScheduler. Chi la 1 vong
        # lap asyncio don gian, tu goi lai run_batch() theo chu ky.
        # =================================================================
        self._continuous_task: Optional[asyncio.Task] = None
        self._continuous_active: bool = False
        self._continuous_account_ids: List[str] = []  # Danh sach account CO ĐỊNH do người dùng chọn lúc bật
        # Cooldown mặc định đủ ngắn để phát hiện sớm nhưng không tự spam TikTok.
        # Mỗi proxy còn có gate riêng tối đa 2 request đồng thời ở run_batch().
        self._continuous_gap_seconds: int = 30
        self._continuous_concurrency: int = 8
        self._cycle_count: int = 0
        self._last_cycle_at: Optional[str] = None

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_running": self.is_running,
            "total": self.total,
            "completed": self.completed,
            "alive": self.alive,
            "dead": self.dead,
            "inconclusive": self.inconclusive,
            "reason_counts": dict(self.reason_counts),
        }

    def get_continuous_status(self) -> Dict[str, Any]:
        return {
            "is_active": self._continuous_active,
            "account_count": len(self._continuous_account_ids),
            "gap_seconds": self._continuous_gap_seconds,
            "concurrency_limit": self._continuous_concurrency,
            "cycle_count": self._cycle_count,
            "last_cycle_at": self._last_cycle_at,
            "is_running_now": self.is_running,
        }

    def _build_proxy_url(self, session: Session, proxy_id: Optional[str]) -> Optional[str]:
        """Dung URL proxy (co auth) cho httpx tu proxy cua account.
        None = khong co proxy -> di truc tiep (IP server, de bi WAF)."""
        if not proxy_id:
            return None
        try:
            proxy = SQLiteProxyRepository(session).get_by_id(proxy_id)
        except Exception:
            proxy = None
        if not proxy or not proxy.host:
            return None
        scheme = (proxy.protocol or "socks5").strip()
        if proxy.username:
            auth = f"{quote(str(proxy.username), safe='')}:{quote(str(proxy.password or ''), safe='')}@"
        else:
            auth = ""
        return f"{scheme}://{auth}{proxy.host}:{proxy.port}"

    @staticmethod
    async def _get_response(
        client: httpx.AsyncClient,
        url: str,
        headers: Dict[str, str],
        source: str,
    ) -> tuple[Optional[httpx.Response], Optional[QuickCheckResult]]:
        url = ensure_tiktok_english_url(url)
        try:
            return await client.get(url, headers=headers), None
        except (httpx.TimeoutException, httpx.NetworkError, httpx.ProxyError) as exc:
            logger.warning(
                "TikTok %s network error: %s: %s",
                source,
                type(exc).__name__,
                str(exc)[:100],
            )
            return None, QuickCheckResult(
                None, f"{source}_network_{type(exc).__name__}", retryable=True
            )
        except Exception as exc:
            logger.warning(
                "TikTok %s unexpected error: %s: %s",
                source,
                type(exc).__name__,
                str(exc)[:100],
            )
            return None, QuickCheckResult(None, f"{source}_unexpected_{type(exc).__name__}")

    async def _fetch_account_info(
        self, client: httpx.AsyncClient, username: str, cookie_header: str
    ) -> QuickCheckResult:
        response, error = await self._get_response(
            client,
            _ACCOUNT_INFO_URL,
            {"Accept": "application/json, text/plain, */*", "Cookie": cookie_header},
            "account_info",
        )
        if error is not None:
            return error
        return _classify_account_info_response(
            response.text or "", username, response.status_code
        )

    async def _fetch_oembed(
        self, client: httpx.AsyncClient, username: str
    ) -> QuickCheckResult:
        profile_url = ensure_tiktok_english_url(
            f"https://www.tiktok.com/@{quote(username, safe='')}"
        )
        url = ensure_tiktok_english_url(
            f"https://www.tiktok.com/oembed?url={quote(profile_url, safe='')}"
        )
        response, error = await self._get_response(
            client,
            url,
            {"Accept": "application/json, text/plain, */*", "Cookie": ""},
            "oembed",
        )
        if error is not None:
            return error
        return _classify_oembed_response(response.text or "", username, response.status_code)

    async def _fetch_profile(
        self, client: httpx.AsyncClient, username: str, cookie_header: str
    ) -> QuickCheckResult:
        url = ensure_tiktok_english_url(
            f"https://www.tiktok.com/@{quote(username, safe='')}"
        )
        response, error = await self._get_response(
            client,
            url,
            {"Cookie": cookie_header},
            "profile",
        )
        if error is not None:
            return error
        return _classify_profile_response(response.text or "", username, response.status_code)

    async def _fetch_and_classify(
        self,
        client: httpx.AsyncClient,
        username: str,
        cookie_header: str,
        run_limited: Callable[
            [Callable[[], Awaitable[QuickCheckResult]]], Awaitable[QuickCheckResult]
        ],
    ) -> QuickCheckResult:
        """Fast pass bằng session, fallback public chạy song song có kiểm soát."""
        if cookie_header:
            account_result = await run_limited(
                lambda: self._fetch_account_info(client, username, cookie_header)
            )
            if account_result.classification == "ALIVE":
                return account_result

        # oEmbed rất nhẹ và thường xác nhận profile public trước. Profile HTML
        # nặng hơn nhưng là nguồn chắc chắn cho statusCode 10221. Chạy song song
        # chỉ ở số account không qua được fast pass; mỗi request vẫn phải lấy
        # semaphore proxy/global riêng trong run_limited().
        oembed_task = asyncio.create_task(
            run_limited(lambda: self._fetch_oembed(client, username))
        )
        profile_task = asyncio.create_task(
            run_limited(lambda: self._fetch_profile(client, username, cookie_header))
        )
        pending = {oembed_task, profile_task}
        results: Dict[asyncio.Task, QuickCheckResult] = {}
        try:
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    results[task] = task.result()

                alive = next(
                    (
                        result
                        for result in results.values()
                        if result.classification in {
                            "ALIVE", "SONG_DA_TUONG_TAC", "SONG_TRANG"
                        }
                    ),
                    None,
                )
                if alive is not None:
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    return alive

            profile_result = results[profile_task]
            if profile_result.classification is not None:
                return profile_result

            # Cookie cũ đôi khi nhận WAF stub 12 KB trong khi guest request cùng
            # proxy vẫn có hydration JSON đầy đủ. Một lần guest fallback giúp
            # giảm CHƯA KẾT LUẬN mà không lặp vô hạn/đẩy nhanh rate-limit.
            await asyncio.sleep(random.uniform(0.4, 0.9))
            retry_result = await run_limited(
                lambda: self._fetch_profile(client, username, "")
            )
            if retry_result.classification is not None:
                return retry_result
            return retry_result if retry_result.reason != "tiktok_state_missing" else profile_result
        finally:
            for task in (oembed_task, profile_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(oembed_task, profile_task, return_exceptions=True)

    @staticmethod
    def _inconclusive_message(result: QuickCheckResult) -> str:
        labels = {
            "tiktok_rate_limited": "TikTok giới hạn tần suất (429)",
            "tiktok_challenge": "TikTok yêu cầu xác minh/WAF",
            "tiktok_empty_response": "TikTok trả phản hồi rỗng",
            "tiktok_state_missing": "TikTok không trả dữ liệu tài khoản",
            "account_info_session_invalid": "cookie TikTok hết hạn/không hợp lệ",
            "account_info_identity_mismatch": "cookie không khớp username",
            "oembed_unavailable": "profile không hỗ trợ oEmbed",
        }
        label = labels.get(result.reason)
        if label is None and "_network_" in result.reason:
            label = "lỗi mạng hoặc proxy"
        if label is None and result.reason.startswith("tiktok_http_"):
            label = f"TikTok HTTP {result.http_status}"
        return label or result.reason

    async def _process_one_account(
        self, clients: Dict[Optional[str], httpx.AsyncClient],
        semaphore: asyncio.Semaphore,
        proxy_semaphores: Dict[str, asyncio.Semaphore],
        account_id: str,
    ) -> None:
        try:
            # Chỉ đọc snapshot ngắn; không giữ SQLite session mở trong lúc chờ mạng.
            with Session(engine) as session:
                repo = SQLiteAccountRepository(session)
                account = repo.get_by_id(account_id)
                if not account or not account.username:
                    return
                username = account.username.lstrip("@")
                proxy_url = self._build_proxy_url(session, account.proxy_id)
                cookie_header = _build_tiktok_cookie_header(account.cookies)

            proxy_key = proxy_url or "__DIRECT__"
            proxy_gate = proxy_semaphores.setdefault(proxy_key, asyncio.Semaphore(2))

            # Connection pool dùng chung theo proxy, nhưng mọi request luôn gửi
            # Cookie header tường minh của đúng account để không lẫn session.
            client = clients.get(proxy_url)
            if client is None:
                client = httpx.AsyncClient(
                    proxy=proxy_url,
                    headers=_HTTP_HEADERS,
                    timeout=httpx.Timeout(connect=5.0, read=8.0, write=5.0, pool=3.0),
                    limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
                    follow_redirects=True,
                    trust_env=False,
                )
                clients[proxy_url] = client

            async def run_limited(
                factory: Callable[[], Awaitable[QuickCheckResult]],
            ) -> QuickCheckResult:
                # Semaphore áp dụng cho TỪNG request, không giữ xuyên suốt cả
                # chuỗi fallback. Nhờ vậy một profile chậm không chặn fast pass
                # của hàng trăm account đang xếp sau trên cùng proxy.
                async with proxy_gate:
                    async with semaphore:
                        await asyncio.sleep(random.uniform(0.03, 0.12))
                        return await factory()

            result = await self._fetch_and_classify(
                client, username, cookie_header, run_limited
            )
            self.reason_counts[result.reason] = self.reason_counts.get(result.reason, 0) + 1

            # Mở session mới để cập nhật sau network await.
            with Session(engine) as session:
                repo = SQLiteAccountRepository(session)
                account = repo.get_by_id(account_id)
                if account is None:
                    return

                if result.classification == "ALIVE":
                    self.alive += 1
                    account.health_status = "ALIVE"
                    source = (
                        "session account-info"
                        if result.reason == "tiktok_account_info"
                        else "Creator oEmbed"
                    )
                    account.current_step = f"Check nhanh TikTok: SỐNG ({source})"
                elif result.classification == "SONG_DA_TUONG_TAC":
                    self.alive += 1
                    account.health_status = "ALIVE"
                    account.profile_status = "COMPLETED"
                    account.current_step = "Check nhanh TikTok: SỐNG (đã có avatar/video)"
                elif result.classification == "SONG_TRANG":
                    self.alive += 1
                    account.health_status = "ALIVE"
                    account.current_step = "Check nhanh TikTok: SỐNG (tài khoản trắng)"
                elif result.classification == "DIE":
                    self.dead += 1
                    account.health_status = "BANNED"
                    account.current_step = f"Check nhanh TikTok: DIE ({result.reason})"
                else:
                    self.inconclusive += 1
                    account.current_step = (
                        f"Check nhanh TikTok: CHƯA KẾT LUẬN "
                        f"({self._inconclusive_message(result)}) - giữ nguyên"
                    )

                if result.profile_metrics:
                    for field_name, value in result.profile_metrics.items():
                        setattr(account, field_name, value)
                if result.profile_data:
                    for field_name, value in result.profile_data.items():
                        if hasattr(account, field_name):
                            setattr(account, field_name, value)
                if result.profile_metrics or result.profile_data:
                    account.metrics_updated_at = datetime.now().isoformat(timespec="seconds")

                repo.save(account)
                event_data = {
                    "id": account.id,
                    "status": account.status,
                    "health_status": account.health_status,
                    "profile_status": account.profile_status,
                    "current_step": account.current_step,
                    "quick_check_reason": result.reason,
                    "quick_check_http_status": result.http_status,
                    "video_count": account.video_count,
                    "follower_count": account.follower_count,
                    "following_count": account.following_count,
                    "likes_count": account.likes_count,
                    "display_name": account.display_name,
                    "bio": account.bio,
                    "avatar_url": account.avatar_url,
                    "verified": account.verified,
                    "private_account": account.private_account,
                    "website_url": account.website_url,
                    "total_views": account.total_views,
                    "metrics_updated_at": account.metrics_updated_at,
                }

            await ws_manager.broadcast({
                "event": "ACCOUNT_STATUS_CHANGED",
                "data": event_data,
            })
        finally:
            self.completed += 1

    async def _continuous_loop(self) -> None:
        """Quét lặp lại danh sách được chọn với cooldown chống rate-limit."""
        while self._continuous_active:
            try:
                target_id_set = set(self._continuous_account_ids)
                with Session(engine) as session:
                    repo = SQLiteAccountRepository(session)
                    all_accounts = repo.get_all()
                    
                    # TUÂN THỦ NGUYÊN TẮC THƯƠNG MẠI:
                    # Lấy toàn bộ tài khoản nằm trong danh sách được chọn (target_id_set)
                    # mà không lọc loại trừ health_status == "ALIVE".
                    # Vòng lặp sau vẫn sẽ kiểm tra lại các tài khoản BANNED bình thường.
                    target_ids = [a.id for a in all_accounts if a.id in target_id_set and not is_sold_account(a)]

                if target_ids:
                    logger.info(
                        f"[*] [Continuous Check] Bắt đầu vòng #{self._cycle_count + 1} "
                        f"cho {len(target_ids)} account đã chọn "
                        f"({self._continuous_concurrency} luồng song song)."
                    )
                    await self.run_batch(
                        target_ids,
                        concurrency_limit=self._continuous_concurrency,
                        broadcast_finished=False,
                    )
                    self._cycle_count += 1
                    self._last_cycle_at = datetime.now().isoformat()
                    await ws_manager.broadcast({
                        "event": "QUICK_CHECK_CONTINUOUS_CYCLE_DONE",
                        "data": self.get_continuous_status()
                    })
                else:
                    logger.info("[*] [Continuous Check] Không có account nào trong danh sách được chọn, đợi ít giây rồi kiểm tra lại.")
            except Exception as e:
                logger.error(f"[-] Lỗi trong vòng lặp Check nhanh liên tục: {str(e)}")

            # Poll mỗi giây để nút dừng vẫn phản hồi nhanh trong thời gian cooldown.
            for _ in range(max(1, self._continuous_gap_seconds)):
                if not self._continuous_active:
                    break
                await asyncio.sleep(1)

        logger.info("[-] [Continuous Check] Vòng lặp liên tục đã dừng hẳn.")

    def start_continuous(self, account_ids: List[str], gap_seconds: int = 30, concurrency_limit: int = 8) -> bool:
        """Bật quét lặp lại cho đúng danh sách account được người dùng chọn."""
        if self._continuous_active:
            return False
        if not account_ids:
            return False
        self._continuous_active = True
        self._continuous_account_ids = list(account_ids)
        self._continuous_gap_seconds = max(15, min(gap_seconds, 300))
        self._continuous_concurrency = max(1, min(concurrency_limit, 32))
        self._cycle_count = 0
        self._continuous_task = asyncio.create_task(self._continuous_loop())
        logger.info(
            f"[+] [Continuous Check] Đã bật chế độ quét LIÊN TỤC cho {len(self._continuous_account_ids)} "
            f"account đã chọn ({self._continuous_concurrency} luồng song song, nghỉ {self._continuous_gap_seconds}s giữa các vòng)."
        )
        return True

    def stop_continuous(self) -> bool:
        if not self._continuous_active:
            return False
        self._continuous_active = False
        self._continuous_account_ids = []
        # KHÔNG cancel() task đang chạy dở run_batch() giữa chừng - để nó tự
        # hoàn tất đợt hiện tại cho gọn gàng, chỉ ngăn nó lặp thêm chu kỳ mới
        # (vòng poll mỗi giây phía trên sẽ tự thoát trong tối đa 1 giây).
        logger.info("[-] [Continuous Check] Đã tắt chế độ liên tục (sẽ dừng hẳn sau khi xong chu kỳ hiện tại, tối đa vài giây).")
        return True

    async def run_batch(
        self,
        account_ids: List[str],
        concurrency_limit: int = 8,
        *,
        broadcast_finished: bool = True,
    ) -> None:
        """Kiểm tra trực tiếp server TikTok, không browser và không dịch vụ ngoài."""
        if self.is_running:
            logger.warning("[!] Da co 1 dot Check nhanh dang chay, bo qua yeu cau moi.")
            return

        with Session(engine) as guard_session:
            guard_repo = SQLiteAccountRepository(guard_session)
            account_ids = [
                account_id for account_id in dict.fromkeys(account_ids)
                if not is_sold_account(guard_repo.get_by_id(account_id))
            ]
        self.is_running = True
        self.total = len(account_ids)
        self.completed = 0
        self.alive = 0
        self.dead = 0
        self.inconclusive = 0
        self.reason_counts = {}
        clients: Dict[Optional[str], httpx.AsyncClient] = {}
        proxy_semaphores: Dict[str, asyncio.Semaphore] = {}

        try:
            semaphore = asyncio.Semaphore(max(1, min(concurrency_limit, 32)))
            tasks = [
                self._process_one_account(clients, semaphore, proxy_semaphores, acc_id)
                for acc_id in account_ids
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException):
                    logger.error("Quick check worker failed: %s", result)
        except Exception as e:
            logger.error(f"[-] Lỗi tổng quát khi chạy Check nhanh hàng loạt: {str(e)}")
        finally:
            # Đóng connection pool dùng chung theo proxy.
            for cl in clients.values():
                try:
                    await cl.aclose()
                except Exception:
                    pass
            self.is_running = False
            if broadcast_finished:
                await ws_manager.broadcast({
                    "event": "QUICK_CHECK_FINISHED",
                    "data": self.get_status()
                })
            logger.info(
                "[+] Check nhanh TikTok xong: %s/%s, alive=%s, dead=%s, "
                "inconclusive=%s, reasons=%s",
                self.completed,
                self.total,
                self.alive,
                self.dead,
                self.inconclusive,
                self.reason_counts,
            )


# Singleton dung chung cho toan app (import truc tiep, khong qua app.state
# de giu dung tinh than "tach rieng hoan toan" ma ban chon)
quick_health_check_service = QuickHealthCheckService()
