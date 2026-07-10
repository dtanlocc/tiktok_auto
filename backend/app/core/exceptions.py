# Tệp tin mới: backend/app/core/exceptions.py

class AccountBannedException(Exception):
    """Ngoại lệ chuyên biệt ném ra khi phát hiện tài khoản TikTok bị cấm vĩnh viễn (Banned)"""
    def __init__(self, message: str = "Tài khoản của bạn đã bị cấm vĩnh viễn."):
        self.message = message
        super().__init__(self.message)