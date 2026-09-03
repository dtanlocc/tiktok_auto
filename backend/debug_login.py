"""
CONG CU DEBUG LOGIN (chay truc tiep tren terminal, KHONG qua UI).

Muc dich: kiem thu luong dang nhap 1 account voi log TUNG BUOC, trinh duyet HIEN:
  - Neu account CO cookies  -> thu dang nhap bang COOKIE truoc (nhanh).
  - Neu KHONG co cookies (hoac cookie het han/hong) -> TU DONG chuyen sang LOGIN OTP.
  - Neu dinh CAPTCHA -> dung cho extension Omocaptcha tu giai roi tiep tuc.
  - Neu account BI BAN -> in ro trang thai BANNED.

Cach chay:
    cd D:\\tiktok_auto\\backend
    python debug_login.py <username>
    python debug_login.py <username> --force-otp     # ep login OTP (bo qua cookie)
    python debug_login.py <username> --headless       # chay an (mac dinh: HIEN)

Sau khi dang nhap xong, cua so GIU MO de ban thao tac tay; nhan Enter o terminal de dong.
"""
import sys
import asyncio
import time

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from sqlmodel import Session
from app.infrastructure.database.connection import engine
from app.infrastructure.database.sqlite_repository import (
    SQLiteAccountRepository, SQLiteProxyRepository,
)
from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter
from app.infrastructure.email.email_service_factory import create_email_service
from app.use_cases.auth.login_strategies import (
    CookieThenCredentialLoginStrategy, CredentialEmailOtpLoginStrategy,
)
from app.use_cases.orchestration.task_dispatcher import _uuid_to_seed
from app.core.exceptions import AccountBannedException

T0 = time.time()
def _t() -> str:
    return f"+{time.time() - T0:6.1f}s"

async def slog(msg: str):
    try:
        print(f"[{_t()}] {msg}", flush=True)
    except Exception:
        print(f"[{_t()}] <log co ky tu dac biet>", flush=True)


async def main():
    args = [a for a in sys.argv[1:]]
    force_otp = "--force-otp" in args
    headless = "--headless" in args
    names = [a for a in args if not a.startswith("--")]
    if not names:
        print("Thieu username. Vd: python debug_login.py sties_fruind370 [--force-otp] [--headless]")
        return
    username = names[0]

    # 1) Nap account + proxy tu DB (dung entity that -> co cookies that).
    with Session(engine) as session:
        account_repo = SQLiteAccountRepository(session)
        proxy_repo = SQLiteProxyRepository(session)
        account = account_repo.get_by_username(username) if hasattr(account_repo, "get_by_username") else None
        if account is None:
            # fallback: quet tat ca tim theo username
            account = next((a for a in account_repo.get_all() if a.username == username), None)
        if account is None:
            print(f"[!] Khong tim thay account '{username}' trong DB.")
            return
        from app.core.config import settings as _settings
        proxy_config = None
        if getattr(_settings, "USE_PROXY", True) and account.proxy_id:
            proxy = proxy_repo.get_by_id(account.proxy_id)
            if proxy:
                proxy_config = {
                    "server": proxy.connection_string,
                    "username": proxy.username,
                    "password": proxy.password,
                }

    has_cookies = bool(account.cookies)
    print("=" * 68)
    print(f"  ACCOUNT : {account.username}")
    print(f"  EMAIL   : {account.email}")
    print(f"  COOKIES : {'CO' if has_cookies else 'KHONG CO'}  ({len(account.cookies or [])} cookie)")
    print(f"  PROXY   : {proxy_config['server'] if proxy_config else 'DIRECT (khong proxy)'}")
    print(f"  OTP cfg : {'DU' if (account.email and account.refresh_token and account.client_id) else 'THIEU'}")
    if force_otp:
        print(f"  MODE    : --force-otp (ep LOGIN OTP, bo qua cookie)")
    elif not has_cookies:
        print(f"  MODE    : KHONG co cookies -> se LOGIN OTP")
    else:
        print(f"  MODE    : Co cookies -> thu COOKIE truoc, hong thi OTP")
    print("=" * 68)

    # 2) Mo trinh duyet (HIEN mac dinh) + dang nhap.
    email_service = create_email_service()
    browser = InvisiblePlaywrightAdapter()
    ok = False
    try:
        await slog("Mo trinh duyet...")
        await browser.initialize(
            proxy_config=proxy_config, seed=_uuid_to_seed(account.id),
            force_visible=not headless,
        )

        # --force-otp: neu account co cookies nhung muon test OTP -> xoa tam cookies
        # trong ban entity (KHONG luu DB) de ep nhanh B.
        if force_otp:
            account.cookies = []

        strategy = CredentialEmailOtpLoginStrategy() if force_otp else CookieThenCredentialLoginStrategy()
        await slog(f"Bat dau dang nhap bang: {type(strategy).__name__}")
        ok = await strategy.login(browser, account, step_logger=slog, email_service=email_service)

    except AccountBannedException as e:
        print(f"\n[{_t()}] ==> KET QUA: TAI KHOAN BI BANNED. ({e})")
        # Ghi trang thai BANNED vao DB (giong use case that): health=BANNED, xoa cookies.
        try:
            with Session(engine) as session:
                repo = SQLiteAccountRepository(session)
                acc2 = repo.get_by_id(account.id)
                if acc2:
                    acc2.health_status = "BANNED"
                    acc2.status = "ERROR"
                    acc2.current_step = "Tài khoản bị Banned"
                    acc2.cookies = []
                    repo.save(acc2)
            print(f"[{_t()}]     Da ghi health_status=BANNED vao DB.")
        except Exception as e_save:
            print(f"[{_t()}]     (Khong ghi duoc BANNED: {e_save})")
        await _keep_open(browser, headless)
        return
    except Exception as e:
        print(f"\n[{_t()}] ==> LOI: {type(e).__name__}: {str(e)[:120]}")
        await _keep_open(browser, headless)
        return

    if ok:
        print(f"\n[{_t()}] ==> KET QUA: DANG NHAP THANH CONG ✅")
        # Luu cookie moi de lan sau dung lai (dung cookie -> khoi ton OTP).
        try:
            fresh = await browser.extract_cookies()
            if fresh:
                with Session(engine) as session:
                    repo = SQLiteAccountRepository(session)
                    acc2 = repo.get_by_id(account.id)
                    if acc2:
                        acc2.cookies = fresh
                        acc2.health_status = "ALIVE"
                        repo.save(acc2)
                print(f"[{_t()}]     Da luu {len(fresh)} cookie moi vao DB.")
        except Exception as e:
            print(f"[{_t()}]     (Khong luu duoc cookie: {e})")
    else:
        print(f"\n[{_t()}] ==> KET QUA: DANG NHAP THAT BAI ❌ (xem log tren de biet nghen o dau)")

    await _keep_open(browser, headless)


async def _keep_open(browser, headless: bool):
    """Giu cua so mo de thao tac tay; nhan Enter de dong."""
    if headless:
        await browser.close()
        return
    print("\n>>> Cua so dang MO. Thao tac tay tuy y. Nhan ENTER o day de dong...")
    try:
        await asyncio.get_event_loop().run_in_executor(None, input, "")
    except Exception:
        # Neu khong co stdin (chay tu tool) -> giu 30s roi dong.
        await asyncio.sleep(30)
    try:
        await browser.close()
    except Exception:
        pass


if __name__ == "__main__":
    asyncio.run(main())
