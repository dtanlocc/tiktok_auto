"""Phat hanh mot ban backend: ky manifest O MAY NAY, tai file len Cloudflare
R2, ghi mot dong vao so dang ky tren Supabase.

⛔ FILE NAM TREN R2, KHONG PHAI Supabase Storage. Do ngay 13/10/2026: Free plan
cua Supabase chan upload o 50 MB va khong nang duoc tu dashboard, trong khi
backend 0.1.12 la 50,7 MiB - vuot dung 0,7 MiB. R2 cho 10 GB luu, khong tinh
egress, mot PUT toi 5 GB. Doi duoc cho chua ma khong phai ky lai manifest nao
la vi `download_url` tro vao function `artifacts` chu khong tro thang vao cho
chua - mot lop gian tiep dat them ngay tu dau, va gio no tra cong.

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

  # Lan dau: kiem thong tin R2 (bucket phai tu tao trong Cloudflare Dashboard)
  uv run python scripts/supabase_release_admin.py check-storage \\
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
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.security.release import (  # noqa: E402
    ReleaseManifest,
    ReleaseManifestSigner,
    ReleaseManifestVerifier,
)
from control_plane.app.models import new_id  # noqa: E402

import r2_sigv4  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DATABASE_URL_FILE = "secrets/supabase-database-url"
_R2_FILE = "secrets/r2-credentials.json"
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


def _r2_config(operator: Path) -> dict[str, str]:
    """Doc thong tin R2 tu thu muc operator.

    ⛔ VI SAO R2 CHU KHONG PHAI Supabase Storage. Do ngay 13/10/2026: Free plan
    cua Supabase chan upload o 50 MB va KHONG nang duoc tu dashboard, trong khi
    backend 0.1.12 la 50,7 MiB - vuot dung 0,7 MiB. R2 cho 10 GB luu, khong
    tinh egress, mot PUT toi 5 GB. Doi duoc cho chua ma khong phai ky lai
    manifest nao la vi `download_url` tro vao function `artifacts`.
    """
    path = operator / _R2_FILE
    if not path.is_file():
        raise SystemExit(
            f"Thieu {_R2_FILE} trong {operator}.\n"
            "Tao file JSON voi bon truong, lay o Cloudflare Dashboard -> R2:\n"
            '  {"account_id": "<32 hex>", "bucket": "releases",\n'
            '   "access_key_id": "...", "secret_access_key": "..."}\n'
            "Token can quyen 'Object Read & Write' cho dung bucket do."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = [
        name
        for name in ("account_id", "bucket", "access_key_id", "secret_access_key")
        if not str(data.get(name, "")).strip()
    ]
    if missing:
        raise SystemExit(f"{_R2_FILE} thieu truong: {', '.join(missing)}")
    account = str(data["account_id"]).strip()
    if len(account) != 32 or any(c not in "0123456789abcdef" for c in account.lower()):
        raise SystemExit(
            "account_id phai la 32 ky tu hex (Cloudflare Dashboard -> R2 -> "
            f"Account ID); dang co {account!r}."
        )
    return {name: str(data[name]).strip() for name in data if isinstance(data[name], str)} | {
        "account_id": account
    }


def _r2_put(config: dict[str, str], key: str, source: Path, payload_sha256: str) -> None:
    """Day file len R2 bang mot PUT da ky SigV4."""
    import time

    import httpx

    size = source.stat().st_size
    url, headers = r2_sigv4.signed_put_headers(
        account_id=config["account_id"],
        bucket=config["bucket"],
        key=key,
        access_key_id=config["access_key_id"],
        secret_access_key=config["secret_access_key"],
        payload_sha256=payload_sha256,
        content_length=size,
        content_type="application/octet-stream",
        amz_date=time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
    )
    # ⛔ DOC CA FILE VAO BO NHO, co y. SigV4 cho PUT ky sha256 THAT cua than
    # request, nen than khong the duoc tinh dan trong luc gui; va 51 MB trong
    # RAM tren may phat trien la khong dang ke. Doi lai, R2 tu choi mot than bi
    # doi tren duong di - mot phep kiem mien phi truoc khi launcher doi chieu
    # sha256 voi manifest da ky.
    body = source.read_bytes()
    with httpx.Client(timeout=600) as client:
        response = client.put(url, headers=headers, content=body)
    if response.status_code not in (200, 201):
        raise SystemExit(
            f"R2 tu choi upload (HTTP {response.status_code}): "
            f"{response.text[:400]}"
        )


def cmd_check_storage(args: argparse.Namespace) -> None:
    """Kiem thong tin R2 truoc khi phat hanh lan dau.

    ⛔ KHONG TAO BUCKET HO. Tao bucket R2 can Cloudflare API voi mot token
    quyen rong hon han quyen can de day file; giu token o muc "Object Read &
    Write" cho DUNG mot bucket thi an toan hon, va doi lay viec phai tu bam
    "Create bucket" mot lan trong dashboard.
    """
    import time

    import httpx

    config = _r2_config(args.operator_directory)
    bucket = config["bucket"]
    # Ky mot GET cho mot key chac chan khong ton tai: 404 nghia la thong tin
    # dang nhap DUNG va bucket co that; 403 nghia la sai khoa hoac sai quyen.
    url = r2_sigv4.presign_get(
        account_id=config["account_id"],
        bucket=bucket,
        key="__kiem-thong-tin-dang-nhap__",
        access_key_id=config["access_key_id"],
        secret_access_key=config["secret_access_key"],
        expires_seconds=60,
        amz_date=time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()),
    )
    with httpx.Client(timeout=60) as client:
        response = client.get(url)
    if response.status_code == 404:
        print(f"R2 OK: bucket '{bucket}' co that va thong tin dang nhap dung.")
        print("  (404 la ket qua mong doi: key kiem thu khong ton tai.)")
        return
    if response.status_code == 403:
        raise SystemExit(
            "R2 tra ve 403. Thuong la: sai access key/secret, token khong co "
            f"quyen 'Object Read & Write', hoac bucket '{bucket}' thuoc account "
            "khac. Kiem lai trong Cloudflare Dashboard -> R2 -> API tokens."
        )
    if response.status_code in (301, 404) or "NoSuchBucket" in response.text:
        raise SystemExit(f"Khong thay bucket '{bucket}' trong account nay.")
    raise SystemExit(
        f"R2 tra ve HTTP {response.status_code}: {response.text[:400]}"
    )


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

    r2 = _r2_config(operator)
    print(f"Tai len R2: {r2['bucket']}/{storage_object} ({size_bytes / 1048576:.1f} MiB)")
    _r2_put(r2, storage_object, source, sha256)

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

    sub.add_parser(
        "check-storage", parents=[common],
        help="Kiem thong tin dang nhap R2 va bucket",
    )

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
        "check-storage": cmd_check_storage,
        "publish": cmd_publish,
        "list": cmd_list,
        "deactivate": cmd_deactivate,
    }[args.command](args)


if __name__ == "__main__":
    main()
