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