from dataclasses import dataclass
from typing import Optional

@dataclass
class Proxy:
    id: Optional[str]
    host: str
    port: int
    username: Optional[str] = None
    password: Optional[str] = None
    protocol: str = "http"  # http, socks5

    @property
    def connection_string(self) -> str:
        """Trả về chuỗi kết nối chuẩn cho Playwright/Curl_cffi"""
        return f"{self.protocol}://{self.host}:{self.port}"