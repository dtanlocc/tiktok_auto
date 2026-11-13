"""Lop dieu khien: nhat ky ra file, va so dang ky lenh.

⛔ CAI NAY SINH RA DE DIEU KHIEN MOT MAY VPS KHONG AI NGOI TRUOC MAN HINH.
Hai rang buoc di theo tu do, va ca hai deu duoc canh o day:

1. Ghi nhat ky KHONG BAO GIO duoc lam hong viec dang lam. Dia day hay thu muc
   khong tao duoc deu phai bi nuot - mot tac vu dang dang video khong duoc
   chet vi khong ghi duoc mot dong nhat ky.
2. Lenh SUA DU LIEU phai duoc xac nhan. Mot lenh chuyen 2000 account sang lo
   khac khong duoc nam cach mot cu go nham trong khung chat.
"""
from __future__ import annotations

import json

import pytest

from app.core import event_log
from app.use_cases.control import registry


@pytest.fixture(autouse=True)
def thu_muc(tmp_path, monkeypatch):
    monkeypatch.setenv("TKAUTO_EVENT_LOG_DIR", str(tmp_path / "log"))
    return tmp_path / "log"


# ---------------------------------------------------------------------------
# Nhat ky ra file
# ---------------------------------------------------------------------------
def test_an_event_is_written_and_read_back(thu_muc):
    event_log.record("sync.done", "Đã đồng bộ 24 nick", so=24)
    ban_ghi = event_log.read_recent()
    assert len(ban_ghi) == 1
    assert ban_ghi[0]["kind"] == "sync.done"
    assert ban_ghi[0]["so"] == 24
    assert ban_ghi[0]["at"]


def test_the_newest_event_comes_first():
    for i in range(3):
        event_log.record("x", f"su kien {i}")
    assert [b["message"] for b in event_log.read_recent()] == [
        "su kien 2", "su kien 1", "su kien 0",
    ]


def test_events_can_be_filtered_by_kind():
    event_log.record("sync.done", "a")
    event_log.record("accounts.promoted", "b")
    assert [b["message"] for b in event_log.read_recent(kind="sync.done")] == ["a"]


def test_one_broken_line_does_not_hide_the_others(thu_muc):
    event_log.record("x", "truoc")
    tep = next(thu_muc.glob("events-*.jsonl"))
    with tep.open("a", encoding="utf-8") as handle:
        handle.write("{ day khong phai json\n")
    event_log.record("x", "sau")
    assert [b["message"] for b in event_log.read_recent()] == ["sau", "truoc"]


def test_a_write_failure_never_reaches_the_caller(monkeypatch):
    """⛔ Mot tac vu dang chay khong duoc chet vi mot dong nhat ky."""
    monkeypatch.setattr(
        event_log.Path, "mkdir",
        lambda *a, **k: (_ for _ in ()).throw(OSError("dia day")),
    )
    ban_ghi = event_log.record("x", "van phai tra ve ban ghi")
    assert ban_ghi["message"] == "van phai tra ve ban ghi"


def test_reading_an_empty_log_is_not_an_error():
    assert event_log.read_recent() == []


def test_old_files_are_pruned(thu_muc):
    thu_muc.mkdir(parents=True, exist_ok=True)
    for ngay in ("2026-01-01", "2026-01-02", "2026-01-03"):
        (thu_muc / f"events-{ngay}.jsonl").write_text("{}\n", encoding="utf-8")
    da_xoa = event_log.prune(keep_days=2)
    assert da_xoa == ["events-2026-01-01.jsonl"]


# ---------------------------------------------------------------------------
# So dang ky lenh
# ---------------------------------------------------------------------------
@pytest.fixture
def so_sach(monkeypatch):
    monkeypatch.setattr(registry, "REGISTRY", {})
    return registry


def test_a_read_command_runs_without_confirmation(so_sach):
    so_sach.register("doc", "chi doc")(lambda: {"ok": True})
    assert so_sach.run("doc") == {"ok": True}


def test_a_mutating_command_is_refused_until_confirmed(so_sach):
    """⛔ Mot lenh chuyen 2000 account khong duoc nam cach mot cu go nham."""
    goi = []
    so_sach.register("sua", "doi du lieu", mutates=True)(lambda: goi.append(1))
    with pytest.raises(so_sach.ConfirmationRequired, match="thay doi du lieu"):
        so_sach.run("sua")
    assert goi == [], "khong duoc chay khi chua xac nhan"
    so_sach.run("sua", confirm=True)
    assert goi == [1]


def test_confirming_a_read_command_changes_nothing(so_sach):
    so_sach.register("doc", "chi doc")(lambda: 7)
    assert so_sach.run("doc", confirm=True) == 7


def test_an_unknown_command_lists_the_real_ones(so_sach):
    so_sach.register("trang_thai", "x")(lambda: None)
    with pytest.raises(so_sach.UnknownCommand, match="trang_thai"):
        so_sach.run("lung_tung")


def test_registering_the_same_name_twice_is_refused(so_sach):
    """Hai lenh cung ten thi mot cai bi che mat ma khong ai biet."""
    so_sach.register("a", "x")(lambda: None)
    with pytest.raises(ValueError, match="da duoc dang ky"):
        so_sach.register("a", "y")(lambda: None)


def test_parameters_are_passed_through(so_sach):
    so_sach.register("hoi", "x", params={"nguong": "so"})(lambda nguong=1: nguong * 2)
    assert so_sach.run("hoi", {"nguong": 50}) == 100


def test_the_listing_marks_which_commands_change_data(so_sach):
    so_sach.register("doc", "x")(lambda: None)
    so_sach.register("sua", "y", mutates=True)(lambda: None)
    mo_ta = {d["name"]: d["mutates"] for d in so_sach.describe()}
    assert mo_ta == {"doc": False, "sua": True}


def test_every_real_command_declares_whether_it_mutates():
    """Lenh that trong commands.py - khong duoc quen khai bao."""
    from app.use_cases.control import commands  # noqa: F401

    assert registry.REGISTRY, "phai co lenh duoc dang ky"
    for lenh in registry.REGISTRY.values():
        assert isinstance(lenh.mutates, bool)
        assert lenh.summary.strip(), f"{lenh.name} thieu mo ta"


# ---------------------------------------------------------------------------
# Danh sach cho phep chay tu xa
# ---------------------------------------------------------------------------
def test_a_command_is_not_remote_unless_it_says_so(so_sach):
    """⛔ DANH SACH CHO PHEP, KHONG PHAI DANH SACH CAM.

    Mot lenh moi them vao KHONG duoc tu nhien chay duoc tren may nguoi khac.
    Quen khai bao thi hau qua la "khong chay duoc" - chu khong phai "chay duoc
    tren may khach ma khong ai dinh".
    """
    so_sach.register("lenh_moi", "vua them")(lambda: "xong")
    assert so_sach.run("lenh_moi") == "xong"          # tai cho: duoc
    with pytest.raises(so_sach.RemoteNotAllowed, match="khong duoc phep chay tu xa"):
        so_sach.run("lenh_moi", remote=True)


def test_a_remote_command_runs_from_both_places(so_sach):
    so_sach.register("doc_duoc", "x", remote=True)(lambda: 1)
    assert so_sach.run("doc_duoc") == 1
    assert so_sach.run("doc_duoc", remote=True) == 1


def test_remote_does_not_bypass_confirmation(so_sach):
    """⛔ Hai phep kiem DOC LAP. Cho phep tu xa khong phai la da xac nhan."""
    goi = []
    so_sach.register("sua_tu_xa", "x", mutates=True, remote=True)(lambda: goi.append(1))
    with pytest.raises(so_sach.ConfirmationRequired):
        so_sach.run("sua_tu_xa", remote=True)
    assert goi == []
    so_sach.run("sua_tu_xa", remote=True, confirm=True)
    assert goi == [1]


def test_the_batch_moving_command_stays_local():
    """Lenh dao lo hang loat khong duoc chay tu xa - quyet dinh 13/11/2026."""
    from app.use_cases.control import commands  # noqa: F401

    assert registry.REGISTRY["gom_tren_nguong"].remote is False
