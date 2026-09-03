from typing import Protocol, List, Dict, Any, Optional, Tuple

class IBrowserService(Protocol):
    """Port định nghĩa các hành vi bắt buộc của một trình duyệt tự động hóa"""
    async def initialize(self, proxy_config: Optional[Dict[str, Any]] = None, seed: Optional[int] = None) -> None:
        """Khởi động trình duyệt, cấu hình Proxy và gán hạt giống Vân tay (Fingerprint Seed)"""
        ...

    async def navigate_to(self, url: str) -> None:
        """Chuyển hướng tới một URL trang web"""
        ...

    async def inject_cookies(self, cookies: List[Dict[str, Any]]) -> None:
        """Nạp cookie vào phiên chạy"""
        ...

    async def extract_cookies(self) -> List[Dict[str, Any]]:
        """Lấy danh sách cookie hiện tại"""
        ...

    async def close(self) -> None:
        """Đóng trình duyệt và giải phóng tài nguyên"""
        ...

    # Bổ sung vào cuối class IBrowserService:
    async def update_profile(
        self,
        avatar_path: Optional[str] = None,
        bio: Optional[str] = None,
        step_logger: Optional[Any] = None,
        db_username: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Tự động đi tới trang cá nhân, đổi Avatar/Bio/Username.
        Trả về (success, username_for_db): username_for_db != None nghĩa là cần
        cập nhật username trong DB thành giá trị đó (Rule B)."""
        ...

    async def check_login_status(self) -> bool:
        """Xác minh thực tế xem phiên trình duyệt hiện tại đã đăng nhập thành công hay chưa"""
        ...

    async def prepare_foryou_home(self, step_logger: Optional[Any] = None) -> bool:
        """Open /foryou and wait for network-idle plus stable, usable feed media."""
        ...

    async def publish_media(
        self,
        image_paths: Optional[List[str]] = None,
        video_path: Optional[str] = None,
        caption: str = "",
        schedule_at: Optional[str] = None,
        step_logger: Optional[Any] = None,
        continue_session: bool = False,
    ) -> bool:
        """Publish media; continuation reuses the authenticated Studio session."""
        ...

    async def collect_studio_analytics(
        self, step_logger: Optional[Any] = None
    ) -> Dict[str, Any]:
        """Collect structured, first-party Studio video metrics."""
        ...
