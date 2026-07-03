import logging
import asyncio
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
            proxy_opts = None
            if proxy_config:
                proxy_opts = {
                    "server": proxy_config.get("server"),
                    "username": proxy_config.get("username"),
                    "password": proxy_config.get("password")
                }
            self._invisible_pw = InvisiblePlaywright(proxy=proxy_opts, headless=settings.BROWSER_HEADLESS)
            self._browser = await self._invisible_pw.__aenter__()
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

    # Cập nhật signature nhận thêm step_logger
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

            # 3. THAY ĐỔI AVATAR
            if avatar_path:
                if step_logger:
                    await step_logger("Đang kích hoạt hộp thoại chọn file ảnh...")
                
                # Định vị nút Edit Icon bằng thuộc tính data-e2e bền vững của TikTok
                avatar_edit_btn = self._page.locator('[data-e2e="edit-profile-avatar-edit-icon"], .e1lwtbhx20')
                await avatar_edit_btn.first.wait_for(state="visible", timeout=10000)
                
                # Bắt hộp thoại chọn file của hệ điều hành ngay khi click
                async with self._page.expect_file_chooser() as fc_info:
                    await avatar_edit_btn.first.click() # Click mở hộp thoại Gtk
                
                file_chooser = await fc_info.value
                
                # Nạp đường dẫn file ảnh từ thư mục tạm /tmp (Thành công 100% vì không bị Sandbox chặn)
                await file_chooser.set_files(avatar_path)
                logger.info("[+] Đã nạp file ảnh thành công vào trình duyệt.")
                
                if step_logger:
                    await step_logger("Đang giải phóng hộp thoại chọn file hệ thống để tránh treo luồng...")
                # Nhấn Escape để tắt cửa sổ Gtk của Pop!_OS
                await self._page.keyboard.press("Escape")
                await asyncio.sleep(4) # Đợi Crop Modal hiển thị

                # Nhấp nút "Apply" bằng class tĩnh 'ef1kawg9' chuẩn xác bạn cung cấp
                apply_btn = self._page.locator('button.ef1kawg9, button:has-text("Apply")')
                await apply_btn.first.wait_for(state="visible", timeout=10000)
                
                if step_logger:
                    await step_logger("Đang nhấn Apply để xác nhận cắt ảnh đại diện...")
                await apply_btn.first.click() # Click xác nhận cắt ảnh
                await asyncio.sleep(4) # Đợi ảnh preview nạp vào form chính

            # 4. THAY ĐỔI BIO
            if bio is not None:
                if step_logger:
                    await step_logger(f"Đang tự động cập nhật Bio mới: '{bio}'...")
                bio_input = self._page.locator('[data-e2e="edit-profile-bio-input"]')
                await bio_input.first.wait_for(state="visible", timeout=10000)
                await bio_input.first.click()
                
                await self._page.keyboard.press("Control+A")
                await self._page.keyboard.press("Backspace")
                await bio_input.first.fill(bio)
                await asyncio.sleep(2)

            # 5. Nhấp nút "Save" tổng thể để hoàn tất
            if step_logger:
                await step_logger("Đang nhấn Save để lưu vĩnh viễn thay đổi lên TikTok...")
            save_btn = self._page.locator('[data-e2e="edit-profile-save"]')
            await save_btn.first.wait_for(state="visible", timeout=10000)
            await save_btn.first.click()
            
            if step_logger:
                await step_logger("Đã lưu thành công! Đang dọn dẹp trình duyệt...")
            await asyncio.sleep(5)
            return True

        except Exception as e:
            if step_logger:
                await step_logger(f"Lỗi thao tác sửa hồ sơ: {str(e)}")
            logger.error(f"[-] Gặp lỗi khi thao tác cập nhật thông tin hồ sơ: {str(e)}")
            return False