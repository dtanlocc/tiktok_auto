"""Sinh ma 2FA (TOTP, RFC 6238) tu khoa bi mat base32.

⛔ KHONG THEM THU VIEN CHO VIEC NAY. TOTP la HMAC-SHA1 tren mot bo dem 8
byte - stdlib (`hmac`, `hashlib`, hai dong giai base32) lam duoc het, va mot
dependency moi la mot thu nua phai ky/seal qua Supabase moi lan phat hanh.
Bu lai, module nay co bai test doi chieu voi VECTOR CHUAN cua RFC 6238, nen
no sai thi test do bat chu khong phai mot nick login hong giua dem.

⛔ VA KHOA 2FA LA CA YEU TO THU HAI. Ai cam khoa nay sinh duoc ma hop le mai
mai - no nhay cam hon ca mat khau ben canh. Khong in khoa ra log; ham o day
nhan khoa, tra ve 6 chu so, va khong ghi gi.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re
import struct
import time
from typing import Optional

#: So chu so cua ma, va chu ky thoi gian - mac dinh cua TikTok (va gan nhu moi
#: dich vu): 6 chu so, doi 30 giay mot lan.
_DIGITS = 6
_PERIOD = 30

#: Ky tu hop le cua base32 (RFC 4648): A-Z va 2-7. Khoa TOTP that chi gom
#: nhung ky tu nay (co the kem dau cach / gach / chu thuong / thieu '=' dem).
_BASE32_CHAR = re.compile(r"[A-Z2-7]", re.I)

#: ⛔ DAI TOI THIEU DE PHAN BIET KHOA 2FA VOI COOKIE. Truong thu 7 cua tep
#: import tung chi la cookies; gio co the la khoa 2FA. Cookie dang JSON mo bang
#: '[', dang chuoi thi co '=' va ';' - deu KHONG phai base32 thuan. Mot khoa
#: 2FA that la base32 lien tuc, TikTok phat 16 hoac 32 ky tu. 16 la nguong an
#: toan: ngan hon thi coi nhu khong phai khoa (tranh nhan nham mot manh cookie
#: ky la thanh khoa 2FA).
_MIN_SECRET_LEN = 16


def normalize_secret(secret: str) -> str:
    """Bo dau cach/gach, viet hoa, va dem '=' cho du boi so cua 8.

    Nem `ValueError` neu sau khi lam sach khong con ky tu base32 nao - de noi
    goi biet khoa rong/hong ngay, thay vi sinh ra mot ma vo nghia.
    """
    raw = "".join(_BASE32_CHAR.findall(secret or ""))
    if not raw:
        raise ValueError("Khoa 2FA rong hoac khong phai base32.")
    raw = raw.upper()
    # base64.b32decode doi do dai la boi so cua 8; dem '=' cho du.
    thieu = (-len(raw)) % 8
    return raw + ("=" * thieu)


def looks_like_totp_secret(value: str) -> bool:
    """Truong thu 7 cua tep import: la khoa 2FA (base32) hay la cookies?

    ⛔ PHAI CHAC CHAN, VI DOAN SAI LA MAT DU LIEU. Doan nham cookie thanh khoa
    2FA thi nick khong co cookie; doan nham khoa 2FA thanh cookie thi khoa chui
    vao cot cookies va nick khong co 2FA (dung loi cu truoc 19/11/2026). Nen:
    CHI nhan khi toan bo chuoi la base32 va du dai - cookie khong bao gio khop
    ca hai.
    """
    s = (value or "").strip()
    if len(s) < _MIN_SECRET_LEN:
        return False
    return all(_BASE32_CHAR.match(ch) for ch in s)


def totp_now(secret: str, *, at: Optional[float] = None,
             digits: int = _DIGITS, period: int = _PERIOD) -> str:
    """Ma TOTP hien tai cho `secret`. `at` = epoch giay (mac dinh: bay gio).

    Tra ve chuoi dung `digits` chu so, da dem '0' dau neu can.
    """
    key = base64.b32decode(normalize_secret(secret), casefold=True)
    counter = int((time.time() if at is None else at) // period)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    # Rut gon dong (RFC 4226 §5.3): 4 bit cuoi chon diem bat dau 4 byte.
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code_int % (10 ** digits)).zfill(digits)


def seconds_remaining(*, at: Optional[float] = None, period: int = _PERIOD) -> float:
    """Con bao nhieu giay nua thi ma doi. De noi goi doi qua moc neu can."""
    now = time.time() if at is None else at
    return period - (now % period)
