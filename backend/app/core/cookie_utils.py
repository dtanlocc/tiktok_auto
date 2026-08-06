# File: backend/app/core/cookie_utils.py
"""
Chuyen doi cookies giua 2 dang:
  A) Dang JSON (mang object kieu Playwright): [{"name":..,"value":..,"domain":..,"path":..}, ...]
     -> day la dang app LUU NOI BO (cookies_json) va dung cho inject_cookies/add_cookies.
  B) Dang CHUOI header (document.cookie / Cookie:): "name=value; name=value; ..."
     -> dang hay copy tu trinh duyet (vd file test_cookies.txt).

- Import: chap nhan CA HAI dang (tu dong nhan dien) -> tra ve List[Dict] Playwright.
- Export: xuat ra dang CHUOI (B) cho de dan lai/nhin.
"""
import json
from typing import List, Dict, Any

# TikTok dat cookie phien chinh (sessionid, sid_*, uid_tt...) tren .tiktok.com.
# Cookie dang chuoi khong mang thong tin domain nen ta gan mac dinh nay.
_TIKTOK_DOMAIN = ".tiktok.com"


def parse_cookies_any(raw: str) -> List[Dict[str, Any]]:
    """Nhan cookies dang JSON HOAC dang chuoi 'a=b; c=d' -> List[Dict] Playwright.
    Chuoi rong / None -> []. JSON hong ma khong phai chuoi hop le -> []."""
    if not raw:
        return []
    raw = raw.strip()
    if not raw:
        return []

    # 1) Dang JSON: bat dau bang '[' (mang) hoac '{' (object)
    if raw[0] in "[{":
        try:
            data = json.loads(raw)
        except Exception:
            data = None
        if isinstance(data, list):
            # Da la mang object Playwright -> giu nguyen (nhu hanh vi cu json.loads).
            return [c for c in data if isinstance(c, dict) and c.get("name")]
        if isinstance(data, dict):
            # Object cookie don le
            if "name" in data and "value" in data:
                return [data]
            # Object {name: value, ...}
            return _pairs_to_cookies(list(data.items()))
        # JSON hong -> thu coi nhu chuoi ben duoi

    # 2) Dang chuoi header 'name=value; name=value; ...'
    return _cookie_string_to_list(raw)


def _cookie_string_to_list(s: str) -> List[Dict[str, Any]]:
    pairs = []
    for part in s.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        if name:
            pairs.append((name, value.strip()))
    return _pairs_to_cookies(pairs)


def _pairs_to_cookies(pairs) -> List[Dict[str, Any]]:
    """Cap (name, value) -> object Playwright. GIU DAY DU (khong khu trung) va
    dung THU TU goc -> xuat lai duoc y het chuoi ban dau. Viec khu trung (neu can)
    de luc INJECT (add_cookies) xu ly, khong lam mat du lieu khi luu/xuat."""
    return [
        {"name": str(name), "value": "" if value is None else str(value),
         "domain": _TIKTOK_DOMAIN, "path": "/"}
        for name, value in pairs
    ]


def cookies_to_string(cookies: List[Dict[str, Any]]) -> str:
    """List[Dict] Playwright -> chuoi 'name=value; name=value; ...;' (dang txt).
    GIU DAY DU moi cookie theo dung thu tu (khong khu trung) va co dau ';' cuoi
    -> khop dinh dang file test_cookies.txt."""
    parts = []
    for c in cookies or []:
        if not isinstance(c, dict):
            continue
        name = c.get("name")
        if not name:
            continue
        parts.append(f"{name}={c.get('value', '')}")
    if not parts:
        return ""
    return "; ".join(parts) + ";"
