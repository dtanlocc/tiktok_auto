from typing import Protocol, List, Optional
from app.domain.entities.account import TikTokAccount
from app.domain.entities.proxy import Proxy

class IProxyRepository(Protocol):
    """Giao thức lưu trữ và truy vấn Proxy"""
    def get_by_id(self, proxy_id: str) -> Optional[Proxy]:
        ...
    def save(self, proxy: Proxy) -> Proxy:
        ...
    def get_all(self) -> List[Proxy]:
        ...


class IAccountRepository(Protocol):
    """Giao thức lưu trữ và truy vấn TikTok Account"""
    def get_by_id(self, account_id: str) -> Optional[TikTokAccount]:
        ...
    def get_all(self) -> List[TikTokAccount]:
        ...
    def save(self, account: TikTokAccount) -> TikTokAccount:
        ...
    def update_status(self, account_id: str, status: str) -> None:
        ...