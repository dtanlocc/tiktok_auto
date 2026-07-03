from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class TikTokAccount:
    id: Optional[str]  # Sẽ map với UUID trong file của bạn (ví dụ: 9e5f94bc-e8a4-...)
    username: str
    password: Optional[str] = None
    email: Optional[str] = None
    email_password: Optional[str] = None
    device_token: Optional[str] = None  # Lưu trữ mã payload dài "M.C528_BAY..."
    cookies: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "IDLE"
    current_step: str = "Chưa kích hoạt"  # Mô tả bước hiện tại (ví dụ: "Đang nạp cookie...")
    proxy_id: Optional[str] = None