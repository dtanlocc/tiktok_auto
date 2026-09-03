from sqlalchemy import Column, String
from sqlmodel import SQLModel, Field
from typing import Optional

class ProxyDbTable(SQLModel, table=True):
    __tablename__ = "proxies"
    id: Optional[str] = Field(default=None, primary_key=True)
    host: str = Field(nullable=False)
    port: int = Field(nullable=False)
    username: Optional[str] = Field(default=None)
    password: Optional[str] = Field(default=None)
    protocol: str = Field(default="http")

class AccountDbTable(SQLModel, table=True):
    __tablename__ = "accounts"

    # Hotmail is the canonical account key. NOCASE prevents duplicates that only
    # differ by letter casing while API `id` remains an alias for compatibility.
    email: str = Field(sa_column=Column(String(collation="NOCASE"), primary_key=True, nullable=False))
    username: str = Field(index=True, unique=True, nullable=False)
    password: Optional[str] = Field(default=None)
    email_password: Optional[str] = Field(default=None)
    refresh_token: Optional[str] = Field(default=None)
    client_id: Optional[str] = Field(default=None)
    cookies_json: str = Field(default="[]")
    
    # 3 CỘT TRẠNG THÁI PHÂN RÃ CHUẨN THƯƠNG MẠI
    status: str = Field(default="IDLE")                  # Phiên chạy
    health_status: str = Field(default="UNKNOWN")          # Sức khỏe (ALIVE, BANNED)
    profile_status: str = Field(default="PENDING")       # Tiến trình hồ sơ (PENDING, COMPLETED)
    
    current_step: str = Field(default="Chưa kích hoạt")
    proxy_id: Optional[str] = Field(default=None, foreign_key="proxies.id", nullable=True)
    
    # 3 CỘT MỚI DÀNH CHO QUẢN TRỊ THƯƠNG MẠI
    country: str = Field(default="US", index=True)
    batch_tag: str = Field(default="DEFAULT", index=True)
    created_at: str = Field(default="")
    # Ghi chu tu do cho tung account (user tu nhap tren UI de theo doi).
    note: str = Field(default="")

    upload_success_count: int = Field(default=0)
    upload_failure_count: int = Field(default=0)
    last_upload_status: str = Field(default="NEVER")
    last_upload_at: str = Field(default="")
    last_upload_error: str = Field(default="")

    video_count: Optional[int] = Field(default=None)
    follower_count: Optional[int] = Field(default=None)
    following_count: Optional[int] = Field(default=None)
    likes_count: Optional[int] = Field(default=None)
    tiktok_user_id: str = Field(default="")
    tiktok_sec_uid: str = Field(default="")
    display_name: str = Field(default="")
    bio: str = Field(default="")
    avatar_url: str = Field(default="")
    verified: bool = Field(default=False)
    private_account: bool = Field(default=False)
    website_url: str = Field(default="")
    total_views: Optional[int] = Field(default=None)
    total_video_likes: Optional[int] = Field(default=None)
    total_comments: Optional[int] = Field(default=None)
    total_shares: Optional[int] = Field(default=None)
    collected_video_count: int = Field(default=0)
    analytics_sync_status: str = Field(default="NEVER")
    analytics_sync_source: str = Field(default="")
    analytics_sync_error: str = Field(default="")
    metrics_updated_at: str = Field(default="")


class TikTokVideoMetricDbTable(SQLModel, table=True):
    __tablename__ = "tiktok_video_metrics"

    account_email: str = Field(primary_key=True, index=True)
    video_id: str = Field(primary_key=True)
    title: str = Field(default="")
    create_time: Optional[int] = Field(default=None)
    view_count: int = Field(default=0)
    like_count: int = Field(default=0)
    comment_count: int = Field(default=0)
    share_count: int = Field(default=0)
    cover_url: str = Field(default="")
    share_url: str = Field(default="")
    synced_at: str = Field(default="")
