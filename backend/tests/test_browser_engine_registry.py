"""Nhan trinh duyet: cho de cam them nhan khac, va mot chan quan trong.

⛔ MOT BAN BACKEND CHI CHUA NHUNG NHAN DA DUOC BIEN DICH VAO NO. Thu vien
trinh duyet di vao tep .exe luc build (Nuitka `--include-package`), nen "doi
nhan luc chay" chi co nghia TRONG SO nhung nhan co mat.

Neu `set_browser_engine` ghi xuong dia mot nhan khong import duoc, trinh duyet
se chet o LAN MO TIEP THEO - cach xa cho da gay ra, va luc do khong ai noi
duoc vi sao. Nen phai tu choi ngay luc chon.

⛔ VA SO PHIEN BAN PHAI DOC DUOC TRONG BAN DA NEN. `importlib.metadata` nem
`PackageNotFoundError` trong Nuitka (khong co dist-info), nen man hinh "dang
chay ban nao" se trong rong o dung noi can no nhat: tren may khach. Vi the
doc tu `__version__` cua module.
"""
from __future__ import annotations

import pytest

from app.core import browser_engine as be


@pytest.fixture(autouse=True)
def kho(tmp_path, monkeypatch):
    """Khong bai nao duoc doc/ghi tep cai dat cua may dang chay."""
    monkeypatch.setattr(
        be.settings, "BROWSER_EXTENSION_SETTINGS_PATH",
        str(tmp_path / "settings.json"), raising=False,
    )
    monkeypatch.setattr(
        be.settings, "BROWSER_ENGINE", "invisible_playwright", raising=False
    )


def test_the_engine_in_this_build_is_reported_with_its_versions():
    info = be.describe("invisible_playwright")
    assert info["available"] is True
    assert info["version"] != "?", "phai doc duoc ban thu vien"
    assert info["core_version"] != "?"
    assert str(info["engine_tag"]).startswith("firefox-")
    assert info["firefox"] != "?"


def test_the_version_looks_like_a_real_version():
    """⛔ BAI TEST NAY TUNG LA MOT BAI TEST RONG, va no da de lot mot loi that.

    Ban dau no so `describe()[...]` voi `invisible_playwright.__version__` -
    tuc so mot gia tri voi chinh no. No xanh du ca hai cung SAI, va dung the
    da xay ra: trong ban Nuitka 0.1.20 dau tien, man hinh phien ban hien
    "0.0.0+unknown" vi goi do suy `__version__` tu `importlib.metadata`, ma
    ban nen khong co dist-info. Bai test khong he keu.

    Gio no kiem HINH DANG cua con so, nen mot gia tri "khong biet" bi bat du
    no den tu dau.
    """
    import re

    for key in ("version", "core_version"):
        gia_tri = str(be.describe("invisible_playwright")[key])
        assert re.fullmatch(r"\d+\.\d+\.\d+", gia_tri), f"{key} = {gia_tri!r}"


def test_an_unknown_engine_is_refused():
    with pytest.raises(ValueError, match="khong co that"):
        be.set_browser_engine("lung tung")


def test_a_known_engine_that_is_not_in_this_build_is_refused():
    """⛔ DAY LA CHAN QUAN TRONG NHAT. Ghi xuong mot nhan khong co thi trinh
    duyet chet o lan mo sau, xa cho gay ra."""
    be.ENGINES["nhan_tuong_tuong"] = be.BrowserEngine(
        key="nhan_tuong_tuong", label="tuong tuong", module="khong_he_ton_tai_xyz"
    )
    try:
        assert be.is_available("nhan_tuong_tuong") is False
        with pytest.raises(ValueError, match="khong co trong ban backend"):
            be.set_browser_engine("nhan_tuong_tuong")
    finally:
        be.ENGINES.pop("nhan_tuong_tuong")


def test_a_stored_engine_that_vanished_falls_back_instead_of_breaking(tmp_path):
    """Ha cap ban backend khong duoc lam app khong mo duoc trinh duyet."""
    be._write_setting(be._BROWSER_ENGINE_KEY, "nhan_da_bien_mat")
    assert be.load_browser_engine() == be.DEFAULT_ENGINE


def test_choosing_the_engine_sticks():
    be.set_browser_engine("invisible_playwright")
    assert be.load_browser_engine() == "invisible_playwright"


def test_adding_an_engine_is_one_line():
    """⛔ Yeu cau that: 'de mo rong hon'. Them nhan khong duoc dong vao ham nao."""
    be.ENGINES["nhan_moi"] = be.BrowserEngine(
        key="nhan_moi", label="nhan moi", module="json"  # module co that -> available
    )
    try:
        assert be.is_available("nhan_moi") is True
        assert be.set_browser_engine("nhan_moi") == "nhan_moi"
        assert be.load_browser_engine() == "nhan_moi"
        keys = [e["key"] for e in be.runtime_versions()["engines"]]
        assert "nhan_moi" in keys
    finally:
        be.ENGINES.pop("nhan_moi")


def test_the_version_screen_answers_in_one_call():
    """Hoi ba lan de biet mot chuyen la ba lan qua nhieu."""
    v = be.runtime_versions()
    assert set(v) == {"app_version", "browser_engine", "engine", "engines"}
    assert v["engine"]["key"] == v["browser_engine"]
