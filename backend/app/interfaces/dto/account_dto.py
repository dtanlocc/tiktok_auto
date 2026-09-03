# File: backend/app/interfaces/dto/account_dto.py
from pydantic import BaseModel, Field
from typing import Optional

class AccountCreateIn(BaseModel):
    """Dữ liệu Web UI gửi lên khi thêm tài khoản thủ công"""
    username: str = Field(..., min_length=3, max_length=50, examples=["tiktok_user_1"])
    email: str = Field(..., min_length=3, examples=["account@hotmail.com"])
    password: Optional[str] = Field(default=None, min_length=6)
    proxy_id: Optional[str] = Field(default=None)

class AccountOut(BaseModel):
    """Cấu trúc dữ liệu an toàn trả về Web UI với các giá trị mặc định phòng thủ chống lỗi sập API"""
    id: str
    email: str = ""
    username: str
    status: str = "IDLE"
    health_status: str = "ALIVE"            # Mặc định phòng thủ
    profile_status: str = "PENDING"         # Mặc định phòng thủ
    current_step: str = "Chưa kích hoạt"
    proxy_id: Optional[str] = None
    has_cookies: bool = False
    
    # CÁC CỘT PHÂN LÔ MỚI CÓ GIÁ TRỊ MẶC ĐỊNH
    country: str = "US"                     # Mặc định phòng thủ
    batch_tag: str = "DEFAULT"              # Mặc định phòng thủ
    created_at: str = ""                    # Mặc định phòng thủ
    note: str = ""                          # Ghi chú tự do (mặc định rỗng)

    upload_success_count: int = 0
    upload_failure_count: int = 0
    last_upload_status: str = "NEVER"
    last_upload_at: str = ""
    last_upload_error: str = ""
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
    analytics_sync_status: str = "NEVER"
    analytics_sync_source: str = ""
    analytics_sync_error: str = ""
    metrics_updated_at: str = ""
    is_sold: bool = False

    class Config:
        from_attributes = True
