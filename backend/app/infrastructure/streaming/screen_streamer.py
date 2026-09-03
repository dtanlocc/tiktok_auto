# File: backend/app/infrastructure/streaming/screen_streamer.py
"""
Chup anh dinh ky moi trinh duyet dang chay va day ve Dashboard qua WebSocket
de xem TRUC TIEP nhieu luong cung luc (giai quyet nhu cau "quan ly man hinh
khi chay da luong" ma khong the xep luoi cua so that do Firefox+Playwright
khong cho dat vi tri cua so).

Thiet ke:
- 1 coroutine chay nen cho MOI task, doc page hien tai qua callable get_page()
  (page co the None luc dau, hoac bi dong khi user tu tay dong browser login).
- Moi chu ky: screenshot JPEG chat luong cao -> base64 -> broadcast event
  BROWSER_FRAME. Khi ket thuc (task xong / browser dong / bi cancel) -> phat
  BROWSER_FRAME_END de UI go o hinh do khoi luoi.
- Loi screenshot (dang dieu huong, page dong...) duoc bo qua nhe nhang; neu
  loi lien tuc qua nhieu lan -> coi nhu page da chet, dung han.
"""
import asyncio
import base64
import logging
import sys
import time
from typing import Awaitable, Callable, Optional, Any

from app.core.config import settings
from app.infrastructure.websocket.socket_manager import screen_ws_manager
from app.infrastructure.streaming.win_capture import (
    capture_hwnd_jpeg,
    downscale_jpeg,
)

logger = logging.getLogger("ScreenStreamer")

# Log theo cum, nhung khong tu ket thuc streamer. Vong doi cua streamer do
# dispatcher quan ly; navigation/captcha co the lam screenshot loi lau hon 15
# frame trong khi browser va task van con song.
_FAILURE_LOG_INTERVAL = 15

# =============================================================================
# CHI STREAM KHI CO NGUOI DANG XEM (tiet kiem CPU cho browser khi chay da luong)
# =============================================================================
# Frontend (tab "Man Hinh Truc Tiep") gui ping dinh ky khi dang mo. Neu KHONG co
# ping nao trong _VIEW_TTL giay gan day -> coi nhu KHONG ai xem -> streamer BO QUA
# viec chup/encode/broadcast hoan toan -> CPU danh het cho cac browser dang chay.
_last_view_ping: float = 0.0
_VIEW_TTL: float = 6.0


def note_screen_view_ping() -> None:
    """Frontend goi (qua API) khi tab Man Hinh Truc Tiep dang mo."""
    global _last_view_ping
    _last_view_ping = time.monotonic()


def screens_are_watched() -> bool:
    return (time.monotonic() - _last_view_ping) < _VIEW_TTL


async def stream_browser_frames(
    get_page: Callable[[], Optional[Any]],
    account_id: str,
    username: str,
    get_hwnd: Optional[Callable[[], Optional[int]]] = None,
    recover_hwnd: Optional[Callable[[], Awaitable[Optional[int]]]] = None,
    capture_allowed: Optional[Callable[[], bool]] = None,
) -> None:
    """Vong lap chup & phat frame cho 1 account. Tu ket thuc khi bi cancel
    (dispatcher goi luc don dep). Loi chup tam thoi chi duoc log va retry.

    Tren Windows uu tien PrintWindow qua HWND. Cach nay khong chen len kenh
    Playwright cua automation. page.screenshot chi la fallback khi khong co HWND.
    Stream chi xem mot chieu, khong pause va khong gui input vao browser."""
    consecutive_failures = 0
    last_capture_error = ""
    interval = max(0.1, settings.SCREEN_STREAM_INTERVAL_MS / 1000.0)
    quality = max(1, min(100, settings.SCREEN_STREAM_JPEG_QUALITY))
    max_width = max(320, getattr(settings, "SCREEN_STREAM_MAX_WIDTH", 1280))
    is_win = sys.platform == "win32"
    last_hwnd_recovery = 0.0
    next_capture_at = time.monotonic()

    try:
        while True:
            # Fixed cadence: capture work (normally ~10-90ms) does not get
            # added on top of the configured interval and slowly lower FPS.
            next_capture_at += interval
            await asyncio.sleep(max(0.0, next_capture_at - time.monotonic()))

            # TOI UU DA LUONG: neu KHONG ai dang xem tab Man Hinh Truc Tiep thi
            # BO QUA toan bo chup/encode/broadcast -> giai phong CPU cho browser.
            # Vong lap van song de tu dong stream lai ngay khi co nguoi xem.
            if not screens_are_watched():
                continue

            # Pause only for the native Windows file chooser. Caption, Post,
            # Post now and Studio verification remain visible because Windows
            # capture uses HWND/PrintWindow, not Playwright's command channel.
            if capture_allowed is not None and not capture_allowed():
                consecutive_failures = 0
                continue

            raw = None
            captured_with_hwnd = False
            hwnd = get_hwnd() if get_hwnd else None
            if is_win and get_hwnd is not None and hwnd:
                raw = await asyncio.to_thread(
                    capture_hwnd_jpeg, hwnd, max_width, quality
                )
                captured_with_hwnd = raw is not None

            # Reacquire by the invisible session-token. Do this at most once a
            # second, and only after OS capture is unavailable/failed.
            if (
                is_win
                and get_hwnd is not None
                and raw is None
                and recover_hwnd is not None
                and time.monotonic() - last_hwnd_recovery >= 1.0
            ):
                last_hwnd_recovery = time.monotonic()
                try:
                    hwnd = await recover_hwnd()
                except Exception as exc:
                    last_capture_error = f"HWND recovery: {type(exc).__name__}: {exc}"
                    hwnd = None
                if hwnd:
                    raw = await asyncio.to_thread(
                        capture_hwnd_jpeg, hwnd, max_width, quality
                    )
                    captured_with_hwnd = raw is not None

            # On Windows headed/cloaked sessions, never fall back to
            # page.screenshot(): it shares Playwright's command channel and can
            # stall for 5 seconds exactly while captcha/Studio is busy. Other
            # platforms and true-headless sessions still use that fallback.
            page = get_page()
            hwnd_capture_expected = is_win and get_hwnd is not None
            if raw is None and page is not None and not hwnd_capture_expected:
                try:
                    raw = await page.screenshot(
                        type="jpeg", quality=quality, timeout=5000
                    )
                except Exception as exc:
                    last_capture_error = f"{type(exc).__name__}: {exc}"
                    raw = None
            elif raw is None and hwnd_capture_expected and not last_capture_error:
                last_capture_error = "HWND cua phien chua san sang hoac PrintWindow that bai"
            if raw is not None and not captured_with_hwnd:
                # Chan frame bat thuong truoc khi base64 de tranh phinh RAM/WS.
                if len(raw) > 8_000_000:
                    raw = None
                else:
                    raw = await asyncio.to_thread(
                        downscale_jpeg, raw, max_width, quality
                    )

            if raw is None:
                consecutive_failures += 1
                if consecutive_failures % _FAILURE_LOG_INTERVAL == 0:
                    logger.warning(
                        "[ScreenStreamer] Chua chup duoc frame %s (%s) sau %d lan; "
                        "tiep tuc thu cho den khi dispatcher dong task. Loi gan nhat: %s",
                        username,
                        account_id,
                        consecutive_failures,
                        last_capture_error or "HWND/Page khong tra frame",
                    )
                if time.monotonic() > next_capture_at + interval:
                    next_capture_at = time.monotonic()
                continue

            consecutive_failures = 0
            last_capture_error = ""
            jpeg_b64 = base64.b64encode(raw).decode("ascii")
            await screen_ws_manager.broadcast({
                "event": "BROWSER_FRAME",
                "data": {
                    "account_id": account_id,
                    "username": username,
                    "jpeg_b64": jpeg_b64,
                },
            })
            # If OS capture/backpressure took longer than a whole frame, drop
            # missed ticks instead of sending a burst of stale frames.
            if time.monotonic() > next_capture_at + interval:
                next_capture_at = time.monotonic()
    except asyncio.CancelledError:
        # Bi dispatcher huy luc don dep task - im lang thoat.
        pass
    except Exception as e:
        logger.warning(f"[!] [ScreenStreamer] Loi vong lap stream {account_id}: {str(e)}")
    finally:
        # Bao UI go o hinh nay khoi luoi (du ket thuc vi ly do gi).
        try:
            await screen_ws_manager.broadcast({
                "event": "BROWSER_FRAME_END",
                "data": {"account_id": account_id},
            })
        except Exception:
            pass
