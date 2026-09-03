# File: backend/app/use_cases/profile/tiktok_update_profile.py
import os
import asyncio
import base64
import tempfile
import logging
import random
from pathlib import Path
from typing import Callable, Awaitable, Optional, Any

from app.domain.ports.repository import IAccountRepository
from app.domain.ports.browser import IBrowserService
from app.domain.ports.email import IEmailService
from app.use_cases.auth.login_strategies import ITikTokLoginStrategy
from app.core.exceptions import AccountBannedException  
from app.core.tiktok_urls import ensure_tiktok_english_url
from app.infrastructure.websocket.socket_manager import ws_manager

logger = logging.getLogger("TikTokUpdateProfileUseCase")

class TikTokUpdateProfileUseCase:
    """Nghiệp vụ đổi thông tin hồ sơ: Bảo vệ vĩnh viễn trạng thái PROFILE_UPDATED trong Database"""
    def __init__(
        self, 
        account_repo: IAccountRepository, 
        browser_service: IBrowserService, 
        login_strategy: ITikTokLoginStrategy,
        email_service: IEmailService,
        step_logger: Optional[Callable[[str], Awaitable[None]]] = None
    ):
        self.account_repo = account_repo
        self.browser_service = browser_service
        self.login_strategy = login_strategy
        self.email_service = email_service
        self.step_logger = step_logger

    async def _is_username_synced(self, account, proxy_config: Optional[dict]) -> Optional[bool]:
        """Kiểm tra username DB đã tồn tại trên TikTok chưa (fetch @db_username qua
        proxy account, tái dùng parser của health-check).
          True  = đã tồn tại (đã đồng bộ).
          False = KHÔNG tìm thấy (nick vẫn user*** chưa đổi) -> cần đồng bộ.
          None  = không rõ (WAF/lỗi) -> coi như an toàn, không làm gì."""
        import httpx
        from urllib.parse import quote
        from app.use_cases.health_check.quick_check_use_case import _classify_profile_html

        proxy_url = None
        try:
            if proxy_config and proxy_config.get("server"):
                server = str(proxy_config["server"])
                u = proxy_config.get("username")
                pw = proxy_config.get("password") or ""
                if u and "://" in server:
                    scheme, rest = server.split("://", 1)
                    proxy_url = f"{scheme}://{quote(str(u), safe='')}:{quote(str(pw), safe='')}@{rest}"
                else:
                    proxy_url = server
        except Exception:
            proxy_url = None

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            async with httpx.AsyncClient(proxy=proxy_url, headers=headers, timeout=25.0,
                                         follow_redirects=True, trust_env=False) as client:
                r = await client.get(ensure_tiktok_english_url(
                    f"https://www.tiktok.com/@{quote(account.username, safe='')}"
                ))
                result = _classify_profile_html(r.text or "", account.username)
        except Exception as e:
            logger.warning(f"[Username Sync] Lỗi pre-check @{account.username}: {str(e)[:80]}")
            return None

        if result in ("SONG_DA_TUONG_TAC", "SONG_TRANG"):
            return True
        if result == "DIE":
            return False
        return None

    async def execute(self, account_id: str, avatar_path: Optional[str] = None, proxy_config: Optional[dict] = None) -> bool:
        account = self.account_repo.get_by_id(account_id)
        if not account:
            raise ValueError("Tài khoản không tồn tại trên hệ thống.")

        # =====================================================================
        # GIÁP BẢO VỆ + ĐỒNG BỘ USERNAME:
        # Nếu đã COMPLETED (đổi avatar/bio rồi) -> kiểm tra username ĐÃ ĐỒNG BỘ chưa:
        # fetch tiktok.com/@db_username (qua proxy account).
        #   - Nếu ĐÃ tồn tại (hoặc không rõ) -> bỏ qua như cũ.
        #   - Nếu KHÔNG tồn tại (nick vẫn là user*** do đổi tên hụt) -> đăng nhập lại
        #     CHỈ để ĐỔI USERNAME đồng bộ (không làm lại avatar/bio).
        # =====================================================================
        username_only = False
        if account.profile_status == "COMPLETED":
            synced = await self._is_username_synced(account, proxy_config)
            if synced is not False:
                # True = đã đồng bộ; None = không rõ (WAF/lỗi) -> an toàn bỏ qua.
                if self.step_logger:
                    await self.step_logger("[!] Đã COMPLETED, username đã đồng bộ (hoặc không rõ) → bỏ qua.")
                logger.info(f"[*] [Guard] {account.username}: COMPLETED + username synced/unknown -> skip.")
                return True
            # synced == False -> @db_username KHÔNG tồn tại -> cần đổi username đồng bộ.
            if self.step_logger:
                await self.step_logger(f"[!] Đã COMPLETED nhưng @{account.username} chưa tồn tại trên TikTok → đăng nhập để ĐỔI USERNAME đồng bộ...")
            logger.info(f"[*] [Username Sync] {account.username} chua ton tai -> se dang nhap doi username.")
            username_only = True

        try:
            if self.step_logger:
                await self.step_logger("Đang khởi động môi trường trình duyệt tàng hình...")

            # 1. Ép chạy luồng đăng nhập (Form OTP hoặc Cookie)
            login_success = await self.login_strategy.login(
                self.browser_service,
                account,
                step_logger=self.step_logger,
                email_service=self.email_service
            )

            if not login_success:
                if self.step_logger:
                    await self.step_logger("[-] Đăng nhập xác thực thất bại. Không thể tiến hành đổi thông tin.")
                return False

            # =================================================================
            # NÂNG CẤP DEFENSIVE SAVE: SAO LƯU COOKIES PHÒNG THỦ NGAY LẬP TỨC
            # =================================================================
            if self.step_logger:
                await self.step_logger("[*] Đăng nhập thành công! Đang tự động lưu trữ Cookies phiên mới...")
            
            fresh_cookies = await self.browser_service.extract_cookies()
            if fresh_cookies:
                account.cookies = fresh_cookies
                account.status = "LOGGED_IN"
                account.health_status = "ALIVE"  # <-- ĐỒNG BỘ TRẠNG THÁI SỐNG Ở ĐÂY
                account.current_step = "Đã sao lưu Cookies thành công"
                self.account_repo.save(account)
                logger.info(f"[+] [Defensive Save] Đã sao lưu phòng thủ Cookies thành công cho {account.username}")

            # =================================================================
            # CHUAN BI AVATAR + BIO - CHI khi KHONG phai che do dong bo username.
            # username_only=True (acc da COMPLETED, chi thieu username) -> bo qua
            # avatar/bio (khong upload lai), truyen None de update_profile chi doi username.
            # =================================================================
            if username_only:
                test_avatar_path = None
                random_bio = None
                if self.step_logger:
                    await self.step_logger("[*] Chế độ ĐỒNG BỘ USERNAME: chỉ đổi username, không đụng avatar/bio.")
            else:
                # 2. Xử lý ảnh đại diện dự phòng nếu cần thiết
                test_avatar_path = avatar_path
                if not test_avatar_path:
                    temp_dir = tempfile.gettempdir()
                    test_avatar_path = os.path.join(temp_dir, "avatar_test.png")
                    if not os.path.exists(test_avatar_path):
                        if self.step_logger:
                            await self.step_logger("[*] Khởi tạo ảnh đại diện mẫu dự phòng...")
                        teal_png_base64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
                        with open(test_avatar_path, "wb") as f:
                            f.write(base64.b64decode(teal_png_base64))

                # 3. Đọc dữ liệu Bio ngẫu nhiên từ file bios.txt
                backend_dir = Path(__file__).resolve().parent.parent.parent.parent
                bios_file_path = backend_dir / "bios.txt"

                if not os.path.exists(bios_file_path):
                    default_bios = ["Happy Day 🚀", "Living life one code at a time 💻", "Keep moving forward ⚡"]
                    with open(bios_file_path, "w", encoding="utf-8") as f:
                        f.write("\n".join(default_bios))

                with open(bios_file_path, "r", encoding="utf-8") as f:
                    bio_lines = [line.strip() for line in f if line.strip()]

                random_bio = random.choice(bio_lines) if bio_lines else "Developer | Automation Bot v4 🚀"
                logger.info(f"[+] Đã chọn Bio ngẫu nhiên: '{random_bio}'")

            # 4. Thực thi kịch bản cập nhật hồ sơ (Đổi avatar & Bio & Username).
            # Truyền db_username để xử lý 3 quy tắc username; nhận về username_for_db
            # (khác None = cần cập nhật username DB thành giá trị đang hiển thị trên web).
            success, username_for_db = await self.browser_service.update_profile(
                avatar_path=test_avatar_path,
                bio=random_bio,
                step_logger=self.step_logger,
                db_username=account.username,
            )

            # RULE B: username web = username DB + phần đuôi -> cập nhật DB thành web.
            username_changed = False
            if username_for_db and username_for_db != account.username:
                if self.step_logger:
                    await self.step_logger(f"[*] Cập nhật username DB: '{account.username}' -> '{username_for_db}'")
                logger.info(f"[+] [Username Rule B] Cap nhat username DB: {account.username} -> {username_for_db}")
                account.username = username_for_db
                username_changed = True

            # 5. Cập nhật kết quả cuối cùng sau khi hoàn tất trọn vẹn kịch bản
            if success:
                new_cookies = await self.browser_service.extract_cookies()
                account.cookies = new_cookies
                
                # Ghi nhận trạng thái hoàn thành tối thượng vào Database
                account.status = "SUCCESS"               
                account.health_status = "ALIVE"          # <-- ĐỒNG BỘ TRẠNG THÁI SỐNG Ở ĐÂY
                account.profile_status = "COMPLETED"     
                account.current_step = "Đổi thông tin thành công"
            else:
                account.status = "LOGGED_IN"
                account.health_status = "ALIVE"          # <-- ĐỒNG BỘ TRẠNG THÁI SỐNG Ở ĐÂY
                account.current_step = "Lỗi đổi thông tin (Cookies đã bảo toàn)"

            displaced_account = None
            if username_changed:
                _, displaced_account = self.account_repo.save_prioritizing_username(account)
            else:
                self.account_repo.save(account)

            if displaced_account:
                logger.warning(
                    "[Username Sync] Uu tien %s -> %s; chuyen %s -> %s",
                    account.email,
                    account.username,
                    displaced_account.email,
                    displaced_account.username,
                )
                await ws_manager.broadcast({
                    "event": "ACCOUNT_UPDATED",
                    "data": {
                        "id": displaced_account.id,
                        "username": displaced_account.username,
                    },
                })
            return success

        # =====================================================================
        # PHỄU LỌC LỖI TƯỜNG MINH: Xử lý Banned trước, Exception chung sau
        # =====================================================================
        except AccountBannedException as e_ban:
            logger.error(f"[!] Nhận diện tài khoản bị cấm (Banned) khi đổi Profile: {str(e_ban)}")
            # SỬA LỖI GÁN NHẦM FIELD: "BANNED" phải nằm ở health_status (giống
            # hệt tiktok_login.py) - "status" là field khác, dùng cho vòng đời
            # phiên chạy (RUNNING/QUEUED/SUCCESS/ERROR), không phải sức khỏe nick.
            account.status = "ERROR"
            account.health_status = "BANNED"
            account.cookies = [] # Xóa sạch cookies hỏng
            account.current_step = "Tài khoản bị Banned"
            self.account_repo.save(account)
            raise e_ban

        except Exception as e:
            logger.error(f"[-] Lỗi đổi thông tin hồ sơ: {str(e)}")
            account.current_step = "Lỗi đổi thông tin"
            self.account_repo.save(account)
            raise e
