import asyncio
import logging
import glob
import os
from typing import Dict, Any, Optional, List
from sqlmodel import Session

from app.core.config import settings
from app.infrastructure.database.connection import engine
from app.infrastructure.database.sqlite_repository import SQLiteAccountRepository, SQLiteProxyRepository
from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter
from app.infrastructure.websocket.socket_manager import ws_manager
from app.use_cases.auth.tiktok_login import TikTokLoginUseCase
import hashlib

logger = logging.getLogger("TaskDispatcher")

def _uuid_to_seed(uuid_str: str) -> int:
    """Chuyển đổi chuỗi UUID của tài khoản thành một số nguyên seed cố định"""
    if not uuid_str:
        return 42 # Fallback seed
    # Băm UUID bằng SHA-256 để đảm bảo tính phân phối đều
    hash_object = hashlib.sha256(uuid_str.encode('utf-8'))
    hex_dig = hash_object.hexdigest()
    # Lấy 8 ký tự đầu chuyển thành số nguyên (32-bit unsigned int)
    return int(hex_dig[:8], 16)

class ConcurrentTaskDispatcher:
    """Hệ thống điều phối, xếp hàng và khống chế giới hạn số luồng chạy song song"""
    def __init__(self, max_tabs: int = settings.MAX_CONCURRENT_TABS):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.max_tabs = max_tabs
        self.semaphore = asyncio.Semaphore(max_tabs)
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self._loop_task: Optional[asyncio.Task] = None
        self.is_running = False

    def set_concurrency_limit(self, limit: int) -> None:
        """Cập nhật động số luồng chạy song song từ Web UI"""
        if limit <= 0:
            return
        self.max_tabs = limit
        self.semaphore = asyncio.Semaphore(limit)
        logger.info(f"[+] Đã cập nhật giới hạn luồng chạy song song thành: {limit}")

    async def submit_task(self, account_id: str, task_type: str, avatar_folder: Optional[str] = None) -> None:
        """Gửi tác vụ vào hàng đợi kèm theo loại tác vụ (task_type)"""
        await self._update_account_status(account_id, "QUEUED", step_desc="Đang xếp hàng...")
        await self.queue.put({
            "account_id": account_id,
            "task_type": task_type,  # Ví dụ: LOGIN_COOKIE, LOGIN_CREDENTIAL, UPDATE_PROFILE
            "avatar_folder": avatar_folder
        })
        logger.info(f"[+] Tài khoản {account_id} | Tác vụ {task_type} đã được đưa vào hàng đợi.")

    async def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self._loop_task = asyncio.create_task(self._process_queue_loop())
        logger.info("[*] Task Dispatcher chạy ngầm đã khởi động.")

    async def stop(self) -> None:
        self.is_running = False
        if self._loop_task:
            self._loop_task.cancel()
        logger.info("[-] Đã dừng Task Dispatcher.")

    async def _process_queue_loop(self) -> None:
        import random  # Đảm bảo đã import thư viện random để sinh số ngẫu nhiên

        while self.is_running:
            try:
                task_payload = await self.queue.get()
                account_id = task_payload["account_id"]
                task_type = task_payload["task_type"]
                avatar_folder = task_payload["avatar_folder"]

                await self.semaphore.acquire()

                # Phân phối tuần tự ảnh đại diện từ thư mục được chỉ định (nếu là tác vụ đổi profile)
                assigned_avatar = None
                if task_type == "UPDATE_PROFILE":
                    assigned_avatar = self._allocate_avatar_from_folder(avatar_folder, len(self.active_tasks))

                worker_task = asyncio.create_task(
                    self._execute_worker_with_semaphore(account_id, task_type, assigned_avatar)
                )
                self.active_tasks[account_id] = worker_task
                self.queue.task_done()

                # =============================================================
                # CẬP NHẬT THƯƠNG MẠI: CƠ CHẾ GIÃN CÁCH THỜI GIAN (STAGGER LAUNCH)
                # =============================================================
                # Nếu vẫn còn tài khoản tiếp theo trong hàng đợi, thực hiện giãn cách ngẫu nhiên
                # để tránh CPU/RAM tăng vọt và qua mặt hệ thống quét hành vi đồng thời của TikTok
                if not self.queue.empty():
                    # Trễ ngẫu nhiên trong khoảng từ 8 đến 15 giây (bạn có thể điều chỉnh lại)
                    stagger_delay = random.uniform(30.0, 60.0)
                    logger.info(f"[*] [Stagger Delay] Đang giãn cách luồng tiếp theo trong {stagger_delay:.1f} giây...")
                    await asyncio.sleep(stagger_delay)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[-] Lỗi trong vòng lặp điều phối tác vụ: {str(e)}")

    def _allocate_avatar_from_folder(self, folder_path: Optional[str], task_index: int) -> Optional[str]:
        """Thuật toán phân phối tuần tự ảnh đại diện từ thư mục máy tính"""
        if not folder_path or not os.path.exists(folder_path):
            return None
        
        extensions = ("*.png", "*.jpg", "*.jpeg", "*.webp")
        image_files = []
        for ext in extensions:
            image_files.extend(glob.glob(os.path.join(folder_path, ext)))
            
        if not image_files:
            return None
            
        image_files.sort()
        assigned_image = image_files[task_index % len(image_files)]
        logger.info(f"[+] Phân bổ ảnh đại diện: {os.path.basename(assigned_image)} cho luồng số {task_index}")
        return assigned_image

    # Tìm kiếm hàm _execute_worker_with_semaphore của task_dispatcher.py và dán đè bằng đoạn mã sau:
    async def _execute_worker_with_semaphore(self, account_id: str, task_type: str, avatar_path: Optional[str]) -> None:
        logger.info(f"[*] Khởi chạy trình duyệt cho tài khoản: {account_id} | Tác vụ: {task_type}")
        
        with Session(engine) as session:
            account_repo = SQLiteAccountRepository(session)
            proxy_repo = SQLiteProxyRepository(session)
            browser_service = InvisiblePlaywrightAdapter()
            
            # Khởi tạo hòm thư dongvanfb chuyên dụng để sẵn sàng quét OTP
            from app.infrastructure.email.dongvan_service import DongVanEmailService
            email_service = DongVanEmailService()
            
            async def log_step(step_desc: str):
                await self._update_step_log(account_id, step_desc, session)

            try:
                # 1. Truy vấn thông tin tài khoản và cấu hình Proxy động liên kết
                account = account_repo.get_by_id(account_id)
                proxy_config = None
                if account and account.proxy_id:
                    proxy = proxy_repo.get_by_id(account.proxy_id)
                    if proxy:
                        proxy_config = {
                            "server": proxy.connection_string,
                            "username": proxy.username,
                            "password": proxy.password
                        }

                await self._update_account_status(account_id, "RUNNING", step_desc="Đang khởi chạy...", session=session)

                # 2. THỐNG NHẤT KHỞI TẠO VÒNG ĐỜI TRÌNH DUYỆT TẠI ĐÂY:
                # Đảm bảo 100% mọi tác vụ (LOGIN & UPDATE_PROFILE) đều chạy đúng Proxy cách ly và Seed vân tay cố định!
                seed_val = _uuid_to_seed(account_id)
                await browser_service.initialize(proxy_config=proxy_config, seed=seed_val)

                # 3. THỰC THI USE CASE TƯƠNG ỨNG (Không chứa lệnh khởi chạy trùng lặp ở trong nữa)
                if task_type.startswith("LOGIN"):
                    method = task_type.split("_")[1]  # COOKIE hoặc CREDENTIAL
                    
                    use_case = TikTokLoginUseCase(
                        account_repo=account_repo, 
                        browser_service=browser_service,
                        step_logger=log_step,
                        email_service=email_service
                    )
                    success = await use_case.execute(account_id, method)

                elif task_type == "UPDATE_PROFILE":
                    from app.use_cases.profile.tiktok_update_profile import TikTokUpdateProfileUseCase
                    from app.use_cases.auth.login_strategies import CredentialEmailOtpLoginStrategy
                    
                    # Tự động inject Strategy đăng nhập bằng thông tin tài khoản và OTP cho kịch bản đổi profile
                    login_strategy = CredentialEmailOtpLoginStrategy()
                    
                    use_case = TikTokUpdateProfileUseCase(
                        account_repo=account_repo,
                        browser_service=browser_service,
                        login_strategy=login_strategy,
                        email_service=email_service,
                        step_logger=log_step
                    )
                    success = await use_case.execute(account_id, avatar_path)

                if success:
                    # NÂNG CẤP ĐỘNG: Nạp lại tài khoản từ DB để lấy đúng trạng thái chuyên biệt
                    updated_account = account_repo.get_by_id(account_id)
                    final_status = updated_account.status if updated_account else "LOGGED_IN"
                    
                    # ĐỒNG BỘ SỨC KHỎE NICK: Bắt buộc truyền thêm health_status="ALIVE" khi thành công
                    await self._update_account_status(
                        account_id, 
                        final_status, 
                        step_desc="Thành công", 
                        health_status="ALIVE",      # <-- CẬP NHẬT Ở ĐÂY CHÍNH XÁC [1]
                        session=session
                    )

                    # =========================================================
                    # ĐẶC QUYỀN KIỂM THỬ THƯƠNG MẠI: GIỮ TRÌNH DUYỆT MỞ KHI LOGIN COOKIE
                    # =========================================================
                    if task_type == "LOGIN_COOKIE":
                        # Bắn thông báo hướng dẫn lên Web UI Dashboard
                        await log_step("⚠️ Trình duyệt đang được giữ lại để kiểm thử. Hãy tự tay đóng cửa sổ khi test xong.")
                        logger.info(f"[!] [Test Mode] Giữ nguyên trình duyệt hoạt động cho {account_id}. Đợi đóng thủ công...")
                        
                        try:
                            # Chờ sự kiện trang bị đóng (User click X trên trình duyệt vật lý) với timeout=0 (vô hạn)
                            if browser_service and browser_service._page:
                                await browser_service._page.wait_for_event("close", timeout=0)
                        except Exception as e_close:
                            logger.info(f"[*] Trình duyệt kiểm thử {account_id} đã được đóng: {str(e_close)}")
                else:
                    await self._update_account_status(account_id, "ERROR", step_desc="Thất bại", session=session)

            except Exception as e:
                logger.error(f"[-] Thất bại chung cuộc cho tài khoản {account_id}: {str(e)}")
                
                health_val = None
                
                # KHỚP CHUẨN XÁC NGOẠI LỆ BANNED ĐỂ GÁN TRẠNG THÁI VẬT LÝ LÀ BANNED CHUYÊN BIỆT
                if "AccountBannedException" in str(type(e)) or "banned" in str(e).lower() or "cấm vĩnh viễn" in str(e).lower():
                    status_val = "ERROR" # Kết thúc phiên chạy với nhãn lỗi
                    health_val = "BANNED" # Đánh dấu sức khỏe vĩnh viễn là Banned!
                    short_error = "Tài khoản bị Banned"
                else:
                    status_val = "ERROR"
                    short_error = "Lỗi kẹt"
                    if "timeout" in str(e).lower():
                        short_error = "Lỗi: Timeout"
                    elif "proxy" in str(e).lower() or "connection" in str(e).lower():
                        short_error = "Lỗi: Proxy kẹt"
                    
                # Gọi hàm update trạng thái đồng bộ xuống DB
                await self._update_account_status(
                    account_id, 
                    status_val, 
                    step_desc=short_error, 
                    health_status=health_val, 
                    session=session
                )

            finally:
                await browser_service.close()
                self.semaphore.release()
                self.active_tasks.pop(account_id, None)

    # BƯỚC A: NÂNG CẤP HÀM UPDATE STATUS ĐỂ CHẤP NHẬN CẬP NHẬT SỨC KHỎE
    async def _update_account_status(
        self, 
        account_id: str, 
        status: str, 
        step_desc: str = "IDLE", 
        health_status: Optional[str] = None,       # <-- THÊM THAM SỐ CẬP NHẬT SỨC KHỎE
        profile_status: Optional[str] = None,      # <-- THÊM THAM SỐ CẬP NHẬT PROFILE
        session: Optional[Session] = None
    ) -> None:
        if not session:
            with Session(engine) as temp_session:
                repo = SQLiteAccountRepository(temp_session)
                repo.update_status(account_id, status)
                account = repo.get_by_id(account_id)
                if account:
                    account.current_step = step_desc
                    if health_status:
                        account.health_status = health_status
                    if profile_status:
                        account.profile_status = profile_status
                    repo.save(account)
        else:
            repo = SQLiteAccountRepository(session)
            repo.update_status(account_id, status)
            account = repo.get_by_id(account_id)
            if account:
                account.current_step = step_desc
                if health_status:
                    account.health_status = health_status
                if profile_status:
                    account.profile_status = profile_status
                repo.save(account)

        # PHÁT TIN WEBSOCKET ĐỒNG BỘ ĐẦY ĐỦ THUỘC TÍNH MỚI LÊN WEB UI LẬP TỨC
        await ws_manager.broadcast({
            "event": "ACCOUNT_STATUS_CHANGED",
            "data": {
                "id": account_id,
                "status": status,
                "health_status": account.health_status if account else "UNKNOWN",
                "profile_status": account.profile_status if account else "PENDING",
                "current_step": step_desc
            }
        })

    async def _update_step_log(self, account_id: str, step_description: str, session: Session) -> None:
        """Ghi log ngắn gọn lên bảng và bắn log chi tiết xuống terminal (Đã thụt lề 4 khoảng trắng chuẩn phương thức Class)"""
        repo = SQLiteAccountRepository(session)
        account = repo.get_by_id(account_id)
        if account:
            account.current_step = step_description
            repo.save(account)

        await ws_manager.broadcast({
            "event": "TASK_STEP_UPDATED",
            "data": {
                "id": account_id,
                "current_step": step_description
            }
        })
        
        await ws_manager.broadcast({
            "event": "TERMINAL_LOG",
            "data": {
                "account_id": account_id,
                "username": account.username if account else "System",
                "message": step_description
            }
        })