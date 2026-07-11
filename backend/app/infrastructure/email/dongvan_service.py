import httpx
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple
from app.domain.ports.email import IEmailService

logger = logging.getLogger("DongVanEmailService")

# Dinh dang thoi gian tra ve trong field "date" cua API dongvanfb, vi du:
# "22:43 - 15/04/2022"  (CHI CHINH XAC TOI PHUT, KHONG CO GIAY)
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
        date_field_granularity_seconds: int,
    ) -> Tuple[bool, str]:
        """
        Kiem tra OTP tra ve co thuc su la ma MOI hay khong, dua vao field "date"
        cua response (thoi diem email duoc ghi nhan boi he thong dongvanfb).

        LUU Y QUAN TRONG: field "date" cua dongvanfb CHI CHINH XAC TOI PHUT
        (khong co giay) - vi du email den luc 21:51:59 van bi API tra ve y het
        "21:51 - .../..." nhu mot email den luc 21:51:00. Vi vay khi so sanh
        voi request_started_at (co day du giay), PHAI cong them mot khoang
        dem rieng (date_field_granularity_seconds, mac dinh 60s) NGOAI cai
        clock_skew_tolerance_seconds (von chi danh cho lech dong ho server-
        server, khac ban chat voi loi lam tron phut nay). Neu khong co dem
        nay, mot ma THAT SU MOI nhung roi vao dau phut se bi loai oan.

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
        total_lower_buffer = clock_skew_tolerance_seconds + date_field_granularity_seconds
        lower_bound = request_started_at - timedelta(seconds=total_lower_buffer)
        upper_bound = now + timedelta(seconds=clock_skew_tolerance_seconds)

        # 1. Email co truoc khi minh BAT DAU xin ma (da tru hao ca do lech dong ho
        #    LAN do lam tron phut cua chinh field date) -> chac chan la ma CU con sot lai.
        if email_dt < lower_bound:
            return False, (
                f"ma_CU: thoi_gian_email={email_dt} som_hon_nguong_cho_phep={lower_bound} "
                f"(da_gom_ca_dem_lam_tron_phut={date_field_granularity_seconds}s)"
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
        date_field_granularity_seconds: int = 60,
    ) -> Optional[str]:
        """
        Goi API dongvanfb su dung co che OAuth2 Microsoft Graph API.

        otp_requested_at: THOI DIEM THAT SU khien TikTok gui OTP. PHAI duoc
            truyen tu noi goi (login strategy). Neu khong truyen, fallback ve
            datetime.now() ngay luc goi ham nay (kem canh bao).

        freshness_window_seconds: OTP chi duoc chap nhan neu thoi gian email
            (field "date" trong response) cach hien tai KHONG QUA gia tri nay.

        clock_skew_tolerance_seconds: dung sai cho LECH DONG HO giua server
            cua ban va server dongvanfb (thuong chi vai giay/chuc giay).

        date_field_granularity_seconds: dung sai RIENG cho viec field "date"
            cua API chi chinh xac toi PHUT (khong co giay) - mac dinh 60s.
            Day la nguyen nhan gay loai nham OTP moi ban gap phai, KHONG lien
            quan gi den lech dong ho ca.
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

        # Luu lai MA GAN NHAT tung thay duoc (du la cu/khong dat freshness),
        # de dung lam phuong an du phong neu het 15 lan van khong co ma dat freshness.
        last_seen_code: Optional[str] = None
        last_seen_date_str: Optional[str] = None

        for attempt in range(max_attempts):
            try:
                logger.info(f"[*] Dang lay ma OTP TikTok lan {attempt+1}/{max_attempts} tu dongvanfb...")
                response = await self.client.post(url, json=payload)

                if response.status_code == 200:
                    data = response.json()
                    if data.get("status") is True and data.get("code"):
                        otp_code = str(data["code"]).strip()
                        email_date_str = data.get("date")

                        last_seen_code = otp_code
                        last_seen_date_str = email_date_str

                        is_fresh, reason = self._is_otp_fresh(
                            email_date_str,
                            request_started_at,
                            freshness_window_seconds,
                            clock_skew_tolerance_seconds,
                            date_field_granularity_seconds,
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

        # =====================================================================
        # HET 15 LAN VAN KHONG CO MA DAT FRESHNESS -> FALLBACK: dung lai MA CU
        # gan nhat da tung thay (neu co) de tiep tuc luong dang nhap, thay vi
        # crash hoac bo cuoc hoan toan. Log ro CANH BAO vi day la ma khong
        # dam bao con hieu luc.
        # =====================================================================
        if last_seen_code is not None:
            logger.warning(
                f"[!] Het {max_attempts} lan van khong co ma dat freshness. "
                f"Dung TAM ma gan nhat lam phuong an du phong: "
                f"code={last_seen_code}, date='{last_seen_date_str}'. "
                f"CANH BAO: ma nay co the da het han, dang nhap co the that bai."
            )
            return last_seen_code

        logger.warning(f"[-] Qua thoi gian cho (Timeout) lay OTP cho {email}. Khong tim thay bat ky ma nao.")
        return None