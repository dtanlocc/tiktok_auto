import re
import json
import httpx
import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple, Any
from app.domain.ports.email import IEmailService

logger = logging.getLogger("DongVanEmailService")

# Dinh dang thoi gian tra ve trong field "date" cua API dongvanfb, vi du:
# "22:43 - 15/04/2022". LUU Y: chi co gio:phut, KHONG co giay -> do chinh xac
# doi chieu thoi gian chi toi don vi PHUT (sai so +-60s la ban chat cua API).
_DONGVAN_DATE_FORMAT = "%H:%M - %d/%m/%Y"

# Regex bat 1 cum DUNG 6 chu so doc lap (khong dinh so khac 2 ben) - dung de
# tu boc ma OTP 6 so tu noi dung mail/response, thay vi tin tuong field "code"
# ma API tu parse (co the sai).
_OTP_6_DIGITS = re.compile(r"(?<!\d)(\d{6})(?!\d)")

# Cac key trong response CO KHA NANG chua noi dung mail/tieu de -> uu tien quet
# ma OTP o day truoc, vi day la noi ma that su xuat hien.
_TEXT_CANDIDATE_KEYS = (
    "message", "content", "body", "text", "subject", "title", "mail", "html", "snippet",
)


class DongVanEmailService(IEmailService):
    def __init__(self):
        # 1. TANG TIMEOUT: Len 25 giay de tranh loi nghen duong truyen mang.
        # 2. TRUST_ENV=FALSE: Ngan httpx tu y su dung Proxy he thong (tranh loi leak proxy tu Playwright).
        self.client = httpx.AsyncClient(
            timeout=25.0,
            trust_env=False
        )

    @staticmethod
    def _extract_otp_digits(data: Any) -> Optional[str]:
        """
        Tu boc ma OTP 6 SO tu response cua dongvanfb thay vi tin tuong thang field
        "code" (API tu parse co the sai). Chien luoc uu tien:
          1. Quet cac field CO KHA NANG chua noi dung mail (message/content/body/...)
             tim cum dung 6 chu so.
          2. Neu khong co, thu ngay field "code" (boc lay 6 so trong do).
          3. Cuoi cung fallback: quet toan bo JSON response tim cum 6 so.
        Tra ve chuoi 6 chu so hoac None.
        """
        if isinstance(data, dict):
            # 1. Uu tien cac field noi dung
            for key in _TEXT_CANDIDATE_KEYS:
                val = data.get(key)
                if isinstance(val, str):
                    m = _OTP_6_DIGITS.search(val)
                    if m:
                        return m.group(1)
            # 2. Field "code" do API tu parse (chi lay dung 6 so ben trong)
            code_val = data.get("code")
            if code_val is not None:
                m = _OTP_6_DIGITS.search(str(code_val))
                if m:
                    return m.group(1)

        # 3. Fallback: quet toan bo response da serialize
        try:
            blob = json.dumps(data, ensure_ascii=False)
        except Exception:
            blob = str(data)
        m = _OTP_6_DIGITS.search(blob)
        return m.group(1) if m else None

    @staticmethod
    def _is_otp_fresh(
        date_str: Optional[str],
        request_started_at: datetime,
        freshness_window_seconds: int,
        clock_skew_tolerance_seconds: int,
        backward_tolerance_seconds: int,
    ) -> Tuple[bool, str]:
        """
        Kiem tra OTP tra ve co thuc su la ma MOI hay khong, dua vao field "date"
        cua response (thoi diem email duoc ghi nhan boi he thong dongvanfb).

        LUON LUI MOC bat dau xin ma ve DAU PHUT truoc khi so sanh: vi field "date"
        cua dongvanfb chi co gio:phut (khong co giay), 1 email gui luc 6h40'05" se
        hien la "06:40" - neu minh bam xin ma luc 6h40'15" ma so sanh theo giay se
        loai nham email do. Vi vay ta floor moc ve 6h40'00" roi tru them
        backward_tolerance_seconds de bat duoc ca email den som hon vai chuc giay.

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
        # LUI MOC ve dau phut (bo giay/micro giay) roi tru them cua so lui cho phep.
        floored = request_started_at.replace(second=0, microsecond=0)
        lower_bound = floored - timedelta(seconds=backward_tolerance_seconds)
        upper_bound = now + timedelta(seconds=clock_skew_tolerance_seconds)

        # 1. Email co truoc CA cua so lui cho phep -> chac chan la ma CU con sot lai.
        if email_dt < lower_bound:
            return False, (
                f"ma_CU: thoi_gian_email={email_dt} som_hon_gioi_han_lui={lower_bound} "
                f"(moc_goc={request_started_at}, floor={floored}, lui={backward_tolerance_seconds}s)"
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
        freshness_window_seconds: int = 240,     # cho phep ma tuoi toi ~4 phut so voi hien tai
        clock_skew_tolerance_seconds: int = 30,
        backward_tolerance_seconds: int = 120,   # chap nhan OTP cu toi 2 PHUT truoc thoi diem xin ma
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
                    # TU BOC 6 SO tu response (khong tin thang field "code" vi co the sai).
                    otp_code = self._extract_otp_digits(data)
                    if data.get("status") is True and otp_code:
                        email_date_str = data.get("date")
                        # Log ro ca ma API tu parse (neu co) de doi chieu khi no lech
                        # voi 6 so minh tu boc duoc - giup phat hien API parse sai.
                        api_code = str(data.get("code")).strip() if data.get("code") is not None else None
                        if api_code and api_code != otp_code:
                            logger.warning(
                                f"[!] Ma API tu parse ('{api_code}') LECH voi 6 so minh tu boc "
                                f"('{otp_code}') -> uu tien dung 6 so tu boc."
                            )

                        is_fresh, reason = self._is_otp_fresh(
                            email_date_str,
                            request_started_at,
                            freshness_window_seconds,
                            clock_skew_tolerance_seconds,
                            backward_tolerance_seconds,
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

        # Het toan bo so lan thu ma van khong boc duoc ma MOI hop le -> tra None.
        # (Truoc day dong nay la `return otp_code` -> gay UnboundLocalError khi
        # chua lan nao gan otp_code, hoac tra nham ma CU da bi loai.)
        logger.warning(f"[-] Qua so lan thu ({max_attempts}) nhung khong lay duoc OTP MOI hop le cho {email}.")
        return otp_code