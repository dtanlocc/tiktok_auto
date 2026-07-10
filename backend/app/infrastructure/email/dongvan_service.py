import httpx
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple
from app.domain.ports.email import IEmailService

logger = logging.getLogger("DongVanEmailService")

# Dinh dang thoi gian tra ve trong field "date" cua API dongvanfb, vi du:
# "22:43 - 15/04/2022"
_DONGVAN_DATE_FORMAT = "%H:%M - %d/%m/%Y"


class DongVanEmailService(IEmailService):
    def __init__(self):
        # 1. TANG TIMEOUT: Len 25 giay de tranh loi nghen duong truyen mang.
        # 2. TRUST_ENV=FALSE: Ngan httpx tu y su dung Proxy he thong (tranh loi leak proxy tu Playwright).
        self.client = httpx.AsyncClient(
            timeout=25.0,
            trust_env=False
        )

    @staticmethod
    def _is_otp_fresh(
        date_str: Optional[str],
        request_started_at: datetime,
        freshness_window_seconds: int,
        clock_skew_tolerance_seconds: int,
    ) -> Tuple[bool, str]:
        """
        Kiem tra OTP tra ve co thuc su la ma MOI hay khong, dua vao field "date"
        cua response (thoi diem email duoc ghi nhan boi he thong dongvanfb).

        Tra ve (is_fresh: bool, reason: str) de log ro nguyen nhan chap nhan/tu choi.
        """
        if not date_str:
            # Khong co timestamp de doi chieu -> khong the xac minh, chap nhan
            # nhung log canh bao ro de biet day la truong hop "mu" (khong kiem chung duoc).
            return True, "khong_co_field_date_trong_response"

        try:
            email_dt = datetime.strptime(date_str.strip(), _DONGVAN_DATE_FORMAT)
        except ValueError:
            return True, f"khong_parse_duoc_dinh_dang_date: '{date_str}'"

        now = datetime.now()
        lower_bound = request_started_at - timedelta(seconds=clock_skew_tolerance_seconds)
        upper_bound = now + timedelta(seconds=clock_skew_tolerance_seconds)

        # 1. Email co truoc khi minh BAT DAU xin ma -> chac chan la ma CU con sot lai.
        if email_dt < lower_bound:
            return False, (
                f"ma_CU: thoi_gian_email={email_dt} som_hon_thoi_diem_bat_dau_xin_ma={lower_bound}"
            )

        # 2. Email co thoi gian trong tuong lai xa hon muc dung sai lech gio cho phep
        #    -> nghi ngo sai lech dong ho giua server minh va server dongvanfb, tu choi de an toan.
        if email_dt > upper_bound:
            return False, (
                f"thoi_gian_email_bat_thuong_o_TUONG_LAI: {email_dt} > gioi_han={upper_bound}"
            )

        # 3. Email qua xa so voi HIEN TAI (vi du da hon 3 phut) -> nghi ngo day la
        #    mot ma cu ma he thong dongvanfb doc lai tu hop thu, khong phai ma vua gui.
        age_seconds = (now - email_dt).total_seconds()
        if age_seconds > freshness_window_seconds:
            return False, (
                f"ma_qua_CU_so_voi_hien_tai: da_{int(age_seconds)}s "
                f"(gioi_han_cho_phep={freshness_window_seconds}s)"
            )

        return True, f"hop_le (email_dt={email_dt}, age={int(age_seconds)}s)"

    async def fetch_last_tiktok_otp(
        self,
        email: str,
        refresh_token: str,
        client_id: str,
        otp_requested_at: Optional[datetime] = None,
        freshness_window_seconds: int = 180,
        clock_skew_tolerance_seconds: int = 30,
    ) -> Optional[str]:
        """
        Goi API dongvanfb su dung co che OAuth2 Microsoft Graph API.

        otp_requested_at: THOI DIEM THAT SU khien TikTok gui OTP (vi du: ngay
            sau khi bam nut chon kenh Email, hoac ngay khi phat hien man hinh
            nhap OTP xuat hien). PHAI duoc truyen tu noi goi (login strategy),
            vi day la noi DUY NHAT biet chinh xac hanh dong nao da kich hoat
            viec gui mail. Neu khong truyen, fallback ve datetime.now() ngay
            luc goi ham nay (kem canh bao, vi luc do co the da tre so voi
            thoi diem gui that su do cac buoc await/sleep truoc do).

        freshness_window_seconds: OTP chi duoc chap nhan neu thoi gian email
            (field "date" trong response) cach hien tai KHONG QUA gia tri nay.

        clock_skew_tolerance_seconds: dung sai cho phep neu dong ho giua server
            cua ban va server dongvanfb bi lech nhau vai chuc giay.
        """
        url = "https://tools.dongvanfb.net/api/get_code_oauth2"
        payload = {
            "email": email,
            "refresh_token": refresh_token,
            "client_id": client_id,
            "type": "tiktok"
        }
        max_attempts = 15
        delay_seconds = 4

        if otp_requested_at is not None:
            request_started_at = otp_requested_at
        else:
            request_started_at = datetime.now()
            logger.warning(
                "[!] Khong nhan duoc otp_requested_at tu noi goi -> dung datetime.now() "
                "lam moc tam thoi. Do chinh xac loc ma CU se giam vi da tre so voi "
                "thoi diem TikTok THAT SU gui mail."
            )

        for attempt in range(max_attempts):
            try:
                logger.info(f"[*] Dang lay ma OTP TikTok lan {attempt+1}/{max_attempts} tu dongvanfb...")
                response = await self.client.post(url, json=payload)

                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") is True and data.get("code"):
                        otp_code = str(data["code"]).strip()
                        email_date_str = data.get("date")

                        is_fresh, reason = self._is_otp_fresh(
                            email_date_str,
                            request_started_at,
                            freshness_window_seconds,
                            clock_skew_tolerance_seconds,
                        )

                        if is_fresh:
                            logger.info(
                                f"[+] Lay ma OTP MOI thanh cong: {otp_code} "
                                f"(date='{email_date_str}', ly_do: {reason})"
                            )
                            return otp_code
                        else:
                            # QUAN TRONG: KHONG return o day. Day la ma cu/rac,
                            # phai tiep tuc vong lap cho toi khi co ma moi thuc su
                            # duoc gui ve, tranh dang nhap bang OTP het han/sai phien.
                            logger.warning(
                                f"[!] Bo qua OTP vi nghi la MA CU (khong dung): "
                                f"code={otp_code}, date='{email_date_str}' -> {reason}"
                            )
                    else:
                        logger.debug(f"[-] dongvanfb tra ve chua co code (dang xu ly): {data.get('message', 'Processing')}")
                else:
                    logger.warning(f"[-] Loi HTTP {response.status_code} tu API dongvanfb.")

            except httpx.TimeoutException as e_timeout:
                logger.error(f"[-] Loi ket noi API dongvanfb do Qua thoi gian cho (Timeout): {type(e_timeout).__name__}")
            except httpx.NetworkError as e_net:
                logger.error(f"[-] Loi mang / DNS khong the phan giai hoac IP bi chan: {type(e_net).__name__} - {str(e_net)}")
            except Exception as e:
                logger.error(f"[-] Loi ket noi API dongvanfb khong xac dinh: {type(e).__name__} - {str(e)}")

            await asyncio.sleep(delay_seconds)

        logger.warning(f"[-] Qua thoi gian cho (Timeout) lay OTP MOI cho {email}.")
        return None