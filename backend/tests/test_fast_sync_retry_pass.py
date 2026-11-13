"""Luot quet lai cuoi dot, cho nhung nick hong vi bi chan mem.

⛔ DO DUOC TREN DU LIEU THAT 13/11/2026. Lo 23 nick: 9 nick hong voi
`browser_tiktok_state_missing` - TikTok tra HTTP 200, khong co dau hieu
challenge, nhung trang KHONG kem du lieu. Do la chan mem khi nhieu luot doc
profile don tu mot IP trong vai phut.

Bang chung do la chan mem chu khong phai du lieu sai:
  - ca 9 nick deu DA CO `sec_uid` va so lieu tu lan chay truoc;
  - dung 4 trong so do chay lai luc vang: 4/4 thanh cong.

`fetch_profile` von da thu 2 lan, nhung cach nhau DUNG 1 GIAY - qua ngan cho
kieu chan nay, nen ca hai lan deu dap vao cung mot buc tuong. Quet lai o CUOI
DOT thi con dot da di qua.

⛔ VA PHAI LA MOT LUOT, KHONG PHAI MOT VONG LAP. Mot nick hong hai lan thi lan
thu ba cung hong, va mot vong lap o day bien mot dot 5 phut thanh mot dot
khong biet bao gio xong.
"""
from __future__ import annotations

import asyncio

import pytest

from app.use_cases.analytics.tiktok_fast_analytics_sync import (
    TikTokFastAnalyticsSyncService,
)


@pytest.fixture
def dich_vu(monkeypatch):
    svc = TikTokFastAnalyticsSyncService()
    monkeypatch.setattr(svc, "NGHI_TRUOC_KHI_QUET_LAI", 0.0, raising=False)
    return svc


def _chay(svc, cuu_duoc: set[str] | None = None) -> list[str]:
    """Chay luot quet lai; tra ve danh sach nick da duoc goi lai."""
    da_goi: list[str] = []
    cuu_duoc = cuu_duoc or set()

    async def gia_process_one(account_id, *a, **k):
        da_goi.append(account_id)
        if account_id in cuu_duoc:
            svc.updated += 1

    svc._process_one = gia_process_one
    asyncio.run(svc._quet_lai_nick_bi_chan({}, None, {}, 0))
    return da_goi


def test_a_soft_blocked_account_is_tried_again(dich_vu):
    dich_vu._ly_do_theo_nick = {"a@b.com": "browser_tiktok_state_missing"}
    assert _chay(dich_vu) == ["a@b.com"]


def test_a_terminal_reason_is_not_tried_again(dich_vu):
    """⛔ "Khong co tai khoan" thi thu lai bao nhieu lan cung the."""
    dich_vu._ly_do_theo_nick = {
        "a@b.com": "tiktok_status_10221",     # khong co nick
        "c@d.com": "tiktok_http_404",
        "e@f.com": "tiktok_user_info",        # thanh cong
    }
    assert _chay(dich_vu) == []


def test_only_the_blocked_ones_are_tried_again(dich_vu):
    dich_vu._ly_do_theo_nick = {
        "a@b.com": "browser_tiktok_state_missing",
        "c@d.com": "tiktok_user_info",
        "e@f.com": "tiktok_rate_limited",
        "g@h.com": "tiktok_status_10221",
    }
    assert sorted(_chay(dich_vu)) == ["a@b.com", "e@f.com"]


def test_rescued_accounts_stop_counting_as_failed(dich_vu):
    """So "hong" nguoi dung nhin thay phai la so hong THAT SU sau khi quet lai."""
    dich_vu._ly_do_theo_nick = {
        "a@b.com": "browser_tiktok_state_missing",
        "c@d.com": "browser_tiktok_state_missing",
    }
    dich_vu.failed = 2
    _chay(dich_vu, cuu_duoc={"a@b.com"})
    assert dich_vu.failed == 1
    assert dich_vu.quet_lai == 2
    assert dich_vu.cuu_duoc_khi_quet_lai == 1


def test_the_failed_count_never_goes_negative(dich_vu):
    dich_vu._ly_do_theo_nick = {"a@b.com": "tiktok_rate_limited"}
    dich_vu.failed = 0
    _chay(dich_vu, cuu_duoc={"a@b.com"})
    assert dich_vu.failed == 0


def test_nothing_to_retry_means_no_waiting(dich_vu, monkeypatch):
    """⛔ Khong duoc nghi 20 giay o cuoi MOI dot khi khong co gi de quet lai."""
    da_nghi = []
    monkeypatch.setattr(
        "app.use_cases.analytics.tiktok_fast_analytics_sync.asyncio.sleep",
        lambda s: da_nghi.append(s) or asyncio.sleep(0),
    )
    dich_vu._ly_do_theo_nick = {"a@b.com": "tiktok_user_info"}
    assert _chay(dich_vu) == []
    assert da_nghi == []


def test_the_retry_runs_once_not_in_a_loop(dich_vu):
    """⛔ Mot nick hong hai lan thi lan ba cung hong."""
    dich_vu._ly_do_theo_nick = {"a@b.com": "browser_tiktok_state_missing"}
    assert _chay(dich_vu).count("a@b.com") == 1


def test_the_retry_reasons_are_the_ones_that_can_clear(dich_vu):
    """Danh sach phai la nhung ly do CO THE het khi thu lai luc vang."""
    for ly_do in ("browser_tiktok_state_missing", "tiktok_rate_limited",
                  "tiktok_empty_response", "tiktok_challenge"):
        assert ly_do in TikTokFastAnalyticsSyncService.LY_DO_QUET_LAI
    for ly_do in ("tiktok_status_10221", "tiktok_http_404", "tiktok_user_info"):
        assert ly_do not in TikTokFastAnalyticsSyncService.LY_DO_QUET_LAI
