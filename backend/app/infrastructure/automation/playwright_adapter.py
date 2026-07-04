import asyncio
import logging
import shutil
import os
from pathlib import Path
from typing import List, Dict, Any, Optional
from invisible_playwright.async_api import InvisiblePlaywright
from app.domain.ports.browser import IBrowserService
from app.core.config import settings

logger = logging.getLogger("PlaywrightAdapter")

class InvisiblePlaywrightAdapter(IBrowserService):
    def __init__(self):
        self._invisible_pw: Optional[InvisiblePlaywright] = None
        self._browser = None
        self._page = None

    async def initialize(self, proxy_config: Optional[Dict[str, Any]] = None) -> None:
        try:
            # Ánh xạ cấu hình Proxy theo đúng định dạng của Playwright
            proxy_opts = None
            if proxy_config:
                proxy_opts = {
                    "server": proxy_config.get("server"),  # socks5://... hoặc http://...
                    "username": proxy_config.get("username"),
                    "password": proxy_config.get("password")
                }

            # Khởi chạy trình duyệt tàng hình theo cấu hình Headless linh hoạt
            self._invisible_pw = InvisiblePlaywright(proxy=proxy_opts, headless=settings.BROWSER_HEADLESS)
            
            # Kích hoạt thủ công Async Context Manager để kiểm soát vòng đời đối tượng
            self._browser = await self._invisible_pw.__aenter__()
            
            # Tạo trang mới
            self._page = await self._browser.new_page()
            logger.info("[+] Khởi tạo Invisible Firefox-13 thành công.")
        except Exception as e:
            logger.error(f"[-] Không thể khởi tạo trình duyệt: {str(e)}")
            await self.close()
            raise e

    async def navigate_to(self, url: str) -> None:
        if not self._page:
            raise RuntimeError("Trình duyệt chưa được khởi tạo.")
        await self._page.goto(url, wait_until="domcontentloaded")

    async def inject_cookies(self, cookies: List[Dict[str, Any]]) -> None:
        if not self._browser:
            raise RuntimeError("Trình duyệt chưa được khởi tạo.")
        
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
        """
        Xác minh Cookies bằng bộ lọc liên kết bất biến (Language & Class Independent)
        Tìm kiếm các thẻ liên kết 'a' dẫn tới trang cá nhân có định dạng '/@username'
        """
        if not self._page:
            return False

        logger.info("[*] Đang đợi trang chủ TikTok ổn định phiên đăng nhập (tối đa 15 giây)...")
        
        # Bộ định vị bất biến: Tìm liên kết chứa ký tự '/@' (chỉ xuất hiện khi đăng nhập)
        profile_link_locator = self._page.locator('a[href*="/@"]')
        
        # Nút Đăng nhập (xuất hiện khi chưa đăng nhập)
        login_locator = self._page.locator('[data-e2e="nav-login-button"], button:has-text("Log in"), button:has-text("Đăng nhập")')

        # Vòng lặp kiểm tra thông minh mỗi giây
        for i in range(15):
            try:
                # 1. Nếu tìm thấy bất kỳ liên kết nào chứa '/@' -> Đã đăng nhập!
                if await profile_link_locator.count() > 0:
                    logger.info(f"[+] Xác minh THÀNH CÔNG sau {i+1} giây (Phát hiện liên kết trang cá nhân '/@').")
                    return True
                
                # 2. Nếu tìm thấy nút Login hiển thị rõ ràng -> Cookies đã chết!
                if await login_locator.count() > 0 and await login_locator.first.is_visible():
                    logger.warning(f"[-] Xác minh THẤT BẠI sau {i+1} giây (Phát hiện nút Log in).")
                    return False
            except Exception:
                pass
            
            # Nghỉ 1 giây rồi lặp lại kiểm tra
            await asyncio.sleep(1)

        logger.warning("[-] Quá thời gian chờ (Timeout) nhưng không thể xác minh trạng thái đăng nhập.")
        return False

    async def update_profile(
        self, 
        avatar_path: Optional[str] = None, 
        bio: Optional[str] = None,
        step_logger: Optional[Any] = None
    ) -> bool:
        """Thực thi kịch bản cập nhật hồ sơ và truyền log realtime về Web UI"""
        if not self._page:
            raise RuntimeError("Trình duyệt chưa được khởi tạo.")

        try:
            if step_logger:
                await step_logger("Đang di chuyển tới trang cá nhân TikTok...")
                
            # 1. Bấm vào nút Profile ở sidebar
            profile_btn = self._page.locator('a[href*="/@"]')
            await profile_btn.first.wait_for(state="visible", timeout=15000)
            await profile_btn.first.click()
            await asyncio.sleep(4)

            # 2. Bấm nút Edit Profile
            if step_logger:
                await step_logger("Đang mở Modal chỉnh sửa thông tin tài khoản...")
            edit_btn = self._page.locator('[data-e2e="edit-profile-entrance"]')
            await edit_btn.first.wait_for(state="visible", timeout=10000)
            await edit_btn.first.click()
            
            # Đợi cho phần tử bọc Avatar trong Modal hiển thị rõ ràng trên màn hình
            avatar_wrapper = self._page.locator('.e17raual2')
            await avatar_wrapper.first.wait_for(state="visible", timeout=15000)
            await asyncio.sleep(2)

            # 3. THAY ĐỔI AVATAR (Phương pháp JS Base64 Injection tột đỉnh của bạn)
            if avatar_path:
                try:
                    abs_origin_path = os.path.abspath(os.path.expanduser(avatar_path))
                    if not os.path.exists(abs_origin_path):
                        raise FileNotFoundError(f"Không tìm thấy file: {abs_origin_path}")

                    if step_logger:
                        await step_logger("Đang nạp ảnh avatar...")

                    import base64
                    with open(abs_origin_path, "rb") as f:
                        file_bytes = f.read()
                    file_b64 = base64.b64encode(file_bytes).decode("utf-8")
                    file_name = os.path.basename(abs_origin_path)  # Dùng tên file gốc được truyền sang
                    
                    ext = file_name.lower().split('.')[-1]
                    mime_map = {
                        'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                        'png': 'image/png', 'webp': 'image/webp',
                        'heic': 'image/heic', 'tiff': 'image/tiff'
                    }
                    mime_type = mime_map.get(ext, 'image/jpeg')

                    logger.info(f"[*] Inject file: {file_name} ({mime_type}), size: {len(file_bytes)} bytes")

                    # Thực thi tiêm mảng byte ảnh trực tiếp vào DOM của TikTok
                    injected = await self._page.evaluate(f"""
                        () => {{
                            const input = document.querySelector(
                                '[data-e2e="edit-profile-avatar-edit-icon"] input[type="file"]'
                            );
                            if (!input) return false;
                            
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
                        raise Exception("Không tìm thấy input[type='file'] trong DOM")

                    logger.info("[+] Đã inject file avatar thành công.")

                    if step_logger:
                        await step_logger("Đợi Crop Modal hiển thị...")
                    await asyncio.sleep(4) # Tăng nhẹ thời gian chờ cho React ổn định khung cắt ảnh

                    # NÂNG CẤP BẤM NÚT APPLY (Class ef1kawg9 chuẩn xác)
                    apply_btn = self._page.locator('button.ef1kawg9, button:has-text("Apply"), button:has-text("Áp dụng")')
                    await apply_btn.first.wait_for(state="visible", timeout=15000)

                    if step_logger:
                        await step_logger("Đang nhấn Apply xác nhận cắt ảnh...")
                    
                    # Thử nhấp mạnh (force=True) để vượt hiệu ứng che khuất của animation
                    try:
                        await apply_btn.first.click(force=True, timeout=5000)
                        logger.info("[+] Đã click Apply thành công bằng force=True.")
                    except Exception:
                        # Phương án dự phòng: Bắn trực tiếp sự kiện click DOM để ép nạp ảnh
                        await apply_btn.first.dispatch_event("click")
                        logger.info("[+] Đã click Apply thành công bằng dispatch_event.")
                    
                    await asyncio.sleep(4) # Đợi Crop Modal đóng và ảnh preview đồng bộ
                    logger.info("[+] Đã cập nhật avatar thành công.")

                except Exception as e3:
                    logger.error(f"[-] Lỗi Bước 3 (Avatar): {str(e3)}")
                    if step_logger:
                        await step_logger(f"[-] Lỗi thay avatar: {str(e3)}")
                    raise e3

            # 4. THAY ĐỔI BIO (Thực thi SAU KHI Avatar đã đổi xong hoàn toàn)
            if bio is not None:
                if step_logger:
                    await step_logger(f"Đang cập nhật Bio: '{bio}'...")
                bio_input = self._page.locator('[data-e2e="edit-profile-bio-input"]')
                await bio_input.first.wait_for(state="visible", timeout=10000)
                await bio_input.first.click()
                await self._page.keyboard.press("Control+A")
                await self._page.keyboard.press("Backspace")
                await bio_input.first.fill(bio)
                await asyncio.sleep(2)

            # 5. Nhấp nút "Save" tổng thể để hoàn tất lưu thay đổi
            if step_logger:
                await step_logger("Đang nhấn Save lưu toàn bộ thay đổi...")
            save_btn = self._page.locator('[data-e2e="edit-profile-save"]')
            await save_btn.first.wait_for(state="visible", timeout=10000)
            await save_btn.first.click()

            if step_logger:
                await step_logger("Đã lưu thành công!")
            await asyncio.sleep(5)
            return True

        except Exception as e:
            if step_logger:
                await step_logger(f"Lỗi thao tác sửa hồ sơ: {str(e)}")
            logger.error(f"[-] Gặp lỗi khi thao tác cập nhật thông tin hồ sơ: {str(e)}")
            return False