"""Mat truoc chay TAY cho lop dieu khien - buoc dau cua con bot.

⛔ DAY CHI LA MOT MAT TRUOC. Moi lenh nam trong
`app/use_cases/control/commands.py`; Telegram hay mail sau nay goi dung nhung
lenh do, nen them lenh moi la ca ba mat truoc deu co ngay.

Dung:
  uv run python scripts/tkauto_bot.py                  # liet ke lenh
  uv run python scripts/tkauto_bot.py trang_thai
  uv run python scripts/tkauto_bot.py tren_nguong nguong=1000 nuoc=US
  uv run python scripts/tkauto_bot.py nick ten=xoslan855
  uv run python scripts/tkauto_bot.py gom_tren_nguong nguong=1000 --xac-nhan
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
os.environ.setdefault("SECURITY_MODE", "development")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.use_cases.control import commands  # noqa: F401  (dang ky lenh)
from app.use_cases.control.registry import (  # noqa: E402
    ConfirmationRequired,
    UnknownCommand,
    describe,
    run,
)


def _ep_kieu(gia_tri: str):
    """"1000" -> 1000, "true" -> True, con lai giu nguyen chuoi."""
    thap = gia_tri.strip().lower()
    if thap in ("true", "false"):
        return thap == "true"
    try:
        return int(gia_tri)
    except ValueError:
        return gia_tri


def main() -> int:
    doi_so = [a for a in sys.argv[1:] if a != "--xac-nhan"]
    xac_nhan = "--xac-nhan" in sys.argv[1:]

    if not doi_so:
        print("Lệnh có sẵn:\n")
        for lenh in describe():
            dau = "  [SỬA DỮ LIỆU] " if lenh["mutates"] else "  "
            print(f"{dau}{lenh['name']:<18} {lenh['summary']}")
            for ten, mo_ta in lenh["params"].items():
                print(f"      {ten:<10} {mo_ta}")
        print("\nLệnh [SỬA DỮ LIỆU] cần thêm --xac-nhan.")
        return 0

    ten, *cap = doi_so
    tham_so = {}
    for muc in cap:
        if "=" not in muc:
            print(f"Tham số phải dạng ten=gia_tri, nhận được: {muc!r}")
            return 2
        khoa, _, gia_tri = muc.partition("=")
        tham_so[khoa.strip()] = _ep_kieu(gia_tri)

    try:
        ket_qua = run(ten, tham_so, confirm=xac_nhan)
    except UnknownCommand as exc:
        print(exc)
        return 2
    except ConfirmationRequired as exc:
        print(f"{exc}\nThêm --xac-nhan để chạy thật.")
        return 3
    except TypeError as exc:
        print(f"Tham số không đúng: {exc}")
        return 2
    print(json.dumps(ket_qua, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
