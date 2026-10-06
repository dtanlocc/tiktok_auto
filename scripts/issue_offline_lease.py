"""Ky mot lease OFFLINE bang khoa rieng cua operator. Khong can server nao.

⛔ DUONG NAY LA OPTION 1 TRONG PROPOSAL, da duoc can nhac san. Xem
docs/hardening/commercial-client-protection-20260904/proposals/ - "Hardened
local client": license duoc kiem offline, khong co control plane. Du an chon
Option 2, nhung Option 1 van la mot duong hop le voi dung mot cai gia duoc ghi
ro: **bo kiem chay tren may cua khach**.

⛔ VA PHAN LON DUONG NAY DA CO SAN, khong phai viet moi:
  - launcher doc lease TU FILE, khong goi mang (`lib.rs:read_lease`);
  - giao dien da xu ly truong hop offline - `SecureBootstrap.tsx` co dong
    "A still-valid offline lease remains usable; status below decides.";
  - `LeaseVerifier` + `LeaseClaims.assert_runtime_valid` da kiem day du chu ky,
    device_id, han dung va phien ban toi thieu.
Thieu dung mot thu: cong cu KY. Day la no.

CAI MAT SO VOI CONTROL PLANE, noi truoc khi dung:

  **Khong thu hoi duoc.** Lease da phat la hop le cho den khi het han; khong co
  ai de hoi "key nay con hieu luc khong". Nen han phai NGAN (mac dinh 30 ngay)
  va thu hoi = ngung phat lai. Dat han mot nam nghia la cho khong mot nam.

  **Khong co auto-update, khong co release manifest.** Ban co license binh thuong
  tai backend tu control plane roi doi chieu manifest da ky; khong co server thi
  phai tu gui backend va bo qua duong do.

CACH DUNG, ba buoc:

  1. Khach chay app lan dau. App tu sinh khoa thiet bi va hien **device id**
     dang `device_<40 ky tu hex>`. Khach gui con so do cho anh.
     (device_id = sha256(khoa cong khai cua thiet bi)[..20], `lib.rs:device_id`)
  2. Anh ky:
       uv run python scripts/issue_offline_lease.py \
         --operator-directory D:/tkauto-operator-dev \
         --device-id device_abc... --license-id lic-khach-01 \
         --expires-days 30 --max-accounts 200 --max-tabs 4 \
         --features accounts.manage,upload.video \
         --output khach-01.lease
  3. Khach dat file vao:
       %LOCALAPPDATA%\\com.tiktokauto.desktop\\license\\current.lease
     (`lib.rs:lease_path`)
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.security.license import (  # noqa: E402
    LeaseClaims,
    LeaseSigner,
    LeaseVerifier,
)


def _parse_features(raw: str) -> tuple[str, ...]:
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    # ⛔ Khong de LeaseClaims la noi duy nhat bao loi: no se noi "features chua
    # mot identifier khong hop le" ma khong noi la cai nao, va nguoi ky dang
    # ngoi truoc terminal chu khong doc traceback.
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise SystemExit(f"Feature bi lap: {value!r}")
        seen.add(value)
    return values


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ky mot lease offline, khong can control plane."
    )
    parser.add_argument("--operator-directory", required=True, type=Path)
    parser.add_argument("--device-id", required=True,
                        help="Lay tu man hinh kich hoat cua khach, dang device_<hex>.")
    parser.add_argument("--license-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--plan", default="offline")
    parser.add_argument("--features", default="")
    parser.add_argument("--max-accounts", type=int, default=100)
    parser.add_argument("--max-tabs", type=int, default=4)
    parser.add_argument("--channel", default="stable",
                        choices=("internal", "beta", "stable"))
    # ⛔ MAC DINH 30 NGAY, co y chon ngan. Khong co duong thu hoi nao khac.
    parser.add_argument("--expires-days", type=int, default=30)
    parser.add_argument("--minimum-version", default="0.1.0")
    parser.add_argument("--minimum-backend-version", default="0.1.0")
    args = parser.parse_args()

    if args.expires_days < 1:
        raise SystemExit("--expires-days phai >= 1.")
    if args.expires_days > 365:
        raise SystemExit(
            "--expires-days > 365 bi tu choi: khong co duong thu hoi, nen mot "
            "lease dai la cho khong dung do lau. Ky lai moi thang."
        )

    operator = args.operator_directory.expanduser().resolve()
    bundle_path = operator / "desktop-public-keys.json"
    private_path = operator / "keys" / "lease-private.pem"
    for path in (bundle_path, private_path):
        if not path.is_file():
            raise SystemExit(f"Khong thay {path}. Tao thu muc operator bang "
                             "scripts/initialize_operator_environment.py truoc.")

    bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
    license_keys: dict[str, str] = bundle.get("license_public_keys") or {}
    if len(license_keys) != 1:
        raise SystemExit(
            "desktop-public-keys.json phai co dung MOT khoa lease de biet ky "
            f"bang kid nao; dang co {len(license_keys)}."
        )
    key_id = next(iter(license_keys))

    now = int(time.time())
    claims = LeaseClaims(
        license_id=args.license_id,
        device_id=args.device_id,
        plan=args.plan,
        features=_parse_features(args.features),
        max_accounts=args.max_accounts,
        max_tabs=args.max_tabs,
        channel=args.channel,
        minimum_version=args.minimum_version,
        minimum_backend_version=args.minimum_backend_version,
        issued_at=now,
        not_before=now,
        expires_at=now + args.expires_days * 86_400,
        jti=f"offline-{secrets.token_hex(12)}",
    )

    signer = LeaseSigner.from_pem(key_id, private_path.read_bytes())
    token = signer.sign(claims)

    # ⛔ TU KIEM LAI BANG KHOA CONG KHAI TRUOC KHI GHI RA FILE. Mot lease ky sai
    # kid, hoac mot bundle khong khop khoa rieng, chi lo ra khi khach mo app -
    # tuc sau khi anh da gui file di. Kiem o day ton mot phan nghin giay.
    verifier = LeaseVerifier.from_pem_mapping(license_keys)
    envelope = verifier.verify(token)
    envelope.claims.assert_runtime_valid(
        device_id=args.device_id,
        app_version=args.minimum_version,
        component="desktop",
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(token, encoding="utf-8")

    print(f"Da ky lease: {args.output}")
    print(f"  kid         : {key_id}")
    print(f"  license_id  : {claims.license_id}")
    print(f"  device_id   : {claims.device_id}")
    print(f"  plan        : {claims.plan}")
    print(f"  features    : {', '.join(claims.features) or '(khong co)'}")
    print(f"  gioi han    : {claims.max_accounts} account, {claims.max_tabs} tab")
    print(f"  het han     : {time.strftime('%d/%m/%Y %H:%M', time.localtime(claims.expires_at))}")
    print()
    print("Khach dat file nay vao:")
    print(r"  %LOCALAPPDATA%\com.tiktokauto.desktop\license\current.lease")


if __name__ == "__main__":
    main()
