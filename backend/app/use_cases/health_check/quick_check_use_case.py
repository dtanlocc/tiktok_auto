# File: tiktok_auto/backend/app/use_cases/health_check/quick_check_use_case.py

"""
CHECK NHANH SONG/CHET - LUONG DOC LAP HOAN TOAN voi ConcurrentTaskDispatcher.

CHIEN LUOC (thiet ke lai): KHONG dung browser nua. Chi FETCH HTML THO cua trang
profile tiktok.com/@username bang HTTPX, QUA PROXY cua chinh account.

Ly do:
- TikTok NHUNG SAN du lieu user trong HTML (statusCode, uniqueId, videoCount,
  avatarLarger) -> chi can parse HTML la biet SONG/CHET, khong can render SPA.
- Fetch qua PROXY cua account (IP mobile/residential) -> KHONG bi WAF challenge
  (IP datacenter cua server thi bi chan bang "SlardarWAF / Please wait..." khi
  check nhieu -> truoc day ket luan nham).
- Khong browser -> cuc nhe, nhanh, chay nhieu account song song thoai mai.

Phan loai (_classify_profile_html):
  statusCode==0 & uniqueId khop @username  -> SONG (co video/avatar that = DA_TUONG_TAC,
                                               nick trang = SONG_TRANG).
  statusCode!=0 & khong co userInfo         -> DIE (10221 = user not found).
  WAF stub / khong ro                       -> None (KHONG ket luan, giu nguyen).
"""
import re
import asyncio
import logging
import random
from datetime import datetime
from typing import List, Dict, Any, Optional
from urllib.parse import quote

import httpx
from sqlmodel import Session

from app.infrastructure.database.connection import engine
from app.infrastructure.database.sqlite_repository import SQLiteAccountRepository, SQLiteProxyRepository
from app.infrastructure.websocket.socket_manager import ws_manager

logger = logging.getLogger("QuickHealthCheck")

# Header gia lap trinh duyet cho request HTML.
_HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
# Regex boc du lieu nhung san trong HTML profile TikTok.
_RE_UNIQUE_ID = re.compile(r'"uniqueId":"([^"]+)"')
_RE_STATUS = re.compile(r'"statusCode":\s*(\d+)')
_RE_VIDEO_COUNT = re.compile(r'"videoCount":\s*(\d+)')
_RE_AVATAR = re.compile(r'"avatarLarger":"([^"]+)"')


def _classify_profile_html(html: str, username: str) -> Optional[str]:
    """Phan loai account tu HTML THO cua trang profile (khong can browser).
    TikTok nhung san du lieu user trong HTML:
      - statusCode: 0 = ton tai;  10221/khac 0 = khong tim thay (die/banned).
      - uniqueId: neu khop @username -> account SONG.
    Tra ve 'SONG_DA_TUONG_TAC' | 'SONG_TRANG' | 'DIE' | None (WAF/khong ro)."""
    if not html:
        return None
    low = html.lower()

    # 1. WAF challenge stub (IP bi chan) -> KHONG ket luan.
    #    (Trang WAF rat ngan + chua 'slardarwaf' / '_wafchallengeid'.)
    if len(html) < 4000 or "slardarwaf" in low or "_wafchallengeid" in low:
        return None

    m_uid = _RE_UNIQUE_ID.search(html)
    uid = m_uid.group(1) if m_uid else None
    has_userinfo = '"userinfo"' in low

    # 2. SONG: uniqueId khop dung @username (account ton tai voi ten do).
    if uid and uid.lower() == username.lower():
        m_vc = _RE_VIDEO_COUNT.search(html)
        video_count = int(m_vc.group(1)) if m_vc else 0
        m_av = _RE_AVATAR.search(html)
        avatar = m_av.group(1) if m_av else ""
        is_default_avatar = (not avatar) or any(x in avatar for x in (
            "tiktok-obj", "100x100", "musically-maliva-obj", "1594805258216454",
        ))
        interacted = video_count > 0 or (avatar and not is_default_avatar)
        return "SONG_DA_TUONG_TAC" if interacted else "SONG_TRANG"

    # 3. DIE: khong co userInfo VA statusCode bao loi (10221 = user not found).
    m_st = _RE_STATUS.search(html)
    status = int(m_st.group(1)) if m_st else None
    if (not has_userinfo) and status is not None and status != 0:
        return "DIE"

    # 4. Khong ro -> KHONG ket luan.
    return None


class QuickHealthCheckService:
    """Singleton doc lap - khong lien quan gi toi ConcurrentTaskDispatcher."""

    def __init__(self):
        self.is_running: bool = False
        self.total: int = 0
        self.completed: int = 0

        # =================================================================
        # CHE DO LIEN TUC (Continuous Mode): tu dong lap lai quet CHI cho
        # danh sach account_ids duoc chi dinh luc bat (do nguoi dung tu chon,
        # KHONG con quet toan bo DB nua), hoan toan tach biet, khong dinh gi
        # toi ConcurrentTaskDispatcher/InteractionScheduler. Chi la 1 vong
        # lap asyncio don gian, tu goi lai run_batch() theo chu ky.
        # =================================================================
        self._continuous_task: Optional[asyncio.Task] = None
        self._continuous_active: bool = False
        self._continuous_account_ids: List[str] = []  # Danh sach account CO ĐỊNH do người dùng chọn lúc bật
        self._continuous_gap_seconds: int = 3   # Nghỉ tối thiểu giữa 2 vòng quét - KHÔNG phải chu kỳ chờ dài
        # Giảm 15 -> 6: quá nhiều luồng cùng lúc từ 1 IP khiến TikTok chặn bằng
        # CAPTCHA (Drag the slider) -> tất cả trả None. Ít luồng hơn = ít captcha hơn.
        self._continuous_concurrency: int = 6
        self._cycle_count: int = 0
        self._last_cycle_at: Optional[str] = None

    def get_status(self) -> Dict[str, Any]:
        return {
            "is_running": self.is_running,
            "total": self.total,
            "completed": self.completed,
        }

    def get_continuous_status(self) -> Dict[str, Any]:
        return {
            "is_active": self._continuous_active,
            "account_count": len(self._continuous_account_ids),
            "gap_seconds": self._continuous_gap_seconds,
            "concurrency_limit": self._continuous_concurrency,
            "cycle_count": self._cycle_count,
            "last_cycle_at": self._last_cycle_at,
            "is_running_now": self.is_running,
        }

    def _build_proxy_url(self, session: Session, proxy_id: Optional[str]) -> Optional[str]:
        """Dung URL proxy (co auth) cho httpx tu proxy cua account.
        None = khong co proxy -> di truc tiep (IP server, de bi WAF)."""
        if not proxy_id:
            return None
        try:
            proxy = SQLiteProxyRepository(session).get_by_id(proxy_id)
        except Exception:
            proxy = None
        if not proxy or not proxy.host:
            return None
        scheme = (proxy.protocol or "socks5").strip()
        if proxy.username:
            auth = f"{quote(str(proxy.username), safe='')}:{quote(str(proxy.password or ''), safe='')}@"
        else:
            auth = ""
        return f"{scheme}://{auth}{proxy.host}:{proxy.port}"

    async def _fetch_and_classify(self, client: httpx.AsyncClient, username: str) -> Optional[str]:
        """GET HTML trang profile @username (QUA PROXY account) roi phan loai bang
        _classify_profile_html - KHONG can browser. Proxy IP mobile it bi WAF nen
        thuong tra HTML that ngay."""
        url = f"https://www.tiktok.com/@{quote(username, safe='')}"
        try:
            r = await client.get(url)
            return _classify_profile_html(r.text or "", username)
        except Exception as e:
            logger.warning(f"⚠️ Loi mang khi check @{username}: {type(e).__name__}: {str(e)[:80]}")
            return None

    async def _process_one_account(
        self, clients: Dict[Optional[str], httpx.AsyncClient],
        semaphore: asyncio.Semaphore, account_id: str
    ) -> None:
        async with semaphore:
            # Gian nhe dau moi lan check de rai deu request (du qua proxy mobile it
            # bi WAF, van nen rai de dep).
            await asyncio.sleep(random.uniform(0.1, 1.0))
            with Session(engine) as session:
                repo = SQLiteAccountRepository(session)
                account = repo.get_by_id(account_id)
                if not account or not account.username:
                    self.completed += 1
                    return

                # Lay/tai su dung httpx client theo proxy (tai su dung ket noi cho nhanh).
                proxy_url = self._build_proxy_url(session, account.proxy_id)
                client = clients.get(proxy_url)
                if client is None:
                    client = httpx.AsyncClient(
                        proxy=proxy_url, headers=_HTTP_HEADERS, timeout=25.0,
                        follow_redirects=True, trust_env=False,
                    )
                    clients[proxy_url] = client

                try:
                    ket_qua = await self._fetch_and_classify(client, account.username)

                    # RETRY khi KHONG RO (None: WAF/loi mang tam thoi) - thu lai toi
                    # 2 lan, nghi ngan giua cac lan. Ket qua SONG/DIE tu HTML da rat
                    # chac chan (statusCode) nen khong can retry them.
                    if ket_qua is None:
                        for _attempt in range(2):
                            await asyncio.sleep(random.uniform(1.5, 3.0))
                            retry = await self._fetch_and_classify(client, account.username)
                            if retry is not None:
                                ket_qua = retry
                                break

                    if ket_qua == "SONG_DA_TUONG_TAC":
                        account.health_status = "ALIVE"
                        account.profile_status = "COMPLETED"
                        account.current_step = "🔍 Check nhanh: SỐNG (đã đổi Avatar/có Video)"
                    elif ket_qua == "SONG_TRANG":
                        account.health_status = "ALIVE"
                        account.current_step = "🔍 Check nhanh: SỐNG (nick trắng, chưa tương tác)"
                    elif ket_qua == "DIE":
                        account.health_status = "BANNED"
                        account.current_step = "☠️ Check nhanh: DIE (không tìm thấy tài khoản)"
                    else:
                        account.current_step = "⏸️ Check nhanh: Không rõ (WAF/lỗi mạng) - giữ nguyên"

                    repo.save(account)
                    await ws_manager.broadcast({
                        "event": "ACCOUNT_STATUS_CHANGED",
                        "data": {
                            "id": account.id,
                            "status": account.status,
                            "health_status": account.health_status,
                            "profile_status": account.profile_status,
                            "current_step": account.current_step,
                        },
                    })
                finally:
                    self.completed += 1

    async def _continuous_loop(self) -> None:
        """Vòng lặp chạy nền LIÊN TỤC KHÔNG NGHỈ: hết 1 vòng quét (chỉ trong
        phạm vi self._continuous_account_ids đang ALIVE) là chạy ngay vòng kế
        tiếp (chỉ nghỉ tối thiểu _continuous_gap_seconds để nhường event loop
        / tránh spam DB liên tục), lặp mãi tới khi stop_continuous() được gọi."""
        while self._continuous_active:
            try:
                target_id_set = set(self._continuous_account_ids)
                with Session(engine) as session:
                    repo = SQLiteAccountRepository(session)
                    all_accounts = repo.get_all()
                    
                    # TUÂN THỦ NGUYÊN TẮC THƯƠNG MẠI:
                    # Lấy toàn bộ tài khoản nằm trong danh sách được chọn (target_id_set)
                    # mà không lọc loại trừ health_status == "ALIVE".
                    # Vòng lặp sau vẫn sẽ kiểm tra lại các tài khoản BANNED bình thường.
                    target_ids = [a.id for a in all_accounts if a.id in target_id_set]

                if target_ids:
                    logger.info(
                        f"[*] [Continuous Check] Bắt đầu vòng #{self._cycle_count + 1} "
                        f"cho {len(target_ids)} account đã chọn "
                        f"({self._continuous_concurrency} luồng song song)."
                    )
                    await self.run_batch(target_ids, concurrency_limit=self._continuous_concurrency)
                    self._cycle_count += 1
                    self._last_cycle_at = datetime.now().isoformat()
                    await ws_manager.broadcast({
                        "event": "QUICK_CHECK_CONTINUOUS_CYCLE_DONE",
                        "data": self.get_continuous_status()
                    })
                else:
                    logger.info("[*] [Continuous Check] Không có account nào trong danh sách được chọn, đợi ít giây rồi kiểm tra lại.")
            except Exception as e:
                logger.error(f"[-] Lỗi trong vòng lặp Check nhanh liên tục: {str(e)}")

            # CHỈ nghỉ tối thiểu vài giây (KHÔNG phải chờ hàng chục phút) -
            # đủ để nhường event loop và tránh vòng lặp trắng (rỗng account)
            # ăn CPU liên tục, vẫn giữ tinh thần "chạy liên tục" như yêu cầu.
            for _ in range(max(1, self._continuous_gap_seconds)):
                if not self._continuous_active:
                    break
                await asyncio.sleep(1)

        logger.info("[-] [Continuous Check] Vòng lặp liên tục đã dừng hẳn.")

    def start_continuous(self, account_ids: List[str], gap_seconds: int = 3, concurrency_limit: int = 15) -> bool:
        """Bật chế độ quét LIÊN TỤC CHỈ cho danh sách account_ids được chỉ
        định (do người dùng tự chọn trên UI) - đa luồng, hết vòng chạy ngay
        vòng kế tiếp - tới khi bấm dừng."""
        if self._continuous_active:
            return False
        if not account_ids:
            return False
        self._continuous_active = True
        self._continuous_account_ids = list(account_ids)
        self._continuous_gap_seconds = max(0, gap_seconds)
        self._continuous_concurrency = max(1, concurrency_limit)
        self._cycle_count = 0
        self._continuous_task = asyncio.create_task(self._continuous_loop())
        logger.info(
            f"[+] [Continuous Check] Đã bật chế độ quét LIÊN TỤC cho {len(self._continuous_account_ids)} "
            f"account đã chọn ({self._continuous_concurrency} luồng song song, nghỉ {self._continuous_gap_seconds}s giữa các vòng)."
        )
        return True

    def stop_continuous(self) -> bool:
        if not self._continuous_active:
            return False
        self._continuous_active = False
        self._continuous_account_ids = []
        # KHÔNG cancel() task đang chạy dở run_batch() giữa chừng - để nó tự
        # hoàn tất đợt hiện tại cho gọn gàng, chỉ ngăn nó lặp thêm chu kỳ mới
        # (vòng poll mỗi giây phía trên sẽ tự thoát trong tối đa 1 giây).
        logger.info("[-] [Continuous Check] Đã tắt chế độ liên tục (sẽ dừng hẳn sau khi xong chu kỳ hiện tại, tối đa vài giây).")
        return True

    async def run_batch(self, account_ids: List[str], concurrency_limit: int = 8) -> None:
        """Check hang loat bang httpx (KHONG browser) - moi account fetch HTML profile
        QUA PROXY cua chinh no roi phan loai. Nhe, nhanh, it bi WAF hon nhieu so voi
        mo browser tu IP server."""
        if self.is_running:
            logger.warning("[!] Da co 1 dot Check nhanh dang chay, bo qua yeu cau moi.")
            return

        self.is_running = True
        self.total = len(account_ids)
        self.completed = 0
        clients: Dict[Optional[str], httpx.AsyncClient] = {}

        try:
            semaphore = asyncio.Semaphore(max(1, concurrency_limit))
            tasks = [
                self._process_one_account(clients, semaphore, acc_id)
                for acc_id in account_ids
            ]
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"[-] Lỗi tổng quát khi chạy Check nhanh hàng loạt: {str(e)}")
        finally:
            # Dong tat ca httpx client (tai su dung theo proxy).
            for cl in clients.values():
                try:
                    await cl.aclose()
                except Exception:
                    pass
            self.is_running = False
            await ws_manager.broadcast({
                "event": "QUICK_CHECK_FINISHED",
                "data": self.get_status()
            })
            logger.info(f"[+] Hoan tat dot Check nhanh: {self.completed}/{self.total} tai khoan.")


# Singleton dung chung cho toan app (import truc tiep, khong qua app.state
# de giu dung tinh than "tach rieng hoan toan" ma ban chon)
quick_health_check_service = QuickHealthCheckService()