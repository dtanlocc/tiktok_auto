from pydantic import BaseModel, Field
from typing import List, Optional

SUPPORTED_PROXY_PROTOCOLS = ("http", "https", "socks5")


class ProxyCreateIn(BaseModel):
    """Dữ liệu đầu vào khi thêm Proxy mới"""
    host: str = Field(..., examples=["127.0.0.1"])
    port: int = Field(..., examples=[8080])
    username: Optional[str] = Field(default=None)
    password: Optional[str] = Field(default=None)
    protocol: str = Field(default="http", examples=["http", "socks5"])
    label: str = Field(default="")
    note: str = Field(default="")
    enabled: bool = Field(default=True)


class ProxyUpdateIn(BaseModel):
    """Chỉ các trường được gửi mới đổi. password: bỏ trống = giữ nguyên, "" = xóa."""
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    password: Optional[str] = None
    protocol: Optional[str] = None
    label: Optional[str] = None
    note: Optional[str] = None
    enabled: Optional[bool] = None


class ProxyImportTextIn(BaseModel):
    text: str


class ProxyCheckIn(BaseModel):
    """Không gửi proxy_ids = kiểm tra toàn bộ kho."""
    proxy_ids: Optional[List[str]] = None


class ProxyOut(BaseModel):
    """Dữ liệu an toàn trả về Web UI (không bao giờ trả mật khẩu)"""
    id: str
    host: str
    port: int
    username: Optional[str]
    protocol: str
    has_password: bool = False
    label: str = ""
    note: str = ""
    enabled: bool = True
    created_at: str = ""
    check_status: str = "UNCHECKED"
    check_error: str = ""
    checked_at: str = ""
    exit_ip: str = ""
    country: str = ""
    latency_ms: Optional[int] = None
    tiktok_ok: Optional[bool] = None
    cdn_ok: Optional[bool] = None
    account_count: int = 0

    class Config:
        from_attributes = True
