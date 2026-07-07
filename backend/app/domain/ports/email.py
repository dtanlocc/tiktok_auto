from typing import Protocol, Optional

class IEmailService(Protocol):
    """Port định nghĩa giao thức kết nối hòm thư để bóc tách mã OTP"""
    async def fetch_last_tiktok_otp(self, email: str, email_password: str) -> Optional[str]:
        """
        Kết nối hòm thư qua IMAP/POP3 và bóc tách mã xác thực (OTP) mới nhất từ TikTok.
        Sẽ được hiện thực hóa chi tiết ở tầng Infrastructure sau.
        """
        ...