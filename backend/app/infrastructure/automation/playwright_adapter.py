import asyncio
import logging
import random
import re
import shutil
import os
import uuid
import tempfile
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from invisible_playwright.async_api import InvisiblePlaywright
from app.domain.ports.browser import IBrowserService
from app.core.config import settings
from app.core.exceptions import AccountBannedException

logger = logging.getLogger("PlaywrightAdapter")

# =============================================================================
# THEO DOI CUA SO (HWND) DE STREAM BANG PrintWindow
# =============================================================================
# Moi adapter khi mo browser se "nhan" 1 cua so MozillaWindowClass MOI xuat hien.
# _claimed_hwnds giu cac HWND da bi adapter khac nhan roi -> tranh 2 adapter
# nhan trung 1 cua so khi chay da luong. _hwnd_lock bao ve tap nay.
import threading as _threading
_claimed_hwnds: set = set()
_hwnd_lock = _threading.Lock()

# Serialize viec copy profile bang THREADING lock (dat BEN TRONG thread copy) de
# tranh nhieu copy song song thrash o dia. Dung threading (khong phai asyncio.Lock)
# de tranh loi loop-binding. Loop KHONG bi block: cho lock xay ra trong thread.
_copy_thread_lock = _threading.Lock()


def _copy_profile_sync(src: str, dst: str, ignore) -> None:
    """Copy profile (chay trong thread). Serialize bang _copy_thread_lock: chi 1
    copy chay 1 luc -> khong thrash dia; nhung vi chay trong thread nen event loop
    van responsive (stream/websocket/cac task khac khong bi dong bang)."""
    with _copy_thread_lock:
        shutil.copytree(src, dst, ignore=ignore)


# =============================================================================
# PROFILE SIEU NHE (Huong B): chi copy DUNG cac thu muc/file lien quan extension
# =============================================================================
# Firefox nap extension THEO PROFILE (khong co --load-extension). Nhung ban than
# extension chi ~648KB (extensions/*.xpi) + vai file registry; phan nang la
# cache/history/site-storage. Whitelist duoi day giu extension + storage RIENG
# cua no (chua ca API key da bake) + registry, bo tat ca con lai -> profile tam
# chi con ~vai MB, copy nhanh hon nhieu. Fingerprint KHONG doi (do seed, khong
# do profile). Xem thao luan Huong B.
_SEED_KEEP_ROOT = {
    "extensions",                  # chua omocaptcha@gmail.com.xpi
    "extensions.json",             # DB addon (installed/enabled/location)
    "addonStartup.json.lz4",       # cache khoi dong -> addon ACTIVE ngay, khong can restart
    "addons.json",
    "extension-preferences.json",
    "extension-settings.json",
    "extension-store",             # data.safe.bin (ExtensionSettingsStore)
    "storage",                     # se LOC ben duoi: chi giu moz-extension cua ext
    "storage.sqlite",              # QuotaManager metadata (de tim thay IDB cua ext)
    "prefs.js",                    # uuids + enable scopes + ExtensionStorageIDB.migrated + signature
    "user.js",
    "compatibility.ini",           # danh dau version -> tranh rescan addon
    "times.json",
    "xulstore.json",               # trang thai UI (nut toolbar ext)
}


def _make_seed_ignore(master_root: str):
    """Tra ve ham ignore cho shutil.copytree: CHI giu cac muc trong _SEED_KEEP_ROOT
    o goc, va trong storage/ chi giu storage RIENG cua extension (moz-extension+++...),
    bo site-storage. Cac cap sau cua extension storage duoc giu nguyen."""
    master_root = os.path.normpath(os.path.abspath(master_root))

    def _ignore(dirpath, names):
        cur = os.path.normpath(os.path.abspath(dirpath))
        rel = os.path.relpath(cur, master_root).replace("\\", "/")
        ig = set()
        if rel == ".":
            for n in names:
                if n not in _SEED_KEEP_ROOT:
                    ig.add(n)
        elif rel == "storage":
            # CHI giu "default" (bo "permanent" + "temporary" - ~18MB IDB noi bo
            # cua Firefox, khong lien quan extension).
            for n in names:
                if n != "default":
                    ig.add(n)
        elif rel == "storage/default":
            # CHI giu storage RIENG cua extension (moz-extension+++...) - noi chua
            # api_key (IDB) + settings. Bo "chrome" va site-storage (https+++...).
            for n in names:
                if not n.startswith("moz-extension"):
                    ig.add(n)
        # cac thu muc con sau do (moz-extension.../idb, /ls, extensions/, ...): giu HET
        return ig

    return _ignore


class InvisiblePlaywrightAdapter(IBrowserService):
    def __init__(self):
        self._invisible_pw: Optional[InvisiblePlaywright] = None
        self._browser = None
        self._page = None
        self._temp_profile_path: Optional[str] = None
        # HWND cua so Firefox cua rieng phien nay (dung cho PrintWindow stream).
        self._hwnd: Optional[int] = None

    async def initialize(self, proxy_config: Optional[Dict[str, Any]] = None, seed: Optional[int] = None, force_visible: bool = False) -> None:
        # force_visible=True (che do DEBUG): dua cua so ra HIEN + foreground de user
        # thao tac tay, KHONG day off-screen. Mac dinh False -> theo cau hinh (an off-screen).
        try:
            # SUA LOI BAO MAT: Bo loc lam sach Proxy (Proxy Sanitization)
            proxy_opts = None
            if proxy_config and proxy_config.get("server"):
                proxy_opts = {
                    "server": proxy_config.get("server")
                }
                if proxy_config.get("username"):
                    proxy_opts["username"] = proxy_config.get("username")
                if proxy_config.get("password"):
                    proxy_opts["password"] = proxy_config.get("password")

            # =================================================================
            # COPY PROFILE MASTER (da co san extension + config) RA BAN TAM
            # cho phien nay. Khong con parse manifest/deploy .xpi moi lan nua -
            # tat ca da duoc chuan bi san 1 lan duy nhat boi setup_master_profile.py
            # =================================================================
            master_profile_dir = getattr(
                settings, "OMOCAPTCHA_MASTER_PROFILE_DIR", os.path.abspath("./profiles/master_omocaptcha")
            )
            if not os.path.isdir(master_profile_dir):
                raise RuntimeError(
                    f"Khong tim thay profile master tai {master_profile_dir}. "
                    f"Hay chay setup_master_profile.py mot lan truoc khi dung adapter nay."
                )

            # QUAN TRONG: tao profile tam O NGOAI thu muc project (trong %TEMP%),
            # KHONG dat trong ./profiles/ nua. Ly do: uvicorn --reload quet de quy
            # thu muc project tim file .py; profile tam 147MB (hang nghin file) tao
            # roi xoa lien tuc khi mo/dong browser khien watcher sap
            # (FileNotFoundError khi 1 temp bi xoa dung luc dang quet) -> restart/kill
            # server ngay giua chung -> task "chuyen RUNNING roi ERROR ngay". Dat o
            # %TEMP% (ngoai cay project) thi watcher khong bao gio dung toi.
            temp_root = os.path.join(tempfile.gettempdir(), "tiktok_auto_profiles")
            os.makedirs(temp_root, exist_ok=True)
            self._temp_profile_path = os.path.join(temp_root, f"temp_{uuid.uuid4()}")
            # =================================================================
            # TOI UU MO BROWSER (tang toc + da luong):
            # 1. Copy trong THREAD (asyncio.to_thread) -> KHONG dong bang event
            #    loop -> cac browser khac launch song song thay vi tuan tu.
            # 2. BO QUA cac thu muc CACHE tai tao duoc (~72MB/147MB: cache2,
            #    startupCache, shader-cache, safebrowsing...) -> copy nhanh gap ~2x.
            #    VAN GIU extensions/, storage/, cookies, prefs, cert9/key4... de
            #    extension Omocaptcha + fingerprint + phien dang nhap nguyen ven.
            # =================================================================
            if getattr(settings, "OMOCAPTCHA_SLIM_PROFILE", True):
                # HUONG B: profile SIEU NHE - chi giu extension + storage rieng cua
                # no + registry (~vai MB). Nhanh hon nhieu, fingerprint khong doi.
                _ignore = _make_seed_ignore(master_profile_dir)
                _mode_desc = "SIEU NHE (chi extension + storage cua no)"
            else:
                # Cach cu: giu gan nhu ca profile, chi bo cac cache tai tao duoc.
                _ignore = shutil.ignore_patterns(
                    "cache2", "startupCache", "shader-cache", "safebrowsing",
                    "thumbnails", "OfflineCache", "jumpListCache", "GPUCache",
                    "security_state",          # HSTS/OCSP - Firefox tu tao lai
                    "gmp-widevinecdm",         # Widevine DRM - TikTok khong dung
                    "datareporting", "sessionstore-backups",
                )
                _mode_desc = "day du (bo cache)"
            # Copy trong thread (khong block loop) + serialize noi bo (tranh thrash dia).
            await asyncio.to_thread(
                _copy_profile_sync, master_profile_dir, self._temp_profile_path, _ignore
            )
            try:
                _sz = sum(f.stat().st_size for f in Path(self._temp_profile_path).rglob("*") if f.is_file())
                logger.info(f"[*] Da copy profile master [{_mode_desc}] ({_sz/1_048_576:.1f}MB) -> {self._temp_profile_path}")
            except Exception:
                logger.info(f"[*] Da copy profile master [{_mode_desc}] -> {self._temp_profile_path}")

            # =================================================================
            # PREFS BO SUNG (extra_prefs) - duoc invisible_playwright overlay
            # SAU CUNG nen override duoc moi thu (xem prefs.translate_profile_to_prefs).
            # =================================================================
            firefox_prefs = {
                # --- 5 pref nhu luc tao master, giu nguyen de dam bao tinh nhat quan ---
                "extensions.autoDisableScopes": 0,
                "extensions.enabledScopes": 15,
                "extensions.startupScanScopes": 15,
                "xpinstall.signatures.required": False,
                "xpinstall.whitelist.required": False,

                # =============================================================
                # SUA LOI "TRINH DUYET 2 DUNG TRANG KHI BI CUA SO 1 CHE":
                # Mac dinh tren Windows, Firefox bat occlusion tracking -> khi 1
                # cua so bi cua so khac che (chay da luong nhieu cua so chong len
                # nhau), no coi cua so do la "bi che khuat" va NGUNG VE/compositor
                # -> man hinh trang, treo khong chay tiep. invisible_playwright chi
                # tat pref nay o CHE DO AN (headless cloak), con che do HIEN thi
                # khong -> phai tu tat o day de moi cua so van ve/chay binh thuong
                # du bi che. (Nguon: _headless.py: "window_occlusion_tracking is
                # disabled so a hidden window keeps painting.")
                "widget.windows.window_occlusion_tracking.enabled": False,

                # Chong Firefox bop (throttle) timer cua tab/cua so o nen (khong
                # duoc focus) - giup phien chay da luong o nen khong bi cham/treo.
                "dom.timeout.enable_budget_timer_throttling": False,
                "dom.min_background_timeout_value": 0,
            }

            # Khoi chay trinh duyet tang hinh theo cau hinh goc va SEED co dinh
            self._invisible_pw = InvisiblePlaywright(
                proxy=proxy_opts,
                # CHE DO DEBUG (force_visible) luon chay HEADED de user thuc su nhin
                # thay + thao tac duoc, bat ke cau hinh BROWSER_HEADLESS.
                headless=settings.BROWSER_HEADLESS and not force_visible,
                humanize=True,
                seed=seed,
                profile_dir=self._temp_profile_path,
                extra_prefs=firefox_prefs,
            )

            # Chup danh sach cua so Firefox TRUOC khi mo -> de sau khi mo, cua so
            # MOI xuat hien chinh la cua so cua phien nay (dung cho PrintWindow stream).
            from app.infrastructure.streaming.win_capture import enum_moz_hwnds
            _before_hwnds = enum_moz_hwnds()

            # LUU Y: khi co profile_dir, __aenter__() tra ve BrowserContext (khong phai Browser).
            self._browser = await self._invisible_pw.__aenter__()

            # Phat hien cua so MOI cua chinh phien nay (chay da luong an toan nho
            # _claimed_hwnds + lock: moi adapter nhan 1 cua so rieng, khong trung).
            self._hwnd = await self._detect_own_hwnd(_before_hwnds)

            # AN cua so bang cach day ra ngoai man hinh (thay cho cloak - cloak
            # khong chay tren RDP). Cua so van render nen automation KHONG treo du
            # khong duoc focus, va PrintWindow van chup stream duoc.
            if self._hwnd and force_visible:
                # CHE DO DEBUG: dua cua so ra HIEN + foreground de user thao tac tay.
                try:
                    from app.infrastructure.streaming.win_capture import show_window_foreground
                    show_window_foreground(self._hwnd)
                    logger.info(f"[DEBUG-VISIBLE] Da dua cua so HWND={self._hwnd} ra HIEN + foreground.")
                except Exception as e_v:
                    logger.warning(f"[DEBUG-VISIBLE] Loi dua cua so ra hien: {str(e_v)}")
            elif self._hwnd and getattr(settings, "HIDE_BROWSER_OFFSCREEN", True):
                try:
                    from app.infrastructure.streaming.win_capture import move_window_offscreen
                    if move_window_offscreen(self._hwnd):
                        logger.info(f"[HIDE-OK] Moved browser window HWND={self._hwnd} OFF-SCREEN (hidden, still runs + streamable, NO click needed).")
                    else:
                        logger.warning(f"[HIDE-FAIL] Could NOT move window HWND={self._hwnd} off-screen (window stays VISIBLE).")
                except Exception as e_off:
                    logger.warning(f"[HIDE-FAIL] move_window_offscreen error: {str(e_off)}")
            elif not self._hwnd and getattr(settings, "HIDE_BROWSER_OFFSCREEN", True) and not force_visible:
                logger.warning("[HIDE-FAIL] No HWND detected for this session -> window stays VISIBLE. (Check: is another app grabbing focus during launch?)")

            # =================================================================
            # SUA LOI CHONG MO 2 TAB CUNG LUC:
            # launch_persistent_context() tu dong sinh san 1 page mac dinh
            # (thuong la about:blank). Neu goi new_page() ngay sau do se tao
            # them 1 cua so/tab thu 2 -> chinh la nguyen nhan "2 trinh duyet".
            # Tai su dung page co san neu ton tai, chi tao moi neu chua co.
            # =================================================================
            existing_pages = getattr(self._browser, "pages", None)
            if existing_pages:
                self._page = existing_pages[0]
                logger.info("[*] Tai su dung tab mac dinh cua Persistent Profile (khong tao them cua so moi).")
            else:
                self._page = await self._browser.new_page()
                logger.info("[*] Khoi tao tab moi sach.")

            logger.info(f"[+] Khoi tao Invisible Firefox thanh cong (kem extension Omocaptcha, khong dung Policy). Browser Seed: {seed} | Proxy: {proxy_opts.get('server') if proxy_opts else 'Direct NET'}")
        except Exception as e:
            logger.error(f"[-] Khong the khoi tao trinh duyet: {str(e)}")
            await self.close()
            raise e

    async def _detect_own_hwnd(self, before_hwnds: set) -> Optional[int]:
        """Sau khi mo browser, tim cua so MozillaWindowClass MOI (chua ton tai
        truoc do va chua bi adapter khac nhan) -> do la cua so cua phien nay."""
        if os.name != "nt":
            return None
        try:
            from app.infrastructure.streaming.win_capture import enum_moz_hwnds
        except Exception:
            return None
        # Poll toi da ~6s cho cua so hien ra (browser vua launch).
        for _ in range(24):
            await asyncio.sleep(0.25)
            try:
                now = enum_moz_hwnds()
            except Exception:
                continue
            with _hwnd_lock:
                candidates = now - before_hwnds - _claimed_hwnds
                if candidates:
                    hwnd = max(candidates)  # cua so moi nhat
                    _claimed_hwnds.add(hwnd)
                    logger.info(f"[HWND] Detected window HWND={hwnd} for this session.")
                    return hwnd
        logger.warning("[HWND] Could NOT detect this session's window within timeout.")
        return None

    async def navigate_to(self, url: str) -> None:
        if not self._page:
            raise RuntimeError("Trinh duyet chua duoc khoi tao.")

        try:
            # TOI UU TOC DO: cho "domcontentloaded" (DOM san sang) thay vi "load"
            # (cho tai HET anh/tracker/ads). TikTok trang rat nang, "load" gan nhu
            # khong bao gio xong nhanh -> truoc day cho toi 30s roi timeout moi
            # fallback (lang phi ~25s/lan dieu huong). DOM san sang la du de thao tac.
            logger.info(f"[*] Dang dieu huong toi {url} (cho DOM san sang)...")
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30000)

        except Exception as e:
            logger.warning(f"[!] Dieu huong DOM timeout: {str(e)}. Thu che do 'commit' (nhanh nhat)...")
            try:
                await self._page.goto(url, wait_until="commit", timeout=15000)
            except Exception as e_fallback:
                logger.error(f"[-] That bai hoan toan khi co tai trang {url}: {str(e_fallback)}")
                raise e_fallback

    async def inject_cookies(self, cookies: List[Dict[str, Any]]) -> None:
        if not self._browser:
            raise RuntimeError("Trinh duyet chua duoc khoi tao.")

        # KHU TRUNG luc inject (giu ban CUOI cung theo name+domain+path): cookies
        # luu/xuat GIU DAY DU (co the co ten trung nhu msToken bi refresh), nhung
        # add_cookies khong nen nhan 2 cookie trung name+domain+path -> loc o day.
        deduped: Dict[tuple, Dict[str, Any]] = {}
        for c in cookies or []:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            key = (c.get("name"), c.get("domain", ""), c.get("path", "/"))
            deduped[key] = c
        clean = list(deduped.values())

        contexts = getattr(self._browser, "contexts", [])
        if contexts:
            await contexts[0].add_cookies(clean)
        else:
            await self._browser.add_cookies(clean)

    async def extract_cookies(self) -> List[Dict[str, Any]]:
        if not self._browser:
            return []

        contexts = getattr(self._browser, "contexts", [])
        if contexts:
            return await contexts[0].cookies()
        else:
            return await self._browser.cookies()

    async def check_login_status(self) -> bool:
        if not self._page:
            return False

        logger.info("[*] Dang doi trang chu TikTok on dinh phien dang nhap...")

        ban_dialog_locator = self._page.locator(
            '.tux-dialog__content-title:has-text("Your account was banned"), '
            '.tux-dialog__content-title:has-text("banned"), '
            '.tux-dialog__content-title:has-text("cam"), '
            '.tux-dialog__content-message:has-text("submit an appeal"), '
            '.tux-dialog__content-message:has-text("appeal"), '
            '.tux-dialog__content-message:has-text("khang nghi")'
        )

        # Cac dau hieu ĐA LOGIN BEN (chi hien khi da dang nhap, guest KHONG co):
        # nav-profile / nav-upload / profile-icon / inbox/messages icon.
        profile_link_locator = self._page.locator(
            '[data-e2e="profile-icon"], [data-e2e="nav-profile"], [data-e2e="nav-upload"], '
            '[data-e2e="messages-icon"], [data-e2e="inbox-icon"], '
            'a[href*="/messages"], a[href*="/tiktokstudio/upload"]'
        )
        login_locator = self._page.locator('[data-e2e="nav-login-button"], button:has-text("Log in"), button:has-text("Dang nhap")')

        for i in range(20):
            try:
                if await ban_dialog_locator.count() > 0 and await ban_dialog_locator.first.is_visible():
                    dialog_title = await ban_dialog_locator.first.inner_text()
                    logger.error(f"[!] PHAT HIEN TAI KHOAN BI BANNED QUA DIALOG: '{dialog_title}'")
                    raise AccountBannedException(f"Tai khoan bi cam vinh vien: {dialog_title}")

                body_text = await self._page.locator("body").inner_text()
                body_text_lower = body_text.lower()
                banned_keywords = ["your account was banned", "submit an appeal", "tai khoan cua ban da bi cam"]
                for keyword in banned_keywords:
                    if keyword in body_text_lower:
                        logger.error(f"[!] PHAT HIEN TAI KHOAN BI BANNED QUA TU KHOA TRONG BODY: '{keyword}'")
                        raise AccountBannedException(f"Tai khoan bi cam vinh vien (Phat hien: {keyword})")

                current_url = self._page.url.lower()
                if "/foryou" in current_url:
                    logger.info(f"[+] Xac minh THANH CONG sau {i+1} giay (Phat hien trinh duyet dang dinh huong toi trang For You: {self._page.url})")
                    return True

                if await profile_link_locator.count() > 0:
                    logger.info(f"[+] Xac minh THANH CONG sau {i+1} giay (Phat hien nav-profile/upload/messages).")
                    return True

                # DAU HIEU BEN NHAT: co cin flag "isLogin":true nhung trong HTML
                # (TikTok chi nhung khi da dang nhap). Chac chan hon dua vao selector.
                try:
                    html = await self._page.content()
                    if '"isLogin":true' in html or '"isLogin": true' in html:
                        logger.info(f"[+] Xac minh THANH CONG sau {i+1} giay (isLogin=true trong HTML).")
                        return True
                except Exception:
                    pass

                if i >= 15:
                    if await login_locator.count() > 0 and await login_locator.first.is_visible():
                        logger.warning(f"[-] Xac minh THAT BAI sau {i+1} giay (Phat hien nut Log in thuc su).")
                        return False

            except AccountBannedException as e_ban:
                raise e_ban
            except Exception:
                pass

            await asyncio.sleep(1)

        logger.warning("[-] Qua thoi gian cho (Timeout) nhung khong the xac minh trang thai dang nhap.")
        return False

    async def _retype_username_input(self, uname_input, value: str) -> None:
        """Xoa sach o username va go lai 'value'."""
        await uname_input.click()
        await self._page.keyboard.press("Control+A")
        await self._page.keyboard.press("Backspace")
        await asyncio.sleep(0.4)
        await uname_input.press_sequentially(value, delay=random.randint(60, 140))
        await asyncio.sleep(0.5)

    async def _type_username_until_valid(
        self, uname_input, base_name: str, step_logger: Optional[Any] = None,
        max_len: int = 18, max_tries: int = 6,
    ) -> Tuple[bool, str]:
        """Go username va cho TikTok validate:
          - Neu co TICH XANH (svg fill #0BE09B trong o username) -> HOP LE -> tra (True, ten).
          - Neu KHONG (dau X / 'isn't available') -> them 1 chu so vao cuoi va thu lai,
            gioi han toi da max_len (18) ky tu.
        Tra ve (is_valid, final_name)."""
        tick = self._page.locator(
            '[data-e2e="edit-profile-username-input"] svg[fill="#0BE09B"], '
            '[data-e2e="edit-profile-username-input"] svg[class*="Tick" i]'
        )
        candidate = (base_name or "").strip()[:max_len]
        if not candidate:
            return False, candidate

        for attempt in range(max_tries):
            await self._retype_username_input(uname_input, candidate)
            await asyncio.sleep(2.8)  # cho TikTok kiem tra tinh kha dung
            try:
                if await tick.count() > 0:
                    logger.info(f"[Username] '{candidate}' HOP LE (tich xanh) sau {attempt+1} lan.")
                    return True, candidate
            except Exception:
                pass

            # Chua hop le -> them 1 chu so (ton trong gioi han 18 ky tu).
            if step_logger:
                await step_logger(f"Username '{candidate}' chua hop le (dau X) -> them so, thu lai...")
            digit = str(random.randint(0, 9))
            if len(candidate) + len(digit) > max_len:
                candidate = candidate[: max_len - len(digit)] + digit
            else:
                candidate = candidate + digit

        return False, candidate

    async def update_profile(
        self,
        avatar_path: Optional[str] = None,
        bio: Optional[str] = None,
        step_logger: Optional[Any] = None,
        db_username: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Tra ve (success, username_for_db). username_for_db != None nghia la
        can CAP NHAT username trong DB thanh gia tri do (Rule B: web = db + duoi)."""
        if not self._page:
            raise RuntimeError("Trinh duyet chua duoc khoi tao.")

        username_for_db: Optional[str] = None
        username_needs_confirm = False  # True neu vua doi username -> can bam Confirm sau Save
        try:
            if step_logger:
                await step_logger("Dang di chuyen toi trang ca nhan TikTok...")

            # Dung nut Profile tren thanh nav (data-e2e="nav-profile" = link
            # <a href="/@username">) -> LUON tro dung profile CUA MINH. Truoc day
            # dung a[href*="/@"] co the khop nham link user KHAC (video/goi y) ->
            # nhay sang profile nguoi khac -> khong co nut Edit -> Timeout.
            profile_btn = self._page.locator('[data-e2e="nav-profile"], a[href^="/@"]')
            await profile_btn.first.wait_for(state="visible", timeout=15000)
            await profile_btn.first.click()
            await asyncio.sleep(5)

            if step_logger:
                await step_logger("Dang mo Modal chinh sua thong tin tai khoan...")
            # Tang timeout 20s: trang profile la SPA, nut Edit render sau khi tai xong.
            edit_btn = self._page.locator('[data-e2e="edit-profile-entrance"], button:has-text("Edit profile")')
            await edit_btn.first.wait_for(state="visible", timeout=20000)
            await edit_btn.first.click()

            # Cho Modal chinh sua ho so mo len. KHONG dung class roi '.e17raual2'
            # (TikTok doi ten class lien tuc -> selector chet -> Timeout). Cho cac
            # selector BEN theo data-e2e / role=dialog / input[type=file] (chinh la
            # noi se inject avatar ben duoi), co nhieu phuong an du phong.
            avatar_wrapper = self._page.locator(
                '[data-e2e="edit-profile-avatar-edit-icon"], '
                'div[role="dialog"] input[type="file"], '
                '[data-e2e="edit-profile-avatar"], '
                'div[role="dialog"] [class*="avatar" i]'
            )
            await avatar_wrapper.first.wait_for(state="attached", timeout=15000)
            await asyncio.sleep(2)

            if avatar_path:
                try:
                    abs_origin_path = os.path.abspath(os.path.expanduser(avatar_path))
                    if not os.path.exists(abs_origin_path):
                        raise FileNotFoundError(f"Khong tim thay file: {abs_origin_path}")

                    if step_logger:
                        await step_logger("Dang nap anh avatar...")

                    import base64
                    with open(abs_origin_path, "rb") as f:
                        file_bytes = f.read()
                    file_b64 = base64.b64encode(file_bytes).decode("utf-8")
                    file_name = os.path.basename(abs_origin_path)

                    ext = file_name.lower().split('.')[-1]
                    mime_map = {
                        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                        'png': 'image/png', 'webp': 'image/webp',
                        'heic': 'image/heic', 'tiff': 'image/tiff'
                    }
                    mime_type = mime_map.get(ext, 'image/jpeg')

                    logger.info(f"[*] Inject file: {file_name} ({mime_type}), size: {len(file_bytes)} bytes")

                    injected = await self._page.evaluate(f"""
                        () => {{
                            const input = document.querySelector(
                                '[data-e2e="edit-profile-avatar-edit-icon"] input[type="file"]'
                            );
                            if (!input) return false;

                            window.__CLEAN_AVATAR_B64__ = "data:{mime_type};base64,{file_b64}";

                            if (!window.__CANVAS_OVERRIDDEN__) {{
                                window.__CANVAS_OVERRIDDEN__ = true;

                                const originalToBlob = HTMLCanvasElement.prototype.toBlob;
                                HTMLCanvasElement.prototype.toBlob = function(callback, type, quality) {{
                                    if (window.__CLEAN_AVATAR_B64__) {{
                                        fetch(window.__CLEAN_AVATAR_B64__)
                                            .then(res => res.blob())
                                            .then(blob => callback(blob))
                                            .catch(() => originalToBlob.call(this, callback, type, quality));
                                    }} else {{
                                        originalToBlob.call(this, callback, type, quality);
                                    }}
                                }};

                                const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
                                HTMLCanvasElement.prototype.toDataURL = function(type, encoderOptions) {{
                                    if (window.__CLEAN_AVATAR_B64__) {{
                                        return window.__CLEAN_AVATAR_B64__;
                                    }}
                                    return originalToDataURL.call(this, type, encoderOptions);
                                }};
                            }}

                            const byteString = atob('{file_b64}');
                            const ab = new ArrayBuffer(byteString.length);
                            const ia = new Uint8Array(ab);
                            for (let i = 0; i < byteString.length; i++) {{
                                ia[i] = byteString.charCodeAt(i);
                            }}
                            const blob = new Blob([ab], {{ type: '{mime_type}' }});
                            const file = new File([blob], '{file_name}', {{ type: '{mime_type}' }});

                            const dt = new DataTransfer();
                            dt.items.add(file);
                            input.files = dt.files;

                            input.dispatchEvent(new Event('change', {{ bubbles: true }}));
                            input.dispatchEvent(new Event('input', {{ bubbles: true }}));

                            return true;
                        }}
                    """)

                    if not injected:
                        raise Exception("Khong tim thay input[type='file'] trong DOM")

                    logger.info("[+] Da inject file avatar thanh cong.")

                    if step_logger:
                        await step_logger("Doi khung cat anh (Crop Modal) on dinh...")
                    await asyncio.sleep(4)

                    if step_logger:
                        await step_logger("Dang tim nut bam xac nhan cat anh...")

                    candidate_selectors = [
                        'button:has-text("Apply")',
                        'button:has-text("Ap dung")',
                        'div[role="dialog"] button:has-text("Apply")',
                        'div[role="dialog"] button:has-text("Ap dung")',
                        '[class*="tux-button"]:has-text("Apply")',
                        '[class*="tux-button"]:has-text("Ap dung")',
                        'button.ef1kawg9',
                        'button:has-text("Save")',
                        'button:has-text("Luu")'
                    ]

                    apply_btn = None
                    for selector in candidate_selectors:
                        try:
                            loc = self._page.locator(selector)
                            count = await loc.count()
                            for idx in range(count):
                                candidate = loc.nth(idx)
                                if await candidate.is_visible() and await candidate.is_enabled():
                                    apply_btn = candidate
                                    logger.info(f"[+] Tim thay nut xac nhan hop le: '{selector}'")
                                    break
                            if apply_btn:
                                break
                        except Exception as sel_err:
                            logger.debug(f"Bo qua selector '{selector}': {str(sel_err)}")

                    if not apply_btn:
                        logger.warning("[-] Khong dinh vi duoc Apply bang phuong phap loc dong. Dung bo gop du phong...")
                        apply_btn = self._page.locator(
                            'button.ef1kawg9, button:has-text("Apply"), button:has-text("Ap dung"), [class*="tux-button"]:has-text("Apply")'
                        ).first

                    await apply_btn.wait_for(state="visible", timeout=15000)

                    if step_logger:
                        await step_logger("Dang nhan nut Apply...")

                    clicked = False
                    for attempt in range(3):
                        try:
                            await apply_btn.click(force=True, timeout=4000)
                            logger.info(f"[+] Da nhan Apply thanh cong o lan thu {attempt+1} bang Click gia lap.")
                            clicked = True
                            break
                        except Exception as e_click:
                            logger.warning(f"[-] Click gia lap that bai o lan thu {attempt+1}: {str(e_click)}. Dang thu Dispatch Event...")
                            try:
                                await apply_btn.dispatch_event("click")
                                logger.info(f"[+] Da nhan Apply thanh cong o lan thu {attempt+1} bang Dispatch Event.")
                                clicked = True
                                break
                            except Exception as e_disp:
                                logger.warning(f"[-] Dispatch Event that bai: {str(e_disp)}. Dang thu Direct JS Click...")
                                try:
                                    await apply_btn.evaluate("node => node.click()")
                                    logger.info(f"[+] Da nhan Apply thanh cong o lan thu {attempt+1} bang Direct JS Click.")
                                    clicked = True
                                    break
                                except Exception as e_js:
                                    logger.error(f"[-] Direct JS Click that bai: {str(e_js)}")
                        await asyncio.sleep(1.5)

                    if not clicked:
                        raise Exception("Toan bo cac no luc nhan nut Apply cat anh deu that bai.")

                    await asyncio.sleep(4)
                    logger.info("[+] Cap nhat avatar vao form thanh cong.")

                except Exception as e3:
                    logger.error(f"[-] Loi Buoc 3 (Avatar): {str(e3)}")
                    if step_logger:
                        await step_logger(f"[-] Loi thay avatar: {str(e3)}")
                    raise e3

            # =================================================================
            # XU LY USERNAME (3 quy tac):
            #  A. web == db            -> khong lam gi.
            #  B. web bat dau bang db nhung co them duoi (web = db + "xxx")
            #                          -> CAP NHAT DB thanh web (tra ve username_for_db).
            #  C. web la username mac dinh cua TikTok (userXXXXX / bat dau "user")
            #                          -> DOI username tren WEB thanh db (go vao o input).
            # =================================================================
            if db_username:
                try:
                    # CHI lay dung phan tu INPUT (o username that su co placeholder
                    # "Username", KHONG co data-e2e). Truoc day them
                    # [data-e2e="edit-profile-username-input"] khop nham 1 DIV wrapper
                    # -> input_value() loi "Node is not an input".
                    uname_input = self._page.locator(
                        'div[role="dialog"] input[placeholder="Username" i], '
                        'div[role="dialog"] input[name="username"], '
                        'input[placeholder="Username" i]'
                    )
                    await uname_input.first.wait_for(state="visible", timeout=8000)
                    web_username = (await uname_input.first.input_value() or "").strip()
                    dbu = db_username.strip()
                    logger.info(f"[Username] web='{web_username}' | db='{dbu}'")

                    if not web_username:
                        pass  # khong doc duoc -> bo qua
                    elif web_username == dbu:
                        # Rule A
                        if step_logger:
                            await step_logger(f"Username web '{web_username}' == DB -> giu nguyen.")
                    elif web_username.startswith(dbu):
                        # Rule B: web = db + duoi -> DB se cap nhat thanh web
                        username_for_db = web_username
                        if step_logger:
                            await step_logger(f"Username web '{web_username}' = DB + duoi -> se cap nhat DB.")
                    elif re.match(r'^user\d', web_username, re.IGNORECASE) or web_username.lower().startswith("user"):
                        # Rule C: username mac dinh (userXXXX) -> doi web thanh db.
                        # Go db username, doi tich xanh (hop le); neu dau X (da co
                        # nguoi/khong hop le) thi them so, thu lai, toi da 18 ky tu.
                        if step_logger:
                            await step_logger(f"Username web '{web_username}' la mac dinh -> dat username '{dbu}' (co kiem tra hop le)...")
                        ok_valid, final_name = await self._type_username_until_valid(
                            uname_input.first, dbu, step_logger=step_logger
                        )
                        if ok_valid:
                            username_needs_confirm = True   # sau Save se co dialog "Set your username?"
                            username_for_db = final_name     # cap nhat DB thanh ten cuoi cung (co the da +so)
                            if step_logger:
                                await step_logger(f"[+] Username hop le: '{final_name}' (tich xanh) -> se luu.")
                        else:
                            # Khong dat duoc ten hop le -> revert ve ten web goc de Save
                            # KHONG bi chan (o username sai lam Save disabled).
                            await self._retype_username_input(uname_input.first, web_username)
                            if step_logger:
                                await step_logger(f"[!] Khong tim duoc username hop le trong 18 ky tu -> giu nguyen '{web_username}'.")
                    else:
                        if step_logger:
                            await step_logger(f"Username web '{web_username}' khong khop quy tac -> giu nguyen.")
                except Exception as e_un:
                    logger.warning(f"[Username] Bo qua xu ly username do loi: {str(e_un)}")

            if bio is not None:
                if step_logger:
                    await step_logger(f"Dang cap nhat Bio: '{bio}'...")
                # Bio: uu tien data-e2e, fallback ve textarea trong modal (bền hơn).
                bio_input = self._page.locator(
                    '[data-e2e="edit-profile-bio-input"], '
                    'div[role="dialog"] textarea, '
                    'textarea[placeholder*="bio" i]'
                )
                await bio_input.first.wait_for(state="visible", timeout=10000)
                await bio_input.first.click()
                await self._page.keyboard.press("Control+A")
                await self._page.keyboard.press("Backspace")
                await bio_input.first.press_sequentially(bio, delay=random.randint(100, 200))
                await asyncio.sleep(2)

            if step_logger:
                await step_logger("Dang nhan Save luu toan bo thay doi...")

            save_btn = self._page.locator(
                '[data-e2e="edit-profile-save"], '
                'button:has-text("Save"), '
                'button:has-text("Luu")'
            )

            await save_btn.first.wait_for(state="visible", timeout=10000)

            try:
                await save_btn.first.click(force=True, timeout=4000)
                logger.info("[+] Da nhan Save thanh cong bang click gia lap.")
            except Exception:
                await save_btn.first.dispatch_event("click")
                logger.info("[+] Da nhan Save thanh cong bang dispatch_event.")

            # =================================================================
            # XAC NHAN DOI USERNAME: neu vua doi username, TikTok hien dialog
            # "Set your username? You can change your username once every 30 days."
            # -> phai bam CONFIRM (set-username-popup-confirm) thi moi thuc su luu.
            # =================================================================
            if username_needs_confirm:
                try:
                    if step_logger:
                        await step_logger("Dang xac nhan doi username (dialog Set your username)...")
                    # Cho dialog confirm hien ra va animate xong (neu bam ngay se
                    # gap "Element is not visible").
                    await asyncio.sleep(2.0)
                    confirm_btn = self._page.locator('[data-e2e="set-username-popup-confirm"]')
                    await confirm_btn.first.wait_for(state="visible", timeout=10000)
                    await asyncio.sleep(0.6)

                    # Thu bam theo nhieu cach (giong nut Apply) cho chac.
                    cf_clicked = False
                    for cf_attempt in range(3):
                        try:
                            await confirm_btn.first.click(timeout=4000)
                            cf_clicked = True
                            break
                        except Exception:
                            try:
                                await confirm_btn.first.click(force=True, timeout=4000)
                                cf_clicked = True
                                break
                            except Exception:
                                try:
                                    await confirm_btn.first.dispatch_event("click")
                                    cf_clicked = True
                                    break
                                except Exception:
                                    await asyncio.sleep(1.0)
                    if cf_clicked:
                        logger.info("[+] Da bam Confirm xac nhan doi username.")
                        # CHO dialog confirm DONG HAN (toi 25s). Doi username la hanh
                        # dong nhay cam -> TikTok co the hien captcha sau Confirm;
                        # extension Omocaptcha can thoi gian tu giai. Neu dong browser
                        # ngay (chi sleep 3s) thi captcha chua giai xong -> KHONG luu.
                        if step_logger:
                            await step_logger("Da bam Confirm, dang cho xu ly (co the co captcha)...")
                        try:
                            await confirm_btn.first.wait_for(state="detached", timeout=25000)
                            if step_logger:
                                await step_logger("[+] Da xac nhan doi username thanh cong.")
                        except Exception:
                            # Dialog van con sau 25s -> co the captcha chua giai / bi tu choi.
                            logger.warning("[Username] Dialog confirm van chua dong sau 25s (co the captcha/rate-limit).")
                            if step_logger:
                                await step_logger("[!] Doi username co the chua hoan tat (captcha/gioi han 30 ngay).")
                    else:
                        logger.warning("[Username] Khong bam duoc nut Confirm sau nhieu lan thu.")
                    await asyncio.sleep(2)
                except Exception as e_cf:
                    logger.warning(f"[Username] Khong thay/khong bam duoc dialog Confirm: {str(e_cf)}")

            if step_logger:
                await step_logger("Da luu thay doi ho so thanh cong!")
            await asyncio.sleep(5)
            return (True, username_for_db)

        except Exception as e:
            if step_logger:
                await step_logger(f"Loi thao tac sua ho so: {str(e)}")
            logger.error(f"[-] Gap loi khi thao tac cap nhat thong tin ho so: {str(e)}")
            return (False, None)

    async def close(self) -> None:
        """Dong trinh duyet va xoa hoan toan thu muc ho so tam thoi ra khoi dia cung"""
        # Nha HWND da nhan de cua so khac co the tai su dung so hieu (khi Windows
        # cap phat lai) va tranh ro ri tap _claimed_hwnds.
        if self._hwnd is not None:
            with _hwnd_lock:
                _claimed_hwnds.discard(self._hwnd)
            self._hwnd = None
        try:
            if self._invisible_pw:
                await self._invisible_pw.__aexit__(None, None, None)
                self._invisible_pw = None
                self._browser = None
                self._page = None
                logger.info("[+] Da dong phien trinh duyet va giai phong tai nguyen.")

            if self._temp_profile_path and os.path.exists(self._temp_profile_path):
                logger.info(f"[*] Dang don dep ho so tam thoi: {self._temp_profile_path}")
                # Xoa trong THREAD -> khong dong bang event loop luc dong browser.
                _p = self._temp_profile_path
                self._temp_profile_path = None
                await asyncio.to_thread(shutil.rmtree, _p, ignore_errors=True)
        except Exception as e:
            logger.error(f"[-] Loi phat sinh khi dong trinh duyet va don dep: {str(e)}")