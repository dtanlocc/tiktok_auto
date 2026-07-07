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

    id: Optional[str] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True, nullable=False)
    password: Optional[str] = Field(default=None)
    email: Optional[str] = Field(default=None)
    email_password: Optional[str] = Field(default=None)
    refresh_token: Optional[str] = Field(default=None)  # Thêm cột
    client_id: Optional[str] = Field(default=None)      # Thêm cột
    cookies_json: str = Field(default="[]")
    status: str = Field(default="IDLE")
    current_step: str = Field(default="Chưa kích hoạt")
    proxy_id: Optional[str] = Field(default=None, foreign_key="proxies.id", nullable=True)