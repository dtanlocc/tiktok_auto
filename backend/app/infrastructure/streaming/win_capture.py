# File: backend/app/infrastructure/streaming/win_capture.py
"""
Chup cua so trinh duyet o TANG HE DIEU HANH (Win32 PrintWindow) thay vi
page.screenshot() cua Playwright.

Ly do: ban Firefox tang hinh (invisible_playwright) co bat nhieu pixel khi
DOC LAI (canvas/webgl substitute_pixels) de chong fingerprint -> page.screenshot()
di qua dung duong readback do nen luon ra ANH NHIEU (hat). Nhung cua so THAT
tren compositor van hien trang dung; PrintWindow(PW_RENDERFULLCONTENT) chup
tu compositor -> ANH SACH, va GIU NGUYEN fingerprint (khong can tat substitution).

Uu diem:
- Chup duoc ca cua so BI CHE hoac da CLOAK (headless an tren Windows) -> khong
  can dua cua so len truoc / khong can focus -> automation da luong khong ket.
- Thu nho + nen JPEG -> frame nhe (~10KB) thay vi ~600-700KB cua screenshot goc.

CHI dung tren Windows. Cac nen tang khac tra ve None (streamer se fallback).
"""
import io
import json
import os
import sys

import ctypes
import logging
from typing import Optional

logger = logging.getLogger("WinCapture")

# Co PW_RENDERFULLCONTENT = 2: buoc cua so tu render TOAN BO noi dung (ke ca
# phan GPU/DirectComposition) ra bitmap, chup duoc ca khi bi che/cloak.
_PW_RENDERFULLCONTENT = 2


def prepare_profile_window_offscreen(profile_dir: str) -> bool:
    """Seed Firefox's startup window position just outside the visible desktop.

    This prevents a headed interactive session from flashing on screen between
    process launch and HWND discovery. The window remains a normal headed window;
    it can later be restored without restarting the browser or losing page state.
    """
    if sys.platform != "win32" or not profile_dir:
        return False
    try:
        import win32api
        import win32con

        width = win32api.GetSystemMetrics(win32con.SM_CXSCREEN)
        height = win32api.GetSystemMetrics(win32con.SM_CYSCREEN)
        path = os.path.join(profile_dir, "xulstore.json")
        data = {}
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    data = loaded
            except Exception:
                data = {}
        browser = data.setdefault("chrome://browser/content/browser.xhtml", {})
        main_window = browser.setdefault("main-window", {})
        main_window.update({
            "screenX": str(max(0, width - 1)),
            "screenY": str(max(0, height - 1)),
            "sizemode": "normal",
        })
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, separators=(",", ":"))
        return True
    except Exception as exc:
        logger.debug(f"prepare_profile_window_offscreen that bai: {exc}")
        return False


def enum_moz_hwnds() -> set:
    """Tra ve tap hcuwr (HWND) cac cua so top-level cua Firefox (class
    'MozillaWindowClass') dang ton tai, du kich thuoc (>200px)."""
    if sys.platform != "win32":
        return set()
    try:
        import win32gui
    except Exception:
        return set()

    found = set()

    def _cb(h, _):
        try:
            if win32gui.GetClassName(h) == "MozillaWindowClass":
                l, t, r, b = win32gui.GetWindowRect(h)
                if (r - l) > 200 and (b - t) > 200:
                    found.add(h)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumWindows(_cb, None)
    except Exception:
        pass
    return found


def find_session_moz_hwnd(session_token) -> Optional[int]:
    """Find the top-level Firefox window owned by one invisible session.

    Comparing the set of windows before/after launch is ambiguous when two
    Firefox sessions launch concurrently: each observer can see both new
    windows and claim the other session's HWND.  Every process launched by
    invisible_playwright carries the session token in its environment, so use
    that positive ownership signal instead.
    """
    if sys.platform != "win32" or not session_token:
        return None
    try:
        import psutil
        import win32gui
        import win32process
    except Exception:
        return None

    candidates = []

    def _cb(hwnd, _):
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
        win32gui.EnumWindows(_cb, None)
    except Exception:
        return None
    return max(candidates)[1] if candidates else None


def move_window_offscreen(hwnd: int, x: int = -3200, y: int = -3200) -> bool:
    """Co che ban cu: dua cua so ra han ngoai desktop, khong minimize va khong
    cuop focus. Firefox van render nho occlusion tracking da bi tat trong prefs."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import win32gui
        import win32con
        # SWP_NOSIZE: giu nguyen kich thuoc (khong dong cham viewport/fingerprint).
        # SWP_NOACTIVATE: khong cuop focus. SWP_NOZORDER: giu nguyen thu tu z.
        win32gui.SetWindowPos(
            hwnd, None, x, y, 0, 0,
            win32con.SWP_NOSIZE | win32con.SWP_NOACTIVATE | win32con.SWP_NOZORDER,
        )
        return True
    except Exception as e:
        logger.debug(f"move_window_offscreen that bai (hwnd={hwnd}): {e}")
        return False


# =============================================================================
# DA XOA (25/08/2026) - deu KHONG con noi nao goi:
#   firefox_pids / kill_firefox_pids  -> thay bang SessionToken cua thu vien
#     (playwright_adapter._reap_session_tree). Cach cu chup snapshot PID roi giet
#     moi firefox.exe la se GIET NHAM browser cua phien khac khi chay da luong.
#   enum_firefox_hwnds_all / nudge_window / hide_window_transparent
#     -> thay bang registry HWND theo tung adapter + move_window_offscreen/show.
# =============================================================================

def show_window_foreground(hwnd: int) -> bool:
    """Dua cua so ra HIEN tren man hinh + dua len FOREGROUND (nguoc voi off-screen).
    Dung cho che do DEBUG/manual takeover: user can nhin thay + thao tac tay.
    Khong dung AttachThreadInput vi no co the deadlock tren RDP; neu Windows khong
    cho cuop focus, cua so van duoc restore + dua len tren va se nhap nhay taskbar."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import win32gui
        import win32con
        # Dat cua so ve vi tri HIEN tren man hinh (phong khi da bi day off-screen).
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOP, 120, 60, 0, 0,
            win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW,
        )
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            try:
                win32gui.FlashWindow(hwnd, True)
            except Exception:
                pass
        return True
    except Exception as e:
        logger.debug(f"show_window_foreground that bai (hwnd={hwnd}): {e}")
        return False


def downscale_jpeg(raw: bytes, max_width: int = 1280, quality: int = 82) -> bytes:
    """Thu nho 1 anh JPEG ve toi da max_width. Tra ve chinh 'raw' neu anh da du
    nho hoac neu co su co (khong bao gio nem loi - stream khong duoc phep chet vi
    1 frame hong).

    VI SAO CAN: page.screenshot() KHONG co tham so chieu rong, no luon tra ve anh
    dung kich thuoc viewport. Duong chup cu (PrintWindow) co thu nho ve
    SCREEN_STREAM_MAX_WIDTH truoc khi nen; khi bo duong do thi buoc thu nho MAT
    theo, moi frame di ra o kich thuoc day du roi con bi base64 phinh them 33%.
    KHONG dung cach ha viewport de anh nho di: viewport thuoc van tay trinh duyet.
    """
    if not raw:
        return raw
    try:
        from PIL import Image
    except Exception:
        return raw
    try:
        img = Image.open(io.BytesIO(raw))
        w, h = img.size
        if w <= max_width:
            return raw
        target_h = max(1, int(h * max_width / w))
        # draft(): bao libjpeg giai ma NGAY o ty le 1/2, 1/4 hoac 1/8 thay vi bung
        # anh day du roi moi thu nho. Do thuc te 25/08/2026 tren frame 1920x947:
        #   giai ma day du + resize : 21.4 ms/frame
        #   co draft()              : 14.5 ms/frame  (-32%)
        # Chi la goi y - PIL tu bo qua neu dinh dang khong ho tro, nen an toan.
        img.draft("RGB", (max_width, target_h))
        img = img.convert("RGB")
        if img.size[0] > max_width:
            # draft chi ha duoc theo luy thua 2 -> can 1 buoc resize cuoi cho dung so.
            img = img.resize(
                (max_width, max(1, int(img.size[1] * max_width / img.size[0]))),
                Image.BILINEAR,
            )
        buf = io.BytesIO()
        # 4:4:4 chroma keeps small coloured text and UI borders sharp. Avoid
        # optimize=True here because it adds CPU work on every live frame.
        img.save(buf, "JPEG", quality=quality, subsampling=0)
        return buf.getvalue()
    except Exception as e:
        logger.debug(f"downscale_jpeg that bai: {e}")
        return raw


def capture_hwnd_jpeg(hwnd: int, max_width: int = 1280, quality: int = 82) -> Optional[bytes]:
    """Chup 1 cua so theo HWND bang PrintWindow -> thu nho ve max_width ->
    nen JPEG. Tra ve bytes JPEG, hoac None neu that bai (cua so da dong...)."""
    if sys.platform != "win32" or not hwnd:
        return None
    try:
        import win32gui
        import win32ui
        from PIL import Image
    except Exception as e:
        logger.debug(f"thieu thu vien chup (pywin32/Pillow): {e}")
        return None

    hdc = None
    mfc = None
    save = None
    bmp = None
    try:
        l, t, r, b = win32gui.GetClientRect(hwnd)
        w, h = r - l, b - t
        if w <= 0 or h <= 0:
            return None

        hdc = win32gui.GetWindowDC(hwnd)
        mfc = win32ui.CreateDCFromHandle(hdc)
        save = mfc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc, w, h)
        save.SelectObject(bmp)

        ctypes.windll.user32.PrintWindow(hwnd, save.GetSafeHdc(), _PW_RENDERFULLCONTENT)

        bits = bmp.GetBitmapBits(True)
        img = Image.frombuffer("RGB", (w, h), bits, "raw", "BGRX", 0, 1)
        if w > max_width:
            img = img.resize((max_width, max(1, int(h * max_width / w))))

        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=quality, subsampling=0)
        return buf.getvalue()
    except Exception as e:
        logger.debug(f"PrintWindow chup that bai (hwnd={hwnd}): {e}")
        return None
    finally:
        try:
            if save is not None:
                save.DeleteDC()
            if mfc is not None:
                mfc.DeleteDC()
            if hdc is not None:
                win32gui.ReleaseDC(hwnd, hdc)
            if bmp is not None:
                win32gui.DeleteObject(bmp.GetHandle())
        except Exception:
            pass
