from __future__ import annotations

import ctypes
import io
import logging
import sys
from typing import Any

logger = logging.getLogger("invisible_browser_studio.windows_capture")
_PW_RENDERFULLCONTENT = 2


def enum_moz_hwnds() -> set[int]:
    """Return usable top-level Firefox windows on Windows."""
    if sys.platform != "win32":
        return set()
    try:
        import win32gui
    except ImportError:
        return set()

    found: set[int] = set()

    def collect(hwnd: int, _: object) -> bool:
        try:
            if win32gui.GetClassName(hwnd) == "MozillaWindowClass":
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                if right - left > 200 and bottom - top > 200:
                    found.add(int(hwnd))
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(collect, None)
    except Exception:
        pass
    return found


def find_session_moz_hwnd(session_token: Any) -> int | None:
    """Find the Firefox HWND positively owned by one invisible session token."""
    if sys.platform != "win32" or not session_token:
        return None
    try:
        import psutil
        import win32gui
        import win32process
    except ImportError:
        return None

    candidates: list[tuple[int, int]] = []

    def collect(hwnd: int, _: object) -> bool:
        try:
            if win32gui.GetClassName(hwnd) != "MozillaWindowClass":
                return True
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width, height = right - left, bottom - top
            if width <= 200 or height <= 200:
                return True
            _, process_id = win32process.GetWindowThreadProcessId(hwnd)
            if session_token.matches(psutil.Process(process_id)):
                candidates.append((width * height, int(hwnd)))
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(collect, None)
    except Exception:
        return None
    return max(candidates)[1] if candidates else None


def show_window_foreground(hwnd: int) -> bool:
    """Restore the exact session window for the legacy visible TD-white mode."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import win32con
        import win32gui

        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetWindowPos(
            hwnd,
            win32con.HWND_TOP,
            120,
            60,
            0,
            0,
            win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
        )
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            win32gui.FlashWindow(hwnd, True)
        return True
    except Exception as exc:
        logger.debug("Could not show Firefox HWND=%s: %s", hwnd, exc)
        return False


def capture_hwnd_jpeg(
    hwnd: int,
    max_width: int = 1280,
    quality: int = 85,
) -> bytes | None:
    """Capture the compositor with PrintWindow, avoiding canvas readback noise."""
    if sys.platform != "win32" or not hwnd:
        return None
    try:
        import win32gui
        import win32ui
        from PIL import Image
    except ImportError as exc:
        logger.debug("Windows capture dependency unavailable: %s", exc)
        return None

    window_dc = memory_dc = compatible_dc = bitmap = None
    try:
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            return None

        window_dc = win32gui.GetWindowDC(hwnd)
        memory_dc = win32ui.CreateDCFromHandle(window_dc)
        compatible_dc = memory_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(memory_dc, width, height)
        compatible_dc.SelectObject(bitmap)
        ctypes.windll.user32.PrintWindow(hwnd, compatible_dc.GetSafeHdc(), _PW_RENDERFULLCONTENT)

        pixels = bitmap.GetBitmapBits(True)
        image = Image.frombuffer("RGB", (width, height), pixels, "raw", "BGRX", 0, 1)
        if width > max_width:
            target_height = max(1, int(height * max_width / width))
            image = image.resize((max_width, target_height), Image.Resampling.BILINEAR)
        buffer = io.BytesIO()
        # 4:2:0 is substantially faster and smaller than 4:4:4 for a live
        # preview. Source screenshots and browser state remain untouched.
        image.save(buffer, "JPEG", quality=quality, subsampling=2)
        return buffer.getvalue()
    except Exception as exc:
        logger.debug("PrintWindow failed for HWND=%s: %s", hwnd, exc)
        return None
    finally:
        try:
            if compatible_dc is not None:
                compatible_dc.DeleteDC()
            if memory_dc is not None:
                memory_dc.DeleteDC()
            if window_dc is not None:
                win32gui.ReleaseDC(hwnd, window_dc)
            if bitmap is not None:
                win32gui.DeleteObject(bitmap.GetHandle())
        except Exception:
            pass
