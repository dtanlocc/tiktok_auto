"""Dung file secret cho Edge Function, tu thu muc operator.

⛔ FILE DUOC GHI RA NGOAI REPO, trong thu muc operator. Ban dau
docs/SUPABASE-KEY.md huong dan ghi vao `supabase/.env` va do la mot buoc lui:
no dat khoa rieng ky lease VAO TRONG cay lam viec cua git. File do co bi
gitignore, nhung "co bi gitignore" la mot lop bao ve, con "khong bao gio nam
trong repo" la khong can lop nao. Moi bi mat khac cua operator da nam ngoai
repo tu truoc; cai nay khong co ly do gi de khac.

⛔ KHOA RIENG DUOC GHI DANG BASE64 MOT DONG, khong co dau -----BEGIN-----.
`importLeasePrivateKey` go dau PEM va moi khoang trang truoc khi decode, nen
hai dang deu chay - nhung mot dong thi khong the bi mat dau dong khi di qua
`supabase secrets set`, dashboard, hay mot lan copy-paste. Mot PEM mat dau dong
la mot function CHET luc deploy chu khong phai luc test.
`test_supabase_edge_functions.py::test_the_lease_key_secret_works_as_a_single_line_without_pem_armor`
la bai test giu cho dieu do dung.

Dung:
  uv run python scripts/supabase_make_function_env.py \\
      --operator-directory D:/tkauto-operator-dev
Roi:
  tools/supabase-cli/node_modules/.bin/supabase secrets set \\
      --env-file D:/tkauto-operator-dev/supabase-functions.env
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_OUTPUT_NAME = "supabase-functions.env"


def _read(path: Path, *, minimum: int = 1) -> str:
    if not path.is_file():
        raise SystemExit(f"Khong thay {path}.")
    value = path.read_text(encoding="utf-8").strip()
    if len(value) < minimum:
        raise SystemExit(f"{path.name} ngan bat thuong ({len(value)} ky tu).")
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--operator-directory", required=True, type=Path)
    parser.add_argument("--minimum-client-version", default="0.1.0")
    parser.add_argument("--minimum-backend-version", default="0.1.0")
    parser.add_argument("--release-bucket", default="releases")
    parser.add_argument(
        "--lease-ttl-seconds",
        type=int,
        default=0,
        help="Bo trong thi dung mac dinh 3600 trong service.ts. "
             "Ha xuong de thu hoi co hieu luc nhanh hon (san 300).",
    )
    args = parser.parse_args()

    operator = args.operator_directory.expanduser().resolve()
    if not operator.is_dir():
        raise SystemExit(f"Khong thay thu muc operator: {operator}")

    pepper = _read(operator / "secrets" / "license-key-pepper", minimum=32)
    bundle = json.loads(_read(operator / "desktop-public-keys.json"))
    lease_keys = bundle.get("license_public_keys") or {}
    if len(lease_keys) != 1:
        raise SystemExit(
            "desktop-public-keys.json phai co dung MOT khoa lease de biet dat "
            f"kid nao; dang co {len(lease_keys)}."
        )
    key_id = next(iter(lease_keys))

    pem = _read(operator / "keys" / "lease-private.pem", minimum=80)
    single_line = re.sub(r"-----[A-Z ]+-----|\s+", "", pem)
    if not single_line or len(single_line) < 40:
        raise SystemExit("Khoa lease khong giai ma duoc thanh mot dong base64.")

    if args.lease_ttl_seconds and not 300 <= args.lease_ttl_seconds <= 7 * 86_400:
        raise SystemExit("--lease-ttl-seconds phai trong [300, 604800].")

    # ⛔ BI MAT VE TAI: doc tu thu muc operator neu co, va KHONG tu sinh neu
    # thieu. Ban Python dung chinh file nay (`download_grant_secret_file`), nen
    # sinh mot gia tri moi o day se lam hai duong phat ve khac nhau - va mot ve
    # do Supabase phat se bi control plane Python tu choi, hoac nguoc lai.
    grant_path = operator / "secrets" / "download-grant-secret"
    grant_secret = ""
    if grant_path.is_file():
        grant_secret = grant_path.read_text(encoding="utf-8").strip()
        if len(grant_secret) < 32:
            raise SystemExit(
                f"{grant_path.name} chi dai {len(grant_secret)} ky tu; can >= 32. "
                "Tao lai bang scripts/initialize_operator_environment.py."
            )

    lines = [
        "# File nay do scripts/supabase_make_function_env.py sinh ra.",
        "# ⛔ CO KHOA RIENG KY LEASE. Khong copy vao repo, khong gui qua chat.",
        "# Dat len Supabase bang:",
        f"#   supabase secrets set --env-file {operator / _OUTPUT_NAME}",
        "#",
        "# SUPABASE_URL va SUPABASE_SERVICE_ROLE_KEY khong can dat: Supabase tu",
        "# cap hai bien do cho moi Edge Function.",
        f"TKAUTO_LICENSE_KEY_PEPPER={pepper}",
        f"TKAUTO_LEASE_SIGNING_KEY_ID={key_id}",
        f"TKAUTO_LEASE_SIGNING_PRIVATE_KEY_PEM={single_line}",
        f"TKAUTO_MINIMUM_CLIENT_VERSION={args.minimum_client_version}",
        f"TKAUTO_MINIMUM_BACKEND_VERSION={args.minimum_backend_version}",
    ]
    if args.lease_ttl_seconds:
        lines.append(f"TKAUTO_LEASE_TTL_SECONDS={args.lease_ttl_seconds}")
    if grant_secret:
        lines.append(f"TKAUTO_DOWNLOAD_GRANT_SECRET={grant_secret}")
        lines.append(f"TKAUTO_RELEASE_BUCKET={args.release_bucket}")

    out = operator / _OUTPUT_NAME
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # ⛔ KHONG IN GIA TRI NAO RA MAN HINH. Man hinh di vao log, anh chup va
    # lich su terminal; ca pepper va khoa rieng deu khong duoc di theo.
    print(f"Da ghi {out}")
    print(f"  pepper   : {len(pepper)} ky tu")
    print(f"  kid      : {key_id}")
    print(f"  khoa ky  : {len(single_line)} ky tu base64, mot dong")
    print(f"  san ban  : {args.minimum_client_version} / {args.minimum_backend_version}")
    if grant_secret:
        print(f"  ve tai   : {len(grant_secret)} ky tu, bucket '{args.release_bucket}'")
    else:
        print("  ve tai   : KHONG CO - hai function releases/artifacts se tra 500.")
        print(f"             Thieu {grant_path}")
    print()
    print("Dat len Supabase:")
    print("  tools/supabase-cli/node_modules/.bin/supabase secrets set \\")
    print(f"      --env-file {out}")


if __name__ == "__main__":
    main()
