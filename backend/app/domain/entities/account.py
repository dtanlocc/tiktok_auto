from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class TikTokAccount:
    id: Optional[str]
    username: str
    password: Optional[str] = None
    email: Optional[str] = None
    email_password: Optional[str] = None
    refresh_token: Optional[str] = None  # OAuth2 Refresh Token (Cột 5 trong file)
    client_id: Optional[str] = None      # Azure AD Client ID (Cột 6 trong file)
    cookies: List[Dict[str, Any]] = field(default_factory=list)
    status: str = "IDLE"
    current_step: str = "Chưa kích hoạt"
    proxy_id: Optional[str] = None