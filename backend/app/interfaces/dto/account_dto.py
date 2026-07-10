# File: backend/app/interfaces/dto/account_dto.py
from pydantic import BaseModel, Field
from typing import Optional

class AccountCreateIn(BaseModel):
    """Dữ liệu Web UI gửi lên khi thêm tài khoản thủ công"""
    username: str = Field(..., min_length=3, max_length=50, examples=["tiktok_user_1"])
    password: Optional[str] = Field(default=None, min_length=6)
    proxy_id: Optional[str] = Field(default=None)

class AccountOut(BaseModel):
    """Cấu trúc dữ liệu an toàn trả về Web UI với các giá trị mặc định phòng thủ chống lỗi sập API"""
    id: str
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

    class Config:
        from_attributes = True