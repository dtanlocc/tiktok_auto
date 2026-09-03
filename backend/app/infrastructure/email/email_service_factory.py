# File: backend/app/infrastructure/email/email_service_factory.py
"""
NOI TAO DUY NHAT dich vu doc OTP tu hom thu.

Mac dinh dung MICROSOFT GRAPH (tu goi OAuth2 + Graph API, khong qua trung gian).
Neu can quay lai API dongvanfb thi dat OTP_PROVIDER="dongvan" trong config/.env.

Ham nay cung gan san callback LUU refresh_token MOI: Microsoft XOAY refresh_token
sau moi lan doi token; khong luu lai thi mot thoi gian sau token cu het hieu luc
va account mat kha nang lay OTP.
"""
import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger("EmailServiceFactory")


def _save_rotated_refresh_token(email: str, new_refresh_token: str) -> None:
    """Ghi de refresh_token moi cho account co email nay (Microsoft xoay token)."""
    try:
        from sqlmodel import Session
        from sqlalchemy import text
        from app.infrastructure.database.connection import engine
        with Session(engine) as s:
            s.execute(
                text("UPDATE accounts SET refresh_token = :rt WHERE email = :em"),
                {"rt": new_refresh_token, "em": email},
            )
            s.commit()
        logger.info(f"[Email] Da luu refresh_token MOI cho {email} (Microsoft xoay token).")
    except Exception as e:
        logger.warning(f"[Email] Khong luu duoc refresh_token moi cho {email}: {e}")


def create_email_service() -> Any:
    """Tra ve dich vu doc OTP theo cau hinh OTP_PROVIDER ('graph' | 'dongvan')."""
    provider = str(getattr(settings, "OTP_PROVIDER", "graph")).strip().lower()
    if provider == "dongvan":
        from app.infrastructure.email.dongvan_service import DongVanEmailService
        logger.info("[Email] Dung API trung gian dongvanfb (OTP_PROVIDER=dongvan).")
        return DongVanEmailService()
    from app.infrastructure.email.ms_graph_service import MicrosoftGraphEmailService
    return MicrosoftGraphEmailService(on_token_rotated=_save_rotated_refresh_token)
