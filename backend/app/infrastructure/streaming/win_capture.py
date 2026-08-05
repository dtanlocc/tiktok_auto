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
import sys
import ctypes
import logging
from typing import Optional

logger = logging.getLogger("WinCapture")

# Co PW_RENDERFULLCONTENT = 2: buoc cua so tu render TOAN BO noi dung (ke ca
# phan GPU/DirectComposition) ra bitmap, chup duoc ca khi bi che/cloak.
_PW_RENDERFULLCONTENT = 2


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


def move_window_offscreen(hwnd: int, x: int = -3200, y: int = -3200) -> bool:
    """Day cua so ra NGOAI vung nhin (toa do am), GIU nguyen kich thuoc va trang
    thai 'shown' -> cua so van render + chay duoc du khong duoc focus (khong bi
    treo nhu minimize). Dung thay cho cloak/headless tren RDP (noi cloak khong chay).
    Tra ve True neu thanh cong."""
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


def show_window_foreground(hwnd: int) -> bool:
    """Dua cua so ra HIEN tren man hinh + dua len FOREGROUND (nguoc voi off-screen).
    Dung cho che do DEBUG: user can nhin thay + thao tac tay. Dung AttachThreadInput
    de vuot qua han che SetForegroundWindow tu tien trinh nen."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import win32gui
        import win32con
        import win32process
        # Dat cua so ve vi tri HIEN tren man hinh (phong khi da bi day off-screen).
        win32gui.SetWindowPos(hwnd, None, 120, 60, 0, 0,
                              win32con.SWP_NOSIZE | win32con.SWP_NOZORDER | win32con.SWP_SHOWWINDOW)
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        # Vuot han che foreground bang AttachThreadInput.
        fg = win32gui.GetForegroundWindow()
        t1 = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
        t2 = win32process.GetWindowThreadProcessId(hwnd)[0]
        attached = False
        if t1 and t1 != t2:
            ctypes.windll.user32.AttachThreadInput(t2, t1, True)
            attached = True
        try:
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            pass
        if attached:
            ctypes.windll.user32.AttachThreadInput(t2, t1, False)
        return True
    except Exception as e:
        logger.debug(f"show_window_foreground that bai (hwnd={hwnd}): {e}")
        return False


def capture_hwnd_jpeg(hwnd: int, max_width: int = 720, quality: int = 55) -> Optional[bytes]:
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
        img.save(buf, "JPEG", quality=quality)
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
