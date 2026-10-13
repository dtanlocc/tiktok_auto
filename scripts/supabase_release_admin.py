"""Phat hanh mot ban backend qua Supabase: ky manifest O MAY NAY, tai file len
Storage, ghi mot dong vao so dang ky.

⛔ KHOA KY RELEASE KHONG BAO GIO ROI MAY NAY, va do la ca y nghia cua cach lam
nay. Manifest duoc ky tai day bang `<operator>/keys/release-private.pem`, roi
chi CHUOI TOKEN DA KY duoc luu vao `license.releases.signed_manifest`. Edge
Function phat lai chuoi do, khong ky gi ca. Chiem duoc Supabase thi nguoi ta
cap duoc lease (dung lau app mien phi) nhung KHONG ky duoc manifest - tuc khong
gui duoc ban cai doc cho khach. Day la ranh gioi quan trong nhat trong ca he
thong; dung bao gio dua release-private.pem len may nao khac.

⛔ VA `download_url` PHAI LA URL CO DINH. No nam trong phan da ky nen khong sua
duoc theo tung request. Vi vay no tro vao function `artifacts`
(`/functions/v1/artifacts/<artifact_id>/download`) chu khong tro thang vao
signed URL cua Storage - signed URL het han sau vai phut va se lam manifest da
ky thanh vo dung. Doi ten function `artifacts` la moi manifest trong DB chet
theo.

Dung:

  # Lan dau: tao bucket private
  uv run python scripts/supabase_release_admin.py init-bucket \\
      --operator-directory D:/tkauto-operator-dev

  # Phat hanh
  uv run python scripts/supabase_release_admin.py publish \\
      --operator-directory D:/tkauto-operator-dev \\
      --file release/backend/0.1.12/tiktok-auto-backend.exe \\
      --version 0.1.12 --minimum-launcher-version 0.1.0

  # Xem / tat mot ban
  uv run python scripts/supabase_release_admin.py list --operator-directory D:/tkauto-operator-dev
  uv run python scripts/supabase_release_admin.py deactivate --operator-directory D:/tkauto-operator-dev --artifact-id artifact_...
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.security.release import (  # noqa: E402
    ReleaseManifest,
    ReleaseManifestSigner,
    ReleaseManifestVerifier,
)
from control_plane.app.models import new_id  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
_CLI = _REPO_ROOT / "tools" / "supabase-cli" / "node_modules" / ".bin" / "supabase.cmd"
_DATABASE_URL_FILE = "secrets/supabase-database-url"
_DEFAULT_BUCKET = "releases"


def _read(path: Path, *, minimum: int = 1) -> str:
    if not path.is_file():
        raise SystemExit(f"Khong thay {path}.")
    value = path.read_text(encoding="utf-8").strip()
    if len(value) < minimum:
        raise SystemExit(f"{path.name} ngan bat thuong ({len(value)} ky tu).")
    return value


def _database_url(operator: Path) -> str:
    return _read(operator / _DATABASE_URL_FILE, minimum=20)


def _project_ref(operator: Path) -> str:
    """Lay project ref tu username cua connection string.

    ⛔ Pooler nhan ra project TU USERNAME (`postgres.<ref>`), nen ref luon co o
    day va khong phai hoi them. Doc tu day thi khong the lech voi database ma
    script dang ghi vao - mot `--project-ref` go tay co the tro sang project
    khac va luc do manifest duoc ky cho mot URL khong ai phuc vu.
    """
    user = urlsplit(_database_url(operator)).username or ""
    if "." not in user:
        raise SystemExit(
            "Khong suy ra duoc project ref tu connection string. Dang dung "
            "'Direct connection'? Lay ban 'Session pooler' (username dang "
            "postgres.<ref>) trong hop thoai Connect cua dashboard."
        )
    ref = user.split(".", 1)[1]
    if not ref.isalnum() or len(ref) < 16:
        raise SystemExit(f"Project ref suy ra duoc khong hop le: {ref!r}")
    return ref


def _connect(operator: Path):
    import psycopg

    return psycopg.connect(
        _database_url(operator), prepare_threshold=None, autocommit=False
    )


def _release_signer(operator: Path) -> ReleaseManifestSigner:
    bundle = json.loads(_read(operator / "desktop-public-keys.json"))
    keys = bundle.get("release_public_keys") or {}
    if len(keys) != 1:
        raise SystemExit(
            "desktop-public-keys.json phai co dung MOT khoa release de biet ky "
            f"bang kid nao; dang co {len(keys)}."
        )
    key_id = next(iter(keys))
    pem = (operator / "keys" / "release-private.pem").read_bytes()
    return ReleaseManifestSigner.from_pem(key_id, pem)


def _run_cli(arguments: list[str], *, cwd: Path | None = None) -> None:
    if not _CLI.is_file():
        raise SystemExit(
            f"Khong thay Supabase CLI o {_CLI}. Chay `npm install` trong "
            "tools/supabase-cli/."
        )
    # ⛔ GOI SHIM .cmd, KHONG PHAI .ps1. ExecutionPolicy mac dinh cua Windows
    # chan file .ps1 ("running scripts is disabled on this system"); shim .cmd
    # khong chiu chinh sach do. Da gap dung loi nay voi `supabase login`.
    result = subprocess.run(
        [str(_CLI), *arguments], cwd=str(cwd or _REPO_ROOT)
    )
    if result.returncode != 0:
        raise SystemExit(f"Lenh CLI that bai: supabase {' '.join(arguments)}")


def cmd_init_bucket(args: argparse.Namespace) -> None:
    """Tao bucket PRIVATE qua SQL.

    ⛔ `public = false` LA BAT BUOC. Bucket public nghia la bat ky ai biet
    duong dan deu tai duoc file backend - va luc do ca chuoi ve tai, kiem
    thiet bi, kiem license tro thanh trang tri. Script nay tu choi bat mot
    bucket dang public thanh noi chua ban phat hanh.
    """
    with _connect(args.operator_directory) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "select public from storage.buckets where id = %s", (args.bucket,)
            )
            row = cursor.fetchone()
            if row is not None:
                if row[0]:
                    raise SystemExit(
                        f"Bucket '{args.bucket}' dang la PUBLIC. Mot bucket public "
                        "lam toan bo phan kiem quyen tai thanh vo nghia. Doi no "
                        "thanh private trong dashboard, hoac dung mot ten khac."
                    )
                print(f"Bucket '{args.bucket}' da co va dang private.")
                return
            cursor.execute(
                "insert into storage.buckets (id, name, public) values (%s, %s, false)",
                (args.bucket, args.bucket),
            )
        connection.commit()
    print(f"Da tao bucket private '{args.bucket}'.")


def cmd_publish(args: argparse.Namespace) -> None:
    operator = args.operator_directory
    source = args.file.expanduser().resolve()
    if not source.is_file():
        raise SystemExit(f"Khong thay file: {source}")

    # ⛔ KIEM DAU PE TRUOC KHI TAI LEN. Ban Python kiem dung cho nay
    # (`register_release`): mot file khong phai .exe duoc ky va phat di se chi
    # lo ra khi khach tai ve roi khong chay duoc.
    with source.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise SystemExit(
                f"{source.name} khong phai file thuc thi Windows (thieu dau 'MZ')."
            )

    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    sha256 = digest.hexdigest()
    size_bytes = source.stat().st_size

    ref = _project_ref(operator)
    artifact_id = new_id("artifact")
    storage_object = f"{args.component}-{args.version}-{args.target}.exe"
    download_url = (
        f"https://{ref}.supabase.co/functions/v1/artifacts/{artifact_id}/download"
    )
    published_at = int(time.time())

    manifest = ReleaseManifest(
        artifact_id=artifact_id,
        component=args.component,
        version=args.version,
        channel=args.channel,
        target=args.target,
        download_url=download_url,
        sha256=sha256,
        size_bytes=size_bytes,
        published_at=published_at,
        minimum_launcher_version=args.minimum_launcher_version,
        mandatory=args.mandatory,
    )
    signer = _release_signer(operator)
    token = signer.sign(manifest)

    # ⛔ TU KIEM LAI BANG KHOA CONG KHAI TRUOC KHI GHI VAO DB. Mot manifest ky
    # sai kid, hoac mot bundle khong khop khoa rieng, chi lo ra khi khach bam
    # cap nhat - tuc sau khi ban phat hanh da di ra. Kiem o day ton mot phan
    # nghin giay.
    # ⛔ `ReleaseManifestVerifier` nhan khoa DA NAP, khong co `from_pem_mapping`
    # nhu `LeaseVerifier`. Hai lop giong nhau den muc de tuong la cung API; da
    # viet sai mot lan.
    from cryptography.hazmat.primitives import serialization

    bundle = json.loads(_read(operator / "desktop-public-keys.json"))
    public_keys = {
        key_id: serialization.load_pem_public_key(pem.encode("utf-8"))
        for key_id, pem in bundle["release_public_keys"].items()
    }
    envelope = ReleaseManifestVerifier(public_keys).verify(token)
    if envelope.manifest.artifact_id != artifact_id:
        raise SystemExit("Manifest tu kiem lai khong khop artifact_id.")

    print(f"Tai len Storage: ss:///{args.bucket}/{storage_object}")
    # ⛔ `--experimental` LA BAT BUOC: `supabase storage cp` tu choi chay ma
    # khong co no ("must set the --experimental flag to run this command").
    # Va vi no la lenh experimental, cu phap co the doi giua hai ban CLI - do
    # la mot ly do nua de `tools/supabase-cli/package.json` ghim chinh xac
    # 2.120.0 thay vi dung dau `^`.
    #
    # ⛔ NGUON PHAI LA TEN FILE TRAN, KHONG PHAI DUONG DAN TUYET DOI WINDOWS.
    # CLI phan tich doi so nhu URI, nen `D:\...` bi hieu la scheme "D" va no
    # bao "Unsupported operation / Run cp -r to copy between local
    # directories" - mot thong bao khong he noi gi ve nguyen nhan. Nen chay CLI
    # ngay trong thu muc chua file va dua ten tran, con `--workdir` chi cho no
    # cho tim `supabase/` de `--linked` lam viec.
    _run_cli(
        [
            "storage", "cp", source.name, f"ss:///{args.bucket}/{storage_object}",
            "--linked", "--experimental",
            "--workdir", str(_REPO_ROOT),
            "--content-type", "application/octet-stream",
        ],
        cwd=source.parent,
    )

    with _connect(operator) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                insert into license.releases (
                    id, component, version, channel, target, storage_object,
                    sha256, size_bytes, published_at, minimum_launcher_version,
                    mandatory, signed_manifest, active
                ) values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,true)
                """,
                (
                    artifact_id, args.component, args.version, args.channel,
                    args.target, storage_object, sha256, size_bytes,
                    published_at, args.minimum_launcher_version, args.mandatory,
                    token,
                ),
            )
            cursor.execute(
                """
                insert into license.audit_events (event_type, actor_id, subject_id, metadata)
                values ('release.registered', 'operator', %s, %s::jsonb)
                """,
                (artifact_id, json.dumps({"component": args.component,
                                          "version": args.version})),
            )
        connection.commit()

    print()
    print("Da phat hanh.")
    print(f"  artifact_id : {artifact_id}")
    print(f"  version     : {args.version} ({args.channel}, {args.target})")
    print(f"  sha256      : {sha256}")
    print(f"  kich thuoc  : {size_bytes / 1024 / 1024:.1f} MB")
    print(f"  download_url: {download_url}")


def cmd_list(args: argparse.Namespace) -> None:
    with _connect(args.operator_directory) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                select id, component, version, channel, target,
                       round(size_bytes / 1048576.0, 1), mandatory, active,
                       to_timestamp(published_at)
                  from license.releases
                 order by published_at desc
                 limit %s
                """,
                (args.limit,),
            )
            rows = cursor.fetchall()
    if not rows:
        print("Chua phat hanh ban nao.")
        return
    print(f"{'artifact_id':<42} {'ban':<12} {'kenh':<9} {'MB':>6}  trang thai")
    for (aid, component, version, channel, target, mb, mandatory, active, when) in rows:
        state = "dang phat" if active else "da tat"
        if mandatory:
            state += " (bat buoc)"
        print(f"{aid:<42} {version:<12} {channel:<9} {mb:>6}  {state}")


def cmd_deactivate(args: argparse.Namespace) -> None:
    with _connect(args.operator_directory) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "update license.releases set active = false where id = %s and active",
                (args.artifact_id,),
            )
            if cursor.rowcount == 0:
                raise SystemExit(
                    f"Khong co ban phat hanh nao dang bat voi id {args.artifact_id}."
                )
            cursor.execute(
                """
                insert into license.audit_events (event_type, actor_id, subject_id)
                values ('release.deactivated', 'operator', %s)
                """,
                (args.artifact_id,),
            )
        connection.commit()
    print(f"Da tat {args.artifact_id}.")
    # ⛔ File van con trong Storage, co y: mot may dang tai do dang se khong bi
    # dut giua duong. Muon xoa han thi xoa object trong dashboard sau.
    print("  File van con trong Storage; tat la khong phat nua, khong phai xoa.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--operator-directory", type=Path, default=argparse.SUPPRESS)
    common.add_argument("--bucket", default=argparse.SUPPRESS)
    parser.add_argument("--operator-directory", type=Path, default=None)
    parser.add_argument("--bucket", default=_DEFAULT_BUCKET)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-bucket", parents=[common], help="Tao bucket private")

    publish = sub.add_parser("publish", parents=[common], help="Phat hanh mot ban")
    publish.add_argument("--file", required=True, type=Path)
    publish.add_argument("--version", required=True)
    publish.add_argument("--component", default="backend",
                         choices=("backend", "desktop"))
    publish.add_argument("--channel", default="stable",
                         choices=("internal", "beta", "stable"))
    publish.add_argument("--target", default="windows-x86_64",
                         choices=("windows-x86_64",))
    publish.add_argument("--minimum-launcher-version", default="0.1.0")
    publish.add_argument("--mandatory", action="store_true")

    listing = sub.add_parser("list", parents=[common], help="Xem cac ban da phat")
    listing.add_argument("--limit", type=int, default=50)

    off = sub.add_parser("deactivate", parents=[common], help="Ngung phat mot ban")
    off.add_argument("--artifact-id", required=True)

    args = parser.parse_args()
    if args.operator_directory is None:
        raise SystemExit(
            "Thieu --operator-directory. Dat o truoc hay sau subcommand deu duoc."
        )
    if not hasattr(args, "bucket") or args.bucket is None:
        args.bucket = _DEFAULT_BUCKET
    args.operator_directory = args.operator_directory.expanduser().resolve()
    if not args.operator_directory.is_dir():
        raise SystemExit(f"Khong thay thu muc operator: {args.operator_directory}")

    {
        "init-bucket": cmd_init_bucket,
        "publish": cmd_publish,
        "list": cmd_list,
        "deactivate": cmd_deactivate,
    }[args.command](args)


if __name__ == "__main__":
    main()
