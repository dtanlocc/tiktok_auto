"""Ban phat hanh phai mang theo DANH SACH TEN vung gio, khong chi du lieu vung.

⛔ BAI TEST NAY CHAN MOT LOI DA LAM BAN 0.1.17 DAU TIEN KHONG MO NOI TRINH DUYET.

Nuitka tu nhan ra `tzdata.zoneinfo` va dong goi 604 file du lieu vung, nen nhin
log build thi tuong da du. Nhung `zoneinfo.available_timezones()` KHONG doc
nhung file do - no doc `tzdata/zones`, mot file van ban liet ke TEN vung, va
file do thuoc goi `tzdata` chu khong thuoc `tzdata.zoneinfo` (xem
`zoneinfo._tzpath.available_timezones`). Tren Windows khong co tzdb he thong
nen khong con nguon nao khac.

Tu invisible-core 51.48.0, engine tu giai mui gio va `_apply_timezone` tu choi
moi vung khong nam trong danh sach do:

    ValueError: the session's timezone 'Asia/Jakarta' is not an IANA zone:
    the engine cannot open it, and a zone it cannot open is the host's

Ket qua: trinh duyet KHONG mo duoc. Va chi trong ban da bien dich - chay tu
source thi doc thang tu site-packages nen moi phep thu dev deu xanh. Lan dau
gap, no di qua ca 618 bai test va chi lo ra khi chay binary that.

⛔ VA NO CHI DOC VAN BAN, khong chay build - mot ban build that ton ~10 phut.
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUILD = _REPO_ROOT / "scripts" / "build_backend_release.ps1"


def _script() -> str:
    return _BUILD.read_text(encoding="utf-8")


def test_the_build_bundles_the_whole_tzdata_package():
    """`tzdata.zoneinfo` mot minh la KHONG du: thieu file `zones`."""
    assert '"--include-package-data=tzdata"' in _script()


def test_the_zone_list_is_what_resolves_a_zone_name():
    """Rang buoc that: ten vung phai tra loi duoc tu `tzdata/zones`.

    Neu thu vien chuan doi cach tim, bai test tren thanh vo nghia ma khong ai
    biet - nen o day kiem dung co che, tren chinh may dang chay.
    """
    import zoneinfo
    from importlib import resources

    danh_sach = resources.files("tzdata").joinpath("zones")
    assert danh_sach.is_file(), "goi tzdata phai co file 'zones'"

    ten = {d.strip() for d in danh_sach.read_text(encoding="utf-8").splitlines() if d.strip()}
    assert "Asia/Jakarta" in ten, "dung vung da lam ban 0.1.17 dau tien that bai"
    assert "Asia/Ho_Chi_Minh" in ten
    assert ten <= zoneinfo.available_timezones()


# ---------------------------------------------------------------------------
# Ban ghi cai dat cua thu vien trinh duyet
# ---------------------------------------------------------------------------
def test_the_build_bundles_the_browser_library_install_record():
    """⛔ THIEU NO LA MAN HINH PHIEN BAN NOI SAI TREN MAY KHACH.

    `invisible_playwright` suy `__version__` tu `importlib.metadata`, va ban
    Nuitka khong co dist-info -> no tra ve "0.0.0+unknown". Chay tu source thi
    luon dung, nen khong lan thu dev nao thay. Da gap that o ban 0.1.20 dau.

    ⛔ VA TEN PHAI LA TEN PHAN PHOI THAT, gach duoi chu khong phai gach ngang.
    Nuitka chi CANH BAO roi bo qua cai sai, nen ban build van ra va van hong -
    lan dau da di dung vao bay do.
    """
    script = _script()
    assert '"--include-distribution-metadata=invisible_playwright"' in script
    assert "--include-distribution-metadata=invisible-playwright" not in script


def test_the_distribution_name_is_spelled_the_way_the_installer_wrote_it():
    """Rang buoc that, kiem tren chinh may dang chay."""
    import importlib.metadata as metadata

    ten = {d.metadata["Name"] for d in metadata.distributions() if d.metadata["Name"]}
    assert "invisible_playwright" in ten
