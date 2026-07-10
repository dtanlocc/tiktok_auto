import asyncio
import logging
import random
import shutil
import os
import uuid
from pathlib import Path
from typing import List, Dict, Any, Optional
from invisible_playwright.async_api import InvisiblePlaywright
from app.domain.ports.browser import IBrowserService
from app.core.config import settings
from app.core.exceptions import AccountBannedException

logger = logging.getLogger("PlaywrightAdapter")


class InvisiblePlaywrightAdapter(IBrowserService):
    def __init__(self):
        self._invisible_pw: Optional[InvisiblePlaywright] = None
        self._browser = None
        self._page = None
        self._temp_profile_path: Optional[str] = None

    async def initialize(self, proxy_config: Optional[Dict[str, Any]] = None, seed: Optional[int] = None) -> None:
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

            self._temp_profile_path = os.path.abspath(f"./profiles/temp_{uuid.uuid4()}")
            shutil.copytree(master_profile_dir, self._temp_profile_path)
            logger.info(f"[*] Da copy profile master -> ban tam: {self._temp_profile_path}")

            # Cung 5 pref nhu luc tao master, giu nguyen de dam bao tinh nhat quan
            firefox_prefs = {
                "extensions.autoDisableScopes": 0,
                "extensions.enabledScopes": 15,
                "extensions.startupScanScopes": 15,
                "xpinstall.signatures.required": False,
                "xpinstall.whitelist.required": False,
            }

            # Khoi chay trinh duyet tang hinh theo cau hinh goc va SEED co dinh
            self._invisible_pw = InvisiblePlaywright(
                proxy=proxy_opts,
                headless=settings.BROWSER_HEADLESS,
                humanize=True,
                seed=seed,
                profile_dir=self._temp_profile_path,
                # extra_prefs=firefox_prefs,
            )

            # LUU Y: khi co profile_dir, __aenter__() tra ve BrowserContext (khong phai Browser).
            self._browser = await self._invisible_pw.__aenter__()

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

    async def navigate_to(self, url: str) -> None:
        if not self._page:
            raise RuntimeError("Trinh duyet chua duoc khoi tao.")

        try:
            logger.info(f"[*] Dang dieu huong toi {url} (Doi tai day du trang)...")
            await self._page.goto(url, wait_until="load", timeout=30000)

        except Exception as e:
            logger.warning(f"[!] Dieu huong 'load' bi timeout: {str(e)}. Tu dong chuyen sang che do nap nhanh DOM...")
            try:
                await self._page.goto(url, wait_until="domcontentloaded", timeout=15000)
            except Exception as e_fallback:
                logger.error(f"[-] That bai hoan toan khi co tai trang {url}: {str(e_fallback)}")
                raise e_fallback

    async def inject_cookies(self, cookies: List[Dict[str, Any]]) -> None:
        if not self._browser:
            raise RuntimeError("Trinh duyet chua duoc khoi tao.")

        contexts = getattr(self._browser, "contexts", [])
        if contexts:
            await contexts[0].add_cookies(cookies)
        else:
            await self._browser.add_cookies(cookies)

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

        profile_link_locator = self._page.locator('[data-e2e="profile-icon"], [data-e2e="messages-icon"], a[href*="/messages"]')
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
                    logger.info(f"[+] Xac minh THANH CONG sau {i+1} giay (Phat hien Avatar hoac Hop thu).")
                    return True

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

    async def update_profile(
        self,
        avatar_path: Optional[str] = None,
        bio: Optional[str] = None,
        step_logger: Optional[Any] = None
    ) -> bool:
        if not self._page:
            raise RuntimeError("Trinh duyet chua duoc khoi tao.")

        try:
            if step_logger:
                await step_logger("Dang di chuyen toi trang ca nhan TikTok...")

            profile_btn = self._page.locator('a[href*="/@"]')
            await profile_btn.first.wait_for(state="visible", timeout=15000)
            await profile_btn.first.click()
            await asyncio.sleep(4)

            if step_logger:
                await step_logger("Dang mo Modal chinh sua thong tin tai khoan...")
            edit_btn = self._page.locator('[data-e2e="edit-profile-entrance"]')
            await edit_btn.first.wait_for(state="visible", timeout=10000)
            await edit_btn.first.click()

            avatar_wrapper = self._page.locator('.e17raual2')
            await avatar_wrapper.first.wait_for(state="visible", timeout=15000)
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

            if bio is not None:
                if step_logger:
                    await step_logger(f"Dang cap nhat Bio: '{bio}'...")
                bio_input = self._page.locator('[data-e2e="edit-profile-bio-input"]')
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

            if step_logger:
                await step_logger("Da luu thay doi ho so thanh cong!")
            await asyncio.sleep(5)
            return True

        except Exception as e:
            if step_logger:
                await step_logger(f"Loi thao tac sua ho so: {str(e)}")
            logger.error(f"[-] Gap loi khi thao tac cap nhat thong tin ho so: {str(e)}")
            return False

    async def close(self) -> None:
        """Dong trinh duyet va xoa hoan toan thu muc ho so tam thoi ra khoi dia cung"""
        try:
            if self._invisible_pw:
                await self._invisible_pw.__aexit__(None, None, None)
                self._invisible_pw = None
                self._browser = None
                self._page = None
                logger.info("[+] Da dong phien trinh duyet va giai phong tai nguyen.")

            if self._temp_profile_path and os.path.exists(self._temp_profile_path):
                logger.info(f"[*] Dang thuc thi don dep xoa sach dia ho so tam thoi: {self._temp_profile_path}")
                shutil.rmtree(self._temp_profile_path, ignore_errors=True)
                self._temp_profile_path = None
        except Exception as e:
            logger.error(f"[-] Loi phat sinh khi dong trinh duyet va don dep: {str(e)}")