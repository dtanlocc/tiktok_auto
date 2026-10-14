"""Chuẩn bị extension cho bản phát hành: giữ chữ ký, và không ship credential.

⛔ BÀI TEST NÀY CHẶN MỘT LỖI ĐÃ LÀM MỌI BẢN BIÊN DỊCH CÓ OMOCAPTCHA HỎNG.
Script cũ mở mọi XPI ra, xoá trường credential, rồi ghi lại — và **bỏ hẳn
`META-INF/`**. Việc đó phá chữ ký Mozilla, nên lúc chạy:

    RuntimeError: OmoCaptcha XPI signature was not preserved

Firefox cũng không kích hoạt add-on không chữ ký. Lỗi chỉ lộ ra khi mở trình
duyệt bằng bản **đã đóng gói** — dev dùng thẳng XPI gốc nên không bao giờ thấy.

Và cái giá của việc sửa XPI đó: nó chỉ đổi `api_key` từ `"YOUR_CLIEN_KEY"` —
chỗ trống của nhà cung cấp — thành `""`. Phá một chữ ký để đổi một placeholder
lấy một chuỗi rỗng.
"""
from __future__ import annotations

import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "sanitize_extensions_for_release.py"
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import sanitize_extensions_for_release as sanitizer  # noqa: E402


def _make_xpi(path: Path, config: dict, *, signed: bool) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("manifest.json", json.dumps({"name": "x"}))
        archive.writestr("configs.json", json.dumps(config))
        if signed:
            archive.writestr("META-INF/mozilla.rsa", b"chu-ky-gia")
            archive.writestr("META-INF/manifest.mf", "Manifest-Version: 1.0\n")


def _run(source: Path, destination: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(source), str(destination)],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_a_signed_archive_is_copied_byte_for_byte(tmp_path):
    """⛔ Sửa một byte là chữ ký hỏng, và Firefox từ chối add-on."""
    source = tmp_path / "src"
    source.mkdir()
    xpi = source / "signed.xpi"
    _make_xpi(xpi, {"api_key": "YOUR_CLIEN_KEY"}, signed=True)
    original = xpi.read_bytes()

    result = _run(source, tmp_path / "out")
    assert result.returncode == 0, result.stderr

    produced = (tmp_path / "out" / "signed.xpi").read_bytes()
    assert produced == original, "Archive da ky phai duoc copy nguyen xi"
    with zipfile.ZipFile(tmp_path / "out" / "signed.xpi") as archive:
        assert any(n.lower().startswith("meta-inf/") for n in archive.namelist())


def test_an_unsigned_archive_still_gets_its_credentials_scrubbed(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    _make_xpi(source / "plain.xpi", {"api_key": "omo_a8f3b2c9d1e4"}, signed=False)

    result = _run(source, tmp_path / "out")
    assert result.returncode == 0, result.stderr

    with zipfile.ZipFile(tmp_path / "out" / "plain.xpi") as archive:
        config = json.loads(archive.read("configs.json"))
    assert config["api_key"] == ""


def test_a_real_credential_inside_a_signed_archive_stops_the_build(tmp_path):
    """⛔ Không thể vừa giữ chữ ký vừa xoá credential, nên phải DỪNG.

    Trước đây script sẽ lặng lẽ xoá nó và phá chữ ký — tức đổi một lỗi nhìn
    thấy được lúc build thành một lỗi chỉ hiện ở máy khách.
    """
    source = tmp_path / "src"
    source.mkdir()
    _make_xpi(source / "signed.xpi", {"api_key": "omo_khoa-that-cua-ai-do"}, signed=True)

    result = _run(source, tmp_path / "out")
    assert result.returncode != 0
    assert "Credential con sot" in (result.stdout + result.stderr)
    assert "signed.xpi" in (result.stdout + result.stderr)


def test_a_vendor_placeholder_is_not_treated_as_a_credential():
    """`YOUR_CLIEN_KEY` là chỗ trống, không phải khoá của ai."""
    assert not sanitizer.looks_like_a_real_credential("YOUR_CLIEN_KEY")
    assert not sanitizer.looks_like_a_real_credential("")
    assert not sanitizer.looks_like_a_real_credential("   ")
    assert sanitizer.looks_like_a_real_credential("omo_a8f3b2c9d1e4f6a7")
    assert sanitizer.looks_like_a_real_credential("sk-live-91ab33cc")


def test_the_output_is_printable_on_a_cp1252_console(tmp_path):
    """⛔ Thông báo có dấu làm build chết ở BƯỚC CUỐI, sau khi đã làm xong việc.

    stdout cua build di qua PowerShell voi codepage cp1252; mot chu co dau o do
    nem `UnicodeEncodeError`. Da gap dung the.
    """
    source = tmp_path / "src"
    source.mkdir()
    _make_xpi(source / "signed.xpi", {"api_key": "YOUR_CLIEN_KEY"}, signed=True)
    result = _run(source, tmp_path / "out")
    assert result.returncode == 0, result.stderr
    result.stdout.encode("cp1252")  # không được ném


def test_the_bundled_extensions_survive_the_real_script(tmp_path):
    """Chạy trên đúng thư mục extension của dự án, không phải đồ giả."""
    source = _REPO_ROOT / "backend" / "extensions"
    if not source.is_dir():
        pytest.skip("Khong thay backend/extensions.")

    result = _run(source, tmp_path / "out")
    assert result.returncode == 0, result.stderr

    for produced in sorted((tmp_path / "out").glob("*.xpi")):
        with zipfile.ZipFile(produced) as archive:
            signed = any(n.lower().startswith("meta-inf/") for n in archive.namelist())
        assert signed, f"{produced.name} mat chu ky sau khi qua script"
        assert produced.read_bytes() == (source / produced.name).read_bytes()
