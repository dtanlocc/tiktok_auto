from pydantic import BaseModel, Field
from typing import Optional

class ProxyCreateIn(BaseModel):
    """Dữ liệu đầu vào khi thêm Proxy mới"""
    host: str = Field(..., examples=["127.0.0.1"])
    port: int = Field(..., examples=[8080])
    username: Optional[str] = Field(default=None)
    password: Optional[str] = Field(default=None)
    protocol: str = Field(default="http", examples=["http", "socks5"])

class ProxyOut(BaseModel):
    """Dữ liệu an toàn trả về Web UI"""
    id: str
    host: str
    port: int
    username: Optional[str]
    protocol: str

    class Config:
        from_attributes = True