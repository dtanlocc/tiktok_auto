# File: backend/app/infrastructure/streaming/screen_streamer.py
"""
Chup anh dinh ky moi trinh duyet dang chay va day ve Dashboard qua WebSocket
de xem TRUC TIEP nhieu luong cung luc (giai quyet nhu cau "quan ly man hinh
khi chay da luong" ma khong the xep luoi cua so that do Firefox+Playwright
khong cho dat vi tri cua so).

Thiet ke:
- 1 coroutine chay nen cho MOI task, doc page hien tai qua callable get_page()
  (page co the None luc dau, hoac bi dong khi user tu tay dong browser login).
- Moi chu ky: screenshot JPEG chat luong thap -> base64 -> broadcast event
  BROWSER_FRAME. Khi ket thuc (task xong / browser dong / bi cancel) -> phat
  BROWSER_FRAME_END de UI go o hinh do khoi luoi.
- Loi screenshot (dang dieu huong, page dong...) duoc bo qua nhe nhang; neu
  loi lien tuc qua nhieu lan -> coi nhu page da chet, dung han.
"""
import asyncio
import base64
import logging
import sys
from typing import Callable, Optional, Any

from app.core.config import settings
from app.infrastructure.websocket.socket_manager import ws_manager
from app.infrastructure.streaming.win_capture import capture_hwnd_jpeg, move_window_offscreen

logger = logging.getLogger("ScreenStreamer")

# So lan chup loi LIEN TIEP toi da truoc khi coi page da chet han.
_MAX_CONSECUTIVE_FAILURES = 15


async def stream_browser_frames(
    get_page: Callable[[], Optional[Any]],
    account_id: str,
    username: str,
    get_hwnd: Optional[Callable[[], Optional[int]]] = None,
) -> None:
    """Vong lap chup & phat frame cho 1 account. Tu ket thuc khi bi cancel
    (dispatcher goi luc don dep) hoac khi chup loi lien tuc.

    Uu tien chup bang PrintWindow (Win32) qua get_hwnd() -> anh SACH va GIU
    fingerprint (page.screenshot cua Firefox tang hinh bi nhieu hat). Neu khong
    co HWND (khong phai Windows / chua nhan duoc cua so) thi fallback ve
    page.screenshot()."""
    interval = max(0.1, settings.SCREEN_STREAM_INTERVAL_MS / 1000.0)
    quality = max(1, min(100, settings.SCREEN_STREAM_JPEG_QUALITY))
    max_width = getattr(settings, "SCREEN_STREAM_MAX_WIDTH", 720)
    is_win = sys.platform == "win32"
    consecutive_failures = 0

    try:
        while True:
            await asyncio.sleep(interval)

            raw = None

            # 1. Uu tien PrintWindow (Windows) - anh sach, giu fingerprint, chup
            #    duoc ca cua so cloak/che.
            hwnd = get_hwnd() if get_hwnd else None
            if is_win and hwnd:
                # GIU cua so luon off-screen (phong TikTok keo ve / fullscreen video
                # / detection cham). SetWindowPos toi vi tri cu la no-op re tien.
                if getattr(settings, "HIDE_BROWSER_OFFSCREEN", True):
                    try:
                        await asyncio.to_thread(move_window_offscreen, hwnd)
                    except Exception:
                        pass
                # capture_hwnd_jpeg dung GDI (blocking) -> chay trong thread.
                raw = await asyncio.to_thread(capture_hwnd_jpeg, hwnd, max_width, quality)

            # 2. Fallback: page.screenshot (co the bi nhieu voi Firefox tang hinh,
            #    nhung dung cho non-Windows / khi chua co HWND).
            if raw is None:
                page = get_page()
                if page is not None:
                    try:
                        raw = await page.screenshot(type="jpeg", quality=quality, timeout=5000)
                    except Exception:
                        raw = None

            if raw is None:
                consecutive_failures += 1
                if consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                    logger.info(
                        f"[*] [ScreenStreamer] Ngung stream {username} ({account_id}) "
                        f"vi chup loi lien tuc {consecutive_failures} lan (browser co le da dong)."
                    )
                    break
                continue

            consecutive_failures = 0
            jpeg_b64 = base64.b64encode(raw).decode("ascii")
            await ws_manager.broadcast({
                "event": "BROWSER_FRAME",
                "data": {
                    "account_id": account_id,
                    "username": username,
                    "jpeg_b64": jpeg_b64,
                },
            })
    except asyncio.CancelledError:
        # Bi dispatcher huy luc don dep task - im lang thoat.
        pass
    except Exception as e:
        logger.warning(f"[!] [ScreenStreamer] Loi vong lap stream {account_id}: {str(e)}")
    finally:
        # Bao UI go o hinh nay khoi luoi (du ket thuc vi ly do gi).
        try:
            await ws_manager.broadcast({
                "event": "BROWSER_FRAME_END",
                "data": {"account_id": account_id},
            })
        except Exception:
            pass
