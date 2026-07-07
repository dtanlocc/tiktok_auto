import httpx
import asyncio
import logging
from typing import Optional
from app.domain.ports.email import IEmailService

logger = logging.getLogger("DongVanEmailService")

class DongVanEmailService(IEmailService):
    def __init__(self):
        # Thiết lập httpx async client kết nối API
        self.client = httpx.AsyncClient(timeout=10.0)

    async def fetch_last_tiktok_otp(self, email: str, refresh_token: str, client_id: str) -> Optional[str]:
        """Gọi API dongvanfb sử dụng cấu thức OAuth2 Microsoft Graph API"""
        url = "https://tools.dongvanfb.net/api/get_code_oauth2"
        payload = {
            "email": email,
            "refresh_token": refresh_token,
            "client_id": client_id,
            "type": "tiktok" # Chỉ bóc tách thư của tiktok
        }

        # Vì mail TikTok có độ trễ truyền tải, chúng ta quét liên tục (Polling) 
        # tối đa 15 lần (mỗi lần cách nhau 4 giây, tổng cộng chờ tối đa ~60 giây)
        max_attempts = 15
        delay_seconds = 4

        for attempt in range(max_attempts):
            try:
                logger.info(f"[*] Đang lấy mã OTP TikTok lần {attempt+1}/{max_attempts} từ dongvanfb...")
                response = await self.client.post(url, json=payload)
                
                if response.status_code == 200:
                    data = response.json()
                    # Response thành công: {"status": true, "code": "123456", ...}
                    if data.get("status") is True and data.get("code"):
                        otp_code = str(data["code"]).strip()
                        logger.info(f"[+] Lấy mã OTP thành công: {otp_code}")
                        return otp_code
                    else:
                        logger.debug(f"[-] dongvanfb trả về chưa có code (đang xử lý): {data.get('message', 'Processing')}")
                else:
                    logger.warning(f"[-] Lỗi HTTP {response.status_code} từ API dongvanfb.")
            except Exception as e:
                logger.error(f"[-] Lỗi kết nối API dongvanfb: {str(e)}")

            await asyncio.sleep(delay_seconds)

        logger.warning(f"[-] Quá thời gian chờ (Timeout) lấy OTP cho {email}.")
        return None