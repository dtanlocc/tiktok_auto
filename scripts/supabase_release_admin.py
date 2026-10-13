"""Phat hanh mot ban backend: ky manifest O MAY NAY, tai file len mot GitHub
Release trong repo PRIVATE, ghi mot dong vao so dang ky tren Supabase.

⛔ BA CHO CHUA DA THU, theo dung thu tu bi loai:
  1. Supabase Storage - Free plan chan upload o 50 MB va KHONG nang duoc tu
     dashboard; backend 0.1.12 la 50,7 MiB, vuot dung 0,7 MiB.
  2. Cloudflare R2 - mien phi 10 GB va khong tinh egress, nhung DOI THEM
     PHUONG THUC THANH TOAN de bat lan dau.
  3. GitHub Release tren repo private - asset toi 2 GB, khong the, va tai
     khoan GitHub thi da co.

Doi duoc cho chua BA LAN ma khong phai ky lai mot manifest nao la vi
`download_url` tro vao function `artifacts` chu khong tro thang vao cho chua -
mot lop gian tiep dat tu dau, va no da tra cong ba lan.

⛔ REPO PHAI LA PRIVATE, va script nay TU CHOI chay neu khong. Asset cua
release tren repo public tai duoc khong can xac thuc gi, va luc do ca chuoi ve
tai + kiem thiet bi + kiem license thanh trang tri. Repo `tiktok_auto` hien la
PUBLIC nen artifact KHONG duoc nam o do - phai la mot repo rieng.

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

  # Lan dau: kiem token va repo (repo private phai tu tao tren GitHub)
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

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DATABASE_URL_FILE = "secrets/supabase-database-url"
_GITHUB_FILE = "secrets/github-artifacts.json"


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


def _github_config(operator: Path) -> dict[str, str]:
    """Doc repo + token tu thu muc operator."""
    path = operator / _GITHUB_FILE
    if not path.is_file():
        raise SystemExit(
            f"Thieu {_GITHUB_FILE} trong {operator}.\n"
            "Tao mot repo PRIVATE moi chi de chua artifact, roi tao mot\n"
            "fine-grained PAT chi co quyen doc/ghi Contents cua dung repo do.\n"
            'Ghi file JSON: {"repo": "owner/ten-repo", "token": "github_pat_..."}'
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    missing = [name for name in ("repo", "token") if not str(data.get(name, "")).strip()]
    if missing:
        raise SystemExit(f"{_GITHUB_FILE} thieu truong: {', '.join(missing)}")
    repo = str(data["repo"]).strip()
    if repo.count("/") != 1 or not all(repo.split("/")):
        raise SystemExit(f"repo phai dang owner/ten-repo; dang co {repo!r}.")
    return {"repo": repo, "token": str(data["token"]).strip()}


def _github(config: dict[str, str]):
    import httpx

    return httpx.Client(
        timeout=600,
        headers={
            "accept": "application/vnd.github+json",
            "authorization": f"Bearer {config['token']}",
            "x-github-api-version": "2022-11-28",
            # GitHub tu choi request khong co User-Agent.
            "user-agent": "tkauto-release-admin",
        },
        follow_redirects=True,
    )


def _assert_private(client, repo: str) -> None:
    """⛔ TU CHOI MOT REPO PUBLIC. Day la phep kiem quan trong nhat trong file.

    Asset cua release tren repo public tai duoc khong can xac thuc gi; dat
    artifact o do la bo het phan kiem quyen ma van tuong la con.
    """
    response = client.get(f"https://api.github.com/repos/{repo}")
    if response.status_code == 404:
        raise SystemExit(
            f"Khong thay repo {repo}. Voi repo private, GitHub tra 404 ca khi "
            "repo co that nhung token khong co quyen doc no - nen kiem lai ca "
            "ten repo lan quyen cua token."
        )
    if response.status_code != 200:
        raise SystemExit(
            f"GitHub tra ve {response.status_code} khi doc repo: "
            f"{response.text[:300]}"
        )
    if not response.json().get("private", False):
        raise SystemExit(
            f"Repo {repo} dang PUBLIC. Asset cua release tren repo public tai "
            "duoc khong can xac thuc gi, nen toan bo phan kiem ve tai/thiet "
            "bi/license tro thanh trang tri. Doi repo thanh private, hoac dung "
            "mot repo khac."
        )


def _upload_asset(
    config: dict[str, str], tag: str, source: Path, asset_name: str
) -> str:
    """Tao release (neu chua co) roi tai asset len. Tra ve asset id."""
    repo = config["repo"]
    with _github(config) as client:
        _assert_private(client, repo)

        response = client.get(
            f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
        )
        if response.status_code == 200:
            release = response.json()
        else:
            response = client.post(
                f"https://api.github.com/repos/{repo}/releases",
                json={
                    "tag_name": tag,
                    "name": tag,
                    "draft": False,
                    "prerelease": False,
                    "body": "Artifact do scripts/supabase_release_admin.py tai len.",
                },
            )
            if response.status_code not in (200, 201):
                raise SystemExit(
                    f"Khong tao duoc release {tag}: {response.status_code} "
                    f"{response.text[:300]}"
                )
            release = response.json()

        # ⛔ MOT TEN ASSET CHI TON TAI MOT LAN TRONG MOT RELEASE. GitHub tra
        # 422 neu trung, va luc do thong bao khong noi ro la trung - nen kiem
        # truoc va noi thang.
        for existing in release.get("assets", []):
            if existing.get("name") == asset_name:
                raise SystemExit(
                    f"Release {tag} da co asset ten {asset_name} (id "
                    f"{existing.get('id')}). Xoa no tren GitHub roi chay lai, "
                    "hoac phat hanh voi so phien ban khac."
                )

        # ⛔ DOC CA FILE VAO BO NHO, co y: 51 MB tren may phat trien la khong
        # dang ke, va `httpx` can biet do dai de dat Content-Length.
        body = source.read_bytes()
        response = client.post(
            f"https://uploads.github.com/repos/{repo}/releases/"
            f"{release['id']}/assets",
            params={"name": asset_name},
            content=body,
            headers={"content-type": "application/octet-stream"},
        )
        if response.status_code not in (200, 201):
            raise SystemExit(
                f"GitHub tu choi upload (HTTP {response.status_code}): "
                f"{response.text[:400]}"
            )
        asset_id = response.json().get("id")
        if not isinstance(asset_id, int):
            raise SystemExit(f"GitHub khong tra ve asset id: {response.text[:300]}")
        return str(asset_id)


def cmd_check_storage(args: argparse.Namespace) -> None:
    """Kiem token va repo truoc khi phat hanh lan dau."""
    config = _github_config(args.operator_directory)
    with _github(config) as client:
        _assert_private(client, config["repo"])
        response = client.get(
            f"https://api.github.com/repos/{config['repo']}/releases",
            params={"per_page": 5},
        )
    if response.status_code != 200:
        raise SystemExit(
            f"Token doc duoc repo nhung khong liet ke duoc release "
            f"({response.status_code}). Token can quyen Contents: Read and "
            f"write. {response.text[:200]}"
        )
    releases = response.json()
    print(f"GitHub OK: {config['repo']} la repo PRIVATE va token doc/ghi duoc.")
    print(f"  release dang co: {len(releases)}")
    for item in releases:
        assets = ", ".join(a["name"] for a in item.get("assets", [])) or "(khong co)"
        print(f"    {item['tag_name']}: {assets}")


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

    github = _github_config(operator)
    tag = f"{args.component}-{args.version}"
    print(
        f"Tai len GitHub: {github['repo']} release {tag}, asset "
        f"{storage_object} ({size_bytes / 1048576:.1f} MiB)"
    )
    # ⛔ `storage_object` GIU ASSET ID, khong giu ten file. Day la mot dinh vi
    # doi theo cho chua: voi Supabase Storage no la duong dan trong bucket, voi
    # R2 cung vay, con voi GitHub thi ten file KHONG du de tai - phai co asset
    # id. Cot do duoc coi la mot dinh vi khong trong suot, va chi function
    # `artifacts` biet cach doc no.
    asset_name = storage_object
    storage_object = _upload_asset(github, tag, source, asset_name)

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
    print(f"  asset id    : {storage_object}")
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
    parser.add_argument("--operator-directory", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser(
        "check-storage", parents=[common],
        help="Kiem token va repo private tren GitHub",
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
