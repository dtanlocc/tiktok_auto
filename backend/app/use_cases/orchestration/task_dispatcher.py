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

logger = logging.getLogger("TaskDispatcher")

class ConcurrentTaskDispatcher:
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
        # Khởi tạo lại Semaphore với giới hạn mới
        self.semaphore = asyncio.Semaphore(limit)
        logger.info(f"[+] Đã cập nhật giới hạn luồng chạy song song thành: {limit}")

    async def submit_task(self, account_id: str, login_method: str, avatar_folder: Optional[str] = None) -> None:
        """Gửi tác vụ vào hàng đợi kèm theo cấu hình thư mục ảnh đại diện"""
        await self._update_account_status(account_id, "QUEUED", step_desc="Đang xếp hàng...")
        await self.queue.put({
            "account_id": account_id,
            "login_method": login_method,
            "avatar_folder": avatar_folder
        })
        logger.info(f"[+] Tài khoản {account_id} đã được xếp vào hàng đợi.")

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
                login_method = task_payload["login_method"]
                avatar_folder = task_payload["avatar_folder"]

                await self.semaphore.acquire()

                # Phân phối tuần tự ảnh đại diện từ thư mục được chỉ định
                assigned_avatar = self._allocate_avatar_from_folder(avatar_folder, len(self.active_tasks))

                worker_task = asyncio.create_task(
                    self._execute_worker_with_semaphore(account_id, login_method, assigned_avatar)
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
        
        # Quét toàn bộ ảnh có đuôi phổ biến trong thư mục
        extensions = ("*.png", "*.jpg", "*.jpeg", "*.webp")
        image_files = []
        for ext in extensions:
            image_files.extend(glob.glob(os.path.join(folder_path, ext)))
            
        if not image_files:
            return None
            
        # Sắp xếp để phân bổ có tính tuần tự, tránh trùng lặp tối đa
        image_files.sort()
        assigned_image = image_files[task_index % len(image_files)]
        logger.info(f"[+] Phân bổ ảnh đại diện: {os.path.basename(assigned_image)} cho luồng số {task_index}")
        return assigned_image

    async def _execute_worker_with_semaphore(self, account_id: str, login_method: str, avatar_path: Optional[str]) -> None:
        logger.info(f"[*] Khởi chạy trình duyệt cho tài khoản: {account_id}")
        
        with Session(engine) as session:
            account_repo = SQLiteAccountRepository(session)
            proxy_repo = SQLiteProxyRepository(session)
            browser_service = InvisiblePlaywrightAdapter()
            
            async def log_step(step_desc: str):
                # Hàm callback ghi log ngắn gọn lên bảng và in log chi tiết xuống console chân trang
                await self._update_step_log(account_id, step_desc, session)
            
            use_case = TikTokLoginUseCase(
                account_repo=account_repo, 
                browser_service=browser_service,
                step_logger=log_step
            )

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
                await browser_service.initialize(proxy_config=proxy_config)
                
                # Thực thi kịch bản đăng nhập và đổi thông tin
                success = await use_case.execute(account_id, login_method, custom_avatar_path=avatar_path)
                
                if success:
                    await self._update_account_status(account_id, "LOGGED_IN", step_desc="Thành công", session=session)
                else:
                    await self._update_account_status(account_id, "ERROR", step_desc="Lỗi xác thực", session=session)

            except Exception as e:
                logger.error(f"[-] Lỗi nghiêm trọng khi thực thi tài khoản {account_id}: {str(e)}")
                # Rút gọn log lỗi hiển thị trên bảng
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
        """Cập nhật trạng thái tài khoản"""
        if not session:
            with Session(engine) as temp_session:
                repo = SQLiteAccountRepository(temp_session)
                repo.update_status(account_id, status)
                # Lưu bước chạy ngắn gọn vào DB
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
        """Ghi log ngắn gọn lên bảng và bắn log chi tiết xuống terminal"""
        repo = SQLiteAccountRepository(session)
        account = repo.get_by_id(account_id)
        if account:
            account.current_step = step_description
            repo.save(account)

        # 1. Phát WebSocket cập nhật cột tiến trình ngắn gọn trên bảng
        await ws_manager.broadcast({
            "event": "TASK_STEP_UPDATED",
            "data": {
                "id": account_id,
                "current_step": step_description
            }
        })
        
        # 2. Phát WebSocket in nhật ký chi tiết xuống console chân trang
        await ws_manager.broadcast({
            "event": "TERMINAL_LOG",
            "data": {
                "account_id": account_id,
                "username": account.username if account else "System",
                "message": step_description
            }
        })