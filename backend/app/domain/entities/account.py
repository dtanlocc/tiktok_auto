from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class TikTokAccount:
    id: Optional[str]
    username: str
    password: Optional[str] = None
    email: Optional[str] = None
    email_password: Optional[str] = None
    refresh_token: Optional[str] = None
    client_id: Optional[str] = None
    cookies: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "IDLE"                  # Phiên chạy (IDLE, QUEUED, RUNNING, SUCCESS, ERROR)
    health_status: str = "UNKNOWN"          # Sức khỏe vật lý (ALIVE, BANNED)
    profile_status: str = "PENDING"       # Tiến trình hồ sơ (PENDING, COMPLETED)
    current_step: str = "Chưa kích hoạt"
    proxy_id: Optional[str] = None
    
    # 3 THÔNG TIN PHÂN LÔ & QUỐC GIA MỚI THÊM
    country: str = "US"
    batch_tag: str = "DEFAULT"
    created_at: Optional[str] = None      # Chuỗi thời gian dạng '2026-07-08 15:00:00'
    note: str = ""                        # Ghi chú tự do (user tự nhập để theo dõi)

    # Hiệu suất đăng nội dung do chính engine này xác minh.
    upload_success_count: int = 0
    upload_failure_count: int = 0
    last_upload_status: str = "NEVER"      # NEVER | SUCCESS | FAILED
    last_upload_at: str = ""
    last_upload_error: str = ""

    # Snapshot thống kê TikTok. None = chưa đồng bộ, không hiển thị thành 0.
    video_count: Optional[int] = None
    follower_count: Optional[int] = None
    following_count: Optional[int] = None
    likes_count: Optional[int] = None
    tiktok_user_id: str = ""
    tiktok_sec_uid: str = ""
    display_name: str = ""
    bio: str = ""
    avatar_url: str = ""
    verified: bool = False
    private_account: bool = False
    website_url: str = ""
    total_views: Optional[int] = None
    total_video_likes: Optional[int] = None
    total_comments: Optional[int] = None
    total_shares: Optional[int] = None
    collected_video_count: int = 0
    analytics_sync_status: str = "NEVER"  # NEVER | SYNCING | SUCCESS | PARTIAL | FAILED
    analytics_sync_source: str = ""
    analytics_sync_error: str = ""
    metrics_updated_at: str = ""
