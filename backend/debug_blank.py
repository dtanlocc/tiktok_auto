"""
TRINH DUYET TRANG DE TEST TAY (khong can account).

Mo 1 trinh duyet HIEN voi profile sach, gan dong extension OmoCaptcha 1.7.7 va
API key da cau hinh, nhung KHONG nap cookies / KHONG dang nhap account nao. Dung de:
  - Tu vao trang bat ky, tu bam, tu kiem tra captcha co duoc giai khong.
  - Thu nghiem thao tac tay ma khong lam anh huong account that.

Cach chay (trong D:\tiktok_auto\backend):
    python debug_blank.py                        # trang trang, chay TRUC TIEP (khong proxy)
    python debug_blank.py https://www.tiktok.com/login?lang=en
    python debug_blank.py --proxy 1.2.3.4:8080   # chay QUA proxy (khop host:port trong kho)
    python debug_blank.py --list-proxy           # xem danh sach proxy trong kho
    python debug_blank.py --headless             # chay an (hiem khi can)

Cua so se GIU MO cho toi khi ban nhan ENTER o terminal (hoac dong cua so).
"""
import sys
import asyncio
import time
import random

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter

T0 = time.time()
def _t() -> str:
    return f"+{time.time() - T0:5.1f}s"


async def main():
    args = sys.argv[1:]
    headless = "--headless" in args

    # --list-proxy: chi in kho proxy roi thoat.
    from sqlmodel import Session
    from app.infrastructure.database.connection import engine
    from app.infrastructure.database.sqlite_repository import SQLiteProxyRepository
    if "--list-proxy" in args:
        with Session(engine) as s:
            for p in SQLiteProxyRepository(s).get_all():
                print(f"  {p.connection_string}")
        return

    # --proxy <host:port>: tim trong kho proxy khop host:port.
    proxy_config = None
    proxy_desc = "TRUC TIEP (khong proxy)"
    if "--proxy" in args:
        i = args.index("--proxy")
        want = args[i + 1] if len(args) > i + 1 else ""
        args = args[:i] + args[i + 2:]
        with Session(engine) as s:
            for p in SQLiteProxyRepository(s).get_all():
                if want in (f"{p.host}:{p.port}", p.host, p.connection_string):
                    proxy_config = {"server": p.connection_string, "username": p.username, "password": p.password}
                    proxy_desc = p.connection_string
                    break
        if proxy_config is None:
            print(f"[!] Khong thay proxy '{want}' trong kho (xem: --list-proxy). Se chay TRUC TIEP.")

    urls = [a for a in args if not a.startswith("--")]
    url = urls[0] if urls else "about:blank"

    # Seed NGAU NHIEN moi lan: day la phien "nhap", khong gan voi account nao nen
    # khong can van tay co dinh. (& 0x7FFFFFFF: seed >= 2^31 lam Firefox treo.)
    seed = random.randint(1, 0x7FFFFFFF)

    print("=" * 64)
    print("  TRINH DUYET TRANG (khong account) - de test tay")
    print(f"  URL   : {url}")
    print(f"  Seed  : {seed}  (ngau nhien moi lan)")
    print(f"  Profile: sach, gan dong OmoCaptcha 1.7.7 + API key")
    print(f"  Cookies: KHONG nap (phien sach)")
    print(f"  Mang   : {proxy_desc}")
    print("=" * 64)

    browser = InvisiblePlaywrightAdapter()
    try:
        print(f"[{_t()}] Dang mo trinh duyet...", flush=True)
        await browser.initialize(proxy_config=proxy_config, seed=seed, force_visible=not headless)
        print(f"[{_t()}] Da mo xong.", flush=True)

        if url != "about:blank":
            print(f"[{_t()}] Dieu huong toi {url} ...", flush=True)
            await browser.navigate_to(url)
            print(f"[{_t()}] Da toi noi.", flush=True)

        # Theo doi captcha o nen: bao ngay khi co captcha & khi duoc giai xong.
        async def watch():
            prev = False
            t_seen = None
            while True:
                await asyncio.sleep(1.5)
                try:
                    cur = await browser.is_captcha_present()
                except Exception:
                    continue
                if cur and not prev:
                    t_seen = time.time()
                    print(f"[{_t()}] >>> CO CAPTCHA - dang cho Omocaptcha giai...", flush=True)
                elif prev and not cur:
                    held = f"{time.time()-t_seen:.0f}s" if t_seen else "?"
                    print(f"[{_t()}] >>> CAPTCHA DA MAT (giai xong sau ~{held})", flush=True)
                prev = cur
        watcher = asyncio.create_task(watch())

        print("\n>>> Cua so dang MO. Thao tac tay tuy y. Nhan ENTER o day de dong...")
        try:
            await asyncio.get_event_loop().run_in_executor(None, input, "")
        except Exception:
            await asyncio.sleep(600)      # khong co stdin -> giu 10 phut
        watcher.cancel()
    except Exception as e:
        print(f"[{_t()}] LOI: {type(e).__name__}: {str(e)[:140]}")
    finally:
        try:
            await browser.close()
        except Exception:
            pass
        print(f"[{_t()}] Da dong trinh duyet.")


if __name__ == "__main__":
    asyncio.run(main())
