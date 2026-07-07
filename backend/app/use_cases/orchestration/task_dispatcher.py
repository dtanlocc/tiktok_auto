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
                seed_val = _uuid_to_seed(account_id)
                # PHÂN PHỐI ĐÚNG LUỒNG CHẠY USE CASE ĐỘC LẬP
                if task_type.startswith("LOGIN"):
                    method = task_type.split("_")[1]  # COOKIE hoặc CREDENTIAL
                    
                    use_case = TikTokLoginUseCase(
                        account_repo=account_repo, 
                        browser_service=browser_service,
                        step_logger=log_step,
                        email_service=email_service
                    )
                    await browser_service.initialize(proxy_config=proxy_config, seed=seed_val)
                    success = await use_case.execute(account_id, method)

                elif task_type == "UPDATE_PROFILE":
                    # Tác vụ đổi thông tin hoàn toàn độc lập (nạp trực tiếp Cookies sống)
                    from app.use_cases.profile.tiktok_update_profile import TikTokUpdateProfileUseCase
                    
                    use_case = TikTokUpdateProfileUseCase(
                        account_repo=account_repo,
                        browser_service=browser_service,
                        step_logger=log_step
                    )
                    success = await use_case.execute(account_id, avatar_path)

                if success:
                    await self._update_account_status(account_id, "LOGGED_IN", step_desc="Thành công", session=session)
                else:
                    await self._update_account_status(account_id, "ERROR", step_desc="Thất bại", session=session)

            except Exception as e:
                logger.error(f"[-] Lỗi nghiêm trọng khi thực thi tài khoản {account_id}: {str(e)}")
                short_error = "Lỗi kẹt"
                if "timeout" in str(e).lower():
                    short_error = "Lỗi: Timeout"
                elif "proxy" in str(e).lower() or "connection" in str(e).lower():
                    short_error = "Lỗi: Proxy kẹt"
                    
                await self._update_account_status(account_id, "ERROR", step_desc=short_error, session=session)
            finally:
                await browser_service.close()
                self.semaphore.release()
                self.active_tasks.pop(account_id, None)

    async def _update_account_status(self, account_id: str, status: str, step_desc: str = "IDLE", session: Optional[Session] = None) -> None:
        """Cập nhật trạng thái tài khoản (Đã thụt lề 4 khoảng trắng chuẩn phương thức Class)"""
        if not session:
            with Session(engine) as temp_session:
                repo = SQLiteAccountRepository(temp_session)
                repo.update_status(account_id, status)
                account = repo.get_by_id(account_id)
                if account:
                    account.current_step = step_desc
                    repo.save(account)
        else:
            repo = SQLiteAccountRepository(session)
            repo.update_status(account_id, status)
            account = repo.get_by_id(account_id)
            if account:
                account.current_step = step_desc
                repo.save(account)

        await ws_manager.broadcast({
            "event": "ACCOUNT_STATUS_CHANGED",
            "data": {
                "id": account_id,
                "status": status,
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