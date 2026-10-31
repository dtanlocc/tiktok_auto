"""Quan li key license tren Supabase, tu may operator.

⛔ KHONG DI QUA EDGE FUNCTION. Day la duong admin: no noi THANG vao Postgres
cua Supabase bang mat khau database. Khong co endpoint admin nao tren Edge
Function, va do la co y - mot endpoint tao key cong khai tren Internet la thu
nguy hiem nhat trong ca he thong, con ket noi Postgres thi chi may nao co mat
khau moi vao duoc.

⛔ PEPPER KHONG BAO GIO VAO DATABASE. `key_digest` trong bang la
HMAC-SHA256(pepper, key). Pepper nam o thu muc operator va trong secret cua
Edge Function - hai noi, khong co noi nao la database. Nen doc duoc toan bo
bang `license.licenses` cung khong dung lai duoc key nao.

⛔ VA KEY THAT CHI HIEN RA MOT LAN, GHI VAO FILE, KHONG IN RA MAN HINH. Man
hinh di vao log, vao anh chup, vao lich su terminal. File di vao thu muc
operator - thu muc KHONG nam trong repo.

Dung:

  # 1. Ap schema (lan dau, hoac sau khi co migration moi)
  uv run python scripts/supabase_license_admin.py apply-schema \\
      --operator-directory D:/tkauto-operator-dev

  # 2. Phat mot key cho mot nguoi trong nhom
  uv run python scripts/supabase_license_admin.py create \\
      --operator-directory D:/tkauto-operator-dev \\
      --customer-reference ban-A --plan nhom \\
      --group hn01 --role member \\
      --features accounts.manage,upload.video \\
      --max-accounts 200 --max-tabs 4 --max-devices 2 --expires-days 30

  # 3. Xem tinh hinh
  uv run python scripts/supabase_license_admin.py list \\
      --operator-directory D:/tkauto-operator-dev

  # 4. Thu hoi
  uv run python scripts/supabase_license_admin.py revoke \\
      --operator-directory D:/tkauto-operator-dev \\
      --license-id lic_... --reason "het han thanh toan"
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.security.license import (  # noqa: E402
    group_feature,
    role_feature,
)
from control_plane.app.models import new_id  # noqa: E402
from control_plane.app.security import (  # noqa: E402
    digest_license_key,
    generate_license_key,
)

_MIGRATIONS = Path(__file__).resolve().parents[1] / "supabase" / "migrations"
#: Ten file secret trong thu muc operator.
_DATABASE_URL_FILE = "secrets/supabase-database-url"
_PEPPER_FILE = "secrets/license-key-pepper"


def _read_secret(operator: Path, relative: str, *, minimum: int = 1) -> str:
    path = operator / relative
    if not path.is_file():
        raise SystemExit(
            f"Thieu {relative} trong {operator}.\n"
            "  - license-key-pepper: do initialize_operator_environment.py tao.\n"
            "  - supabase-database-url: tu dan vao, lay o Supabase Dashboard ->\n"
            "    Project Settings -> Database -> Connection string -> URI.\n"
            "    Dung ban 'Session pooler' (cong 5432) cho apply-schema."
        )
    value = path.read_text(encoding="utf-8").strip()
    if len(value) < minimum:
        raise SystemExit(f"{relative} ngan bat thuong ({len(value)} ky tu).")
    return value


def _connect(operator: Path):
    # ⛔ Import muon de cac lenh khong can DB van chay duoc khi thieu psycopg.
    import psycopg

    url = _read_secret(operator, _DATABASE_URL_FILE, minimum=20)
    if not url.startswith(("postgresql://", "postgres://")):
        raise SystemExit(
            "supabase-database-url phai la URI postgresql://. Day khong phai "
            "URL du an (https://<ref>.supabase.co) ma la connection string cua "
            "database."
        )
    # ⛔ `prepare_threshold=None` LA BAT BUOC NEU DUNG POOLER CHE DO
    # TRANSACTION (cong 6543). Pooler do khong giu phien giua cac cau lenh, nen
    # prepared statement cua psycopg bi "mat" va bao loi kieu
    # "prepared statement ... does not exist" mot cach khong lien quan gi den
    # cau lenh dang chay. Dat o day thi ca hai cong deu dung duoc.
    return psycopg.connect(url, prepare_threshold=None, autocommit=False)


def _mask(value: str) -> str:
    if len(value) <= 12:
        return "*" * len(value)
    return f"{value[:7]}...{value[-4:]} ({len(value)} ky tu)"


def _parse_features(raw: str) -> list[str]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    seen: set[str] = set()
    for value in values:
        if value in seen:
            raise SystemExit(f"Feature bi lap: {value!r}")
        seen.add(value)
    return values


def cmd_apply_schema(args: argparse.Namespace) -> None:
    files = sorted(_MIGRATIONS.glob("*.sql"))
    if not files:
        raise SystemExit(f"Khong thay file .sql nao trong {_MIGRATIONS}.")
    with _connect(args.operator_directory) as connection:
        with connection.cursor() as cursor:
            for path in files:
                print(f"  -> {path.name}")
                # psycopg gui cau lenh KHONG CO tham so bang simple protocol,
                # nen mot file nhieu cau lenh chay duoc nguyen ven.
                cursor.execute(path.read_text(encoding="utf-8"))
        connection.commit()
    print(f"Da ap {len(files)} file migration.")


def cmd_create(args: argparse.Namespace) -> None:
    operator = args.operator_directory
    pepper = _read_secret(operator, _PEPPER_FILE, minimum=32)

    features = _parse_features(args.features)
    if args.group:
        features.append(group_feature(args.group))
    if args.role:
        features.append(role_feature(args.role))
    # ⛔ Loai trung SAU khi them group/role: nguoi dung co the da tu go
    # `group.hn01` vao --features.
    features = list(dict.fromkeys(features))

    license_id = new_id("lic")
    license_key = generate_license_key()
    expires_at = int(time.time()) + args.expires_days * 86_400

    with _connect(operator) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                insert into license.licenses (
                    id, key_digest, customer_reference, plan, features,
                    max_accounts, max_tabs, max_devices, channel, expires_at
                ) values (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
                """,
                (
                    license_id,
                    digest_license_key(license_key, pepper),
                    args.customer_reference,
                    args.plan,
                    json.dumps(features),
                    args.max_accounts,
                    args.max_tabs,
                    args.max_devices,
                    args.channel,
                    expires_at,
                ),
            )
            cursor.execute(
                """
                insert into license.audit_events (event_type, actor_id, subject_id, metadata)
                values ('license.created', 'operator', %s, %s::jsonb)
                """,
                (license_id, json.dumps({"customer": args.customer_reference})),
            )
        connection.commit()

    # ⛔ GHI RA FILE TRONG THU MUC OPERATOR, ngoai repo. Day la lan duy nhat
    # key ton tai dang ro: database chi co digest, va khong co duong nao tinh
    # nguoc lai.
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in args.customer_reference)
    out = operator / f"license-{safe or 'khach'}-{license_id[-8:]}.json"
    out.write_text(
        json.dumps(
            {
                "license_id": license_id,
                "license_key": license_key,
                "customer_reference": args.customer_reference,
                "plan": args.plan,
                "features": features,
                "max_accounts": args.max_accounts,
                "max_tabs": args.max_tabs,
                "max_devices": args.max_devices,
                "channel": args.channel,
                "expires_at": expires_at,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print("Da tao license.")
    print(f"  license_id : {license_id}")
    print(f"  key        : {_mask(license_key)}")
    print(f"  features   : {', '.join(features) or '(khong co)'}")
    print(f"  het han    : {time.strftime('%d/%m/%Y %H:%M', time.localtime(expires_at))}")
    print(f"  key day du : {out}")


def cmd_verify_key(args: argparse.Namespace) -> None:
    """Kiem mot chuoi key co khop license nao khong, khong kich hoat gi.

    ⛔ CAI NAY TON TAI VI MOT VONG HOI-DAP DA MAT. May chu tra 401 cho ca ba
    truong hop rat khac nhau - key sai, chu ky thiet bi hong, dong ho may lech
    - va khong co cach nao doan tu ngoai. Lenh nay tra loi dut diem cau hoi
    "key nay co dung khong", de loai bo mot kha nang truoc khi di tim hai cai
    kia.

    ⛔ KEY DUOC GO/DAN VAO, KHONG NAM TREN DONG LENH. Doi so dong lenh di vao
    lich su shell va vao danh sach tien trinh; `getpass` thi khong hien ra man
    hinh va khong luu lai dau.
    """
    import getpass

    pepper = _read_secret(args.operator_directory, _PEPPER_FILE, minimum=32)
    # ⛔ `getpass` TREO KHI STDIN LA PIPE TREN WINDOWS. `win_getpass` doc thang
    # tu console bang msvcrt chu khong doc stdin, nen mot lenh dang
    # `echo key | ... verify-key` khong bao gio tra ve - no ngoi cho ai do go
    # vao cua so console, va phai kill bang tay. Da gap dung the khi thu lenh
    # nay. Chi dung getpass khi that su co terminal; con lai doc stdin nhu moi
    # cong cu khac.
    if sys.stdin is not None and sys.stdin.isatty():
        raw = getpass.getpass("Dan key vao day (khong hien ra man hinh): ")
    else:
        raw = sys.stdin.readline()
    key = raw.strip()
    if not key:
        raise SystemExit("Khong nhan duoc key nao.")

    # ⛔ Bao TRUOC nhung loi hinh dang, vi chung la nguyen nhan thuong gap nhat
    # va digest khong phan biet duoc chung voi "key la".
    issues = []
    if any(ord(c) > 127 for c in key):
        issues.append("co ky tu khong phai ASCII (thuong do copy tu Word/chat)")
    for bad in "0189":
        if bad in key:
            issues.append(
                f"co chu so {bad!r} - key dung bang chu base32 (A-Z va 2-7) "
                f"nen khong bao gio chua 0, 1, 8 hay 9; thuong la go nham "
                f"O/I/B/g"
            )
            break
    if not key.upper().startswith("TKAUTO-"):
        issues.append("khong bat dau bang 'TKAUTO-'")
    if len(key) != 46:
        issues.append(f"dai {len(key)} ky tu, key that dai 46")

    digest = digest_license_key(key, pepper)
    with _connect(args.operator_directory) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select id, customer_reference, revoked_at, expires_at, max_devices,
                       (select count(*) from license.devices d
                         where d.license_id = l.id and d.revoked_at is null)
                  from license.licenses l where key_digest = %s
                """,
                (digest,),
            )
            row = cursor.fetchone()

    if row is None:
        print()
        print("KHONG KHOP license nao.")
        if issues:
            print("Nhung cho dang ngo trong chuoi vua nhap:")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print("  Chuoi dung hinh dang nhung khong co trong database.")
            print("  Thuong la: key cua mot license da bi xoa, hoac pepper o day")
            print("  khac pepper ma Edge Function dang dung.")
        raise SystemExit(1)

    import time

    now = int(time.time())
    print()
    print("KHOP license:")
    print(f"  license_id : {row[0]}")
    print(f"  khach      : {row[1] or '(khong ghi)'}")
    if row[2]:
        print("  trang thai : DA THU HOI -> may chu se tra 403, khong phai 401")
    elif row[3] <= now:
        print("  trang thai : HET HAN -> may chu se tra 403, khong phai 401")
    else:
        print(f"  trang thai : dang dung, con {(row[3] - now) // 86400} ngay")
        print(f"  may        : {row[5]}/{row[4]}")
        if row[5] >= row[4]:
            print("               DA DAY -> may moi se bi tu choi 403 device_limit")
        print()
        print("  Key nay DUNG. Mot loi 401 luc kich hoat luc nay chi con hai")
        print("  nguyen nhan, va ca hai deu o phia may khach:")
        print("    - dong ho may do lech qua 5 phut so voi gio thuc;")
        print("    - khoa thiet bi trong Windows Credential Manager bi hong.")


def cmd_list(args: argparse.Namespace) -> None:
    with _connect(args.operator_directory) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select id, customer_reference, plan, group_feature, role_feature,
                       active_devices, max_devices, days_left, revoked
                  from license.overview
                 order by created_at_utc desc
                 limit %s
                """,
                (args.limit,),
            )
            rows = cursor.fetchall()
    if not rows:
        print("Chua co license nao.")
        return
    print(f"{'license_id':<28} {'khach':<14} {'nhom':<12} {'may':<7} {'con lai':<9} trang thai")
    for row in rows:
        (lid, customer, plan, group, role, active, max_devices, days, revoked) = row
        status = "DA THU HOI" if revoked else ("HET HAN" if days <= 0 else "dang dung")
        print(
            f"{lid:<28} {(customer or '-'):<14} {(group or '-'):<12} "
            f"{f'{active}/{max_devices}':<7} {f'{int(days)} ngay':<9} {status}"
        )


def cmd_revoke(args: argparse.Namespace) -> None:
    with _connect(args.operator_directory) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                update license.licenses
                   set revoked_at = extract(epoch from now())::bigint,
                       revoked_reason = %s
                 where id = %s and revoked_at is null
                """,
                (args.reason, args.license_id),
            )
            if cursor.rowcount == 0:
                raise SystemExit(
                    f"Khong co license nao dang hieu luc voi id {args.license_id}."
                )
            cursor.execute(
                """
                insert into license.audit_events (event_type, actor_id, subject_id, metadata)
                values ('license.revoked', 'operator', %s, %s::jsonb)
                """,
                (args.license_id, json.dumps({"reason": args.reason})),
            )
        connection.commit()
    # ⛔ Thu hoi chi co hieu luc o LAN GIA HAN KE TIEP. Lease da phat van hop le
    # cho den khi het han, vi client kiem no offline bang chu ky. Voi TTL mac
    # dinh 3600 thi do la toi mot gio.
    # ⛔ DO TRE THAT LA BAO NHIEU. `renew_license` goi /lease/renew va mot 403
    # se lam `invalidate_local_entitlement` chay ngay - dung backend, xoa .exe,
    # xoa lease. Giao dien goi renew moi 15 phut (SecureBootstrap.tsx) va mot
    # lan nua luc mo app. Nen:
    #   - app dang mo, co mang : <= 15 phut
    #   - mo lai app           : ngay lap tuc
    #   - may offline          : khong co 403 nao de nhan, nen phai doi lease
    #                            het han (<= 1 gio) roi watchdog 60 giay doc ra.
    # Truoc day cho nay chi noi "toi 1 gio", tuc noi qua do tre trong truong
    # hop thuong gap nhat.
    print(f"Da thu hoi {args.license_id}.")
    print("  Co hieu luc o dot gia han ke tiep:")
    print("    app dang mo, co mang : toi 15 phut")
    print("    mo lai app           : ngay lap tuc")
    print("    may offline          : khi lease het han (toi 1 gio) + 60 giay")
    print("  Luc do backend bi dung va file .exe bi xoa khoi may khach.")


def cmd_revoke_device(args: argparse.Namespace) -> None:
    with _connect(args.operator_directory) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                update license.devices
                   set revoked_at = extract(epoch from now())::bigint
                 where id = %s and revoked_at is null
                """,
                (args.device_id,),
            )
            if cursor.rowcount == 0:
                raise SystemExit(f"Khong co thiet bi dang hieu luc voi id {args.device_id}.")
            cursor.execute(
                """
                insert into license.audit_events (event_type, actor_id, subject_id, metadata)
                values ('device.revoked', 'operator', %s, %s::jsonb)
                """,
                (args.device_id, json.dumps({"reason": args.reason})),
            )
        connection.commit()
    print(f"Da thu hoi thiet bi {args.device_id}; o cho may do nay duoc tra lai cho license.")


class RestoreRefused(ValueError):
    """Khong go thu hoi duoc, kem ly do doc duoc cho nguoi van hanh."""


def restore_device(cursor, device_id: str, reason: str, actor: str) -> dict:
    """Go thu hoi mot thiet bi - doi xung voi revoke.

    ⛔ HAM NAY TON TAI VI MOT MAY DA BI KET THAT (31/10/2026). Thu hoi thiet
    bi roi thi may do khong con duong nao quay lai:

      - kich hoat lai bang CHINH key cu -> `device_revoked`: cung license thi
        thu hoi van chan;
      - kich hoat bang key MOI          -> HTTP 409 `device_conflict`: chuyen
        thiet bi sang license khac doi license CU phai da chet, ma no con 13 ngay.

    Duong duy nhat con lai la thu hoi ca license - giet mot key con han chi de
    cuu mot cai may. Mot hanh dong thu hoi do chinh operator lam khong nen la
    mot ban an vinh vien.

    ⛔ VA NO KHONG DUOC LACH `max_devices`. Go thu hoi la THEM LAI mot may dang
    hoat dong; license da day thi phai tu choi, khong thi day la duong vong de
    vuot gioi han so may ma khong ai thay.

    ⛔ DUNG CHUNG CHO CA CLI VA GIAO DIEN. Hai ban kiem tra o hai noi thi som
    muon cung lech, va cai lech se nam o phia nao it duoc chay hon.
    """
    cursor.execute(
        """
        select d.license_id, d.revoked_at,
               l.revoked_at, l.expires_at, l.max_devices, l.customer_reference
          from license.devices d
          join license.licenses l on l.id = d.license_id
         where d.id = %s
           for update of d, l
        """,
        (device_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise LookupError(f"Khong thay thiet bi {device_id}.")
    license_id, device_revoked, lic_revoked, expires_at, max_devices, khach = row
    if device_revoked is None:
        raise RestoreRefused(
            f"Thiet bi {device_id} dang hieu luc, khong can go thu hoi."
        )
    now = int(time.time())
    if lic_revoked is not None or expires_at <= now:
        raise RestoreRefused(
            f"License {license_id} ({khach or 'khong ten'}) da thu hoi hoac het "
            "han; go thu hoi thiet bi khong giup gi. Phat key moi va kich hoat "
            "lai - thiet bi se tu chuyen sang key do."
        )
    cursor.execute(
        "select count(*) from license.devices "
        " where license_id = %s and revoked_at is null",
        (license_id,),
    )
    dang_dung = cursor.fetchone()[0]
    if dang_dung >= max_devices:
        raise RestoreRefused(
            f"License {license_id} ({khach or 'khong ten'}) da dung "
            f"{dang_dung}/{max_devices} may. Thu hoi mot may khac truoc, hoac "
            "phat key co nhieu may hon."
        )
    cursor.execute(
        "update license.devices set revoked_at = null, last_seen_at = %s "
        " where id = %s and revoked_at is not null",
        (now, device_id),
    )
    if cursor.rowcount == 0:
        raise RestoreRefused(f"Khong go duoc thu hoi cho {device_id}.")
    cursor.execute(
        """
        insert into license.audit_events (event_type, actor_id, subject_id, metadata)
        values ('device.restored', %s, %s, %s::jsonb)
        """,
        (actor, device_id, json.dumps({"reason": reason, "license_id": license_id})),
    )
    return {
        "license_id": license_id,
        "customer_reference": khach,
        "dang_dung": dang_dung + 1,
        "max_devices": max_devices,
    }


def cmd_restore_device(args: argparse.Namespace) -> None:
    with _connect(args.operator_directory) as connection:
        with connection.cursor() as cursor:
            try:
                ket_qua = restore_device(
                    cursor, args.device_id, args.reason, "operator"
                )
            except (LookupError, RestoreRefused) as exc:
                raise SystemExit(str(exc)) from exc
        connection.commit()
    print(
        f"Da go thu hoi thiet bi {args.device_id}; may do dung lai duoc key cua "
        f"'{ket_qua['customer_reference'] or 'khong ten'}' "
        f"({ket_qua['dang_dung']}/{ket_qua['max_devices']} may)."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    # ⛔ CO NHAN CA HAI THU TU, va do la mot loi da xay ra thay vi mot su tu te.
    # Ban dau `--operator-directory` chi nam o parser goc, nen
    # `... apply-schema --operator-directory X` - dung thu tu ma con nguoi go,
    # va dung thu tu ghi trong docs/SUPABASE-KEY.md - bao "the following
    # arguments are required: --operator-directory" trong khi no DANG o tren
    # dong lenh. Mot thong bao loi noi sai su that.
    #
    # `default=SUPPRESS` o parser con la phan bat buoc: khong co no thi khi co
    # duoc dat o truoc subcommand, parser con van ghi None de len gia tri do
    # (cai bay co dien cua argparse) va co bien thanh mat.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--operator-directory", type=Path, default=argparse.SUPPRESS
    )
    parser.add_argument("--operator-directory", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "apply-schema",
        parents=[common],
        help="Chay cac file supabase/migrations/*.sql",
    )

    create = sub.add_parser("create", parents=[common], help="Phat mot license key moi")
    create.add_argument("--customer-reference", default="")
    create.add_argument("--plan", default="nhom")
    create.add_argument("--features", default="")
    create.add_argument("--group", default="", help="group_id, se thanh feature group.<id>")
    create.add_argument("--role", default="", help="role, se thanh feature role.<ten>")
    create.add_argument("--max-accounts", type=int, default=100)
    create.add_argument("--max-tabs", type=int, default=4)
    create.add_argument("--max-devices", type=int, default=1)
    create.add_argument("--channel", default="stable",
                        choices=("internal", "beta", "stable"))
    create.add_argument("--expires-days", type=int, default=30)

    sub.add_parser(
        "verify-key", parents=[common],
        help="Kiem mot chuoi key co khop license nao khong",
    )

    listing = sub.add_parser("list", parents=[common], help="Xem license dang co")
    listing.add_argument("--limit", type=int, default=50)

    revoke = sub.add_parser("revoke", parents=[common], help="Thu hoi mot license")
    revoke.add_argument("--license-id", required=True)
    revoke.add_argument("--reason", required=True)

    revoke_device = sub.add_parser("revoke-device", parents=[common], help="Thu hoi mot thiet bi")
    revoke_device.add_argument("--device-id", required=True)
    revoke_device.add_argument("--reason", default="")

    restore_device = sub.add_parser(
        "restore-device", parents=[common], help="Go thu hoi mot thiet bi"
    )
    restore_device.add_argument("--device-id", required=True)
    restore_device.add_argument("--reason", default="")

    args = parser.parse_args()
    if args.operator_directory is None:
        raise SystemExit(
            "Thieu --operator-directory. Dat o truoc hay sau subcommand deu "
            "duoc, vi du:\n"
            "  scripts/supabase_license_admin.py apply-schema "
            "--operator-directory D:/tkauto-operator-dev"
        )
    args.operator_directory = args.operator_directory.expanduser().resolve()
    if not args.operator_directory.is_dir():
        raise SystemExit(f"Khong thay thu muc operator: {args.operator_directory}")

    if args.command == "create" and not 1 <= args.expires_days <= 3_650:
        raise SystemExit("--expires-days phai trong [1, 3650].")

    {
        "apply-schema": cmd_apply_schema,
        "create": cmd_create,
        "verify-key": cmd_verify_key,
        "list": cmd_list,
        "revoke": cmd_revoke,
        "revoke-device": cmd_revoke_device,
        "restore-device": cmd_restore_device,
    }[args.command](args)


if __name__ == "__main__":
    main()
