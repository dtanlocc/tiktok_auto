from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

class AccountCreateIn(BaseModel):
    """Dữ liệu Web UI gửi lên khi thêm tài khoản thủ công"""
    username: str = Field(..., min_length=3, max_length=50, examples=["tiktok_user_1"])
    password: Optional[str] = Field(default=None, min_length=6)
    proxy_id: Optional[str] = Field(default=None)

class AccountOut(BaseModel):
    """Cấu trúc dữ liệu an toàn trả về Web UI"""
    id: str
    username: str
    status: str
    current_step: str  # Bổ sung trường này để đồng bộ tiến trình chạy lên UI
    proxy_id: Optional[str]
    has_cookies: bool

    class Config:
        from_attributes = True