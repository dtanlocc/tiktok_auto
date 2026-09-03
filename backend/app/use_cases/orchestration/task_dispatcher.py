import asyncio
import logging
import glob
import os
import time
import random
from typing import Dict, Any, Optional, List, Tuple
from sqlmodel import Session

from app.core.config import settings
from app.infrastructure.database.connection import engine
from app.infrastructure.database.sqlite_repository import SQLiteAccountRepository, SQLiteProxyRepository
from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter
from app.infrastructure.websocket.socket_manager import ws_manager
from app.use_cases.auth.tiktok_login import TikTokLoginUseCase
from app.domain.account_rules import is_sold_account
import hashlib

logger = logging.getLogger("TaskDispatcher")

_UPLOAD_TASK_TYPES = frozenset({"UPLOAD_MEDIA", "UPLOAD_VIDEO", "UPLOAD_MEDIA_BATCH"})


def _upload_route_concurrency_override(task_type: str, use_proxy: bool) -> Optional[int]:
    """Serialize publishing only when multiple sessions share one proxy.

    In direct/VPN mode the UI concurrency value is the user's explicit total
    concurrency choice, so imposing the old override of one made a displayed
    value of four misleading: dispatcher coroutines were active while only one
    browser could publish. Different direct sessions may now use every machine
    slot selected by the user.
    """
    return 1 if use_proxy and task_type in _UPLOAD_TASK_TYPES else None


class SoldAccountArchived(RuntimeError):
    """Stop an in-flight task when its account is moved to the sold archive."""


def _uuid_to_seed(uuid_str: str) -> int:
    """Chuyển đổi chuỗi UUID của tài khoản thành một số nguyên seed cố định"""
    if not uuid_str:
        return 42 # Fallback seed
    # Băm UUID bằng SHA-256 để đảm bảo tính phân phối đều
    hash_object = hashlib.sha256(uuid_str.encode('utf-8'))
    hex_dig = hash_object.hexdigest()
    # Lấy 8 ký tự đầu -> số nguyên, RỒI CHẶN VỀ 31-BIT (& 0x7FFFFFFF).
    # ==========================  BUG ĐÃ ĐO ĐƯỢC (13/08/2026)  ==========================
    # seed >= 2^31 (2.147.483.648) + profile_dir  ->  Firefox TREO CỨNG lúc khởi chạy
    # (launch_persistent_context timeout 180s, "trình duyệt mở lên nhưng không làm gì").
    # Đã kiểm chứng: seed=4028411051 + profile_dir -> treo; cùng seed KHÔNG profile_dir
    # -> chạy OK; seed=834634588 + profile_dir -> chạy OK 8s.
    # => Chặn 31-bit: seed nhỏ giữ NGUYÊN (không đổi vân tay), seed lớn được ánh xạ về
    #    vùng an toàn. Vẫn cố định theo account (cùng account -> cùng seed).
    return int(hex_dig[:8], 16) & 0x7FFFFFFF

class ConcurrentTaskDispatcher:
    """Hệ thống điều phối, xếp hàng và khống chế giới hạn số luồng chạy song song"""
    def __init__(self, max_tabs: int = settings.MAX_CONCURRENT_TABS):
        self.queue: asyncio.Queue = asyncio.Queue()
        self.max_tabs = max_tabs
        self.semaphore = asyncio.Semaphore(max_tabs)
        self.active_tasks: Dict[str, asyncio.Task] = {}
        # account_id -> adapter/browser DANG CHAY. Registry nay cap page hien tai
        # cho streamer mot chieu; khong tao session/profile thu hai.
        self.active_browsers: Dict[str, InvisiblePlaywrightAdapter] = {}

        # =================================================================
        # GIOI HAN DONG THOI THEO TUNG PROXY (moi proxy toi da N luong cung luc)
        # =================================================================
        # semaphore TONG (self.semaphore) chi khong che tong so luong tren MAY,
        # KHONG biet 2 luong co dung chung 1 proxy hay khong. proxy_max_concurrent
        # gioi han so phien chay dong thoi tren MOI proxy (host:port) - duoc thuc
        # thi qua bo phan phoi proxy dong ben duoi (_acquire_balanced_proxy).
        self.proxy_max_concurrent: int = max(1, getattr(settings, "PROXY_MAX_CONCURRENT", 2))

        # PHAN PHOI PROXY DONG (khong gan proxy truoc): khi 1 account toi luot chay,
        # dispatcher chon proxy IT TAI NHAT trong kho (con slot duoi proxy_max_concurrent)
        # -> tai duoc phan DEU ra cac proxy, khong bi don cuc vao 1 proxy. _proxy_running
        # dem so phien dang chay tren tung proxy (host:port); _proxy_cond de cho/danh
        # thuc khi co slot proxy tro trong.
        self._proxy_running: Dict[str, int] = {}
        self._proxy_cond: asyncio.Condition = asyncio.Condition()
        # Cache danh sach proxy (kho proxy hiem khi doi) -> khong mo Session DB moi
        # lan gianh proxy. TTL ngan de proxy moi import van duoc nhan trong ~15s.
        self._proxy_cache: Optional[List[Any]] = None
        self._proxy_cache_at: float = 0.0

        # Moc thoi gian (monotonic) lan MO GAN NHAT tren tung proxy (host:port).
        # Dung cho GIAN CACH THEO PROXY: 2 proxy KHAC nhau mo song song ngay, chi
        # gian cach khi mo lien tiep tren CUNG 1 proxy (tranh dam 1 IP don dap).
        # DAY LA MOC DA DUOC DAT CHO (reserved), khong phai moc da mo that: xem
        # _reserve_launch_slot().
        self._last_launch_monotonic: Dict[str, float] = {}
        # Khoa bao ve _last_launch_monotonic. Bug cu: N task cung DOC 1 moc, cung
        # tinh ra 1 khoang cho, cung ngu bang nhau roi CUNG BAT -> "gian cach" khong
        # gian cach gi ca. Nay doc-tinh-GHI dien ra tron ven trong khoa (khong co
        # await ben trong) roi moi ngu ben ngoai -> task sau nhin thay moc cua task
        # truoc va xep hang phia sau.
        self._stagger_lock: asyncio.Lock = asyncio.Lock()

        # =================================================================
        # CONG LAUNCH: gioi han so lan MO TRINH DUYET duoc chong nhau.
        # =================================================================
        # Do thuc te 25/08/2026 (6 phien): 1 launch chong nhau -> 8.7s/launch;
        # 3 -> 23.2s; 6 -> 32.6-53.3s. Ly do da profile duoc: initialize() co doan
        # CHEN event loop (LifetimeGuard.bind cua thu vien quet bien moi truong cua
        # moi process bang psutil - 2.96s CPU thuan/lan launch). Bi chen nen cac
        # launch KHONG that su song song, mo dong loat chi lam MOI launch cham gap
        # N lan. Cong nay KHONG lam wall-time nhanh hon (nut that la thong luong
        # launch cua may) - no giu do tre TUNG launch o muc thap va gioi han so doan
        # chen loop chong nhau, nho vay dashboard/websocket do bi dong bang.
        # CHI chan luc MO; mo xong roi thi ca 8 luong chay song song binh thuong.
        self._launch_gate: asyncio.Semaphore = asyncio.Semaphore(
            max(1, int(getattr(settings, "BROWSER_LAUNCH_GATE", 2)))
        )

        # SO LUONG DANG CHAY o CHE DO KHONG PROXY (USE_PROXY=False, chay thang qua
        # mang that/VPN). Truoc day che do nay KHONG bi gioi han boi o "số luồng"
        # tren UI (vi o do von la gioi han THEO PROXY) -> user dat 4 luong nhung
        # thuc te chi tran tong MAX_CONCURRENT_TABS quyet dinh. Nay dung chinh o UI
        # (proxy_max_concurrent) lam gioi han so luong chay song song truc tiep.
        self._direct_running: int = 0
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

        # Cac account DANG xep hang HOAC dang chay (chong chay TRUNG 1 account 2
        # luong cung luc). Them luc submit_task, xoa trong finally cua worker. Fix
        # lo hong TOCTOU cua active_tasks (chi dang ky sau khi da gianh slot tong).
        self._pending_accounts: set = set()

    # Khong thay semaphore luc dang chay. Voi MAX_CONCURRENT_TABS=0, semaphore tong
    # duoc tat han; gioi han dong do UI dieu khien theo proxy/che do mang truc tiep.

    def is_account_busy(self, account_id: str) -> bool:
        """Account nay dang xep hang hoac dang chay? (de chan submit trung)."""
        return account_id in self._pending_accounts

    def recover_orphaned_runtime_state(self) -> int:
        """Clear runtime-only states left by a crashed/restarted backend.

        Queue and browser handles are in-memory, so RUNNING/QUEUED rows from a
        previous process can never complete. Mark them as an interrupted
        runtime state before accepting new work; upload submission will then
        overwrite the upload status with RUNNING for the new run.
        """
        recovered = 0
        with Session(engine) as session:
            repo = SQLiteAccountRepository(session)
            for account in repo.get_all():
                if account.status in ("RUNNING", "QUEUED"):
                    account.status = "ERROR"
                    account.current_step = "Backend restarted - task cũ đã được giải phóng"
                    repo.save(account)
                    recovered += 1
        if recovered:
            logger.warning("[RECOVERY] Đã giải phóng %s account RUNNING/QUEUED mồ côi.", recovered)
        return recovered

    def set_proxy_concurrency_limit(self, limit: int) -> None:
        """Cap nhat dong so luong chay DONG THOI TREN MOI PROXY tu Web UI. Vi viec
        chon proxy doc self.proxy_max_concurrent moi lan nen thay doi co hieu luc
        NGAY cho cac lan gan proxy tiep theo. Danh thuc cac account dang cho slot
        de chung danh gia lai voi gioi han moi."""
        if limit <= 0:
            return
        self.proxy_max_concurrent = limit
        logger.info(f"[+] Đã cập nhật giới hạn luồng chạy đồng thời / 1 proxy thành: {limit}")

        async def _wake():
            async with self._proxy_cond:
                self._proxy_cond.notify_all()
        try:
            asyncio.get_running_loop().create_task(_wake())
        except RuntimeError:
            pass  # khong co event loop (vd goi tu test dong bo) -> bo qua

    def _load_all_proxies(self) -> List[Any]:
        """Doc toan bo proxy trong kho, CO CACHE (TTL 15s) de khong mo Session DB
        moi lan gianh proxy trong hot path da luong."""
        now = time.monotonic()
        if self._proxy_cache is not None and (now - self._proxy_cache_at) < 15.0:
            return self._proxy_cache
        try:
            with Session(engine) as s:
                self._proxy_cache = SQLiteProxyRepository(s).get_all()
                self._proxy_cache_at = now
                return self._proxy_cache
        except Exception as e:
            logger.warning(f"[-] Khong doc duoc kho proxy: {str(e)}")
            if self._proxy_cache is not None:
                # Con cache cu -> dung tam (con hon chay truc tiep lo IP that).
                return self._proxy_cache
            # Cache LANH + doc DB loi -> KHONG tra [] (se bi hieu la "khong co proxy"
            # roi chay TRUC TIEP, lo IP that len TikTok). Nem loi de task that bai
            # AN TOAN thay vi de-anonymize. (Neu that su khong cau hinh proxy nao,
            # get_all() tra [] THANH CONG - khac voi truong hop nay.)
            raise RuntimeError(f"Khong doc duoc kho proxy (cache lanh): {str(e)}")

    async def _acquire_balanced_proxy(
        self,
        account_id: str,
        session: Session,
        max_concurrent_override: Optional[int] = None,
    ):
        """Chon proxy IT TAI NHAT con slot (duoi proxy_max_concurrent) va tang bo
        dem. CHO neu tat ca proxy da day. Tra ve (proxy_entity, proxy_key='host:port')
        hoac (None, None) neu kho KHONG co proxy nao (-> chay truc tiep khong proxy)."""
        proxies = self._load_all_proxies()
        if not proxies:
            return None, None
        waited_logged = False
        async with self._proxy_cond:
            while True:
                effective_limit = self.proxy_max_concurrent
                if max_concurrent_override is not None:
                    effective_limit = min(
                        effective_limit, max(1, max_concurrent_override)
                    )
                best = best_key = best_run = None
                for p in proxies:
                    key = f"{p.host}:{p.port}"
                    run = self._proxy_running.get(key, 0)
                    if run < effective_limit and (best is None or run < best_run):
                        best, best_key, best_run = p, key, run
                if best is not None:
                    self._proxy_running[best_key] = self._proxy_running.get(best_key, 0) + 1
                    return best, best_key
                # Tat ca proxy da day -> cho slot tro trong.
                if not waited_logged:
                    waited_logged = True
                    await self._update_account_status(
                        account_id, "QUEUED",
                        step_desc=f"⏳ Mọi proxy đang đủ {effective_limit} luồng phù hợp, chờ slot trống...",
                        session=session,
                    )
                await self._proxy_cond.wait()

    async def _acquire_direct_slot(
        self,
        account_id: str,
        session: Session,
        max_concurrent_override: Optional[int] = None,
    ) -> bool:
        """CHE DO KHONG PROXY (USE_PROXY=False): gianh 1 slot trong so luong toi da
        do NGUOI DUNG dat tren UI (o 'số luồng', chinh la self.proxy_max_concurrent).
        CHO neu da du. Tra ve True khi da giu duoc slot (nho goi _release_direct_slot).
        Nho vay o so luong tren UI co tac dung CA khi khong dung proxy."""
        waited_logged = False
        async with self._proxy_cond:
            while True:
                effective_limit = self.proxy_max_concurrent
                if max_concurrent_override is not None:
                    effective_limit = min(
                        effective_limit, max(1, max_concurrent_override)
                    )
                if self._direct_running < effective_limit:
                    self._direct_running += 1
                    return True
                if not waited_logged:
                    waited_logged = True
                    await self._update_account_status(
                        account_id, "QUEUED",
                        step_desc=f"⏳ Đang chạy đủ {effective_limit} luồng phù hợp, chờ slot trống...",
                        session=session,
                    )
                await self._proxy_cond.wait()

    async def _release_direct_slot(self) -> None:
        """Tra lai 1 slot cua che do khong proxy + danh thuc account dang cho."""
        async with self._proxy_cond:
            self._direct_running = max(0, self._direct_running - 1)
            self._proxy_cond.notify_all()

    async def _reserve_launch_slot(self, stagger_key: str, has_proxy: bool) -> Tuple[float, str]:
        """DAT CHO mot moc thoi diem duoc phep mo browser tren 'stagger_key'.

        Tra ve (so_giay_phai_cho, ly_do). NGUOI GOI phai tu sleep so giay do - ham
        nay KHONG ngu trong khi giu khoa.

        CACH LAM: moc cua task nay = max(bay gio, moc_da_dat_truoc + khoang_gian_cach),
        va GHI NGAY moc do lai khi VAN CON GIU KHOA. Nho vay task ke tiep doc duoc
        moc cua task nay va tu xep sau -> N task xep thanh HANG DOI thay vi cung ngu
        roi cung bat.

        BUG DA SUA: ban cu doc self._last_launch_monotonic[key], tinh _remain, ngu,
        RO I MOI ghi lai moc - nen N task cung doc 1 gia tri, cung ngu bang nhau va
        bat dong loat. "Gian cach" khong he gian cach, dong thoi con lam moi launch
        chong nhau -> tung launch cham gap N lan (do duoc: 6 launch cung luc =
        53.3s/launch so voi 8.7s khi mo lan luot).
        """
        if has_proxy:
            # CO PROXY: gian 30-60s tren CUNG 1 proxy (tranh dam 1 IP don dap).
            lo = getattr(settings, "STAGGER_MIN_SECONDS", 30.0)
            hi = getattr(settings, "STAGGER_MAX_SECONDS", 60.0)
            why = "cùng proxy"
        else:
            # KHONG PROXY: moi account deu ra bang 1 IP (mang may/VPN) nen van can
            # gian, nhung ngan hon - so luong song song da do o "số luồng" khong che.
            lo = getattr(settings, "STAGGER_DIRECT_MIN_SECONDS", 3.0)
            hi = getattr(settings, "STAGGER_DIRECT_MAX_SECONDS", 8.0)
            why = "mở lệch nhau"

        async with self._stagger_lock:          # KHONG co await nao ben trong khoa
            now = time.monotonic()
            last = self._last_launch_monotonic.get(stagger_key)
            if last is None:
                slot = now                      # phien dau tien tren khoa nay: mo ngay
            else:
                slot = max(now, last + random.uniform(lo, max(lo, hi)))
            self._last_launch_monotonic[stagger_key] = slot
        return max(0.0, slot - now), why

    async def _release_proxy(self, proxy_key: Optional[str]) -> None:
        """Tra 1 slot cua proxy (host:port) va danh thuc account dang cho."""
        if not proxy_key:
            return
        async with self._proxy_cond:
            self._proxy_running[proxy_key] = max(0, self._proxy_running.get(proxy_key, 0) - 1)
            self._proxy_cond.notify_all()

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

        # Xoa sach _pending_accounts: task dang chay se tu discard trong finally,
        # nhung task da bi drain khoi hang doi thi khong bao gio toi finally ->
        # clear het de khong ket "dang ban" gia.
        self._pending_accounts.clear()

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
            "proxy_max_concurrent": self.proxy_max_concurrent,   # so luong toi da / 1 proxy
            "machine_max_tabs": self.max_tabs,                   # tran tong toan may
        }

    async def submit_task(
        self,
        account_id: str,
        task_type: str,
        avatar_folder: Optional[str] = None,
        extra_config: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Gửi tác vụ vào hàng đợi kèm theo loại tác vụ (task_type).
        extra_config: cấu hình bổ sung tuỳ loại tác vụ - hiện dùng cho
        INTERACT_VIDEOS (mode, hashtag, duration_minutes, xác suất tym/cmt,
        danh sách câu bình luận...)."""
        # CHONG TRUNG: neu account nay dang xep hang / dang chay thi BO QUA (khong
        # mo 2 browser cho cung 1 account). Xoa khoi _pending_accounts o finally worker.
        with Session(engine) as guard_session:
            guarded_account = SQLiteAccountRepository(guard_session).get_by_id(account_id)
            if is_sold_account(guarded_account):
                logger.info("[ARCHIVE] Skip sold account %s for task %s", account_id, task_type)
                await ws_manager.broadcast({
                    "event": "TERMINAL_LOG",
                    "data": {
                        "account_id": account_id,
                        "username": guarded_account.username if guarded_account else account_id,
                        "message": "Đã bỏ qua: tài khoản thuộc mục ĐÃ BÁN (chỉ lưu trữ).",
                    },
                })
                return False
        if account_id in self._pending_accounts:
            logger.info(f"[!] Bo qua submit TRUNG cho account {account_id} (dang xep hang/chay).")
            return False
        self._pending_accounts.add(account_id)
        # Start a fresh observable run. Do not let a previous FAILED/NEVER
        # result remain visible while this upload is queued; otherwise a
        # restarted backend looks as if the new task failed before it ran.
        if task_type in ("UPLOAD_MEDIA", "UPLOAD_VIDEO", "UPLOAD_MEDIA_BATCH"):
            with Session(engine) as run_session:
                run_repo = SQLiteAccountRepository(run_session)
                run_account = run_repo.get_by_id(account_id)
                if run_account:
                    run_account.last_upload_status = "RUNNING"
                    run_account.last_upload_error = ""
                    run_account.current_step = "Đang xếp hàng upload..."
                    run_repo.save(run_account)
        await self._update_account_status(account_id, "QUEUED", step_desc="Đang xếp hàng...")
        await self.queue.put({
            "account_id": account_id,
            "task_type": task_type,  # Ví dụ: LOGIN_COOKIE, LOGIN_CREDENTIAL, UPDATE_PROFILE, INTERACT_VIDEOS
            "avatar_folder": avatar_folder,
            "extra_config": extra_config or {},
        })
        logger.info(f"[+] Tài khoản {account_id} | Tác vụ {task_type} đã được đưa vào hàng đợi.")
        return True

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
                extra_config = task_payload.get("extra_config") or {}

                # Phân phối ảnh đại diện TRƯỚC khi giành slot tổng. QUAN TRỌNG: phải
                # đứng TRƯỚC semaphore.acquire() -> nếu _allocate_avatar_from_folder
                # lỗi (đường dẫn hỏng...) thì chưa giữ permit nào, tránh RÒ RỈ permit
                # (nếu lỗi sau acquire mà chưa tạo worker thì permit không ai release).
                assigned_avatar = None
                if task_type == "UPDATE_PROFILE":
                    assigned_avatar = self._allocate_avatar_from_folder(avatar_folder, self.global_task_counter)
                    self.global_task_counter += 1

                await self.semaphore.acquire()
                # Giua acquire() va create_task() KHONG duoc co lenh nao co the raise.
                worker_task = asyncio.create_task(
                    self._execute_worker_with_semaphore(account_id, task_type, assigned_avatar, extra_config)
                )
                self.active_tasks[account_id] = worker_task
                self.queue.task_done()

                # LUU Y: KHONG con giãn cách TOÀN CỤC ở đây nữa. Giãn cách được
                # chuyển vào worker và tính THEO TỪNG PROXY (xem stagger theo proxy
                # trong _execute_worker_with_semaphore) -> 2 proxy khác nhau mở
                # song song NGAY, không phải chờ nhau 30-60s như trước.

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[-] Lỗi trong vòng lặp điều phối tác vụ: {str(e)}")
                # Task chua kip tao worker (vd loi cap phat avatar TRUOC acquire) ->
                # go account khoi _pending_accounts de con submit lai duoc (khong ket
                # "dang ban" vinh vien). Permit khong bi ro ri vi alloc dung TRUOC acquire.
                try:
                    _aid = locals().get("account_id")
                    if _aid:
                        self._pending_accounts.discard(_aid)
                except Exception:
                    pass

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
            from app.infrastructure.email.email_service_factory import create_email_service
            email_service = create_email_service()

            # Task chup & stream anh man hinh trinh duyet ve Dashboard (neu bat).
            streamer_task: Optional[asyncio.Task] = None

            # proxy_key = host:port cua proxy DUOC GAN DONG cho lan chay nay (chon
            # luc chay, khong dung proxy gan san). proxy_acquired danh dau da gianh
            # duoc slot proxy hay chua -> chi tra slot trong finally khi THUC SU da
            # gianh (tranh tra nham neu bi cancel luc dang cho).
            proxy_key: Optional[str] = None
            proxy_acquired: bool = False
            direct_slot_held: bool = False   # da giu slot che do KHONG proxy chua

            def ensure_account_is_operational():
                session.expire_all()
                current_account = account_repo.get_by_id(account_id)
                if is_sold_account(current_account):
                    raise SoldAccountArchived(
                        "Tài khoản vừa được chuyển vào mục ĐÃ BÁN; tác vụ đã dừng."
                    )
                return current_account

            async def log_step(step_desc: str):
                # CHECKPOINT PAUSE: neu dang bi tam dung (toan cuc hoac rieng
                # account nay), worker se dung ngay tai day cho toi khi duoc
                # resume, truoc khi ghi log va tiep tuc buoc tiep theo.
                ensure_account_is_operational()
                await self._wait_if_paused(account_id)
                await self._update_step_log(account_id, step_desc, session)

            try:
                # 1. Truy vấn thông tin tài khoản và cấu hình Proxy động liên kết
                account = account_repo.get_by_id(account_id)
                if is_sold_account(account):
                    logger.info("[ARCHIVE] Account %s moved to DA BAN before execution; skip.", account_id)
                    await ws_manager.broadcast({
                        "event": "TERMINAL_LOG",
                        "data": {
                            "account_id": account_id,
                            "username": account.username if account else account_id,
                            "message": "Đã hủy trước khi mở browser: account hiện thuộc mục ĐÃ BÁN.",
                        },
                    })
                    return

                # =========================================================
                # GAN PROXY DONG luc chay (KHONG dung proxy gan san truoc): chon
                # proxy IT TAI NHAT trong kho, ton trong gioi han self.proxy_max_concurrent
                # luong / proxy. -> tu dong phan DEU cac account dang chay ra cac
                # proxy, khong bi don cuc vao 1 proxy. CHO neu moi proxy da day.
                # =========================================================
                # USE_PROXY=False -> KHONG gan proxy, chay TRUC TIEP qua mang that
                # (dung khi bat VPN toan may). Bat lai bang cach set USE_PROXY=True.
                proxy_config = None
                proxy_entity, proxy_key = None, None
                # Proxy mode keeps one publisher per proxy IP while allowing
                # different proxies in parallel. Direct/VPN mode obeys the UI
                # total-concurrency value instead of silently forcing one.
                use_proxy = bool(getattr(settings, "USE_PROXY", True))
                route_override = _upload_route_concurrency_override(
                    task_type, use_proxy
                )
                if use_proxy:
                    proxy_entity, proxy_key = await self._acquire_balanced_proxy(
                        account_id,
                        session,
                        max_concurrent_override=route_override,
                    )
                else:
                    # KHONG proxy -> van phai TON TRONG o "số luồng" tren UI.
                    await self._acquire_direct_slot(
                        account_id,
                        session,
                        max_concurrent_override=route_override,
                    )
                    direct_slot_held = True
                proxy_acquired = proxy_key is not None
                if proxy_entity is not None:
                    proxy_config = {
                        "server": proxy_entity.connection_string,
                        "username": proxy_entity.username,
                        "password": proxy_entity.password,
                    }
                    # Ghi lai proxy THUC SU dung lan nay vao account (de UI hien dung
                    # + biet no dang chay qua proxy nao). Chi luu + bao neu khac cu.
                    if account and account.proxy_id != proxy_entity.id:
                        account.proxy_id = proxy_entity.id
                        account_repo.save(account)
                        await ws_manager.broadcast({
                            "event": "ACCOUNT_PROXY_CHANGED",
                            "data": {"id": account_id, "proxy_id": proxy_entity.id},
                        })

                # =========================================================
                # GIÃN CÁCH THEO PROXY: không mở 2 phiên liên tiếp trên CÙNG 1
                # proxy quá sát nhau (tránh đấm 1 IP dồn dập). Proxy KHÁC không
                # bị ảnh hưởng -> 2 proxy khác nhau mở SONG SONG ngay. Chỉ chờ
                # phần còn lại nếu proxy này vừa được mở gần đây.
                # =========================================================
                stagger_key = proxy_key if proxy_key is not None else "__direct__"
                _remain, _why = await self._reserve_launch_slot(stagger_key, proxy_key is not None)
                if _remain > 0:
                    await self._update_account_status(
                        account_id, "QUEUED",
                        step_desc=f"⏳ Giãn cách {_remain:.0f}s ({_why}) trước khi mở...",
                        session=session,
                    )
                    await asyncio.sleep(_remain)

                # Account có thể bị chuyển sang ĐÃ BÁN trong lúc chờ proxy hoặc
                # giãn cách. Chặn lại ngay trước điểm mở browser tốn tài nguyên.
                account = ensure_account_is_operational()

                # 2. THỐNG NHẤT KHỞI TẠO VÒNG ĐỜI TRÌNH DUYỆT TẠI ĐÂY:
                # Đảm bảo 100% mọi tác vụ (LOGIN & UPDATE_PROFILE) đều chạy đúng Proxy cách ly và Seed vân tay cố định!
                seed_val = _uuid_to_seed(account_id)
                # CỔNG LAUNCH: chỉ cho BROWSER_LAUNCH_GATE lần mở chồng nhau. Mở dồn
                # KHÔNG xong sớm hơn (một phần của initialize() chẹn event loop nên
                # các launch không thật sự song song) mà chỉ kéo mỗi launch chậm gấp
                # N lần. Chờ ở cổng KHÔNG tính vào timeout của adapter.
                if self._launch_gate.locked():
                    await self._update_account_status(
                        account_id, "QUEUED",
                        step_desc="⏳ Đang chờ lượt mở trình duyệt...",
                        session=session,
                    )
                async with self._launch_gate:
                    await self._update_account_status(
                        account_id, "RUNNING", step_desc="Đang khởi chạy...", session=session
                    )
                    await browser_service.initialize(proxy_config=proxy_config, seed=seed_val)

                # Siết thêm một lần sau launch: nếu người dùng vừa chuyển nhóm
                # trong lúc Firefox khởi tạo, đóng ngay trước khi truy cập TikTok.
                account = ensure_account_is_operational()

                # Dang ky SAU launch va TRUOC frame dau tien. Khi preview xuat hien,
                # UI se luon tim thay chinh browser tao ra frame do.
                browser_service.bind_automation_gate(
                    self._get_or_create_account_event(account_id)
                )
                self.active_browsers[account_id] = browser_service

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
                            recover_hwnd=browser_service.recover_stream_hwnd,
                            capture_allowed=lambda: not browser_service.stream_suspended,
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

                elif task_type == "SYNC_ANALYTICS":
                    from app.use_cases.analytics.tiktok_analytics_sync import TikTokAnalyticsSyncUseCase
                    from app.use_cases.auth.login_strategies import CookieThenCredentialLoginStrategy

                    use_case = TikTokAnalyticsSyncUseCase(
                        account_repo=account_repo,
                        browser_service=browser_service,
                        login_strategy=CookieThenCredentialLoginStrategy(),
                        email_service=email_service,
                        step_logger=log_step,
                    )
                    success = await use_case.execute(account_id)

                elif task_type in ("UPLOAD_VIDEO", "UPLOAD_MEDIA", "UPLOAD_MEDIA_BATCH"):
                    from app.use_cases.upload.tiktok_upload_video import TikTokUploadMediaUseCase
                    from app.use_cases.auth.login_strategies import CookieThenCredentialLoginStrategy

                    login_strategy = CookieThenCredentialLoginStrategy()
                    use_case = TikTokUploadMediaUseCase(
                        account_repo=account_repo,
                        browser_service=browser_service,
                        login_strategy=login_strategy,
                        email_service=email_service,
                        step_logger=log_step,
                    )
                    video_paths = list(extra_config.get("video_paths") or [])
                    item_count = len(video_paths) if task_type == "UPLOAD_MEDIA_BATCH" else 1
                    upload_timeout = max(
                        60,
                        int(getattr(settings, "UPLOAD_TASK_TIMEOUT_SECONDS", 600))
                        * max(1, item_count),
                    )
                    try:
                        if task_type == "UPLOAD_MEDIA_BATCH":
                            success = await asyncio.wait_for(
                                use_case.execute_video_batch(
                                    account_id,
                                    video_paths=video_paths,
                                    captions=list(extra_config.get("captions") or []),
                                    result_sink=extra_config.get("_result_sink"),
                                ),
                                timeout=upload_timeout,
                            )
                        else:
                            success = await asyncio.wait_for(
                                use_case.execute(
                                    account_id,
                                    image_path=extra_config.get("image_path"),
                                    video_path=extra_config.get("video_path", ""),
                                    caption=extra_config.get("caption", ""),
                                    schedule_at=extra_config.get("schedule_at"),
                                ),
                                timeout=upload_timeout,
                            )
                    except asyncio.TimeoutError as exc:
                        raise RuntimeError(
                            f"Upload vuot qua {upload_timeout}s; da dong rieng browser bi treo de tra slot."
                        ) from exc

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
                    success_step = (
                        updated_account.current_step
                        if task_type == "UPLOAD_MEDIA_BATCH"
                        and updated_account
                        and updated_account.current_step
                        else "Thành công"
                    )
                    await self._update_account_status(
                        account_id, 
                        final_status, 
                        step_desc=success_step,
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
                    failed_account = account_repo.get_by_id(account_id)
                    failure_step = (
                        failed_account.current_step
                        if task_type == "UPLOAD_MEDIA_BATCH"
                        and failed_account
                        and failed_account.current_step
                        else "Thất bại"
                    )
                    await self._update_account_status(
                        account_id, "ERROR", step_desc=failure_step, session=session
                    )

            except SoldAccountArchived as exc:
                logger.info("[ARCHIVE] Stop in-flight task %s: %s", account_id, exc)
                try:
                    await ws_manager.broadcast({
                        "event": "TERMINAL_LOG",
                        "data": {
                            "account_id": account_id,
                            "username": account.username if account else account_id,
                            "message": str(exc),
                        },
                    })
                except Exception:
                    pass

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
                if self.active_browsers.get(account_id) is browser_service:
                    self.active_browsers.pop(account_id, None)
                # Tra slot PROXY truoc (neu da gianh) roi moi nha slot TONG -> account
                # khac dang cho proxy nay duoc chay ngay khi slot tong con trong.
                if proxy_acquired:
                    await self._release_proxy(proxy_key)
                if direct_slot_held:
                    await self._release_direct_slot()
                self.semaphore.release()
                self.active_tasks.pop(account_id, None)
                # Go khoi _pending_accounts -> account co the duoc submit lai (chay
                # lan sau) sau khi lan nay ket thuc.
                self._pending_accounts.discard(account_id)
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
                # Username co the vua duoc dong bo tu profile TikTok trong use
                # case. Gui lai gia tri moi nhat de bang account tren Web UI cap
                # nhat ngay, khong can reload trang/GET lai danh sach.
                "username": account.username if account else "",
                "status": status,
                "health_status": account.health_status if account else "UNKNOWN",
                "profile_status": account.profile_status if account else "PENDING",
                "current_step": step_desc,
                "upload_success_count": account.upload_success_count if account else 0,
                "upload_failure_count": account.upload_failure_count if account else 0,
                "last_upload_status": account.last_upload_status if account else "NEVER",
                "last_upload_at": account.last_upload_at if account else "",
                "last_upload_error": account.last_upload_error if account else "",
                "video_count": account.video_count if account else None,
                "follower_count": account.follower_count if account else None,
                "following_count": account.following_count if account else None,
                "likes_count": account.likes_count if account else None,
                "total_views": account.total_views if account else None,
                "total_video_likes": account.total_video_likes if account else None,
                "total_comments": account.total_comments if account else None,
                "total_shares": account.total_shares if account else None,
                "collected_video_count": account.collected_video_count if account else 0,
                "analytics_sync_status": account.analytics_sync_status if account else "NEVER",
                "analytics_sync_error": account.analytics_sync_error if account else "",
                "metrics_updated_at": account.metrics_updated_at if account else "",
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
