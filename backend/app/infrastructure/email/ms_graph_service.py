# File: backend/app/infrastructure/email/ms_graph_service.py
"""
LAY MA OTP TIKTOK TRUC TIEP TU HOM THU MICROSOFT (Outlook/Hotmail) BANG OAUTH2.

Thay cho API trung gian dongvanfb: ta tu goi thang Microsoft identity platform +
Microsoft Graph. Account da co san dung 3 thu can thiet: email, refresh_token,
client_id -> chinh la bo credential OAuth2 cua Microsoft (dongvanfb truoc gio cung
chi dung dung bo nay de goi ho).

Luong:
  1. refresh_token + client_id  --POST-->  login.microsoftonline.com/.../token
     => access_token (co cache theo refresh_token, tu gia han truoc 60s).
  2. access_token  --GET-->  graph.microsoft.com/v1.0/me/messages (Inbox + Junk)
     => doc subject/bodyPreview cac mail MOI NHAT.
  3. Boc cum 6 chu so + LOC theo receivedDateTime de chi nhan MA MOI (khong lay
     nham ma cu cua lan dang nhap truoc).

Uu diem so voi dongvanfb:
  - Khong phu thuoc ben thu 3 (khong lo API sap/het han/gioi han tan suat).
  - receivedDateTime cua Graph chinh xac TOI GIAY (ISO-8601 UTC), trong khi
    dongvanfb chi tra "HH:MM" -> loc ma cu chinh xac hon han.
  - Doc duoc ca thu muc SPAM (Junk) - mail OTP hay roi vao day.
"""
import re
import json
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Any, Dict, Tuple, Callable, List

import httpx

from app.domain.ports.email import IEmailService

logger = logging.getLogger("MSGraphEmailService")

# Cum DUNG 6 chu so, khong dinh vao so dai hon (vd 1234567).
_OTP_6_DIGITS = re.compile(r"(?<!\d)(\d{6})(?!\d)")

# Cac endpoint chinh chu cua Microsoft.
_TOKEN_URLS = (
    "https://login.microsoftonline.com/consumers/oauth2/v2.0/token",  # tai khoan ca nhan (hotmail/outlook)
    "https://login.microsoftonline.com/common/oauth2/v2.0/token",     # du phong (ca to chuc)
)
_GRAPH = "https://graph.microsoft.com/v1.0"

# Thu tu scope se thu. Refresh token cua cac nick nuoi thuong duoc cap cho 1 trong
# cac ho scope duoi day; xin dung ho nao thi Microsoft moi tra access_token.
_SCOPES = (
    "https://graph.microsoft.com/Mail.Read offline_access",
    "https://graph.microsoft.com/.default offline_access",
    "Mail.Read offline_access",
)

# Nhan biet mail cua TikTok (gui tu nhieu domain khac nhau tuy khu vuc).
_TIKTOK_HINTS = ("tiktok", "verification", "verify", "security code", "ma xac minh", "xác minh")


class MicrosoftGraphEmailService(IEmailService):
    """Doc OTP TikTok tu hom thu Microsoft qua Graph API (OAuth2 refresh_token).

    on_token_rotated: callback tuy chon (email, refresh_token_moi) -> Microsoft CO
    XOAY refresh_token; neu khong luu lai ban moi, token cu se het han sau mot thoi
    gian va account se mat kha nang lay OTP.
    """

    def __init__(self, on_token_rotated: Optional[Callable[[str, str], None]] = None):
        # trust_env=False: khong an theo proxy he thong (tranh dinh proxy cua Playwright).
        self.client = httpx.AsyncClient(timeout=25.0, trust_env=False)
        self._on_token_rotated = on_token_rotated
        # cache access_token: refresh_token -> (access_token, han_dung_utc)
        self._token_cache: Dict[str, Tuple[str, datetime]] = {}

    async def aclose(self) -> None:
        try:
            await self.client.aclose()
        except Exception:
            pass

    # ------------------------------------------------------------------ TOKEN
    async def _get_access_token(self, email: str, refresh_token: str, client_id: str) -> Optional[str]:
        """Doi refresh_token -> access_token (co cache). Thu lan luot cac tenant/scope
        vi moi nguon nick cap token theo mot ho scope khac nhau."""
        cached = self._token_cache.get(refresh_token)
        if cached and cached[1] > datetime.now(timezone.utc):
            return cached[0]

        last_err = None
        for token_url in _TOKEN_URLS:
            for scope in _SCOPES:
                data = {
                    "client_id": client_id,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                    "scope": scope,
                }
                try:
                    r = await self.client.post(
                        token_url, data=data,
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                    )
                except Exception as e:
                    last_err = f"{type(e).__name__}: {e}"
                    continue
                if r.status_code == 200:
                    body = r.json()
                    access = body.get("access_token")
                    if not access:
                        last_err = "response 200 nhung thieu access_token"
                        continue
                    # Gia han truoc 60s cho chac.
                    ttl = int(body.get("expires_in", 3600))
                    self._token_cache[refresh_token] = (
                        access, datetime.now(timezone.utc) + timedelta(seconds=max(60, ttl - 60))
                    )
                    # Microsoft XOAY refresh_token -> bao ra ngoai de luu lai DB.
                    new_rt = body.get("refresh_token")
                    if new_rt and new_rt != refresh_token and self._on_token_rotated:
                        try:
                            self._on_token_rotated(email, new_rt)
                        except Exception as e_cb:
                            logger.warning(f"[Graph] Luu refresh_token moi that bai: {e_cb}")
                    logger.info(f"[Graph] Lay access_token OK cho {email} (scope='{scope.split()[0]}').")
                    return access
                # 400/401 = sai scope/tenant cho nick nay -> thu to hop tiep theo.
                try:
                    err = r.json()
                    last_err = f"HTTP {r.status_code} {err.get('error')}: {str(err.get('error_description'))[:120]}"
                except Exception:
                    last_err = f"HTTP {r.status_code}"
        logger.error(f"[Graph] KHONG doi duoc refresh_token -> access_token cho {email}. Loi cuoi: {last_err}")
        return None

    # ------------------------------------------------------------------- MAIL
    async def _list_recent_messages(self, access_token: str, top: int = 15) -> List[Dict[str, Any]]:
        """Lay cac mail moi nhat o Inbox VA Junk (OTP hay roi vao spam)."""
        headers = {"Authorization": f"Bearer {access_token}"}
        params = {
            "$top": str(top),
            "$select": "subject,bodyPreview,receivedDateTime,from",
            "$orderby": "receivedDateTime desc",
        }
        out: List[Dict[str, Any]] = []
        for path in ("/me/mailFolders/inbox/messages", "/me/mailFolders/junkemail/messages"):
            try:
                r = await self.client.get(_GRAPH + path, headers=headers, params=params)
                if r.status_code == 200:
                    out.extend(r.json().get("value", []) or [])
                elif r.status_code == 401:
                    return []      # token het han -> de vong ngoai lay token moi
                else:
                    logger.debug(f"[Graph] {path} -> HTTP {r.status_code}")
            except Exception as e:
                logger.debug(f"[Graph] Loi doc {path}: {type(e).__name__}")
        return out

    @staticmethod
    def _looks_like_tiktok(msg: Dict[str, Any]) -> bool:
        blob = " ".join([
            str(msg.get("subject") or ""),
            str(msg.get("bodyPreview") or ""),
            json.dumps(msg.get("from") or {}, ensure_ascii=False),
        ]).lower()
        return any(h in blob for h in _TIKTOK_HINTS)

    @staticmethod
    def _extract_otp(msg: Dict[str, Any]) -> Optional[str]:
        """Boc cum 6 chu so: uu tien SUBJECT (TikTok de ma ngay tieu de), roi den body."""
        for field in ("subject", "bodyPreview"):
            m = _OTP_6_DIGITS.search(str(msg.get(field) or ""))
            if m:
                return m.group(1)
        return None

    @staticmethod
    def _received_utc(msg: Dict[str, Any]) -> Optional[datetime]:
        raw = msg.get("receivedDateTime")
        if not raw:
            return None
        try:
            # Graph tra ISO-8601 UTC: '2026-08-13T07:26:11Z'
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            return None

    # --------------------------------------------------------------- PUBLIC
    async def fetch_last_tiktok_otp(
        self,
        email: str,
        refresh_token: str,
        client_id: str,
        otp_requested_at: Optional[datetime] = None,
        max_attempts: int = 15,
        delay_seconds: float = 4.0,
        backward_tolerance_seconds: int = 90,
    ) -> Optional[str]:
        """Cho & lay ma OTP TikTok MOI NHAT gui SAU thoi diem otp_requested_at.

        Cung chu ky voi ban dongvanfb cu nen thay the truc tiep, khong phai sua
        cho goi. Poll toi max_attempts lan, moi lan cach delay_seconds giay.
        """
        if not (email and refresh_token and client_id):
            logger.error("[Graph] Thieu email/refresh_token/client_id -> khong lay duoc OTP.")
            return None

        # Moc so sanh (UTC). Lui them backward_tolerance de khong bo sot mail den
        # hoi som vai chuc giay so voi luc bam xin ma (lech dong ho + do tre).
        if otp_requested_at is None:
            otp_requested_at = datetime.now()
            logger.warning("[Graph] Khong nhan duoc otp_requested_at -> dung datetime.now().")
        if otp_requested_at.tzinfo is None:
            since_utc = otp_requested_at.astimezone().astimezone(timezone.utc)
        else:
            since_utc = otp_requested_at.astimezone(timezone.utc)
        since_utc -= timedelta(seconds=backward_tolerance_seconds)

        access = await self._get_access_token(email, refresh_token, client_id)
        if not access:
            return None

        for attempt in range(1, max_attempts + 1):
            msgs = await self._list_recent_messages(access)
            if not msgs:
                # Co the token vua het han -> xin lai 1 lan roi thu tiep.
                self._token_cache.pop(refresh_token, None)
                access = await self._get_access_token(email, refresh_token, client_id) or access

            best: Optional[Tuple[datetime, str]] = None
            for m in msgs:
                if not self._looks_like_tiktok(m):
                    continue
                got = self._extract_otp(m)
                if not got:
                    continue
                when = self._received_utc(m)
                if when is None:
                    continue
                if when < since_utc:
                    continue                      # MA CU (truoc luc xin) -> bo qua
                if best is None or when > best[0]:
                    best = (when, got)

            if best:
                logger.info(f"[Graph] Lay OTP MOI thanh cong: {best[1]} (mail luc {best[0].isoformat()}).")
                return best[1]

            logger.info(f"[Graph] Chua thay ma moi cho {email} (lan {attempt}/{max_attempts}) - doi mail...")
            await asyncio.sleep(delay_seconds)

        logger.warning(f"[Graph] Het {max_attempts} lan thu nhung khong co OTP MOI cho {email}.")
        return None
