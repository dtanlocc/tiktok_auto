import asyncio
import logging
from typing import Dict, Any, Optional
from sqlmodel import Session

from app.core.config import settings
from app.infrastructure.database.connection import engine
from app.infrastructure.database.sqlite_repository import SQLiteAccountRepository, SQLiteProxyRepository
from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter
from app.infrastructure.websocket.socket_manager import ws_manager
from app.use_cases.auth.tiktok_login import TikTokLoginUseCase

logger = logging.getLogger("TaskDispatcher")

class ConcurrentTaskDispatcher:
    """Hệ thống điều khiển, xếp hàng và khống chế giới hạn số tab mở song song"""
    def __init__(self, max_tabs: int = settings.MAX_CONCURRENT_TABS):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.semaphore = asyncio.Semaphore(max_tabs)
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self._loop_task: Optional[asyncio.Task] = None
        self.is_running = False

    async def submit_task(self, account_id: str, login_method: str) -> None:
        """Nhận yêu cầu chạy tài khoản từ Web UI và đẩy vào hàng đợi bất đồng bộ"""
        await self._update_account_status(account_id, "QUEUED")
        
        await self.queue.put({
            "account_id": account_id,
            "login_method": login_method
        })
        logger.info(f"[+] Tài khoản {account_id} đã được đưa vào hàng đợi.")

    async def start(self) -> None:
        """Khởi động Daemon Task xử lý hàng đợi chạy ngầm"""
        if self.is_running:
            return
        self.is_running = True
        self._loop_task = asyncio.create_task(self._process_queue_loop())
        logger.info("[*] Task Dispatcher chạy ngầm đã khởi động.")

    async def stop(self) -> None:
        """Dừng an toàn hệ thống điều phối"""
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

                # Chờ cho đến khi có khe trống (slot) trong Semaphore
                await self.semaphore.acquire()

                # Tạo tác vụ chạy trình duyệt bất đồng bộ (Non-blocking)
                worker_task = asyncio.create_task(
                    self._execute_worker_with_semaphore(account_id, login_method)
                )
                self.active_tasks[account_id] = worker_task
                self.queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[-] Lỗi trong vòng lặp điều phối tác vụ: {str(e)}")

    async def _execute_worker_with_semaphore(self, account_id: str, login_method: str) -> None:
        """Thực thi tác vụ trong một luồng độc lập, áp dụng Unit of Work Pattern"""
        logger.info(f"[*] Khởi chạy trình duyệt cho tài khoản: {account_id}")
        
        # Mở Database Session riêng biệt cho luồng này để tránh Race Conditions
        with Session(engine) as session:
            account_repo = SQLiteAccountRepository(session)
            proxy_repo = SQLiteProxyRepository(session)
            
            # Khởi tạo Adapter Trình duyệt tàng hình độc lập
            browser_service = InvisiblePlaywrightAdapter()
            
            # Thiết lập hàm callback ghi nhận log tiến độ truyền tải qua WebSocket
            async def log_step(step_desc: str):
                await self._update_step_log(account_id, step_desc, session)
            
            # Khởi tạo nhân Use Case kết hợp hàm callback log_step
            use_case = TikTokLoginUseCase(
                account_repo=account_repo, 
                browser_service=browser_service,
                step_logger=log_step
            )

            try:
                # Tìm kiếm thông tin Proxy (nếu tài khoản có liên kết proxy)
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

                # Đồng bộ trạng thái RUNNING về Web UI qua WebSockets
                await self._update_account_status(account_id, "RUNNING", session=session)

                # Truyền proxy động vào trình duyệt tàng hình để thực thi đăng nhập
                await browser_service.initialize(proxy_config=proxy_config)
                
                success = await use_case.execute(account_id, login_method)
                
                if success:
                    logger.info(f"[+] Tài khoản {account_id} xử lý thành công.")
                else:
                    logger.warning(f"[-] Tài khoản {account_id} xử lý thất bại.")

            except Exception as e:
                logger.error(f"[-] Lỗi nghiêm trọng khi thực thi tài khoản {account_id}: {str(e)}")
                await self._update_account_status(account_id, "ERROR", session=session)
            finally:
                # Đảm bảo dọn dẹp trình duyệt và giải phóng slot trong Semaphore bất kể lỗi gì xảy ra
                await browser_service.close()
                self.semaphore.release()
                self.active_tasks.pop(account_id, None)

    async def _update_account_status(self, account_id: str, status: str, session: Optional[Session] = None) -> None:
        """Helper cập nhật trạng thái tài khoản đồng thời lên DB và Web UI realtime"""
        if not session:
            with Session(engine) as temp_session:
                repo = SQLiteAccountRepository(temp_session)
                repo.update_status(account_id, status)
        else:
            repo = SQLiteAccountRepository(session)
            repo.update_status(account_id, status)

        # Phát sóng trạng thái mới về Web UI Dashboard
        await ws_manager.broadcast({
            "event": "ACCOUNT_STATUS_CHANGED",
            "data": {
                "id": account_id,
                "status": status
            }
        })

    async def _update_step_log(self, account_id: str, step_description: str, session: Session) -> None:
        """Cập nhật bước chạy hiện tại lên cơ sở dữ liệu và truyền trực tiếp về console của Web UI"""
        # 1. Ghi nhận vào DB
        repo = SQLiteAccountRepository(session)
        account = repo.get_by_id(account_id)
        if account:
            account.current_step = step_description
            repo.save(account)

        # 2. Truyền tin realtime qua WebSocket về bảng điều khiển
        await ws_manager.broadcast({
            "event": "TASK_STEP_UPDATED",
            "data": {
                "id": account_id,
                "current_step": step_description
            }
        })
        
        # Đồng thời bắn tin nhắn log vào terminal console chung ở chân trang
        await ws_manager.broadcast({
            "event": "TERMINAL_LOG",
            "data": {
                "account_id": account_id,
                "username": account.username if account else "System",
                "message": step_description
            }
        })