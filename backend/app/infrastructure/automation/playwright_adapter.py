import asyncio
import logging
import random
import time
import re
import shutil
import os
import uuid
import tempfile
import json
import zipfile
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

# =============================================================================
# CHE DO AN VA STREAM CHI XEM
# -----------------------------------------------------------------------------
# Task nen dung true-headless cua invisible_playwright. Dashboard chi xem bang
# page.screenshot; khong co kenh gui chuot/ban phim nguoc vao Playwright page.
# =============================================================================
from invisible_playwright.async_api import InvisiblePlaywright
from invisible_playwright import (
    merge_faithful_canvas_readback,
    set_input_files_native,
)
from app.domain.ports.browser import IBrowserService
from app.core.config import settings
from app.core.exceptions import AccountBannedException
from app.core.tiktok_urls import ensure_tiktok_english_url
from app.infrastructure.automation.extension_profile_builder import (
    ExtensionProfileBuilder,
    InstalledExtension,
    firefox_prefs_for_extensions,
    parse_extension_paths,
    parse_json_object,
)
from app.use_cases.upload.caption_hashtags import (
    choose_stable_hashtag_suggestion,
    hashtag_query_candidates,
)

logger = logging.getLogger("PlaywrightAdapter")


def _foryou_state_ready(state: Dict[str, Any], network_idle: bool) -> bool:
    """Return True only for a fully rendered, signed-in For You observation."""
    return bool(
        network_idle
        and state.get("ready") == "complete"
        and state.get("loggedIn")
        and not state.get("login")
        and int(state.get("feedItems") or 0) > 0
        and int(state.get("mediaReady") or 0) > 0
        and int(state.get("pendingImages") or 0) == 0
        and int(state.get("busy") or 0) == 0
        and state.get("fontsLoaded")
    )


def _classify_distribution_text(value: str) -> str:
    """Classify only explicit TikTok review/FYF labels, never infer from views."""
    text = " ".join((value or "").split()).casefold()
    if re.search(
        r"not eligible (?:for|to appear in) (?:the )?for you|"
        r"ineligible (?:for|to appear in) (?:the )?for you|"
        r"kh[oô]ng (?:đủ|du) điều kiện.*(?:dành cho bạn|for you)",
        text,
    ):
        return "FYF_INELIGIBLE"
    if re.search(
        r"under review|being reviewed|processing review|"
        r"đang (?:được )?(?:xem xét|xét duyệt|kiểm duyệt)",
        text,
    ):
        return "UNDER_REVIEW"
    return "PUBLISHED"

# =============================================================================
# THEO DOI CUA SO (HWND) DE STREAM BANG PrintWindow
# =============================================================================
# Moi adapter nhan cua so MozillaWindowClass co process mang DUNG session-token
# cua invisible_playwright. _claimed_hwnds chi la lop bao ve phu, khong con dung
# phep doan "cua so nao xuat hien sau" khi nhieu browser khoi dong cung luc.
import threading as _threading
_claimed_hwnds: set = set()
_hwnd_lock = _threading.Lock()

def _reap_session_tree(token) -> int:
    """Giet DUNG cay tien trinh cua 1 phien, nhan dien bang SessionToken cua
    invisible_playwright (moi process cua phien mang bien moi truong
    INVPW_SESSION_TOKEN = token do).

    Dung thay cho cach cu "chup snapshot PID roi giet moi firefox.exe la": khop
    DUONG theo token nen chay da luong khong bao gio dung nham phien khac.
    Tra ve so tien trinh da gui lenh dung. Chay trong thread (psutil la blocking).
    """
    try:
        from invisible_core.process import find_processes, terminate
    except Exception:
        return 0
    try:
        procs = find_processes(token)
        if not procs:
            return 0
        n = terminate(procs)
        if n:
            logger.info(f"[CLEANUP] Da don {n} tien trinh cua rieng phien nay (theo token).")
        return n
    except Exception as e:
        logger.debug(f"_reap_session_tree loi: {e}")
        return 0


class InvisiblePlaywrightAdapter(IBrowserService):
    def __init__(self):
        self._invisible_pw: Optional[InvisiblePlaywright] = None
        self._browser = None
        self._page = None
        self._temp_profile_path: Optional[str] = None
        self._native_upload_staging_dirs: set[str] = set()
        # HWND cua so Firefox cua rieng phien nay (dung cho PrintWindow stream).
        self._hwnd: Optional[int] = None
        self._window_visible: bool = False
        self._launch_headless: bool = True
        self._automation_gate: Optional[asyncio.Event] = None
        self._stream_suspended: bool = False
        self.last_publish_distribution_status: str = "UNKNOWN"
        # True only after TikTok has accepted a publish action far enough to
        # redirect, show an explicit success message, or accept ``Post now``.
        # The upload use case uses this to decide whether a read-only public
        # profile check is safe after Studio itself produces a false negative.
        self.last_publish_acknowledged: bool = False
        # A successful For You readiness check issues one short-lived ticket.
        # Upload consumes it before navigating to Studio, preventing callers
        # from bypassing the mandatory home-load gate.
        self._foryou_ready_at: Optional[float] = None

    @property
    def stream_suspended(self) -> bool:
        return self._stream_suspended

    def bind_automation_gate(self, gate: asyncio.Event) -> None:
        self._automation_gate = gate

    async def _wait_automation_gate(self) -> None:
        gate = self._automation_gate
        if gate is None or gate.is_set():
            return
        await gate.wait()

    async def initialize(self, proxy_config: Optional[Dict[str, Any]] = None, seed: Optional[int] = None, force_visible: bool = False) -> None:
        # force_visible=True (che do DEBUG): dua cua so ra HIEN + foreground de user
        # thao tac tay, KHONG day off-screen. Mac dinh False -> theo cau hinh (an off-screen).
        # Luu tham so launch -> co the RE-LAUNCH khi browser CHET giua chung (navigate_to
        # phat hien 'browsingContext undefined' = tab/browser chet -> mo lai).
        self._foryou_ready_at = None
        self._init_proxy_config = proxy_config
        self._init_seed = seed
        self._init_force_visible = force_visible
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
            # Build profile MOI tu cac extension ngoai. Khong copy profile master,
            # khong mang cookie/cache/site-storage cua phien khac sang phien nay.
            source_paths = parse_extension_paths(
                getattr(settings, "BROWSER_EXTENSION_PATHS", "")
            )
            if not source_paths:
                source_paths = [getattr(settings, "BROWSER_EXTENSIONS_DIR")]

            json_overrides = parse_json_object(
                getattr(settings, "BROWSER_EXTENSION_JSON_OVERRIDES", "{}"),
                "BROWSER_EXTENSION_JSON_OVERRIDES",
            )
            uuid_overrides = parse_json_object(
                getattr(settings, "BROWSER_EXTENSION_UUIDS_JSON", "{}"),
                "BROWSER_EXTENSION_UUIDS_JSON",
            )

            # OmoCaptcha must keep its Mozilla signature intact.  Its API key
            # is written to browser.storage.local after Firefox activates the
            # signed XPI; rewriting configs.json would invalidate the signature.
            omo_uuid = getattr(settings, "OMOCAPTCHA_EXTENSION_UUID", "")
            if omo_uuid:
                uuid_overrides.setdefault("omocaptcha@gmail.com", omo_uuid)

            extension_builder = ExtensionProfileBuilder(
                source_paths,
                json_resource_overrides=json_overrides,
                uuid_overrides=uuid_overrides,
                storage_local_seed_resources={
                    "omocaptcha@gmail.com": "configs.json",
                },
                storage_local_overrides={
                    "omocaptcha@gmail.com": {
                        "api_key": getattr(settings, "OMOCAPTCHA_KEY", ""),
                        "initialized": True,
                    },
                },
                fail_if_empty=getattr(settings, "BROWSER_EXTENSIONS_REQUIRED", True),
            )
            installed_extensions = await asyncio.to_thread(
                extension_builder.prepare_profile, self._temp_profile_path
            )
            await asyncio.to_thread(
                self._validate_extension_packages, installed_extensions
            )
            logger.info(
                "[*] Da tao profile moi va gan %d extension: %s",
                len(installed_extensions),
                ", ".join(
                    f"{item.addon_id}@{item.version}" for item in installed_extensions
                ) or "khong co",
            )

            configured_headless = bool(getattr(settings, "BROWSER_HEADLESS", True))
            # Task nen true-headless; force_visible chi dung cho luong debug/login.
            self._launch_headless = bool((not force_visible) and configured_headless)
            true_headless_enabled = bool(
                getattr(settings, "BROWSER_TRUE_HEADLESS", True)
            )
            use_true_headless = bool(self._launch_headless and true_headless_enabled)
            # _resolve_headless() cua invisible_playwright doc bien nay. Giu o cap
            # process; force_visible truyen headless=False nen wrapper tu bo qua.
            os.environ["INVPW_TRUE_HEADLESS"] = "1" if true_headless_enabled else "0"
            hide_offscreen = bool(
                (not force_visible)
                and (not self._launch_headless)
                and getattr(settings, "HIDE_BROWSER_OFFSCREEN", True)
            )

            # =================================================================
            # PREFS BO SUNG (extra_prefs) - duoc invisible_playwright overlay
            # SAU CUNG nen override duoc moi thu (xem prefs.translate_profile_to_prefs).
            # =================================================================
            firefox_prefs = {
                # Keep the profile's pre-start UUID mapping identical to the
                # prefs passed through the invisible_playwright launcher.
                **firefox_prefs_for_extensions(installed_extensions),

                # TikTok Studio ignores ?lang=en on some routes and resolves
                # translations from the browser context instead. Keep the
                # Firefox prefs aligned with InvisiblePlaywright(locale=...) so
                # navigator.language, Intl and Accept-Language all say en-US.
                "intl.accept_languages": "en-US, en",
                "intl.locale.requested": "en-US",

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

                # LUU Y: TUNG thu ep software-render (layers.gpu-process.enabled=False,
                # gfx.webrender.software=True, ...) de sua GPU crash luc debug tren
                # RDP mat display. NHUNG sau khi cai lai firefox + reboot, software-
                # render lai gay loi khac: cua so OFF-SCREEN khong duoc VE -> phai
                # "re chuot vao icon de active" moi len + firefox de crash
                # ("Connection closed while reading from the driver"). => BO software-
                # render, de firefox dung hardware/compositor mac dinh (cua so an van
                # ve binh thuong nho pref occlusion o tren).

                # =============================================================
                # CHONG "PROFILE KHONG MO DUOC" / KET O MAN "RESTORE SESSION":
                # Neu profile bi dong khong sach (mo tay, bi kill, crash) -> lan
                # sau Firefox co the hien man khoi phuc phien / bao profile dang
                # dung -> treo khong chay tiep. Cac pref nay tat han co che do:
                # KHONG hoi khoi phuc, KHONG dem so lan crash, bo qua session cu.
                # (Doc invisible_playwright canh bao: mo profile bang browser khac
                #  khi automation dang dung se lam HONG profile - phong ngua o day.)
                # =============================================================
                "browser.sessionstore.resume_from_crash": False,
                "toolkit.startup.max_resumed_crashes": -1,
                "browser.sessionstore.max_resumed_crashes": 0,
                "browser.startup.page": 0,               # khong mo tab phien truoc
                "browser.sessionstore.resume_session_once": False,

                # =============================================================
                # FIX GOC "TRINH DUYET MO LEN NHUNG KHONG CHAY" (13/08/2026)
                # Log firefox luc treo:
                #   Juggler listening to the pipe
                #   [FrameTree] removeListeners(_eventListeners) failed
                #               (half-destroyed webProgress)     <-- HONG O DAY
                #   ... roi treo den timeout 180s / goto bao browsingContext undefined
                # => Luc khoi dong, Firefox tu mo/dong cac tab noi bo (first-run,
                #    welcome, newtab, pocket, remote-settings). Tab bi HUY ngay khi
                #    juggler dang gan listener -> FrameTree vo -> browsingContext
                #    khong bao gio dang ky -> treo. Tat het cac thu tu-mo do:
                # =============================================================
                "browser.startup.homepage_override.mstone": "ignore",  # khong mo trang "What's new"
                "browser.startup.firstrunSkipsHomepage": True,
                "browser.aboutwelcome.enabled": False,                 # tat man Welcome
                "browser.newtabpage.enabled": False,                   # newtab = trang trong
                "browser.newtabpage.activity-stream.feeds.topsites": False,
                "browser.newtabpage.activity-stream.feeds.section.topstories": False,
                "extensions.pocket.enabled": False,                    # tat Pocket (spocs 403)
                "datareporting.policy.dataSubmissionEnabled": False,
                "datareporting.healthreport.uploadEnabled": False,
                "toolkit.telemetry.enabled": False,
                "toolkit.telemetry.unified": False,
                "browser.contentblocking.report.hide_vpn_banner": True,
                "browser.discovery.enabled": False,
                "app.normandy.enabled": False,                         # tat thi nghiem tu dong
                "app.shield.optoutstudies.enabled": False,
                "browser.region.network.url": "",                      # bo goi mang luc khoi dong
                "browser.safebrowsing.downloads.remote.enabled": False,

                # Không override process count, BFCache, disk/image/media cache.
                # Nhóm cũ chỉ tiết kiệm ~3% RAM nhưng làm video giật và tạo hành vi
                # khác Firefox stock. Để engine invisible quản lý các bề mặt này.
            }

            # TikTok Studio reads Canvas2D pixels while preparing crops and
            # thumbnails. The default anti-fingerprint substitution would
            # otherwise become visible speckle in the resulting media. Apply
            # this consistently to every product session for the identity.
            firefox_prefs = merge_faithful_canvas_readback(
                firefox_prefs, True
            ) or firefox_prefs

            # =================================================================
            # KHOI CHAY DUNG WRAPPER CU: headless=True => binary self-cloak;
            # force_visible/debug => headed-visible.
            # =================================================================
            from app.infrastructure.streaming.win_capture import enum_moz_hwnds
            # True-headless khong co HWND. Neu tat tuy chon nay thi wrapper quay ve
            # DWM self-cloak va ta van theo doi HWND cho PrintWindow.
            _before_hwnds = set() if use_true_headless else enum_moz_hwnds()
            # =============================================================
            # invisible-core hien tai + firefox_prefs o tren deu tat Windows
            # occlusion tracking, nen headed-offscreen van tiep tuc render.
            # =============================================================
            self._invisible_pw = InvisiblePlaywright(
                proxy=proxy_opts,
                headless=self._launch_headless,
                humanize=True,
                seed=seed,
                # Language preference is English (US), but geography remains
                # automatic from the real/proxy egress (for example Indonesia).
                locale=getattr(settings, "TIKTOK_WEB_LOCALE", "en-US"),
                timezone="auto",
                profile_dir=self._temp_profile_path,
                extra_prefs=firefox_prefs,
                # TikTok Studio reads Canvas2D pixels while preparing crops and
                # thumbnails. The default anti-fingerprint substitution would
                # otherwise become visible speckle in the resulting media.
                # Keep this stable for every task using this account identity,
                # rather than changing canvas behaviour only on upload pages.
            )
            self._invisible_pw.set_firefox_extensions(
                item.xpi_path for item in installed_extensions
            )
            # =============================================================
            # LUOI AN TOAN (khong phai cach chua chinh).
            # Phong truong hop hi huu launch bi treo (vd may qua tai):
            # cat o BROWSER_LAUNCH_TIMEOUT giay, giet tien trinh firefox cua lan do
            # (tranh ro ri) roi mo lai 1 lan. KHONG con la co che "hen xui" nua.
            # =============================================================
            _lt = max(15, int(getattr(settings, "BROWSER_LAUNCH_TIMEOUT", 25)))
            _tries = max(1, int(getattr(settings, "BROWSER_LAUNCH_MAX_TRIES", 2)))
            self._browser = None
            _err = None
            for _att in range(1, _tries + 1):
                _t0 = time.monotonic()
                try:
                    self._browser = await asyncio.wait_for(
                        self._invisible_pw.__aenter__(), timeout=_lt
                    )
                    logger.info(f"[LAUNCH] OK sau {time.monotonic()-_t0:.1f}s (lan {_att}/{_tries}).")
                    break
                except Exception as e_l:
                    _err = e_l
                    _kind = "treo qua %ss" % _lt if isinstance(e_l, asyncio.TimeoutError) else str(e_l)[:70]
                    logger.warning(f"[LAUNCH] Lan {_att}/{_tries} hong ({_kind}) -> don + mo lai.")
                    # PHAI lay token TRUOC __aexit__: _teardown() reap xong se dat
                    # lai _session_token = SessionToken() rong (falsy, khop 0 process).
                    _token = getattr(self._invisible_pw, "_session_token", None)
                    # Dong context (co han gio, khong de treo o buoc don). Ban than
                    # __aexit__ -> _teardown() DA tu reap theo token roi; buoc duoi
                    # chi la luoi an toan cho truong hop chinh __aexit__ bi treo qua 10s.
                    try:
                        await asyncio.wait_for(self._invisible_pw.__aexit__(None, None, None), timeout=10)
                    except Exception:
                        pass
                    # GIET dut tien trinh CUA RIENG lan hong nay, nhan dien bang
                    # SessionToken cua thu vien (bien moi truong INVPW_SESSION_TOKEN
                    # dong dau tren ca cay process).
                    # ====================== VI SAO KHONG DUNG SNAPSHOT PID =========
                    # Ban cu chup firefox_pids() luc bat dau roi giet MOI firefox.exe
                    # khong nam trong snapshot do. Chay DA LUONG thi cac phien khac
                    # khoi dong SAU snapshot -> bi giet oan, phien dang chay tot lan ra
                    # "Connection closed" va account roi ERROR. Token khop DUONG (chi
                    # dung process mang dung token nay) nen an toan tuyet doi.
                    if _token:
                        try:
                            await asyncio.to_thread(_reap_session_tree, _token)
                        except Exception:
                            pass
                    if _att < _tries:
                        await asyncio.sleep(1.5)
                        self._invisible_pw = InvisiblePlaywright(
                            proxy=proxy_opts,
                            headless=self._launch_headless,
                            humanize=True,
                            seed=seed,
                            locale=getattr(settings, "TIKTOK_WEB_LOCALE", "en-US"),
                            timezone="auto",
                            profile_dir=self._temp_profile_path,
                            extra_prefs=firefox_prefs,
                        )
                        self._invisible_pw.set_firefox_extensions(
                            item.xpi_path for item in installed_extensions
                        )
            if self._browser is None:
                raise _err or RuntimeError(f"Khong mo duoc trinh duyet sau {_tries} lan.")

            # AN CUA SO NGAY SAU __aenter__, TRUOC moi thao tac page. Ban vua roi
            # tao/doi tab truoc khi an nen cua so lo ra lau va co the can focus.
            self._hwnd = None
            self._window_visible = False
            try:
                self._hwnd = (
                    None
                    if use_true_headless
                    else await self._detect_own_hwnd(_before_hwnds)
                )
                if self._hwnd:
                    from app.infrastructure.streaming.win_capture import (
                        move_window_offscreen,
                        show_window_foreground,
                    )
                    try:
                        if self._launch_headless:
                            # Khong show/move: patched binary da DWMWA_CLOAK ngay
                            # ben trong. HWND chi duoc luu cho PrintWindow stream.
                            self._window_visible = False
                        elif hide_offscreen:
                            ok = await asyncio.wait_for(
                                asyncio.to_thread(move_window_offscreen, self._hwnd), timeout=5
                            )
                            self._window_visible = not bool(ok)
                        else:
                            ok = await asyncio.wait_for(
                                asyncio.to_thread(show_window_foreground, self._hwnd), timeout=5
                            )
                            self._window_visible = bool(ok)
                    except Exception:
                        logger.warning("[WINDOW] Khong dat duoc trang thai cua so (bo qua).")
                    logger.info(
                        f"[WINDOW] HWND={self._hwnd} "
                        f"{'dang HIEN' if self._window_visible else 'dang CLOAK/OFF-SCREEN'}."
                    )
            except Exception as e_v:
                logger.warning(f"[WINDOW] Loi: {str(e_v)}")

            # Tao page sach sau khi persistent context san sang. Tab khoi dong cua
            # patched Firefox co luc chua co browsingContext; tai su dung no se lam
            # task dung ngay lan goto dau tien.
            _default_pages = list(getattr(self._browser, "pages", None) or [])
            self._page = await self._browser.new_page()
            locale_state = await self._page.evaluate(
                "() => ({ language: navigator.language, languages: navigator.languages })"
            )
            if not str(locale_state.get("language") or "").casefold().startswith("en"):
                raise RuntimeError(
                    f"TikTok browser locale was not applied: {locale_state!r}"
                )
            logger.info(
                "[LOCALE] TikTok browser fixed to %s (%s).",
                locale_state.get("language"),
                locale_state.get("languages"),
            )
            for _old_page in _default_pages:
                if _old_page is self._page:
                    continue
                try:
                    await _old_page.close()
                except Exception:
                    pass
            await self._verify_loaded_extensions(installed_extensions)
            # Firefox can replace its startup top-level HWND when the initial
            # tab is closed. Re-resolve after the clean page exists so the
            # streamer starts with the stable, token-owned window.
            if not use_true_headless:
                await self.recover_stream_hwnd()
            logger.info("[*] Khoi tao tab moi sach.")

            _display_mode = (
                ("true-headless" if use_true_headless else "headed-cloaked")
                if self._launch_headless
                else ("headed-visible" if self._window_visible else "headed-offscreen")
            )
            logger.info(f"[+] Khoi tao browser session ({_display_mode}). Seed: {seed} | Proxy: {proxy_opts.get('server') if proxy_opts else 'Direct NET'}")
        except Exception as e:
            logger.error(f"[-] Khong the khoi tao trinh duyet: {str(e)}")
            await self.close()
            raise e

    @staticmethod
    def _validate_extension_packages(
        installed_extensions: List[InstalledExtension],
    ) -> None:
        """Validate sensitive bundled config without changing signed XPIs."""

        expected_key = getattr(settings, "OMOCAPTCHA_KEY", "")
        for item in installed_extensions:
            if item.addon_id != "omocaptcha@gmail.com":
                continue
            try:
                with zipfile.ZipFile(item.xpi_path) as archive:
                    json.loads(archive.read("configs.json").decode("utf-8-sig"))
                    has_signature = any(
                        name.casefold().startswith("meta-inf/")
                        for name in archive.namelist()
                    )
            except (KeyError, UnicodeDecodeError, json.JSONDecodeError, zipfile.BadZipFile) as exc:
                raise RuntimeError("OmoCaptcha 1.7.7 package/config is invalid") from exc
            if not has_signature:
                raise RuntimeError("OmoCaptcha XPI signature was not preserved")
            storage_path = (
                item.xpi_path.parents[1]
                / "browser-extension-data"
                / item.addon_id
                / "storage.js"
            )
            try:
                storage = json.loads(storage_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise RuntimeError("OmoCaptcha storage seed is invalid") from exc
            if (
                expected_key
                and storage.get("api_key") != expected_key
                or storage.get("initialized") is not True
            ):
                raise RuntimeError(
                    "OmoCaptcha storage does not contain the configured API key"
                )

    async def _verify_loaded_extensions(
        self, installed_extensions: List[InstalledExtension]
    ) -> None:
        """Verify Firefox activated each requested extension before use.

        Playwright's Firefox transport can hang when navigating directly to a
        ``moz-extension://`` JSON resource.  Firefox's own extension registry
        is the authoritative source and also exposes signature/disabled state.
        """

        if not installed_extensions:
            return
        registry_path = Path(self._temp_profile_path or "") / "extensions.json"
        expected = {item.addon_id: item for item in installed_extensions}
        loaded: Dict[str, Dict[str, Any]] = {}
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            try:
                registry = await asyncio.to_thread(
                    lambda: json.loads(registry_path.read_text(encoding="utf-8"))
                )
                loaded = {
                    str(addon.get("id")): addon
                    for addon in registry.get("addons", [])
                    if addon.get("id") in expected
                }
            except (OSError, json.JSONDecodeError):
                loaded = {}
            if len(loaded) == len(expected):
                break
            await asyncio.sleep(0.2)

        for addon_id, item in expected.items():
            state = loaded.get(addon_id)
            if not state:
                raise RuntimeError(f"Firefox did not register extension {addon_id}")
            if (
                not state.get("active")
                or state.get("appDisabled")
                or state.get("userDisabled")
                or str(state.get("version", "")) != item.version
            ):
                raise RuntimeError(f"Firefox did not activate extension {addon_id}")
            if addon_id == "omocaptcha@gmail.com" and int(
                state.get("signedState") or 0
            ) <= 0:
                raise RuntimeError("Firefox did not accept the OmoCaptcha signature")
            if addon_id == "omocaptcha@gmail.com":
                storage_path = (
                    Path(self._temp_profile_path or "")
                    / "browser-extension-data"
                    / addon_id
                    / "storage.js"
                )
                try:
                    storage = await asyncio.to_thread(
                        lambda: json.loads(storage_path.read_text(encoding="utf-8"))
                    )
                except (OSError, json.JSONDecodeError) as exc:
                    raise RuntimeError("OmoCaptcha storage was not initialized") from exc
                expected_key = getattr(settings, "OMOCAPTCHA_KEY", "")
                if expected_key and storage.get("api_key") != expected_key:
                    raise RuntimeError("OmoCaptcha loaded an unexpected API key")
            logger.info(
                "[EXTENSION] Active: %s@%s (signed/configuration verified).",
                item.addon_id,
                item.version,
            )

    async def _detect_own_hwnd(self, before_hwnds: set) -> Optional[int]:
        """Find this session's Firefox HWND by its process session-token."""
        if os.name != "nt":
            return None
        try:
            from app.infrastructure.streaming.win_capture import (
                enum_moz_hwnds,
                find_session_moz_hwnd,
            )
        except Exception:
            return None
        session_token = getattr(self._invisible_pw, "_session_token", None)
        # Poll toi da ~6s cho cua so hien ra (browser vua launch).
        for _ in range(24):
            await asyncio.sleep(0.25)
            if session_token:
                try:
                    owned_hwnd = await asyncio.to_thread(
                        find_session_moz_hwnd, session_token
                    )
                except Exception:
                    owned_hwnd = None
                if owned_hwnd:
                    with _hwnd_lock:
                        _claimed_hwnds.add(owned_hwnd)
                    logger.info(
                        "[HWND] Detected token-owned window HWND=%s for this session.",
                        owned_hwnd,
                    )
                    return owned_hwnd
                # A minted token is authoritative. Never guess another
                # concurrently launching session's window.
                continue
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

    async def recover_stream_hwnd(self) -> Optional[int]:
        """Reacquire only this session's HWND without touching Playwright.

        The stream calls this after an OS capture failure.  It is safe during
        captcha/upload waits because the lookup runs in a worker thread and is
        keyed by the browser process token rather than the Playwright channel.
        """
        if os.name != "nt" or self._invisible_pw is None:
            return None
        session_token = getattr(self._invisible_pw, "_session_token", None)
        if not session_token:
            return None
        try:
            from app.infrastructure.streaming.win_capture import find_session_moz_hwnd

            owned_hwnd = await asyncio.to_thread(find_session_moz_hwnd, session_token)
        except Exception:
            return None
        if not owned_hwnd:
            return None
        old_hwnd = self._hwnd
        if old_hwnd != owned_hwnd:
            with _hwnd_lock:
                if old_hwnd is not None:
                    _claimed_hwnds.discard(old_hwnd)
                _claimed_hwnds.add(owned_hwnd)
            self._hwnd = owned_hwnd
            logger.info("[HWND] Stream recovered token-owned HWND=%s.", owned_hwnd)
        return owned_hwnd

    @property
    def window_is_visible(self) -> bool:
        return bool(self._window_visible and self._hwnd)

    async def show_window(self) -> bool:
        """Restore cua so cua session hien tai cho che do debug truc tiep.

        Khong tao browser/page moi, vi vay URL, cookies, local storage va moi state
        trong tab deu duoc giu nguyen. True-headless khong co HWND va se tra False.
        """
        if self._launch_headless or not self._hwnd:
            return False
        from app.infrastructure.streaming.win_capture import show_window_foreground

        try:
            shown = await asyncio.wait_for(
                asyncio.to_thread(show_window_foreground, self._hwnd), timeout=5
            )
        except Exception:
            shown = False
        self._window_visible = bool(shown)
        return self._window_visible

    async def hide_window(self) -> bool:
        """Move the current headed window back off-screen without minimizing it."""
        if self._launch_headless or not self._hwnd:
            return False
        from app.infrastructure.streaming.win_capture import move_window_offscreen

        try:
            hidden = await asyncio.wait_for(
                asyncio.to_thread(move_window_offscreen, self._hwnd), timeout=5
            )
        except Exception:
            hidden = False
        if hidden:
            self._window_visible = False
        return bool(hidden)

    async def _ensure_page(self):
        """Tra ve 1 page HOP LE dang mo. Neu self._page da bi dong/detach (nguyen
        nhan loi 'browsingContext is undefined' khi goto), lay lai page moi nhat tu
        context (hoac tao page moi). Fix truong hop browser mo len nhung goto tren
        page cu bi treo/loi."""
        try:
            if self._page and not self._page.is_closed():
                return self._page
        except Exception:
            pass
        # Tim context: uu tien context cua page cu; neu khong co, lay tu browser
        # (persistent context: self._browser CHINH LA context, co .pages truc tiep;
        #  browser thuong: co .contexts[0]). Thu ca 2 + cho toi 5 lan cho page ready.
        for _ in range(5):
            ctx = None
            try:
                ctx = self._page.context if self._page else None
            except Exception:
                ctx = None
            if ctx is None and self._browser is not None:
                if getattr(self._browser, "contexts", None):
                    ctx = self._browser.contexts[0]
                elif hasattr(self._browser, "pages"):
                    ctx = self._browser          # persistent context = chinh no
            if ctx is not None:
                try:
                    pages = [p for p in ctx.pages if not p.is_closed()]
                    if pages:
                        self._page = pages[-1]
                        return self._page
                    self._page = await ctx.new_page()
                    return self._page
                except Exception:
                    pass
            await asyncio.sleep(0.6)
        return self._page

    async def navigate_to(self, url: str) -> None:
        await self._wait_automation_gate()
        if not self._page and not self._browser:
            raise RuntimeError("Trinh duyet chua duoc khoi tao.")

        from urllib.parse import urlparse
        url = ensure_tiktok_english_url(url)
        target_host = urlparse(url).netloc.split(":")[0]
        last_err = None

        # THU TOI 4 LAN. Dung wait_until="commit" (GIONG go tay URL: commit xong tra
        # ve NGAY, KHONG cho load event cua SPA nang nhu TikTok). Neu context CHET
        # ('browsingContext undefined' / 'Connection closed' = tab/browser chet) va
        # doi page van khong cuu duoc -> RE-LAUNCH ca browser 1 lan roi thu tiep.
        relaunched = False
        for attempt in range(4):
            page = await self._ensure_page()
            if page is None:
                last_err = RuntimeError("Khong lay duoc page de dieu huong.")
                if not relaunched and hasattr(self, "_init_seed"):
                    relaunched = True
                    logger.warning("[!] Khong co page -> RE-LAUNCH lai browser...")
                    try: await self._relaunch()
                    except Exception as e_re: logger.warning(f"[!] re-launch loi: {e_re}")
                    continue
                await asyncio.sleep(1)
                continue
            try:
                logger.info(f"[*] Dieu huong toi {url} (lan {attempt+1}, wait=commit)...")
                await page.goto(url, wait_until="commit", timeout=30000)
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=12000)
                except Exception:
                    pass
                return
            except Exception as e:
                last_err = e
                msg = str(e)
                logger.warning(f"[!] goto loi (lan {attempt+1}): {msg[:90]}")
                dead = ("browsingContext" in msg or "Connection closed" in msg
                        or "closed" in msg or "Target page" in msg or "crash" in msg.lower())
                if dead:
                    # Lan dau doi page; van chet & chua re-launch -> MO LAI ca browser.
                    if not relaunched and hasattr(self, "_init_seed") and attempt >= 1:
                        relaunched = True
                        logger.warning("[!] Context CHET -> RE-LAUNCH lai browser (cung proxy/seed)...")
                        try: await self._relaunch()
                        except Exception as e_re: logger.warning(f"[!] re-launch loi: {e_re}")
                        continue
                    self._page = None
                    await asyncio.sleep(1.0)
                    continue
                # Loi khac (vd timeout commit): neu THUC TE da toi dung host -> coi nhu OK.
                try:
                    cur = page.url or ""
                except Exception:
                    cur = ""
                if target_host and target_host in cur:
                    logger.info(f"[*] goto bao loi nhung da toi {cur} -> coi nhu OK.")
                    return
                await asyncio.sleep(1.0)

        logger.error(f"[-] That bai dieu huong toi {url} sau nhieu lan: {last_err}")
        raise last_err if last_err else RuntimeError(f"Khong dieu huong duoc toi {url}")

    async def _relaunch(self) -> None:
        """Dong browser hien tai + MO LAI voi cung proxy/seed (khi tab/browser chet
        giua chung). Dung 1 lan trong navigate_to de cuu phien thay vi bao loi ngay."""
        try:
            await self.close()
        except Exception:
            pass
        await self.initialize(
            proxy_config=getattr(self, "_init_proxy_config", None),
            seed=getattr(self, "_init_seed", None),
            force_visible=getattr(self, "_init_force_visible", False),
        )

    async def inject_cookies(self, cookies: List[Dict[str, Any]]) -> None:
        await self._wait_automation_gate()
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
        await self._wait_automation_gate()
        if not self._browser:
            return []

        contexts = getattr(self._browser, "contexts", [])
        if contexts:
            return await contexts[0].cookies()
        else:
            return await self._browser.cookies()

    async def check_login_status(self) -> bool:
        await self._wait_automation_gate()
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

        # Chỉ dùng marker dành riêng cho account đã đăng nhập. TikTok vẫn hiện
        # nút/link Upload cho guest rồi redirect sang /login, nên nav-upload và
        # link Studio tuyệt đối không được xem là bằng chứng đăng nhập.
        profile_link_locator = self._page.locator(
            '[data-e2e="profile-icon"], [data-e2e="nav-profile"], '
            '[data-e2e="messages-icon"], [data-e2e="inbox-icon"], '
            'a[href*="/messages"]'
        )
        login_locator = self._page.locator('[data-e2e="nav-login-button"], button:has-text("Log in"), button:has-text("Dang nhap")')

        for i in range(20):
            await self._wait_automation_gate()
            try:
                if await ban_dialog_locator.count() > 0 and await ban_dialog_locator.first.is_visible():
                    dialog_title = await ban_dialog_locator.first.inner_text()
                    logger.error(f"[!] PHAT HIEN TAI KHOAN BI BANNED QUA DIALOG: '{dialog_title}'")
                    raise AccountBannedException(f"Tai khoan bi cam vinh vien: {dialog_title}")

                # CHONG FALSE-POSITIVE: khi con modal CAPTCHA (chua giai) thi feed For You
                # mo phia sau co the khien cac dau hieu "da login" khop nham -> CHUA duoc
                # coi la dang nhap. Bo qua vong nay, cho captcha giai xong (hoac timeout).
                if await self.is_captcha_present():
                    await asyncio.sleep(1)
                    continue

                if await profile_link_locator.count() > 0:
                    logger.info(f"[+] Xac minh THANH CONG sau {i+1} giay (Phat hien profile/messages cua account).")
                    return True

                # Read only hydration scripts instead of serializing the full DOM.
                try:
                    hydrated_login = await self._page.evaluate(r"""() => Array.from(document.scripts).some(
                      script => /"isLogin"\s*:\s*true/.test(script.textContent || '')
                    )""")
                    if hydrated_login:
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

    async def prepare_foryou_home(self, step_logger=None) -> bool:
        """Open For You and require a fully loaded, stable signed-in feed.

        URL/``interactive``/a generic ``main`` element are deliberately
        insufficient. Studio remains blocked until the load event and critical
        render requests are quiet, feed media is usable, visible images/fonts
        are ready, and loading overlays are gone for several consecutive
        observations. Feed media URLs and item order are intentionally allowed
        to rotate because For You is a live feed. Continuous video streaming and
        telemetry are not treated as unfinished page rendering.
        """
        async def log(message):
            if step_logger:
                await step_logger(message)

        self._foryou_ready_at = None
        await log("Đang mở trang For You và chờ tải hoàn toàn...")
        await self.navigate_to("https://www.tiktok.com/foryou?lang=en")
        try:
            await self._page.wait_for_load_state("load", timeout=30000)
        except Exception as exc:
            logger.warning("[UPLOAD] For You không phát load event: %s", str(exc)[:120])
            await log("Trang For You chưa hoàn tất tải tài liệu; chưa được chuyển sang đăng bài.")
            return False

        page = self._page
        critical_types = {"document", "script", "stylesheet", "font", "image"}
        critical_inflight: set[int] = set()
        last_critical_activity = [time.monotonic()]

        def on_request(request) -> None:
            try:
                if request.resource_type in critical_types:
                    critical_inflight.add(id(request))
                    last_critical_activity[0] = time.monotonic()
            except Exception:
                pass

        def on_request_done(request) -> None:
            key = id(request)
            if key in critical_inflight:
                critical_inflight.discard(key)
                last_critical_activity[0] = time.monotonic()

        page.on("request", on_request)
        page.on("requestfinished", on_request_done)
        page.on("requestfailed", on_request_done)
        stable = 0
        last_state: Dict[str, Any] = {}
        try:
            for _ in range(90):
                await self._wait_automation_gate()
                if await self.is_captcha_present():
                    stable = 0
                    await asyncio.sleep(1)
                    continue
                try:
                    state = await self._page.evaluate(r"""() => {
                  const visible = el => !!(el && (el.offsetParent !== null || el.getClientRects().length));
                  const inViewport = el => {
                    if (!visible(el)) return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0 && rect.bottom > 0 &&
                      rect.right > 0 && rect.top < window.innerHeight && rect.left < window.innerWidth;
                  };
                  const loggedIn = Array.from(document.querySelectorAll(
                    '[data-e2e="profile-icon"],[data-e2e="nav-profile"],'
                    + '[data-e2e="messages-icon"],[data-e2e="inbox-icon"],a[href*="/messages"]'
                  )).some(visible) || Array.from(document.scripts).some(
                    script => /"isLogin"\s*:\s*true/.test(script.textContent || '')
                  );
                  const feedSelectors = [
                    '[data-e2e="recommend-list-item-container"]',
                    '[data-e2e*="recommend-list-item"]',
                    '[data-e2e="browse-video"]',
                    '[data-e2e="feed-video"]'
                  ];
                  let feedItems = Array.from(document.querySelectorAll(feedSelectors.join(','))).filter(visible);
                  if (!feedItems.length) {
                    feedItems = Array.from(document.querySelectorAll('main video')).filter(visible)
                      .map(video => video.closest('article, section, div') || video);
                  }
                  feedItems = Array.from(new Set(feedItems));
                  const feedRoot = feedItems[0]?.parentElement || document.querySelector('main');
                  const videos = Array.from((feedRoot || document).querySelectorAll('video')).filter(inViewport);
                  const playableVideos = videos.filter(video =>
                    video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA &&
                    (video.videoWidth > 0 || !!video.poster || !!video.currentSrc)
                  );
                  const images = Array.from((feedRoot || document).querySelectorAll('img')).filter(inViewport);
                  const pendingImages = images.filter(image => !image.complete || image.naturalWidth <= 0).length;
                  const loadedImages = images.length - pendingImages;
                  const busySelectors = [
                    '[aria-busy="true"]',
                    '[data-e2e="loading"]', '[data-e2e*="skeleton"]',
                    '.TUXLoading', '[class*="Skeleton"]'
                  ];
                  const busy = Array.from(document.querySelectorAll(busySelectors.join(','))).filter(inViewport).length;
                  const login = Array.from(document.querySelectorAll('[data-e2e="nav-login-button"]')).some(visible);
                  const mediaReady = playableVideos.length + loadedImages;
                  const mediaKey = videos.slice(0, 3).map(video =>
                    `${video.currentSrc || video.poster || ''}:${video.readyState}`
                  ).join('|') || images.slice(0, 3).map(image => image.currentSrc || image.src || '').join('|');
                  return {
                    ready: document.readyState,
                    loggedIn,
                    login,
                    feedItems: feedItems.length,
                    mediaReady,
                    pendingImages,
                    busy,
                    fontsLoaded: !document.fonts || document.fonts.status === 'loaded',
                    fingerprint: `${location.pathname}|${feedItems.length}|${mediaKey}|${document.documentElement.scrollHeight}`
                  };
                    }""")
                    last_state = state
                    critical_quiet = bool(
                        not critical_inflight
                        and time.monotonic() - last_critical_activity[0] >= 2.0
                    )
                    good = bool(
                        "/foryou" in (self._page.url or "").lower()
                        and _foryou_state_ready(state, critical_quiet)
                    )
                    # The feed legitimately rotates videos, signed CDN URLs and
                    # scroll height while remaining fully usable. Requiring an
                    # identical content fingerprint made healthy signed-in feeds
                    # time out forever. Five consecutive structural-ready states
                    # still reject transient or partially rendered pages.
                    stable = stable + 1 if good else 0
                    if stable >= 5:
                        self._foryou_ready_at = time.monotonic()
                        await log("✅ Trang For You đã tải hoàn toàn và ổn định; bắt đầu chuyển sang màn đăng bài.")
                        return True
                except Exception as exc:
                    logger.debug("[UPLOAD] Quan sát For You chưa sẵn sàng: %s", exc)
                    stable = 0
                await asyncio.sleep(1)
            logger.warning(
                "[UPLOAD] For You timeout, critical_inflight=%d, trạng thái cuối: %s",
                len(critical_inflight),
                last_state,
            )
            await log("Trang For You chưa tải hoàn toàn sau 90 giây; dừng trước khi mở trang đăng bài.")
            return False
        finally:
            try:
                page.remove_listener("request", on_request)
                page.remove_listener("requestfinished", on_request_done)
                page.remove_listener("requestfailed", on_request_done)
            except Exception:
                pass

    def _consume_foryou_upload_ticket(self) -> None:
        ready_at = self._foryou_ready_at
        self._foryou_ready_at = None
        current_url = (getattr(self._page, "url", "") or "").lower()
        if (
            ready_at is None
            or time.monotonic() - ready_at > 90
            or "/foryou" not in current_url
        ):
            raise RuntimeError(
                "Chưa có xác nhận For You tải hoàn toàn; không được mở TikTok Studio Upload."
            )

    async def is_captcha_present(self) -> bool:
        """True neu dang co hop captcha (geetest/slider/puzzle) hien tren trang."""
        await self._wait_automation_gate()
        if not self._page:
            return False
        try:
            # A stalled browsing context must never freeze the whole upload.
            # asyncio.wait_for is required because page.evaluate has no timeout
            # argument in Playwright and can otherwise wait indefinitely.
            return bool(await asyncio.wait_for(
                self._page.evaluate("""() => {
                  // Cac container CHI ton tai khi modal captcha TikTok dang bat (dac hieu,
                  // khong false-positive). Bao gom cac bien the da biet: web-v2, secsdk,
                  // geetest slider/rotate/puzzle.
                  const sels = ['.captcha_verify_container','[class*="captcha_verify"]',
                    '#captcha-verify-container','#captcha-verify-container-web-v2','[id*="captcha-verify"]',
                    '#captcha_container','[id="captcha_container"]','div[class*="captcha-verify"]',
                    '.captcha-disable-scroll','[class*="secsdk-captcha"]',
                    '.geetest_captcha','[class*="geetest_"]'];
                  for (const s of sels){
                    const el=document.querySelector(s);
                    if(el && (el.offsetParent!==null || (el.getClientRects && el.getClientRects().length>0))) return true;
                  }
                  for (const f of document.querySelectorAll('iframe')){ if(/captcha/i.test(f.getAttribute('src')||'')) return true; }
                  const t = (document.body.innerText||'');
                  return /drag the slider|kéo thanh trượt|ghép hình|verify to continue|xác minh để tiếp tục|slide to complete|rotate the shapes|xoay hình/i.test(t);
                }"""),
                timeout=2.5,
            ))
        except Exception:
            return False

    async def is_account_banned(self) -> bool:
        """Kiem tra NHANH (1 lan, khong loop) xem trang co dang hien thong bao BAN
        khong. Dung de thoat som trong luong login (tiet kiem thoi gian) thay vi cho
        het cac buoc OTP. Khong dua ket luan sai cho nick binh thuong (chi True khi
        that su thay dau hieu ban)."""
        await self._wait_automation_gate()
        if not self._page:
            return False
        try:
            return await self._page.evaluate("""() => {
              const t=(document.body.innerText||'').toLowerCase();
              if(/your account was banned|submit an appeal|permanently banned|account was suspended|tài khoản của bạn đã bị cấm|đã bị cấm vĩnh viễn|vi phạm nguyên tắc cộng đồng/i.test(t)) return true;
              const dt=document.querySelector('.tux-dialog__content-title');
              if(dt && /ban|cấm|suspend/i.test(dt.innerText||'')) return true;
              return false;
            }""")
        except Exception:
            return False

    async def wait_captcha_cleared(self, timeout: float = 120.0, step_logger=None) -> bool:
        """Neu co CAPTCHA -> DUNG cho extension solver da cau hinh tu xu ly; chi tiep tuc khi
        captcha BIEN MAT. Tra True neu khong co captcha / da giai xong; False neu het
        gio ma captcha van con. Cho captcha xuat hien tre toi 3s truoc khi ket luan."""
        appeared = False
        for _ in range(4):
            await self._wait_automation_gate()
            if await self.is_captcha_present():
                appeared = True
                break
            await asyncio.sleep(0.8)
        if not appeared:
            return True
        if step_logger:
            await step_logger("Phát hiện CAPTCHA -> chờ extension giải xong (không tiếp tục cho tới khi xong)...")
        waited = 0.0
        while waited < timeout:
            await self._wait_automation_gate()
            if not await self.is_captcha_present():
                if step_logger:
                    await step_logger(f"[+] Captcha đã được giải sau ~{int(waited)}s -> tiếp tục.")
                await asyncio.sleep(1.5)   # on dinh sau khi captcha bien mat
                return True
            await asyncio.sleep(2)
            waited += 2
        if step_logger:
            await step_logger("[!] Captcha vẫn chưa giải xong sau thời gian chờ.")
        return False

    async def wait_first_visible(self, selectors: List[str], timeout: float = 12.0):
        """Cho phan tu DAU TIEN trong danh sach selector hien ra (state-based, thay cho
        sleep() cung). Tra ve locator dau tien thay duoc, hoac None neu het gio."""
        if not self._page:
            return None
        waited = 0.0
        while waited < timeout:
            await self._wait_automation_gate()
            for sel in selectors:
                try:
                    loc = self._page.locator(sel).first
                    if await loc.count() > 0 and await loc.is_visible():
                        return loc
                except Exception:
                    pass
            await asyncio.sleep(0.5)
            waited += 0.5
        return None

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
        await self._wait_automation_gate()
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

                    avatar_input = self._page.locator(
                        '[data-e2e="edit-profile-avatar-edit-icon"] input[type="file"], '
                        'div[role="dialog"] input[type="file"][accept*="image"]'
                    ).first
                    await avatar_input.wait_for(state="attached", timeout=15000)
                    trigger = await self._resolve_native_upload_trigger(
                        avatar_input, "photo"
                    )
                    await set_input_files_native(
                        avatar_input,
                        [abs_origin_path],
                        trigger=trigger,
                        timeout_ms=15000,
                    )
                    logger.info("[+] Da gan file avatar bang native chooser an.")

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
            # XU LY USERNAME (4 quy tac):
            #  A. web == db            -> khong lam gi.
            #  B. web bat dau bang db nhung co them duoi (web = db + "xxx")
            #                          -> CAP NHAT DB thanh web (tra ve username_for_db).
            #  C. web la username mac dinh cua TikTok (userXXXXX / bat dau "user")
            #                          -> DOI username tren WEB thanh db (go vao o input).
            #  D. web la username that nhung khac db
            #                          -> CAP NHAT DB thanh web.
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
                        # Rule D: username dang hien tren profile TikTok la nguon
                        # su that. Khong bo qua ten that chi vi no khong con lien
                        # quan theo tien to voi username cu da import vao DB.
                        username_for_db = web_username
                        if step_logger:
                            await step_logger(
                                f"Username web '{web_username}' khac DB '{dbu}' "
                                "-> se cap nhat DB theo username web."
                            )
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
                        # extension solver can thoi gian tu xu ly. Neu dong browser
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

    # =====================================================================
    # UPLOAD VIDEO (da verify end-to-end)
    # =====================================================================
    # Diem mau chot: input file cua TikTok KHONG set duoc bang set_input_files
    # / file_chooser tren Firefox-va (bi chan) VA inject synthetic DataTransfer
    # thi ket voi file LON (nhanh VOD/Vmok). GIAI PHAP: tu dong hoa HOP THOAI
    # CHON FILE cua Windows (click that vao "Chon video" -> dialog #32770 ->
    # dien duong dan bang win32 -> Open) => dua FILE THAT tren dia vao, chay
    # cho MOI kich thuoc y het lam tay. Captcha luc vao trang do extension
    # extension solver tu xu ly.
    async def _click_by_texts(self, texts, timeout=8000, no_wait_after=True):
        """Click phan tu dau tien khop 1 trong cac chuoi text (da/anh)."""
        for t in texts:
            try:
                loc = self._page.get_by_text(t, exact=False).first
                await loc.click(timeout=timeout, no_wait_after=no_wait_after)
                return True
            except Exception:
                continue
        return False

    async def _resolve_native_upload_trigger(self, target, media_kind: str):
        """Find the visible control a person clicks to open ``target``."""
        semantic_pattern = re.compile(
            (
                r"^\s*(Select video|Choose video|Upload video|Chọn video|Tải video lên)\s*$"
                if media_kind == "video"
                else r"^\s*(Select photos?|Choose photos?|Upload photos?|Chọn ảnh|Tải ảnh lên)\s*$"
            ),
            re.I,
        )
        semantic_candidates = []
        try:
            semantic_candidates.append(
                self._page.get_by_role("button", name=semantic_pattern).first
            )
        except Exception:
            pass
        try:
            semantic_candidates.append(self._page.get_by_text(semantic_pattern).first)
        except Exception:
            pass
        for candidate in semantic_candidates:
            try:
                if (
                    await asyncio.wait_for(candidate.count(), timeout=2.0)
                    and await asyncio.wait_for(candidate.is_visible(), timeout=2.0)
                    and await asyncio.wait_for(candidate.is_enabled(), timeout=2.0)
                ):
                    return candidate
            except Exception:
                continue

        # Compatibility fallback: derive the control linked to the file input
        # only when Studio exposes no stable role/text for its visible button.
        try:
            handle = await asyncio.wait_for(
                target.element_handle(timeout=3000), timeout=3.5
            )
            if handle is not None:
                candidate_handle = await asyncio.wait_for(
                    handle.evaluate_handle(
                        """
                        input => {
                            const linked = input.labels && input.labels.length
                                ? input.labels[0]
                                : input.closest('label');
                            if (linked) return linked;
                            let node = input.parentElement;
                            while (node && node !== document.body) {
                                const rect = node.getBoundingClientRect();
                                const style = getComputedStyle(node);
                                const role = node.getAttribute('role');
                                const clickable = node.tagName === 'BUTTON'
                                    || role === 'button'
                                    || node.tabIndex >= 0
                                    || style.cursor === 'pointer';
                                const visible = style.display !== 'none'
                                    && style.visibility !== 'hidden'
                                    && rect.width >= 8 && rect.height >= 8;
                                if (clickable && visible) return node;
                                node = node.parentElement;
                            }
                            return null;
                        }
                        """
                    ),
                    timeout=3.0,
                )
                candidate = candidate_handle.as_element()
                if (
                    candidate is not None
                    and await asyncio.wait_for(candidate.is_visible(), timeout=2.0)
                ):
                    return candidate
        except Exception:
            pass

        text = (
            r"select\s+(video|file)|choose\s+(video|file)|upload|"
            r"ch[oọ]n\s+(video|t[eệ]p|[aả]nh)|t[aả]i\s+l[eê]n"
        )
        for selector in ("label", "button", '[role="button"]'):
            try:
                candidates = self._page.locator(selector).filter(
                    has_text=re.compile(text, re.I)
                )
                count = await asyncio.wait_for(candidates.count(), timeout=2.0)
                for index in range(min(count, 12)):
                    candidate = candidates.nth(index)
                    if (
                        await asyncio.wait_for(candidate.is_visible(), timeout=2.0)
                        and await asyncio.wait_for(candidate.is_enabled(), timeout=2.0)
                    ):
                        return candidate
            except Exception:
                continue
        raise RuntimeError(
            f"Khong tim thay nut chon {media_kind} dang hien thi cho file input."
        )

    async def _wait_media_input_accepted(
        self,
        target,
        expected_count: int,
        timeout_seconds: float = 15.0,
    ) -> bool:
        """Verify a native selection even when TikTok replaces its React input."""
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            try:
                actual = await target.evaluate(
                    "element => element.files.length",
                    timeout=500,
                )
                if int(actual) == expected_count:
                    return True
            except Exception:
                pass
            try:
                editor = self._page.locator(
                    '.public-DraftEditor-content, [contenteditable="true"]'
                ).first
                progress = self._page.locator('[role="progressbar"]').first
                uploading = self._page.get_by_text(
                    re.compile(r"uploading|đang tải lên", re.I)
                ).first
                if (
                    (
                        await asyncio.wait_for(editor.count(), timeout=2.0)
                        and await asyncio.wait_for(editor.is_visible(), timeout=2.0)
                    )
                    or (
                        await asyncio.wait_for(progress.count(), timeout=2.0)
                        and await asyncio.wait_for(progress.is_visible(), timeout=2.0)
                    )
                    or (
                        await asyncio.wait_for(uploading.count(), timeout=2.0)
                        and await asyncio.wait_for(uploading.is_visible(), timeout=2.0)
                    )
                ):
                    return True
            except Exception:
                pass
            await asyncio.sleep(0.25)
        return False

    async def _set_files_via_native_dialog(self, paths: List[str], media_kind: str) -> bool:
        """Open the native chooser with one video or up to 35 photos."""
        abs_paths = [os.path.abspath(os.path.expanduser(path)) for path in paths]
        if not abs_paths:
            raise ValueError("Khong co file de tai len.")
        missing = [path for path in abs_paths if not os.path.isfile(path)]
        if missing:
            raise FileNotFoundError(f"Khong tim thay file: {missing[0]}")

        # Official invisible_playwright path: attach through Playwright's file
        # input channel. This does not open an OS chooser in headed-cloaked mode.
        last_error = None
        for attempt in range(1, 2):
            try:
                inputs = self._page.locator('input[type="file"]')
                count = await asyncio.wait_for(inputs.count(), timeout=3.0)
                if not count:
                    await inputs.first.wait_for(state="attached", timeout=15000)
                    count = await asyncio.wait_for(inputs.count(), timeout=3.0)
                target = None
                for index in range(count):
                    candidate = inputs.nth(index)
                    accept = ((await candidate.get_attribute(
                        "accept", timeout=2000
                    )) or "").lower()
                    matches = (
                        media_kind == "photo"
                        and ("image" in accept or ".jpg" in accept or ".png" in accept)
                    ) or (
                        media_kind == "video"
                        and ("video" in accept or ".mp4" in accept or ".mov" in accept)
                    )
                    if matches:
                        target = candidate
                        break
                target = target or inputs.first
                handle = await asyncio.wait_for(
                    target.element_handle(timeout=5000), timeout=5.5
                )
                if handle is None:
                    raise RuntimeError("Input file da bien mat.")
                await handle.set_input_files(abs_paths, timeout=15000)
                logger.info(
                    "[Upload] Da gan %s qua Playwright input channel (lan %d).",
                    media_kind,
                    attempt,
                )
                return True
            except Exception as exc:
                last_error = exc
                # TikTok React may replace the input after accepting the file.
                # Check the editor/progress state before resolving and retrying.
                for _ in range(5):
                    await asyncio.sleep(0.6)
                    try:
                        editor = self._page.locator(
                            '.public-DraftEditor-content, [contenteditable="true"]'
                        ).first
                        progress = self._page.locator('[role="progressbar"]').first
                        uploading = self._page.get_by_text(
                            re.compile(r"uploading|đang tải lên", re.I)
                        ).first
                        accepted = (
                            (
                                await asyncio.wait_for(editor.count(), timeout=2.0)
                                and await asyncio.wait_for(editor.is_visible(), timeout=2.0)
                            )
                            or (
                                await asyncio.wait_for(progress.count(), timeout=2.0)
                                and await asyncio.wait_for(progress.is_visible(), timeout=2.0)
                            )
                            or (
                                await asyncio.wait_for(uploading.count(), timeout=2.0)
                                and await asyncio.wait_for(uploading.is_visible(), timeout=2.0)
                            )
                        )
                        if accepted:
                            logger.info("[Upload] TikTok da nhan file sau khi thay input React.")
                            return True
                    except Exception:
                        pass

        logger.warning(
            "[Upload] Playwright path channel bi patched-Firefox B178; "
            "chuyen sang native chooser DWM-cloaked. Loi: %s",
            last_error,
        )

        # B178: the real-path protocol command is still broken in firefox-21.
        # Use the helper kept in our vendored invisible_playwright build
        # source. It opens the real Windows chooser, DWM-cloaks it immediately,
        # never takes focus/clipboard, and preserves trusted input/change events
        # for files of any size.
        try:
            # pywinauto's filename edit can corrupt supplementary Unicode
            # characters (notably emoji) even though the original file exists.
            # Give only the native chooser an ASCII alias. Keep the alias until
            # the browser session ends because Firefox may read it after the
            # chooser has already closed.
            native_paths = await asyncio.to_thread(
                self._stage_native_upload_paths,
                abs_paths,
            )
            inputs = self._page.locator('input[type="file"]')
            count = await asyncio.wait_for(inputs.count(), timeout=3.0)
            target = inputs.first
            for index in range(count):
                candidate = inputs.nth(index)
                accept = ((await candidate.get_attribute(
                    "accept", timeout=2000
                )) or "").lower()
                matches = (
                    media_kind == "photo"
                    and ("image" in accept or ".jpg" in accept or ".png" in accept)
                ) or (
                    media_kind == "video"
                    and ("video" in accept or ".mp4" in accept or ".mov" in accept)
                )
                if matches:
                    target = candidate
                    break
            trigger = await self._resolve_native_upload_trigger(target, media_kind)
            await set_input_files_native(
                target,
                native_paths,
                trigger=trigger,
                allow_input_replacement=True,
                timeout_ms=15000,
            )
            if not await self._wait_media_input_accepted(target, len(abs_paths)):
                raise RuntimeError(
                    "Native chooser da dong nhung TikTok khong hien editor/progress."
                )
            logger.info(
                "[Upload] Da gan %d file %s qua native chooser DWM-cloaked.",
                len(abs_paths),
                media_kind,
            )
            return True
        except Exception as exc:
            logger.warning(
                "[Upload] Native chooser that bai: %s (%r)", exc, exc,
                exc_info=True,
            )
            return False

    def _stage_native_upload_paths(self, paths: List[str]) -> List[str]:
        """Return ASCII aliases for paths the Windows chooser cannot type.

        A same-volume hard link avoids copying large videos. If the selected
        media is on another drive, copying is the compatibility fallback. The
        staging directory is owned by this adapter and removed by ``close``.
        """
        if all(str(path).isascii() for path in paths):
            return list(paths)

        if self._temp_profile_path:
            staging_dir = os.path.join(self._temp_profile_path, "upload_staging")
            os.makedirs(staging_dir, exist_ok=True)
        else:
            staging_dir = tempfile.mkdtemp(prefix="tiktok_auto_upload_")
        self._native_upload_staging_dirs.add(staging_dir)

        staged: List[str] = []
        for index, source in enumerate(paths, start=1):
            if str(source).isascii():
                staged.append(source)
                continue
            suffix = Path(source).suffix.lower()
            alias = os.path.join(
                staging_dir,
                f"media_{index}_{uuid.uuid4().hex}{suffix}",
            )
            try:
                os.link(source, alias)
                method = "hard-link"
            except OSError:
                shutil.copy2(source, alias)
                method = "copy"
            staged.append(alias)
            logger.info(
                "[Upload] Da tao alias ASCII bang %s cho ten file Unicode.",
                method,
            )
        return staged

    async def _set_file_via_native_dialog(self, video_path: str) -> bool:
        """Compatibility wrapper for the previous video-only flow."""
        return await self._set_files_via_native_dialog([video_path], "video")

    async def _human_click(self, locator, timeout: int = 5000) -> None:
        """Approach a control with the pointer, pause briefly, then click it."""
        try:
            box = await asyncio.wait_for(locator.bounding_box(), timeout=2.5)
        except Exception:
            box = None
        if box:
            x = box["x"] + box["width"] * random.uniform(0.38, 0.62)
            y = box["y"] + box["height"] * random.uniform(0.35, 0.65)
            await self._page.mouse.move(x, y, steps=random.randint(6, 14))
            await asyncio.sleep(random.uniform(0.12, 0.32))
        await locator.click(
            timeout=timeout,
            no_wait_after=True,
            delay=random.randint(70, 160),
        )
        await asyncio.sleep(random.uniform(0.35, 0.75))

    async def _read_visible_page_text(self, timeout_ms: int = 1500) -> Optional[str]:
        """Read rendered text with Playwright's own protocol timeout.

        Do not wrap an unbounded DOM command in asyncio.wait_for: some driver
        calls do not finish cancellation and can retain the Playwright channel.
        ``inner_text(timeout=...)`` times out inside Playwright instead.
        """
        if not self._page:
            return None
        try:
            return await self._page.locator("body").inner_text(timeout=timeout_ms)
        except Exception:
            return None

    async def _dismiss_upload_popups(self, max_actions: int = 8) -> int:
        """Accept safe Studio upload coachmarks until none remain.

        TikTok can render ``Turn on`` and then ``Got it`` as consecutive
        overlays. Only these affirmative, non-publishing actions (and their
        translations) are accepted here. Generic Cancel/Continue buttons are
        excluded so this helper cannot cancel or publish content by itself.
        """
        action_patterns = (
            # ``visible_text`` is the entire BODY innerText. MULTILINE keeps
            # the exact-label safety boundary while allowing a button label
            # to match one line inside the rest of Studio's page copy.
            ("Turn on", re.compile(r"^\s*(Turn on|Enable|Bật|Bật lên)\s*$", re.I | re.M)),
            ("Got it", re.compile(r"^\s*(Got it|I understand|OK|Okay|Đã hiểu|Tôi hiểu)\s*$", re.I | re.M)),
        )
        actions = 0
        actionable_deadline = time.monotonic() + 20.0
        while actions < max(1, max_actions):
            visible_text = await self._read_visible_page_text()
            if visible_text is None:
                return actions
            selected = next(
                (
                    (action_name, pattern)
                    for action_name, pattern in action_patterns
                    if pattern.search(visible_text)
                ),
                None,
            )
            if selected is None:
                return actions

            action_name, pattern = selected
            clicked = False
            candidates = []
            try:
                candidates.append(self._page.get_by_role("button", name=pattern).first)
            except Exception:
                pass
            # Exact-text BUTTON fallback only. Never click an arbitrary parent
            # element or a generated TikTok class.
            candidates.append(
                self._page.locator("button:visible").filter(has_text=pattern).first
            )
            for button in candidates:
                try:
                    await button.click(
                        timeout=2500,
                        no_wait_after=True,
                        delay=random.randint(70, 160),
                    )
                    actions += 1
                    clicked = True
                    logger.info("[UPLOAD] Đã chấp nhận popup: %s", action_name)
                    await asyncio.sleep(random.uniform(0.35, 0.75))
                    break
                except Exception:
                    continue
            if clicked:
                continue
            if time.monotonic() >= actionable_deadline:
                raise RuntimeError(
                    "Popup Turn on/Got it đang hiện nhưng nút không bấm được sau 20 giây."
                )
            await asyncio.sleep(0.4)
        return actions

    async def _handle_upload_interruptions(
        self,
        step_logger=None,
        captcha_timeout: float = 120.0,
    ) -> int:
        """Resolve optional upload interruptions without delaying the happy path.

        Turn on/Got it are always accepted when present. CAPTCHA is only
        waited on when it is actually visible; otherwise this returns
        immediately. The integer result is non-zero when an overlay or CAPTCHA
        interrupted the current action, allowing the caller to retry safely.
        """
        async def log(message: str) -> None:
            if step_logger:
                await step_logger(message)

        interruptions = await self._dismiss_upload_popups()
        visible_text = await self._read_visible_page_text()
        captcha_pattern = re.compile(
            r"drag the slider|kéo thanh trượt|ghép hình|verify to continue|"
            r"xác minh để tiếp tục|slide to complete|rotate the shapes|xoay hình",
            re.I,
        )
        if visible_text is None or not captcha_pattern.search(visible_text):
            return interruptions

        interruptions = max(1, interruptions)
        await log(
            "Phát hiện CAPTCHA trong lúc upload -> tạm dừng và chờ extension xử lý..."
        )
        deadline = time.monotonic() + max(1.0, captcha_timeout)
        while time.monotonic() < deadline:
            await self._wait_automation_gate()
            await asyncio.sleep(1.0)
            current_text = await self._read_visible_page_text()
            if current_text is not None and not captcha_pattern.search(current_text):
                await log("[+] CAPTCHA đã được xử lý -> tiếp tục đúng bước đang làm.")
                await asyncio.sleep(0.7)
                # CAPTCHA can reveal a queued coachmark immediately after it
                # disappears. Clear that prerequisite before returning.
                interruptions += await self._dismiss_upload_popups()
                return interruptions
        raise RuntimeError(
            f"CAPTCHA vẫn còn sau {int(captcha_timeout)} giây; không tiếp tục thao tác phía sau."
        )

    async def _confirm_post_now_popup(self) -> Optional[bool]:
        """Confirm TikTok's final immediate-publish dialog, if it is visible.

        This publishing action is deliberately separate from the coachmark
        helper and is only called after the primary Post button was clicked.
        Returns True when clicked, False when absent, and None when the popup
        is visible but its button is not actionable yet.
        """
        pattern = re.compile(
            r"^\s*(Post now|Publish now|Đăng ngay)\s*$", re.I | re.M
        )
        visible_text = await self._read_visible_page_text()
        if visible_text is None or not pattern.search(visible_text):
            return False
        candidates = []
        try:
            candidates.append(self._page.get_by_role("button", name=pattern).first)
        except Exception:
            pass
        candidates.append(self._page.locator("button:visible").filter(has_text=pattern).first)
        popup_visible = False
        for button in candidates:
            try:
                await button.wait_for(state="visible", timeout=700)
                popup_visible = True
                await button.click(
                    timeout=2500,
                    no_wait_after=True,
                    delay=random.randint(70, 160),
                )
                logger.info("[UPLOAD] Đã xác nhận popup Post now.")
                await asyncio.sleep(random.uniform(0.35, 0.75))
                return True
            except Exception:
                continue
        return None if popup_visible else False

    async def _publish_success_visible(self) -> bool:
        """Accept only an explicit completed-publish message.

        TikTok's confirmation dialog can say that a video *will be published*.
        A loose ``published`` match treats that future-tense dialog copy as a
        success and leaves for Studio Posts before clicking ``Post now``.
        """
        visible_text = await self._read_visible_page_text()
        if visible_text is None:
            return False
        return bool(re.search(
            r"^\s*(?:"
            r"your (?:video|post) (?:has been|was) (?:posted|published)|"
            r"(?:video|post) (?:posted|published) successfully|"
            r"(?:post|publish|schedule) successful(?:ly)?|"
            r"your (?:video|post) is (?:being processed|processing)|"
            r"video submitted for review|"
            r"đã (?:đăng|xuất bản|lên lịch) thành công|"
            r"(?:video|bài đăng) đang được xử lý"
            r")\s*[.!]?\s*$",
            visible_text,
            re.I | re.M,
        ))

    async def _video_upload_entry_ready(self) -> bool:
        """Detect Studio's upload entry by semantics, then its real file input."""
        visible_text = await self._read_visible_page_text()
        if visible_text is not None and re.search(
            r"\b(Select video to upload|Select video|Choose (?:a )?video(?: to upload)?|"
            r"Chọn video(?: để tải lên)?|Tải video lên)\b",
            visible_text,
            re.I,
        ):
            return True

        # Some Studio variants attach the real input without the old prompt.
        # This is a read-only check; it never clicks a probe element.
        try:
            inputs = self._page.locator('input[type="file"]')
            count = await asyncio.wait_for(inputs.count(), timeout=2.0)
            for index in range(min(count, 8)):
                accept = ((await inputs.nth(index).get_attribute(
                    "accept", timeout=1500
                )) or "").lower()
                if "video" in accept or ".mp4" in accept or ".mov" in accept:
                    return True
        except Exception:
            pass
        return False

    def _publish_button(self, scheduled: bool = False):
        """Resolve the currently rendered primary action, never a hidden clone."""
        labels = (r"Post|Publish|Đăng|Schedule|Lên lịch" if scheduled
                  else r"Post|Publish|Đăng")
        return self._page.locator("button:visible").filter(
            has_text=re.compile(rf"^\s*({labels})\s*$", re.IGNORECASE)
        ).first

    async def _publish_button_in_viewport(
        self,
        scheduled: bool = False,
        timeout_seconds: int = 20,
        step_logger=None,
    ):
        """Human-scroll Studio's nested content pane until Post is in viewport."""
        deadline = time.monotonic() + timeout_seconds
        last_error = None
        while time.monotonic() < deadline:
            await self._handle_upload_interruptions(step_logger=step_logger)
            button = self._publish_button(scheduled=scheduled)
            try:
                if not await button.count() or not await button.is_visible():
                    await asyncio.sleep(0.5)
                    continue
                box = await button.bounding_box()
                viewport = self._page.viewport_size or await self._page.evaluate(
                    "() => ({width: window.innerWidth, height: window.innerHeight})"
                )
                viewport_height = viewport["height"]
                if (box and box["y"] >= 0 and box["y"] + box["height"] <= viewport_height
                        and await button.is_enabled()):
                    return button

                # TikTok Studio uses a nested DIV scroller. Point the mouse inside
                # that pane and wheel down, instead of Playwright's unreliable
                # scrollIntoView on the off-screen button.
                scroll_point = await button.evaluate("""element => {
                  let node = element.parentElement;
                  while (node) {
                    const style = getComputedStyle(node);
                    if (/(auto|scroll)/.test(style.overflowY) && node.scrollHeight > node.clientHeight) {
                      const rect = node.getBoundingClientRect();
                      return {x: rect.left + rect.width * 0.72, y: rect.top + rect.height * 0.72};
                    }
                    node = node.parentElement;
                  }
                  return null;
                }""")
                x = scroll_point["x"] if scroll_point else viewport["width"] * 0.7
                y = scroll_point["y"] if scroll_point else viewport_height * 0.7
                x = max(5, min(x, viewport["width"] - 5))
                y = max(5, min(y, viewport_height - 5))
                await self._page.mouse.move(x, y, steps=random.randint(5, 9))
                await self._page.mouse.wheel(0, random.randint(420, 680))
                await asyncio.sleep(random.uniform(0.35, 0.65))
            except Exception as exc:
                last_error = exc
            await asyncio.sleep(0.5)
        raise RuntimeError(f"Nut Post khong vao duoc viewport: {last_error}")

    async def _fill_publish_caption(self, caption: str, step_logger=None) -> None:
        if not caption:
            return

        async def log(message: str) -> None:
            if step_logger:
                await step_logger(message)

        def normalize_caption(value: str) -> str:
            # Draft.js inserts zero-width markers around hashtag entities. They
            # are invisible in Studio but used to make the strict comparison
            # reject a caption that was actually entered correctly.
            without_markers = re.sub(r"[\u200b-\u200f\u2060\ufeff]", "", value or "")
            return " ".join(without_markers.split())

        editor = self._page.locator('.public-DraftEditor-content, [contenteditable="true"]').first
        await editor.wait_for(state="visible", timeout=30000)
        last_error = None
        for _ in range(5):
            # Studio may open an automatic-content-check coachmark immediately
            # after upload. It overlays the editor even though the editor still
            # reports visible/enabled.
            await self._handle_upload_interruptions(step_logger=step_logger)
            try:
                await editor.click(timeout=5000)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.7)
        if last_error:
            raise RuntimeError(f"Khong focus duoc o caption sau khi dong popup: {last_error}")
        # Wait for Studio's filename auto-caption hydration to settle before
        # clearing. If we clear too early, React repopulates the filename while
        # the real caption is being typed and the two strings get interleaved.
        await asyncio.sleep(0.8)
        # TikTok only turns a hashtag into a clickable entity after the user
        # types the token and selects its suggestion. Inserting the whole
        # caption in one protocol command leaves plain text instead of a tag.
        # Type ordinary text in chunks, type each hashtag sequentially to open
        # the suggestion list, then click the matching visible option/link.
        async def read_caption() -> str:
            try:
                return normalize_caption(await editor.inner_text(timeout=5000))
            except Exception:
                return ""

        async def clear_caption() -> str:
            for _ in range(2):
                await editor.click(timeout=5000)
                await self._page.keyboard.press("Control+A")
                await self._page.keyboard.press("Backspace")
                await self._page.keyboard.press("Delete")
                await asyncio.sleep(0.2)
            return await read_caption()

        async def type_chunk(value: str, *, hashtag: bool = False) -> None:
            if not value:
                return
            delay = random.randint(48, 92) if hashtag else random.randint(32, 68)
            # A fixed 20-second timeout aborts legitimate long filenames and
            # captions mid-entry. Budget for the chosen per-character delay,
            # protocol overhead and a detached/re-rendered Draft.js frame.
            timeout_ms = max(20_000, min(180_000, 10_000 + len(value) * (delay + 25)))
            await editor.press_sequentially(value, delay=delay, timeout=timeout_ms)

        expected = normalize_caption(caption)

        async def restore_plain_caption() -> bool:
            """Recover from optional rich-hashtag UI failures without losing the post."""
            for _ in range(2):
                try:
                    remaining = await clear_caption()
                    if remaining:
                        continue
                    await editor.click(timeout=5000)
                    await self._page.keyboard.insert_text(caption)
                    await asyncio.sleep(0.35)
                    if expected in await read_caption():
                        return True
                except Exception:
                    await asyncio.sleep(0.35)
            return False

        remaining = await clear_caption()
        if remaining:
            raise RuntimeError(f"Khong the xoa caption tu dong truoc khi nhap (con lai: {remaining[:100]})")
        # TikTok xác định ranh giới hashtag bằng khoảng trắng: mọi ký tự nối
        # liền sau dấu # (kể cả _, -, ., chữ có dấu...) thuộc cùng một tag.
        # Không dùng \w vì sẽ cắt sai các tag dạng ``#lol_-5I8h``.
        hashtag_re = re.compile(r"#[^\s]+", re.UNICODE)
        cursor = 0
        selected_hashtags: List[str] = []
        auto_selected_hashtags: List[str] = []
        auto_queries = []
        if (
            getattr(settings, "AUTO_HASHTAGS_ENABLED", True)
            and not hashtag_re.search(caption)
        ):
            auto_queries = hashtag_query_candidates(
                caption,
                limit=max(0, int(getattr(settings, "AUTO_HASHTAG_QUERY_LIMIT", 6))),
            )

        async def select_hashtag(
            token: str,
            *,
            allow_keyboard_fallback: bool = True,
            excluded_tokens: Optional[List[str]] = None,
        ) -> Optional[str]:
            # The upload editor exposes hashtag suggestions as a transient
            # popup.  They are not guaranteed to be links (and often have no
            # href at all), so identify the suggestion by its visible text and
            # popup semantics instead of relying on a generated URL.
            candidates = self._page.locator(
                "[role='listbox']:visible [role='option']:visible, "
                "[role='menu']:visible [role='menuitem']:visible, "
                "[role='option']:visible, "
                "[data-e2e*='hashtag' i]:visible, "
                "[data-e2e*='search' i]:visible [role='button']:visible"
            )
            # Suggestions are loaded asynchronously after the last character;
            # a single immediate DOM query races the network and misses them.
            for _ in range(12):
                try:
                    count = await candidates.count()
                except Exception:
                    count = 0
                visible_indexes: List[int] = []
                suggestion_texts: List[str] = []
                for index in range(min(count, 40)):
                    candidate = candidates.nth(index)
                    try:
                        if not await candidate.is_visible():
                            continue
                        text_value = " ".join(
                            (await candidate.inner_text(timeout=800)).split()
                        )
                        if text_value:
                            visible_indexes.append(index)
                            suggestion_texts.append(text_value)
                    except Exception:
                        continue
                choice = choose_stable_hashtag_suggestion(
                    token,
                    suggestion_texts,
                    excluded_tokens=excluded_tokens or (),
                )
                if choice is not None:
                    try:
                        candidate_index = visible_indexes[choice.source_index]
                        await candidates.nth(candidate_index).click(
                            timeout=4000,
                            no_wait_after=True,
                        )
                        logger.info(
                            "[UPLOAD] Hashtag %s -> %s (usage=%s)",
                            token,
                            choice.token,
                            choice.usage_count or "not-shown",
                        )
                        return choice.token
                    except Exception:
                        pass
                await asyncio.sleep(0.3)
            # Some Studio builds expose the popup only through keyboard
            # navigation.  Enter selects the highlighted suggestion and keeps
            # the hashtag as a rich entity in the editor.
            if allow_keyboard_fallback:
                try:
                    await self._page.keyboard.press("ArrowDown")
                    await self._page.keyboard.press("Enter")
                    await asyncio.sleep(0.25)
                    return token
                except Exception:
                    pass
            return None

        async def refocus_caption_end() -> None:
            # Clicking a suggestion moves focus into the popup. Explicitly
            # restore the Draft.js editor before inserting the next chunk;
            # otherwise subsequent text can be appended without the space
            # that TikTok inserted after the selected hashtag.
            await self._handle_upload_interruptions(step_logger=step_logger)
            await editor.click(timeout=5000)
            await self._page.keyboard.press("Control+End")

        try:
            for match in hashtag_re.finditer(caption):
                before = caption[cursor:match.start()]
                if before:
                    # Keep ordinary chunks on the Draft.js editor as real key
                    # events too. keyboard.insert_text() can restore the caret at
                    # the start of the block after a mention selection, which
                    # reverses text around the hashtag (e.g. #tagPrefix).
                    await refocus_caption_end()
                    await type_chunk(before)
                    await asyncio.sleep(random.uniform(0.08, 0.28))
                token = match.group(0)
                # TikTok opens the hashtag suggestion menu from the literal '#'
                # typed in Description. Keep the whole token as real key events;
                # filling the editor in one operation does not trigger suggestions.
                await type_chunk(token, hashtag=True)
                await asyncio.sleep(random.uniform(0.38, 0.72))
                selected_token = await select_hashtag(token)
                if selected_token:
                    selected_hashtags.append(selected_token)
                    await log(f"Đã chọn hashtag TikTok: {selected_token}")
                    await refocus_caption_end()
                else:
                    logger.warning("[UPLOAD] TikTok không hiện gợi ý hashtag cho %s; giữ nguyên text.", token)
                    await refocus_caption_end()
                cursor = match.end()
            tail = caption[cursor:]
            if tail:
                await refocus_caption_end()
                await type_chunk(tail)

            # No explicit hashtag: query TikTok Studio with conservative title
            # keywords and keep only options that its live suggestion menu returns.
            # A failed query is deleted immediately; generic #fyp/#viral tags are
            # never invented by the application.
            max_auto_hashtags = max(0, int(getattr(settings, "AUTO_HASHTAGS_MAX", 3)))
            if auto_queries and max_auto_hashtags:
                await log("Caption đã nhập xong. Đang tìm hashtag phù hợp từ TikTok...")
            for slug in auto_queries:
                if len(selected_hashtags) >= max_auto_hashtags:
                    break
                token = f"#{slug}"
                await refocus_caption_end()
                typed_query = f" {token}"
                await type_chunk(typed_query, hashtag=True)
                await asyncio.sleep(random.uniform(0.38, 0.72))
                selected_token = await select_hashtag(
                    token,
                    allow_keyboard_fallback=False,
                    excluded_tokens=selected_hashtags,
                )
                if selected_token:
                    selected_hashtags.append(selected_token)
                    auto_selected_hashtags.append(selected_token)
                    await log(f"Đã chọn hashtag TikTok: {selected_token}")
                    await refocus_caption_end()
                    continue
                # No TikTok suggestion means this is only a guessed keyword. Remove
                # the exact query instead of publishing a low-confidence hashtag.
                await refocus_caption_end()
                for _ in range(len(typed_query)):
                    await self._page.keyboard.press("Backspace")
                await asyncio.sleep(0.15)
        except Exception as exc:
            # Hashtag suggestions are optional UI. A transient popup/editor
            # re-render must not cancel a video that already uploaded to 100%.
            logger.warning("[UPLOAD] Hashtag UI failed; restoring plain caption: %s", exc)
            await log(
                "⚠️ Gợi ý hashtag bị gián đoạn; đã khôi phục caption gốc và tiếp tục đăng."
            )
            if not await restore_plain_caption():
                raise RuntimeError(f"Khong khoi phuc duoc caption sau loi hashtag: {exc}") from exc
            selected_hashtags.clear()
            auto_selected_hashtags.clear()

        last_value = ""
        for _ in range(2):
            await asyncio.sleep(0.35)
            try:
                last_value = await read_caption()
                if expected in last_value:
                    if auto_selected_hashtags and step_logger:
                        await step_logger(
                            "Đã tự chọn hashtag ổn định từ gợi ý TikTok: "
                            + " ".join(auto_selected_hashtags)
                        )
                    if selected_hashtags:
                        logger.info("[UPLOAD] Đã kích hoạt hashtag TikTok: %s", ", ".join(selected_hashtags))
                    await log(
                        "Caption và hashtag đã sẵn sàng."
                        if selected_hashtags
                        else "Caption đã sẵn sàng."
                    )
                    return
            except Exception:
                pass
            await editor.click(timeout=5000)
        if await restore_plain_caption():
            await log("⚠️ Đã nhập lại caption bằng chế độ dự phòng; tiếp tục đăng.")
            return
        raise RuntimeError(
            f"Caption khong duoc ghi nhan day du (gia tri hien tai: {last_value[:80]})"
        )

    async def _wait_publish_ready(
        self,
        timeout_seconds: int = 180,
        step_logger=None,
    ) -> bool:
        """Wait for editor + enabled primary action, stable twice."""
        stable = 0
        editor = self._page.locator('.public-DraftEditor-content, [contenteditable="true"]').first
        post = self._publish_button(scheduled=False)
        for _ in range(timeout_seconds):
            await self._wait_automation_gate()
            if await self._handle_upload_interruptions(step_logger=step_logger):
                stable = 0
                continue
            try:
                good = (
                    await editor.count() > 0
                    and await editor.is_visible()
                    and await post.count() > 0
                    and await post.is_visible()
                    and await post.is_enabled()
                )
                stable = stable + 1 if good else 0
                if stable >= 2:
                    return True
            except Exception:
                stable = 0
            await asyncio.sleep(1)
        return False

    async def _click_publish_and_confirm(self, step_logger=None, scheduled: bool = False) -> bool:
        """Use invisible_playwright's locator click and require explicit success."""
        async def log(message):
            if step_logger:
                await step_logger(message)

        self.last_publish_acknowledged = False
        await self._handle_upload_interruptions(step_logger=step_logger)
        button = await self._publish_button_in_viewport(
            scheduled=scheduled,
            timeout_seconds=60,
            step_logger=step_logger,
        )
        # Re-resolve once at the bottom and let the real locator click perform
        # the final actionability check through invisible_playwright.
        button = self._publish_button(scheduled=scheduled)
        await self._human_click(button, timeout=30000)
        await log("Đã bấm Đăng; đang chờ TikTok xác nhận...")

        reposts_after_popup = 0
        for _ in range(45):
            await self._wait_automation_gate()
            try:
                current_url = (self._page.url or "").lower()
                if "/tiktokstudio/content" in current_url:
                    self.last_publish_acknowledged = True
                    return True
                # Turn on/Got it and CAPTCHA can appear after the primary Post
                # click as well. Resolve them before interpreting any success
                # copy or looking for the optional Post now confirmation.
                accepted = await self._handle_upload_interruptions(
                    step_logger=step_logger
                )
                # Immediate posts can require a second, explicit "Post now"
                # confirmation. Check it BEFORE generic success text: dialog
                # copy can itself contain "posted/published", which is not a
                # completed post until this button has actually been clicked.
                # Never accept it for scheduled posts because that would
                # bypass the requested schedule.
                if not scheduled:
                    post_now_result = await self._confirm_post_now_popup()
                    if post_now_result is True:
                        self.last_publish_acknowledged = True
                        await log("Đã xác nhận Post now; đang chờ TikTok đăng bài...")
                        continue
                    if post_now_result is None:
                        # Popup exists but its action is still hydrating. Do not
                        # mistake dialog copy for a publish-success message.
                        await asyncio.sleep(1)
                        continue
                if await self._publish_success_visible():
                    self.last_publish_acknowledged = True
                    return True
                # A coachmark can be injected at the exact moment Post is
                # clicked. Retry Post only after accepting such a popup; never
                # repeatedly click Post while merely waiting for TikTok.
                if accepted and reposts_after_popup < 2:
                    retry_button = self._publish_button(scheduled=scheduled)
                    if (
                        await retry_button.count()
                        and await retry_button.is_visible()
                        and await retry_button.is_enabled()
                    ):
                        await asyncio.sleep(random.uniform(0.55, 1.10))
                        await self._human_click(retry_button, timeout=10000)
                        reposts_after_popup += 1
            except Exception:
                pass
            await asyncio.sleep(1)
        await log("TikTok chưa trả về xác nhận đăng bài; tác vụ được đánh dấu thất bại để tránh báo sai.")
        return False

    async def _review_before_publish(
        self,
        step_logger=None,
        min_seconds: float = 5.0,
        max_seconds: float = 9.0,
    ) -> None:
        """Leave a short visible review pause after editing and before Post."""
        async def log(message):
            if step_logger:
                await step_logger(message)

        low = max(0.0, float(min_seconds))
        high = max(low, float(max_seconds))
        deadline = time.monotonic() + random.uniform(low, high)
        await log("Đang rà soát lại caption và cài đặt trước khi đăng...")
        while time.monotonic() < deadline:
            await self._wait_automation_gate()
            await self._handle_upload_interruptions(step_logger=step_logger)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            await asyncio.sleep(min(remaining, random.uniform(0.7, 1.2)))

    async def _verify_post_in_studio(
        self,
        caption: str,
        step_logger=None,
        timeout_seconds: int = 75,
    ) -> bool:
        """Require the new caption to appear in Studio Posts.

        A publish toast or redirect is only an acknowledgement. TikTok can
        still discard a video whose bytes were not committed, so this check is
        the source of truth for the final task result.
        """
        async def log(message):
            if step_logger:
                await step_logger(message)

        expected = " ".join((caption or "").split()).strip()
        self.last_publish_distribution_status = "UNKNOWN"
        if not expected:
            await log("Khong co caption de doi chieu trong Studio Posts.")
            return False
        needle = expected[:64]
        await log("Đang chờ TikTok tự chuyển sang trang bài đăng...")
        auto_deadline = time.monotonic() + 12.0
        auto_redirected = False
        while time.monotonic() < auto_deadline:
            await self._wait_automation_gate()
            current_url = str(getattr(self._page, "url", "") or "").lower()
            if "/tiktokstudio/content" in current_url:
                auto_redirected = True
                break
            await asyncio.sleep(random.uniform(0.65, 1.05))

        if auto_redirected:
            await log(
                "TikTok đã tự chuyển sang Studio Posts; chờ video xuất hiện ổn định..."
            )
            await asyncio.sleep(random.uniform(4.0, 7.0))
        else:
            await log(
                "TikTok chưa tự chuyển trang; mở Studio Posts trong cùng phiên để xác minh..."
            )
            try:
                # Fallback only: the upload tab can become half-destroyed after
                # Post. Keep the normal auto-redirect page whenever it exists.
                old_page = self._page
                self._page = await asyncio.wait_for(self._browser.new_page(), timeout=20)
                if old_page is not None and old_page is not self._page:
                    try:
                        await asyncio.wait_for(old_page.close(), timeout=5)
                    except Exception:
                        pass
                await self.navigate_to("https://www.tiktok.com/tiktokstudio/content?lang=en")
            except Exception as exc:
                logger.warning("[Upload] Khong mo duoc Studio Posts: %s", exc)
                return False

        deadline = time.monotonic() + max(10, timeout_seconds)
        next_reload = time.monotonic() + 18
        while time.monotonic() < deadline:
            await self._wait_automation_gate()
            await asyncio.sleep(3)
            try:
                match = self._page.get_by_text(needle, exact=False)
                match_count = await asyncio.wait_for(match.count(), timeout=6)
                for index in range(min(match_count, 5)):
                    matched = match.nth(index)
                    if await asyncio.wait_for(matched.is_visible(), timeout=6):
                        nearby_text = ""
                        try:
                            nearby_text = await matched.evaluate(
                                r"""element => {
                                  let node = element;
                                  for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) {
                                    const text = (node.innerText || '').replace(/\s+/g, ' ').trim();
                                    if (text.length >= 20 && text.length <= 1600 &&
                                        /under review|being reviewed|not eligible|ineligible|xét duyệt|kiểm duyệt|đủ điều kiện/i.test(text)) {
                                      return text;
                                    }
                                  }
                                  return '';
                                }"""
                            )
                        except Exception:
                            nearby_text = ""
                        distribution = _classify_distribution_text(nearby_text)
                        self.last_publish_distribution_status = distribution
                        await log("Da xac minh video xuat hien trong Studio Posts.")
                        if distribution == "FYF_INELIGIBLE":
                            await log("⚠ TikTok ghi rõ bài không đủ điều kiện xuất hiện trên For You; cần mở Analytics để xem lý do/kháng nghị.")
                        elif distribution == "UNDER_REVIEW":
                            await log("⏳ Bài đã đăng nhưng TikTok đang xét duyệt; chưa được kết luận là bị hạn chế phân phối.")
                        return True

                # Some Studio versions split captions across nested spans.
                body_text = await asyncio.wait_for(
                    self._page.locator("body").inner_text(timeout=5000), timeout=7
                )
                normalized_body = " ".join(body_text.split()).casefold()
                if needle.casefold() in normalized_body:
                    self.last_publish_distribution_status = "PUBLISHED"
                    await log("Da xac minh video xuat hien trong Studio Posts.")
                    return True
            except Exception:
                pass

            if time.monotonic() >= next_reload:
                try:
                    await self._page.reload(wait_until="domcontentloaded", timeout=30000)
                except Exception:
                    pass
                next_reload = time.monotonic() + 18

        await log(
            "Chưa thấy video trong Studio Posts sau thời gian xác minh; "
            "đánh dấu thất bại để tránh báo thành công sai."
        )
        return False

    async def collect_studio_analytics(self, step_logger=None) -> Dict[str, Any]:
        """Capture structured TikTok Studio JSON and reject guessed DOM numbers."""
        from app.use_cases.analytics.tiktok_analytics_sync import extract_studio_video_metrics

        async def log(message: str) -> None:
            if step_logger:
                await step_logger(message)

        payloads: List[Any] = []
        capture_tasks: set = set()
        json_urls: List[str] = []
        capture_errors: List[str] = []

        async def capture_response(response) -> None:
            try:
                url = (response.url or "").lower()
                # TikTok regularly renames Studio's private URL paths. Capture
                # every first-party JSON response, then let the strict structured
                # extractor below decide whether an object is genuinely a video.
                if "tiktok.com" not in url:
                    return
                headers = await response.all_headers()
                content_type = str(headers.get("content-type", "")).lower()
                if "json" not in content_type:
                    return
                body = await asyncio.wait_for(response.json(), timeout=6)
                if isinstance(body, (dict, list)):
                    payloads.append(body)
                    if len(json_urls) < 20:
                        json_urls.append(url[:240])
            except Exception as exc:
                if len(capture_errors) < 5:
                    capture_errors.append(f"{type(exc).__name__}: {str(exc)[:120]}")
                return

        def on_response(response) -> None:
            task = asyncio.create_task(capture_response(response))
            capture_tasks.add(task)
            task.add_done_callback(capture_tasks.discard)

        async def read_semantic_studio_table() -> List[Dict[str, Any]]:
            """Read TikTok's Posts table by column names, never CSS classes."""
            try:
                rows = await self._page.evaluate("""() => {
                  const text = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                  const lower = (value) => text(value).toLowerCase();
                  const metric = (value) => {
                    const raw = text(value).replace(/,/g, '').toUpperCase();
                    const match = raw.match(/(\d+(?:\.\d+)?)\s*([KMB])?/);
                    if (!match) return null;
                    const scale = match[2] === 'K' ? 1e3 : match[2] === 'M' ? 1e6 : match[2] === 'B' ? 1e9 : 1;
                    return Math.max(0, Math.round(Number(match[1]) * scale));
                  };
                  const hash = (value) => {
                    let result = 2166136261;
                    for (let i = 0; i < value.length; i++) {
                      result ^= value.charCodeAt(i);
                      result = Math.imul(result, 16777619);
                    }
                    return (result >>> 0).toString(16).padStart(8, '0');
                  };
                  const output = [];
                  const containers = [...document.querySelectorAll('table, [role="table"], [role="grid"]')];
                  if (!containers.length && document.querySelector('[role="columnheader"]')) containers.push(document);
                  for (const table of containers) {
                    const headers = [...table.querySelectorAll('thead th, [role="columnheader"]')].map((node) => lower(node.innerText));
                    const find = (names) => headers.findIndex((header) => names.some((name) => header.includes(name)));
                    const postIndex = find(['post', 'video', 'content', 'bài đăng', 'nội dung']);
                    const viewIndex = find(['view', 'lượt xem']);
                    const likeIndex = find(['like', 'lượt thích']);
                    const commentIndex = find(['comment', 'bình luận']);
                    const shareIndex = find(['share', 'chia sẻ']);
                    if (postIndex < 0 || viewIndex < 0 || (likeIndex < 0 && commentIndex < 0)) continue;
                    const bodyRows = [...table.querySelectorAll('tbody tr, [role="row"]')]
                      .filter((row) => !row.querySelector('[role="columnheader"]'));
                    for (let rowIndex = 0; rowIndex < bodyRows.length; rowIndex++) {
                      const row = bodyRows[rowIndex];
                      let cells = [...row.querySelectorAll(':scope > td, :scope > [role="cell"], :scope > [role="gridcell"]')];
                      if (!cells.length) cells = [...row.querySelectorAll('[role="cell"], [role="gridcell"]')];
                      if (cells.length <= Math.max(postIndex, viewIndex)) continue;
                      const postCell = cells[postIndex];
                      const hrefs = [...row.querySelectorAll('a[href]')].map((node) => node.href);
                      const href = hrefs.find((value) => /\/video\/\d+/.test(value)) || hrefs[0] || '';
                      const idMatch = href.match(/\/video\/(\d+)/) || href.match(/[?&](?:item_id|video_id)=(\d+)/);
                      const lines = String(postCell.innerText || '').split(/\\r?\\n/).map(text).filter(Boolean);
                      const title = lines
                        .filter((line) => !/^\d{1,2}:\d{2}$/.test(line) && !/^(everyone|private|friends|only me)$/i.test(line))
                        .sort((a, b) => b.length - a.length)[0] || 'Không có caption';
                      const identity = idMatch ? idMatch[1] : `studio-${hash(`${title}|${rowIndex}`)}`;
                      const views = metric(cells[viewIndex]?.innerText);
                      const likes = likeIndex >= 0 ? metric(cells[likeIndex]?.innerText) : null;
                      const comments = commentIndex >= 0 ? metric(cells[commentIndex]?.innerText) : null;
                      const shares = shareIndex >= 0 ? metric(cells[shareIndex]?.innerText) : null;
                      if (views === null && likes === null && comments === null && shares === null) continue;
                      output.push({
                        video_id: identity,
                        title,
                        create_time: null,
                        view_count: views || 0,
                        like_count: likes || 0,
                        comment_count: comments || 0,
                        share_count: shares || 0,
                        cover_url: '',
                        share_url: href,
                      });
                    }
                  }

                  // TikTok Studio currently renders the Posts grid with plain
                  // divs instead of native/ARIA table elements. `data-tt` is
                  // the stable semantic contract used by the Studio bundle;
                  // class names are generated and must not be depended on.
                  const studioRoot = document.querySelector('[data-tt="components_PostTable_FlexColumn"]');
                  if (studioRoot) {
                    const postInfos = [...studioRoot.querySelectorAll('[data-tt="components_PostInfoCell_FlexRow"]')];
                    const seenRows = new Set();
                    for (let rowIndex = 0; rowIndex < postInfos.length; rowIndex++) {
                      const postInfo = postInfos[rowIndex];
                      let row = postInfo;
                      while (row && row !== studioRoot) {
                        if (row.getAttribute?.('data-tt') === 'components_RowLayout_FlexRow') break;
                        row = row.parentElement;
                      }
                      if (!row || row === studioRoot || seenRows.has(row)) continue;
                      seenRows.add(row);

                      let postBranch = postInfo;
                      while (postBranch.parentElement && postBranch.parentElement !== row) {
                        postBranch = postBranch.parentElement;
                      }
                      const metricBranch = [...row.children].find((child) => child !== postBranch) || row;
                      const metricValues = [...metricBranch.querySelectorAll('.TUXText, [data-tt*="TUXText"]')]
                        .map((node) => text(node.innerText))
                        .filter((value) => /^\d+(?:[.,]\d+)?\s*[KMB]?$/.test(value));
                      if (metricValues.length < 1) continue;

                      const titleNode = postInfo.querySelector('[data-tt="components_PostInfoCell_TruncateText"]');
                      const title = text(titleNode?.innerText) || 'Không có caption';
                      const postLines = String(postInfo.innerText || '').split(/\\r?\\n/).map(text).filter(Boolean);
                      const createdText = postLines.find((line) =>
                        /(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|tháng)\b/i.test(line)
                      ) || '';
                      const hrefs = [...postInfo.querySelectorAll('a[href]')].map((node) => node.href);
                      const href = hrefs.find((value) => /\/video\/\d+/.test(value)) || hrefs[0] || '';
                      const idMatch = href.match(/\/video\/(\d+)/) || href.match(/[?&](?:item_id|video_id)=(\d+)/);
                      const identitySeed = `${title}|${createdText}`;
                      const identity = idMatch ? idMatch[1] : `studio-${hash(identitySeed)}`;
                      let createTime = null;
                      if (/^\d{15,20}$/.test(identity)) {
                        try { createTime = Number(BigInt(identity) >> 32n); } catch (_) {}
                      }
                      if (output.some((item) => item.video_id === identity)) continue;
                      const cover = postInfo.querySelector('img[src]');
                      output.push({
                        video_id: identity,
                        title,
                        create_time: createTime,
                        view_count: metric(metricValues[0]) || 0,
                        like_count: metric(metricValues[1]) || 0,
                        comment_count: metric(metricValues[2]) || 0,
                        share_count: 0,
                        cover_url: cover?.src || '',
                        share_url: href,
                      });
                    }
                  }
                  return output;
                }""")
                return rows if isinstance(rows, list) else []
            except Exception as exc:
                logger.warning("[ANALYTICS] Khong doc duoc bang Studio semantic: %s: %s", type(exc).__name__, exc)
                return []

        try:
            # Invisible context is most stable with its existing page. Creating
            # a second page can block while the cloaked window is painting, so
            # reuse the authenticated tab and navigate it to Studio.
            if self._page is None or self._page.is_closed():
                self._page = await asyncio.wait_for(self._browser.new_page(), timeout=20)
            self._page.on("response", on_response)
            await self.navigate_to("https://www.tiktok.com/tiktokstudio/content?lang=en")
            await log("TikTok Studio đã mở; đang tải toàn bộ danh sách bài đăng...")

            stable_rounds = 0
            last_count = -1
            dom_by_id: Dict[str, Dict[str, Any]] = {}
            for _ in range(18):
                await self._wait_automation_gate()
                await asyncio.sleep(2)
                videos, saw_last_page = extract_studio_video_metrics(payloads)
                for dom_video in await read_semantic_studio_table():
                    video_id = str(dom_video["video_id"])
                    existing = dom_by_id.get(video_id)
                    if existing:
                        for key in ("view_count", "like_count", "comment_count", "share_count"):
                            dom_video[key] = max(int(existing.get(key) or 0), int(dom_video.get(key) or 0))
                    dom_by_id[video_id] = dom_video
                combined_count = len({str(row["video_id"]) for row in videos} | set(dom_by_id))
                if combined_count == last_count:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                    last_count = combined_count
                if saw_last_page and stable_rounds >= 2:
                    break
                try:
                    await self._page.evaluate("""() => {
                      window.scrollTo(0, document.documentElement.scrollHeight);
                      for (const el of document.querySelectorAll('*')) {
                        if (el.scrollHeight > el.clientHeight + 200) el.scrollTop = el.scrollHeight;
                      }
                    }""")
                except Exception:
                    pass
                try:
                    more = self._page.locator("button:visible").filter(
                        has_text=re.compile(r"load more|show more|xem thêm|next", re.IGNORECASE)
                    ).first
                    if await more.count() and await more.is_enabled():
                        await more.click(timeout=3000, no_wait_after=True)
                except Exception:
                    pass
                if stable_rounds >= 4:
                    break

            if capture_tasks:
                await asyncio.gather(*list(capture_tasks), return_exceptions=True)

            # Hydration JSON is a safe fallback because it remains structured.
            try:
                script_texts = await self._page.locator(
                    'script[type="application/json"], script#__UNIVERSAL_DATA_FOR_REHYDRATION__, script#__NEXT_DATA__'
                ).all_text_contents()
                for text_value in script_texts:
                    try:
                        parsed = json.loads(text_value)
                        if isinstance(parsed, (dict, list)):
                            payloads.append(parsed)
                    except Exception:
                        continue
            except Exception:
                pass

            videos, saw_last_page = extract_studio_video_metrics(payloads)
            used_dom_fallback = False
            if not videos and dom_by_id:
                videos = list(dom_by_id.values())
                used_dom_fallback = True
            body_text = ""
            try:
                body_text = (await self._page.locator("body").inner_text(timeout=5000)).casefold()
            except Exception:
                pass
            if any(token in body_text for token in ("captcha", "verify to continue", "xác minh")):
                return {"videos": videos, "complete": False, "error": "TikTok yêu cầu xác minh/captcha khi mở Studio."}
            empty_confirmed = not videos and any(token in body_text for token in (
                "no posts yet", "no content", "chưa có bài đăng", "không có nội dung",
            ))
            if not videos and not empty_confirmed:
                try:
                    title = await self._page.title()
                except Exception:
                    title = ""
                try:
                    dom_shape = await self._page.evaluate("""() => {
                      const nodes = [...document.querySelectorAll('body *')];
                      const header = nodes.find((node) => {
                        const own = String(node.innerText || '').replace(/\s+/g, ' ').trim().toLowerCase();
                        return own === 'posts (created on)' || own === 'bài đăng (được tạo vào)';
                      });
                      let root = header;
                      for (let i = 0; i < 3 && root?.parentElement; i++) root = root.parentElement;
                      return {
                        tables: document.querySelectorAll('table').length,
                        grids: document.querySelectorAll('[role="grid"], [role="table"]').length,
                        rows: document.querySelectorAll('[role="row"]').length,
                        dataE2e: document.querySelectorAll('[data-e2e]').length,
                        semanticPostCells: document.querySelectorAll('[data-tt="components_PostInfoCell_FlexRow"]').length,
                        snippet: String(root?.outerHTML || '').slice(0, 800),
                      };
                    }""")
                except Exception as exc:
                    dom_shape = {"error": f"{type(exc).__name__}: {exc}"}
                logger.warning(
                    "[ANALYTICS] No video JSON: url=%s title=%r payloads=%d json_urls=%s errors=%s body=%r dom=%s",
                    getattr(self._page, "url", ""), title, len(payloads), json_urls[-6:],
                    capture_errors, " ".join(body_text.split())[:350], dom_shape,
                )
            return {
                "videos": videos,
                "complete": bool((saw_last_page and not used_dom_fallback) or empty_confirmed),
                "partial_reason": (
                    "Đọc từ bảng Posts chính chủ TikTok Studio; bảng này chưa cung cấp đủ share/phân trang nên dữ liệu được đánh dấu một phần."
                    if used_dom_fallback else ""
                ),
                "error": "" if videos or empty_confirmed else "Không bắt được phản hồi JSON chứa chỉ số video.",
            }
        finally:
            try:
                if self._page:
                    self._page.remove_listener("response", on_response)
            except Exception:
                pass

    async def publish_media(
        self,
        image_paths: Optional[List[str]] = None,
        video_path: Optional[str] = None,
        caption: str = "",
        schedule_at: Optional[str] = None,
        step_logger=None,
        continue_session: bool = False,
    ) -> bool:
        """Publish photos when present; video is only a fallback."""
        if image_paths:
            return await self._upload_photos(image_paths, caption, schedule_at, step_logger)
        if not video_path:
            raise ValueError("Khong co anh hoac video de dang.")
        return await self.upload_video(
            video_path,
            caption,
            schedule_at,
            step_logger,
            continue_session=continue_session,
        )

    async def _upload_photos(
        self,
        image_paths: List[str],
        caption: str = "",
        schedule_at: Optional[str] = None,
        step_logger=None,
    ) -> bool:
        """Open the real Photos tab, upload 1-35 images, then publish."""
        async def log(message):
            if step_logger:
                await step_logger(message)

        if not 1 <= len(image_paths) <= 35:
            raise ValueError("TikTok cho phép từ 1 đến 35 ảnh mỗi bài.")

        self._consume_foryou_upload_ticket()
        await log(f"Mở TikTok Studio và chọn tab Photos ({len(image_paths)} ảnh)...")
        await self.navigate_to("https://www.tiktok.com/tiktokstudio/upload?lang=en")
        photo_tab = self._page.get_by_role("tab", name=re.compile(r"^(Photos|Ảnh)$", re.I), exact=True).first
        tab_deadline = time.monotonic() + 45.0
        while time.monotonic() < tab_deadline:
            await self._handle_upload_interruptions(step_logger=step_logger)
            try:
                if await photo_tab.count() and await photo_tab.is_visible():
                    await photo_tab.click(timeout=10000)
                    break
            except Exception:
                pass
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("Không thấy tab Photos sau khi đã xử lý CAPTCHA/popup.")

        photo_input = self._page.locator(
            'input[type="file"][accept*="image/jpeg"], input[type="file"][accept*="image/png"], input[type="file"][accept^="image/"]'
        ).first
        input_deadline = time.monotonic() + 20.0
        while time.monotonic() < input_deadline:
            await self._handle_upload_interruptions(step_logger=step_logger)
            if await photo_input.count():
                break
            await asyncio.sleep(0.5)
        else:
            raise RuntimeError("Ô upload ảnh không xuất hiện sau khi đã xử lý CAPTCHA/popup.")

        await self._handle_upload_interruptions(step_logger=step_logger)
        await log("Đang chọn ảnh bằng hộp thoại Windows...")
        if not await self._set_files_via_native_dialog(image_paths, "photo"):
            raise Exception("Không đưa được ảnh vào ô tải lên của TikTok Studio.")
        if not await self._wait_publish_ready(
            timeout_seconds=180,
            step_logger=step_logger,
        ):
            raise Exception("Ảnh chưa xử lý xong hoặc màn chỉnh sửa chưa sẵn sàng.")

        await self._handle_upload_interruptions(step_logger=step_logger)
        await self._fill_publish_caption(caption, step_logger=step_logger)
        await self._handle_upload_interruptions(step_logger=step_logger)
        if schedule_at:
            await self._handle_upload_interruptions(step_logger=step_logger)
            scheduled = await self._set_tiktok_schedule(schedule_at)
            if not scheduled:
                raise Exception("Không đặt được lịch TikTok; không tự chuyển sang đăng ngay.")
        await log("Ảnh đã sẵn sàng. Đang đăng bài...")
        return await self._click_publish_and_confirm(step_logger=step_logger, scheduled=bool(schedule_at))

    async def upload_video(self, video_path: str, caption: str = "",
                           schedule_at: Optional[str] = None, step_logger=None,
                           continue_session: bool = False) -> bool:
        """Dang 1 video len TikTok. schedule_at=None -> dang ngay; nguoc lai
        (chuoi 'YYYY-MM-DD HH:MM') -> dat lich dang qua tuy chon 'Len lich' cua
        TikTok. Tra ve True neu dang/len lich thanh cong."""
        await self._wait_automation_gate()
        page = self._page
        if not page:
            raise RuntimeError("Trinh duyet chua khoi tao.")
        if not continue_session:
            self._consume_foryou_upload_ticket()

        async def log(m):
            if step_logger:
                await step_logger(m)

        await log(
            "Quay lại trang Post video trong cùng phiên..."
            if continue_session
            else "Mở TikTok Studio Upload..."
        )
        await self.navigate_to("https://www.tiktok.com/tiktokstudio/upload?lang=en")
        page = self._page
        if not page:
            raise RuntimeError("Trang upload không còn khả dụng sau khi điều hướng.")

        # 1) CAPTCHA/popup là tùy chọn: có thì xử lý, không có thì ô upload
        # được kiểm tra ngay và luồng tiếp tục, không chờ timeout cố định.
        await log("Kiểm tra CAPTCHA/popup và chờ ô upload...")
        file_ready = False
        file_started = time.monotonic()
        file_deadline = file_started + 135.0
        reload_after = file_started + 40.0
        reloads = 0
        while time.monotonic() < file_deadline:
            await self._handle_upload_interruptions(step_logger=step_logger)
            current_url = (str(getattr(self._page, "url", "") or "")).lower()
            if "/login" in current_url and "redirect_url" in current_url:
                raise RuntimeError(
                    "TikTok Studio yeu cau dang nhap lai; cookie hien tai khong co phien Studio hop le."
                )
            if await self._video_upload_entry_ready():
                file_ready = True
                break
            # A transient Studio response can leave a blank shell that never
            # hydrates. Preserve cookies/profile and retry only this navigation.
            if time.monotonic() >= reload_after and reloads < 2:
                reloads += 1
                await log(
                    f"Trang upload chưa sẵn sàng; tải lại trong cùng phiên "
                    f"(lần {reloads}/2)..."
                )
                await self.navigate_to(
                    "https://www.tiktok.com/tiktokstudio/upload?lang=en"
                )
                reload_after = time.monotonic() + 40.0
            await asyncio.sleep(1)
        if not file_ready:
            current_url = str(getattr(self._page, "url", "") or "")
            raise Exception(
                "Ô upload không xuất hiện sau 2 lần tải lại "
                f"(URL cuối: {current_url[:180]})."
            )

        # 2) Dua FILE THAT vao qua hop thoai Windows.
        await self._handle_upload_interruptions(step_logger=step_logger)
        await log("Chọn video (hộp thoại Windows)...")
        self._stream_suspended = True
        try:
            file_attached = await self._set_file_via_native_dialog(video_path)
        finally:
            self._stream_suspended = False
        if not file_attached:
            raise Exception("Không đưa được video vào ô upload (native dialog).")
        await self._handle_upload_interruptions(step_logger=step_logger)

        # 3) Cho video UPLOAD LEN SERVER XONG (progress ~100%) roi moi cho dang.
        #    ============================ QUAN TRONG ============================
        #    TikTok BAT nut 'Đăng' NGAY khi upload moi 0%. Neu bam Dang som (khi
        #    byte video chua len xong VOD/CDN), frontend hien toast 'Video
        #    published' + redirect NHUNG backend KHONG luu -> KHONG co video nao
        #    len account (profile & Studio Posts trong rong). Da kiem chung
        #    07/08/2026: progress chay 0%->99% mat ~25s cho file 19MB.
        #    => PHAI doi progress bar dat ~100% / bien mat truoc khi dang.
        await log("Đang tải video lên máy chủ TikTok...")
        ready = False
        reached_high = False       # da tung thay progress >= 95%
        done_streak = 0
        editor = page.locator('.public-DraftEditor-content, [contenteditable="true"]').first
        post_button = self._publish_button()
        progress = page.locator('[role="progressbar"]').first
        uploading_text = page.get_by_text(re.compile(r"uploading|đang tải lên", re.I)).first
        failure_text = page.get_by_text(
            re.compile(r"upload failed|tải lên thất bại|failed to upload|network error|please try again|đã xảy ra lỗi", re.I)
        ).first
        for i in range(140):       # ~420s cho file lon + mang cham
            await asyncio.sleep(3)
            if await self._handle_upload_interruptions(step_logger=step_logger):
                done_streak = 0
            has_bar = await progress.count() > 0 and await progress.is_visible()
            progress_value = None
            if has_bar:
                raw_value = await progress.get_attribute("aria-valuenow")
                try:
                    progress_value = float(raw_value) if raw_value is not None else None
                except ValueError:
                    progress_value = None
            s = {
                "cap": await editor.count() > 0 and await editor.is_visible(),
                "post": await post_button.count() > 0 and await post_button.is_enabled(),
                "hasBar": has_bar,
                "minVal": progress_value,
                "uploading": await uploading_text.count() > 0 and await uploading_text.is_visible(),
                "failed": await failure_text.count() > 0 and await failure_text.is_visible(),
            }
            if s["failed"]:
                raise Exception("TikTok báo tải video lên thất bại.")
            if s["minVal"] is not None and s["minVal"] >= 95:
                reached_high = True
            # Upload coi nhu XONG khi: da tung >=95%, gio khong con chu 'uploading',
            # va (khong con thanh progress hoac thanh da >=99%).
            upload_done = reached_high and (not s["uploading"]) and (not s["hasBar"] or (s["minVal"] is not None and s["minVal"] >= 99))
            if s["cap"] and s["post"] and upload_done:
                done_streak += 1
                if done_streak >= 2:          # on dinh ~6s moi chac chan
                    ready = True
                    break
            else:
                done_streak = 0
            # File RAT NHO: upload xong tuc thi, chua kip thay thanh progress nao.
            if s["cap"] and s["post"] and not s["hasBar"] and not s["uploading"] and not reached_high and i >= 5:
                ready = True
                break
        if not ready:
            raise Exception("Video tải lên quá lâu / chưa đạt 100% -> hủy để tránh đăng rỗng.")
        await log("Video đã tải lên xong (100%). Chuẩn bị đăng...")
        await self._handle_upload_interruptions(step_logger=step_logger)

        # 4) Caption (thay caption mac dinh lay tu ten file).
        if caption:
            await log("Điền caption...")
            await self._fill_publish_caption(caption, step_logger=step_logger)
            await self._handle_upload_interruptions(step_logger=step_logger)

        # Keep account defaults for privacy/comments/reuse. Those controls are
        # only touched when they become explicit inputs in a future UI.

        # 5) Dat lich (neu co) qua tuy chon 'Len lich' cua TikTok.
        scheduled = False
        if schedule_at:
            await self._handle_upload_interruptions(step_logger=step_logger)
            await log(f"Đặt lịch đăng: {schedule_at}...")
            scheduled = await self._set_tiktok_schedule(schedule_at)
            if not scheduled:
                raise Exception("Không đặt được lịch TikTok; không tự chuyển sang đăng ngay.")

        # Keep the completed editor visible for a short review instead of
        # clicking Post immediately after the last caption/setting action.
        await self._review_before_publish(step_logger=step_logger)

        # 6) Bam Dang/Len lich voi VONG LAP: popup phu (vd 'New editing features/
        #    Got it', 'Bật kiểm tra nội dung') hay chen vao DUNG luc bam -> chan
        #    Post. Nen: moi vong -> dismiss popup -> JS-click Post -> kiem tra da
        #    roi man upload chua; neu chua thi dismiss + click lai.
        await log("Bấm Đăng...")
        acknowledged = await self._click_publish_and_confirm(
            step_logger=step_logger, scheduled=bool(schedule_at)
        )
        if not acknowledged or schedule_at:
            return acknowledged
        verify_caption = caption or os.path.splitext(os.path.basename(video_path))[0]
        return await self._verify_post_in_studio(
            verify_caption,
            step_logger=step_logger,
        )

    async def _set_tiktok_schedule(self, schedule_at: str) -> bool:
        """Chon 'Len lich' cua TikTok + dien ngay/gio. schedule_at: 'YYYY-MM-DD HH:MM'.
        CANH BAO (da kiem chung 06/08/2026): tuy chon 'Len lich' cua TikTok bi KHOA
        voi nhieu nick (bot/moi) — radio value=schedule hien ra nhung KHONG tick duoc
        du click bang moi cach (toa do / label.click / get_by_text). Day KHONG phai
        bug code ma la han che phia TikTok. -> Dat lich TIN CAY dung ScheduledUploadService
        (hen gio phia app: toi gio thi dang ngay). Ham nay chi best-effort cho nick
        du dieu kien; that bai thi upload_video se fallback dang ngay."""
        page = self._page
        try:
            # Chon radio 'Len lich'
            await self._click_by_texts(["Lên lịch", "Schedule"], timeout=5000, no_wait_after=False)
            await asyncio.sleep(1.5)
            # Tach ngay + gio
            date_part, _, time_part = schedule_at.partition(" ")
            # Dien vao cac o input date/time neu co (TikTok dung input text tuy bien)
            filled = await page.evaluate("""(args) => {
              const [d, t] = args;
              let n = 0;
              const inputs = Array.from(document.querySelectorAll('input'));
              // heuristic: o co placeholder/aria ve gio va ngay
              inputs.forEach(i => {
                const k = ((i.placeholder||'')+' '+(i.getAttribute('aria-label')||'')).toLowerCase();
                if (/time|giờ/.test(k) && t) { i.value = t; i.dispatchEvent(new Event('input',{bubbles:true})); i.dispatchEvent(new Event('change',{bubbles:true})); n++; }
                else if (/date|ngày/.test(k) && d) { i.value = d; i.dispatchEvent(new Event('input',{bubbles:true})); i.dispatchEvent(new Event('change',{bubbles:true})); n++; }
              });
              return n;
            }""", [date_part, time_part])
            return filled > 0
        except Exception as e:
            logger.warning(f"[Upload] _set_tiktok_schedule loi: {e}")
            return False

    async def close(self) -> None:
        """Dong trinh duyet va xoa hoan toan thu muc ho so tam thoi ra khoi dia cung"""
        # Nha HWND da nhan de cua so khac co the tai su dung so hieu (khi Windows
        # cap phat lai) va tranh ro ri tap _claimed_hwnds.
        if self._hwnd is not None:
            with _hwnd_lock:
                _claimed_hwnds.discard(self._hwnd)
            self._hwnd = None
        self._window_visible = False
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

            for staging_dir in list(self._native_upload_staging_dirs):
                await asyncio.to_thread(shutil.rmtree, staging_dir, ignore_errors=True)
            self._native_upload_staging_dirs.clear()
        except Exception as e:
            logger.error(f"[-] Loi phat sinh khi dong trinh duyet va don dep: {str(e)}")
