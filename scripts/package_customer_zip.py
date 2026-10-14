"""Dong goi ban gui khach: DUNG MOT FILE launcher, khong co backend.

⛔ KHONG BO BACKEND VAO GOI, va day la diem de nham nhat. Thu muc
`release/licensed-unsigned/<ver>/` co ca `backend-<ver>.exe`, nhung file do la
DE OPERATOR DANG KY (`supabase_release_admin.py publish`), khong phai de gui
khach. Launcher doc lease roi tai backend tu kho artifact ve
`%LOCALAPPDATA%\\...\\artifacts` va doi chieu sha256 voi manifest DA KY truoc
khi chay. Gui kem backend thi khach co mot ban khong ai kiem, va no cung khong
duoc dung - launcher chi chay ban no tu tai.

⛔ VA GOI NAY KHONG CO ENGINE TRINH DUYET. Ban friends ship san thu muc
`engine/` (549 MB); ban licensed thi khong - `ensure_binary()` tu tai ve
`%LOCALAPPDATA%\\invisible-playwright\\` o lan mo trinh duyet dau tien. Nen goi
chi 4-5 MB, doi lai lan dau khach phai doi tai ~665 MB. Dieu nay duoc noi ro
trong README di kem, vi mot ban cai 4 MB roi treo mot tieng khong giai thich
gi la cach nhanh nhat de khach tuong app hong.
"""
from __future__ import annotations

import argparse
import hashlib
import zipfile
from pathlib import Path

_README = """TIKTOK AUTO {version}

CHI MOT FILE: TikTokAuto.exe. Backend khong di kem - may se tu tai ve sau khi
kich hoat key, va doi chieu chu ky truoc khi chay.

CACH DUNG
  1. Giai nen ra mot thu muc bat ky, chay TikTokAuto.exe.
  2. Dan LICENSE KEY duoc cap vao o kich hoat.
     Key dai 46 ky tu, dang TKAUTO-XXXX-XXXX-...
     Chi gom chu A-Z va so 2-7 - KHONG co so 0, 1, 8, 9. Neu thay nhung so do
     thi la go nham (0 thay cho O, 1 thay cho I).
  3. Dan khoa CAPTCHA (OmoCaptcha) khi duoc hoi.
  4. Doi may tai backend ve (khoang 51 MB), roi app tu chay.

LAN DAU MO TRINH DUYET SE TAI THEM ~665 MB
  Engine trinh duyet (549 MB) va du lieu dinh vi (116 MB) duoc tai ve lan dau
  va dung lai cho nhung lan sau. Can mang kha va kien nhan o lan dau tien.

CAN MANG, VA CAN DONG HO DUNG GIO
  App kiem license dinh ky. Mat mang qua mot gio thi backend bi go khoi may va
  phai co mang de tai lai.
  Dong ho may lech qua 5 phut so voi gio thuc se lam kich hoat that bai - mo
  Settings > Time & language > Date & time va bat "Set time automatically".

CANH BAO SmartScreen
  File chua ky so nen Windows se canh bao o lan chay dau: bam "More info" ->
  "Run anyway".
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--release-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "release" / "licensed-unsigned",
    )
    args = parser.parse_args()

    source = (args.release_root / args.version).resolve()
    launcher = source / "TikTokAuto.exe"
    if not launcher.is_file():
        raise SystemExit(
            f"Khong thay {launcher}. Chay build_licensed_unsigned_release.ps1 truoc."
        )

    readme = _README.format(version=args.version)
    (source / "README-KHACH.txt").write_text(readme, encoding="utf-8")

    out = source / f"TikTokAuto-{args.version}-gui-khach.zip"
    if out.exists():
        out.unlink()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(launcher, "TikTokAuto.exe")
        archive.writestr("README.txt", readme)

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print(f"Da dong goi: {out}")
    print(f"  kich thuoc : {out.stat().st_size / 1048576:.1f} MB")
    print(f"  sha256     : {digest}")
    print()
    print("Gui file zip nay cho khach. KHONG gui backend-*.exe trong cung thu muc:")
    print("  no de dang ky bang supabase_release_admin.py publish, khach tu tai.")


if __name__ == "__main__":
    main()
