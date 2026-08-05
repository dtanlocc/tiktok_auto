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

        # =================================================================
        # GIOI HAN DONG THOI THEO TUNG PROXY (moi proxy toi da N luong cung luc)
        # =================================================================
        # semaphore TONG (self.semaphore) chi khong che tong so luong tren MAY.
        # No KHONG biet 2 luong co dung chung 1 proxy hay khong -> co the 3-4
        # account cung dam 1 proxy -> qua tai. proxy_semaphores them 1 lop khong
        # che RIENG cho tung proxy: moi proxy_id co 1 Semaphore(PROXY_MAX_CONCURRENT).
        # Account phai gianh CA slot tong LAN slot proxy moi duoc mo browser.
        self.proxy_max_concurrent: int = max(1, getattr(settings, "PROXY_MAX_CONCURRENT", 2))
        self.proxy_semaphores: Dict[str, asyncio.Semaphore] = {}
        self._loop_task: Optional[asyncio.Task] = None
        self.is_running = False
        
        self.global_task_counter = 0

        # =================================================================
        # CO CHE TAM DUNG / TIEP TUC (Toan cuc + Tung account)
        # =================================================================
        # global_pause_event: khi "set" -> khong pause (chay binh thuong).
        # Khi ".clear()" -> MOI account dang chay se dung lai o checkpoint
        # gan nhat (xem _wait_if_paused). Khong anh huong tien trinh
        # browser dang thuc thi giua chung 1 buoc (vi asyncio khong the
        # ngat ngang 1 await dang cho page load) - pause co hieu luc tai
        # diem checkpoint TIEP THEO, thuong chi tre vai giay.
        self.global_pause_event: asyncio.Event = asyncio.Event()
        self.global_pause_event.set()

        # account_pause_events: moi account co 1 Event rieng, mac dinh "set"
        # (khong pause) ngay khi task cua no bat dau chay.
        self.account_pause_events: Dict[str, asyncio.Event] = {}

        # is_globally_paused / paused_account_ids: co dung de UI truy van
        # trang thai hien tai (vi Event khong tu expose trang thai ra ngoai
        # 1 cach tien loi cho REST API).
        self.is_globally_paused: bool = False
        self.paused_account_ids: set = set()

    def set_concurrency_limit(self, limit: int) -> None:
        """Cập nhật động số luồng chạy song song từ Web UI"""
        if limit <= 0:
            return
        self.max_tabs = limit
        self.semaphore = asyncio.Semaphore(limit)
        logger.info(f"[+] Đã cập nhật giới hạn luồng chạy song song thành: {limit}")

    def _get_proxy_semaphore(self, proxy_id: str) -> asyncio.Semaphore:
        """Lay (hoac tao lazy) semaphore RIENG cho 1 proxy_id, gioi han so luong
        chay dong thoi tren proxy do = self.proxy_max_concurrent."""
        sem = self.proxy_semaphores.get(proxy_id)
        if sem is None:
            sem = asyncio.Semaphore(self.proxy_max_concurrent)
            self.proxy_semaphores[proxy_id] = sem
        return sem

    # =====================================================================
    # DIEU KHIEN TOAN CUC: Tam dung / Tiep tuc / Dung khan cap
    # =====================================================================
    def pause_global(self) -> None:
        """Tam dung TAT CA cac account dang chay - moi task se dung lai o
        checkpoint gan nhat va cho lenh tiep tuc. Hang doi van nhan task moi
        nhung se khong duoc xu ly cho toi khi resume."""
        self.global_pause_event.clear()
        self.is_globally_paused = True
        logger.info("[*] [GLOBAL PAUSE] Da tam dung toan bo he thong.")

    def resume_global(self) -> None:
        """Tiep tuc lai toan bo he thong sau khi tam dung."""
        self.global_pause_event.set()
        self.is_globally_paused = False
        logger.info("[*] [GLOBAL RESUME] Da tiep tuc toan bo he thong.")

    async def broadcast_global_state(self) -> None:
        """Bao trang thai pause/running toan cuc hien tai len WebUI qua WebSocket."""
        await ws_manager.broadcast({
            "event": "GLOBAL_STATE_CHANGED",
            "data": self.get_global_status()
        })

    async def emergency_stop_all(self) -> None:
        """DUNG KHAN CAP: huy ngay lap tuc toan bo task dang chay (kem dong
        trinh duyet cua tung task qua except CancelledError trong worker),
        va xoa sach hang doi cac task chua kip chay. Dispatcher VAN o trang
        thai is_running=True sau khi goi ham nay, tuc la van san sang nhan
        va xu ly task MOI duoc submit sau do (khac voi stop() dung de tat
        han dispatcher luc app shutdown)."""
        # Dam bao khong co task nao dang "ket" o trang thai cho pause khi bi huy
        self.global_pause_event.set()
        self.is_globally_paused = False
        for ev in self.account_pause_events.values():
            ev.set()
        self.paused_account_ids.clear()

        cancelled_count = 0
        for account_id, task in list(self.active_tasks.items()):
            if not task.done():
                task.cancel()
                cancelled_count += 1

        # Xoa sach hang doi cac task CHUA duoc lay ra xu ly
        drained_count = 0
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
                drained_count += 1
            except asyncio.QueueEmpty:
                break

        logger.info(
            f"[!] [EMERGENCY STOP] Da huy {cancelled_count} task dang chay "
            f"va xoa {drained_count} task con trong hang doi."
        )
        await self.broadcast_global_state()

    # =====================================================================
    # DIEU KHIEN TUNG ACCOUNT: Tam dung / Tiep tuc rieng le
    # =====================================================================
    def _get_or_create_account_event(self, account_id: str) -> asyncio.Event:
        ev = self.account_pause_events.get(account_id)
        if ev is None:
            ev = asyncio.Event()
            ev.set()  # mac dinh: khong pause
            self.account_pause_events[account_id] = ev
        return ev

    def pause_account(self, account_id: str) -> None:
        """Tam dung rieng 1 account - cac account khac van chay binh thuong."""
        ev = self._get_or_create_account_event(account_id)
        ev.clear()
        self.paused_account_ids.add(account_id)
        logger.info(f"[*] [ACCOUNT PAUSE] Da tam dung rieng account {account_id}.")

    def resume_account(self, account_id: str) -> None:
        """Tiep tuc lai 1 account da bi tam dung rieng."""
        ev = self._get_or_create_account_event(account_id)
        ev.set()
        self.paused_account_ids.discard(account_id)
        logger.info(f"[*] [ACCOUNT RESUME] Da tiep tuc account {account_id}.")

    async def broadcast_account_pause_state(self, account_id: str) -> None:
        """Bao trang thai pause hien tai cua 1 account len WebUI qua WebSocket."""
        await ws_manager.broadcast({
            "event": "ACCOUNT_PAUSE_CHANGED",
            "data": {
                "id": account_id,
                "is_paused": account_id in self.paused_account_ids
            }
        })

    async def _wait_if_paused(self, account_id: str) -> None:
        """Checkpoint duoc goi tu step_logger (xem log_step trong
        _execute_worker_with_semaphore) - moi khi worker bao cao 1 buoc
        moi, no se dung o day cho toi khi CA global lan account-rieng deu
        khong con bi pause. Day la co che 'pause theo checkpoint', khong
        phai ngat ngang tuc thi giua 1 thao tac Playwright dang cho."""
        ev = self._get_or_create_account_event(account_id)
        await self.global_pause_event.wait()
        await ev.wait()

    def get_global_status(self) -> Dict[str, Any]:
        """Tra ve trang thai hien tai de frontend dong bo UI khi tai lai trang."""
        return {
            "is_running": self.is_running,
            "is_globally_paused": self.is_globally_paused,
            "paused_account_ids": list(self.paused_account_ids),
            "active_count": len(self.active_tasks),
            "queued_count": self.queue.qsize(),
        }

    async def submit_task(
        self,
        account_id: str,
        task_type: str,
        avatar_folder: Optional[str] = None,
        extra_config: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Gửi tác vụ vào hàng đợi kèm theo loại tác vụ (task_type).
        extra_config: cấu hình bổ sung tuỳ loại tác vụ - hiện dùng cho
        INTERACT_VIDEOS (mode, hashtag, duration_minutes, xác suất tym/cmt,
        danh sách câu bình luận...)."""
        await self._update_account_status(account_id, "QUEUED", step_desc="Đang xếp hàng...")
        await self.queue.put({
            "account_id": account_id,
            "task_type": task_type,  # Ví dụ: LOGIN_COOKIE, LOGIN_CREDENTIAL, UPDATE_PROFILE, INTERACT_VIDEOS
            "avatar_folder": avatar_folder,
            "extra_config": extra_config or {},
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
                extra_config = task_payload.get("extra_config") or {}

                await self.semaphore.acquire()

                # Phân phối tuần tự ảnh đại diện từ thư mục được chỉ định (nếu là tác vụ đổi profile)
                assigned_avatar = None
                if task_type == "UPDATE_PROFILE":
                    # Sử dụng bộ đếm tịnh tiến liên tục để lấy ảnh
                    assigned_avatar = self._allocate_avatar_from_folder(avatar_folder, self.global_task_counter)
                    self.global_task_counter += 1

                worker_task = asyncio.create_task(
                    self._execute_worker_with_semaphore(account_id, task_type, assigned_avatar, extra_config)
                )
                self.active_tasks[account_id] = worker_task
                self.queue.task_done()

                # =============================================================
                # CẬP NHẬT THƯƠNG MẠI: CƠ CHẾ GIÃN CÁCH THỜI GIAN (STAGGER LAUNCH)
                # =============================================================
                # Nếu vẫn còn tài khoản tiếp theo trong hàng đợi, thực hiện giãn cách ngẫu nhiên
                # để tránh CPU/RAM tăng vọt và qua mặt hệ thống quét hành vi đồng thời của TikTok
                if not self.queue.empty():
                    # Giãn cách cấu hình được qua config (STAGGER_MIN/MAX_SECONDS).
                    _lo = getattr(settings, "STAGGER_MIN_SECONDS", 30.0)
                    _hi = getattr(settings, "STAGGER_MAX_SECONDS", 60.0)
                    stagger_delay = random.uniform(_lo, max(_lo, _hi))
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
    async def _execute_worker_with_semaphore(
        self, account_id: str, task_type: str, avatar_path: Optional[str], extra_config: Optional[Dict[str, Any]] = None
    ) -> None:
        logger.info(f"[*] Khởi chạy trình duyệt cho tài khoản: {account_id} | Tác vụ: {task_type}")
        
        with Session(engine) as session:
            account_repo = SQLiteAccountRepository(session)
            proxy_repo = SQLiteProxyRepository(session)
            browser_service = InvisiblePlaywrightAdapter()
            
            # Khởi tạo hòm thư dongvanfb chuyên dụng để sẵn sàng quét OTP
            from app.infrastructure.email.dongvan_service import DongVanEmailService
            email_service = DongVanEmailService()

            # Task chup & stream anh man hinh trinh duyet ve Dashboard (neu bat).
            streamer_task: Optional[asyncio.Task] = None

            # Semaphore RIENG cua proxy account nay (neu co proxy). proxy_acquired
            # danh dau da gianh duoc slot proxy hay chua -> chi release trong finally
            # khi THUC SU da acquire (tranh over-release neu bi cancel luc dang cho).
            proxy_sem: Optional[asyncio.Semaphore] = None
            proxy_acquired: bool = False

            async def log_step(step_desc: str):
                # CHECKPOINT PAUSE: neu dang bi tam dung (toan cuc hoac rieng
                # account nay), worker se dung ngay tai day cho toi khi duoc
                # resume, truoc khi ghi log va tiep tuc buoc tiep theo.
                await self._wait_if_paused(account_id)
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

                # =========================================================
                # GIANH SLOT PROXY: moi proxy toi da self.proxy_max_concurrent
                # luong cung luc. Neu proxy dang du tai -> CHO tra slot (van
                # dang giu 1 slot TONG - chap nhan, vi so proxy thuong it hon
                # so luong tong nen day moi la nut that that su user muon).
                # =========================================================
                if account and account.proxy_id:
                    proxy_sem = self._get_proxy_semaphore(account.proxy_id)
                    if proxy_sem.locked():
                        # Proxy dang chay du proxy_max_concurrent luong -> bao cho.
                        await self._update_account_status(
                            account_id, "QUEUED",
                            step_desc=f"⏳ Proxy đang đủ {self.proxy_max_concurrent} luồng, chờ tới lượt...",
                            session=session,
                        )
                    await proxy_sem.acquire()
                    proxy_acquired = True

                await self._update_account_status(account_id, "RUNNING", step_desc="Đang khởi chạy...", session=session)

                # 2. THỐNG NHẤT KHỞI TẠO VÒNG ĐỜI TRÌNH DUYỆT TẠI ĐÂY:
                # Đảm bảo 100% mọi tác vụ (LOGIN & UPDATE_PROFILE) đều chạy đúng Proxy cách ly và Seed vân tay cố định!
                seed_val = _uuid_to_seed(account_id)
                await browser_service.initialize(proxy_config=proxy_config, seed=seed_val)

                # STREAM man hinh: bat dau chup dinh ky page hien tai va day ve
                # Dashboard qua WebSocket (event BROWSER_FRAME) de xem da luong.
                if settings.SCREEN_STREAM_ENABLED:
                    from app.infrastructure.streaming.screen_streamer import stream_browser_frames
                    _uname = account.username if account else account_id
                    streamer_task = asyncio.create_task(
                        stream_browser_frames(
                            lambda: browser_service._page,
                            account_id,
                            _uname,
                            get_hwnd=lambda: getattr(browser_service, "_hwnd", None),
                        )
                    )

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
                    from app.use_cases.auth.login_strategies import CookieThenCredentialLoginStrategy

                    # UU TIEN COOKIE: thu login bang cookie truoc (nhanh, tiet kiem OTP),
                    # chi khi cookie het han/khong co moi fallback sang Credential+OTP.
                    login_strategy = CookieThenCredentialLoginStrategy()
                    
                    use_case = TikTokUpdateProfileUseCase(
                        account_repo=account_repo,
                        browser_service=browser_service,
                        login_strategy=login_strategy,
                        email_service=email_service,
                        step_logger=log_step
                    )
                    # Truyen proxy_config de use case pre-check username qua proxy account.
                    success = await use_case.execute(account_id, avatar_path, proxy_config=proxy_config)

                elif task_type == "INTERACT_VIDEOS":
                    from app.use_cases.interaction.tiktok_video_interaction import TikTokVideoInteractionUseCase
                    from app.use_cases.auth.login_strategies import CookieLoginStrategy

                    # Tuong tac video chi hop ly voi account DA co cookie tu truoc
                    # (khong ep dang nhap Credential+OTP moi lan, qua ton kem/rui ro).
                    login_strategy = CookieLoginStrategy()

                    use_case = TikTokVideoInteractionUseCase(
                        account_repo=account_repo,
                        browser_service=browser_service,
                        login_strategy=login_strategy,
                        email_service=email_service,
                        step_logger=log_step,
                    )
                    success = await use_case.execute(
                        account_id,
                        mode=extra_config.get("mode", "foryou"),
                        hashtag=extra_config.get("hashtag"),
                        duration_minutes=extra_config.get("duration_minutes", 10),
                        like_probability=extra_config.get("like_probability", 0.4),
                        comment_probability=extra_config.get("comment_probability", 0.05),
                        comment_list=extra_config.get("comment_list", []),
                        min_watch_seconds=extra_config.get("min_watch_seconds", 3.0),
                        max_watch_seconds=extra_config.get("max_watch_seconds", 15.0),
                    )

                if success:
                    # NÂNG CẤP ĐỘNG: Nạp lại tài khoản từ DB để lấy đúng trạng thái chuyên biệt
                    updated_account = account_repo.get_by_id(account_id)
                    final_status = updated_account.status if updated_account else "LOGGED_IN"

                    # SUA BUG KET RUNNING: neu use case tra ve True nhung KHONG doi
                    # status (vd nhanh guard "da COMPLETED, bo qua"), thi status van
                    # con la "RUNNING"/"QUEUED" do chinh lan chay nay set luc dau ->
                    # account se ket cung o RUNNING mai. 1 tac vu THANH CONG khong bao
                    # gio duoc ket thuc o trang thai dang chay -> ep ve SUCCESS.
                    if final_status in ("RUNNING", "QUEUED", "IDLE"):
                        final_status = "SUCCESS"

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
                    # CHI giu browser mo khi cua so THUC SU NHIN THAY DUOC (headed
                    # VA khong bi day ra ngoai man hinh). Neu headless (cloak) hoac
                    # off-screen -> user khong the dong tay -> wait_for_event("close")
                    # cho VO HAN -> ket semaphore mai mai.
                    _window_visible = (not settings.BROWSER_HEADLESS) and (not getattr(settings, "HIDE_BROWSER_OFFSCREEN", True))
                    if task_type in ("LOGIN_COOKIE", "LOGIN_CREDENTIAL") and _window_visible:
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
                    # Use case tra ve False (khong raise). Bao chi tiet len terminal
                    # Dashboard de nhin thay ngay (truoc day chi hien "Thất bại").
                    try:
                        await ws_manager.broadcast({
                            "event": "TERMINAL_LOG",
                            "data": {
                                "account_id": account_id,
                                "username": account.username if account else account_id,
                                "message": f"❌ Tác vụ {task_type} trả về THẤT BẠI (xem các bước log phía trên để biết lý do).",
                            },
                        })
                    except Exception:
                        pass
                    await self._update_account_status(account_id, "ERROR", step_desc="Thất bại", session=session)

            except asyncio.CancelledError:
                # Task nay bi huy do bam "Dung khan cap toan cuc" (emergency_stop_all)
                # hoac dispatcher.stop() luc tat app. Can cap nhat trang thai ro rang
                # thay vi de "RUNNING" mai mai tren UI, roi RE-RAISE de asyncio biet
                # task da huy thanh cong (khong nuot mat CancelledError).
                logger.warning(f"[!] Task cua tai khoan {account_id} da bi HUY (Emergency Stop / Shutdown).")
                try:
                    await self._update_account_status(
                        account_id, "ERROR", step_desc="Đã bị dừng khẩn cấp (Emergency Stop)", session=session
                    )
                except Exception:
                    pass  # Neu DB/session da khong con hop le luc shutdown thi bo qua, uu tien raise CancelledError
                raise

            except Exception as e:
                # In FULL traceback ra terminal backend (truoc day chi in str(e)
                # -> mat stack, kho biet loi o dau).
                logger.exception(f"[-] Thất bại chung cuộc cho tài khoản {account_id}")

                # Va DAY chi tiet loi + noi phat sinh len terminal Dashboard de
                # nhin thay NGAY nguyen nhan (giai quyet "khong co gi ben terminal").
                import traceback as _tb
                _err_detail = f"{type(e).__name__}: {str(e)}"
                _last_frame = ""
                try:
                    _tbs = _tb.extract_tb(e.__traceback__)
                    if _tbs:
                        _f = _tbs[-1]
                        _last_frame = f" [{_f.filename.split(chr(92))[-1].split('/')[-1]}:{_f.lineno} {_f.name}]"
                except Exception:
                    pass
                try:
                    await ws_manager.broadcast({
                        "event": "TERMINAL_LOG",
                        "data": {
                            "account_id": account_id,
                            "username": account.username if account else account_id,
                            "message": f"❌ LỖI ({task_type}): {_err_detail}{_last_frame}",
                        },
                    })
                except Exception:
                    pass

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
                # Dung stream anh truoc khi dong browser (tranh screenshot vao
                # page dang bi dong -> loi rac).
                if streamer_task is not None:
                    streamer_task.cancel()
                    try:
                        await streamer_task
                    except BaseException:
                        # QUAN TRONG: CancelledError la BaseException (KHONG phai
                        # Exception). Neu chi bat "except Exception" thi CancelledError
                        # se lot ra, BO QUA browser_service.close() + semaphore.release()
                        # ben duoi -> ro ri slot semaphore + browser -> dispatcher het
                        # slot -> task moi ket cung. Phai bat BaseException.
                        pass
                await browser_service.close()
                # Nha slot PROXY truoc (neu da giu) roi moi nha slot TONG -> account
                # khac dang cho proxy nay duoc chay ngay khi slot tong con trong.
                if proxy_acquired and proxy_sem is not None:
                    proxy_sem.release()
                self.semaphore.release()
                self.active_tasks.pop(account_id, None)
                # Don sach entry pause cua account nay - task da ket thuc
                # (thanh cong, loi, hay bi huy khan cap) nen khong con y nghia
                # de "cho pause" nua.
                self.account_pause_events.pop(account_id, None)
                self.paused_account_ids.discard(account_id)

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