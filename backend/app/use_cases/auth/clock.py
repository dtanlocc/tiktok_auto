"""Gio chuan cho TOTP - bu lech dong ho may.

⛔ TOTP SONG CHET THEO DONG HO. Ma 2FA sinh tu thoi gian; lech 15-20 giay la
TikTok tu choi "code expired or incorrect" du khoa dung va thuat toan dung.
Do 19/11/2026 may nay cham +6.85s - va email-OTP khong he phu thuoc gio nen
lech do an ky lau khong ai thay. Tool nay chay tren VPS / may ban be, noi dong
ho co the lech ma chu may khong chinh duoc, nen KHONG the tin dong ho may.

Giai phap: hoi gio chuan qua NTP (chi mot goi UDP 48 byte, KHONG gui du lieu
nguoi dung), do do lech MOT LAN, cache lai, va cong vao khi sinh ma. NTP
khong toi duoc thi lui ve dong ho may (nhu cu) chu khong chan login.
"""
from __future__ import annotations

import logging
import socket
import struct
import threading
import time

logger = logging.getLogger("Clock")

#: Hoi vai may NTP cong cong; may dau tra loi la dung. Khong gui gi ngoai goi
#: NTP tieu chuan.
_NTP_HOSTS = ("pool.ntp.org", "time.google.com", "time.cloudflare.com")
_NTP_PORT = 123

#: Do lech cache bao lau roi do lai. Dong ho khong truot nhanh, 30 phut la du.
_TTL_GIAY = 1800.0

#: Giua epoch cua NTP (1900) va epoch Unix (1970).
_EPOCH_1900 = 2208988800

_lock = threading.Lock()
_cache = {"offset": 0.0, "do_luc": 0.0, "ok": False}


def _hoi_mot_may(host: str, timeout: float = 3.0) -> float:
    """Tra ve do lech (gio_chuan - gio_may) tinh bang giay, hoac nem loi."""
    pkt = b"\x1b" + 47 * b"\0"          # LI=0, VN=3, Mode=3 (client)
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        t1 = time.time()
        s.sendto(pkt, (host, _NTP_PORT))
        data, _ = s.recvfrom(1024)
        t4 = time.time()
    finally:
        s.close()
    if len(data) < 48:
        raise ValueError("NTP tra ve goi ngan bat thuong")
    secs = struct.unpack("!I", data[40:44])[0] - _EPOCH_1900
    frac = struct.unpack("!I", data[44:48])[0] / 2 ** 32
    gio_chuan = secs + frac
    # Bu nua thoi gian khu hoi (gia dinh di/ve can nhau).
    return gio_chuan - (t1 + t4) / 2.0


def do_lech(*, force: bool = False) -> float:
    """Do lech dong ho (giay), >0 nghia la may CHAM hon gio chuan.

    Cache trong `_TTL_GIAY`. NTP hong thi giu gia tri cache cu (hoac 0.0 lan
    dau) - khong bao gio nem ra ngoai.
    """
    now = time.monotonic()
    with _lock:
        con_han = (now - _cache["do_luc"]) < _TTL_GIAY
        if _cache["ok"] and con_han and not force:
            return _cache["offset"]

    offset = None
    for host in _NTP_HOSTS:
        try:
            offset = _hoi_mot_may(host)
            break
        except Exception as exc:
            logger.debug("[Clock] NTP %s khong tra loi: %s", host, exc)

    with _lock:
        if offset is None:
            # Khong do duoc: giu cache cu, khong chan gi.
            if not _cache["ok"]:
                logger.warning(
                    "[Clock] Khong hoi duoc NTP; dung dong ho may cho TOTP. "
                    "Neu may lech gio, ma 2FA co the bi tu choi."
                )
            return _cache["offset"]
        _cache.update(offset=offset, do_luc=now, ok=True)
        if abs(offset) >= 2.0:
            logger.info(
                "[Clock] Dong ho may lech %+.1fs so voi gio chuan; da bu khi sinh ma 2FA.",
                offset,
            )
        return offset


def gio_chuan() -> float:
    """Epoch giay da bu lech - dung cho TOTP thay cho time.time()."""
    return time.time() + do_lech()
