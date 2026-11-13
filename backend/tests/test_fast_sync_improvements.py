"""Dong bo nhanh: noi ro vi sao hong, va chay duoc cho ca lo chua dong bo.

⛔ HAI LO HONG NAY DO DUOC TU DU LIEU THAT, 13/11/2026.

1. THONG BAO LOI BI VUT. Code chi giu `type(exc).__name__`, nen 29 ca hong
   ngay 12-13/11 chi de lai dung chu "browser_profile_network_RuntimeError"
   trong rong. Nguoi van hanh khong co gi de doc, va phai chay lai bang tay
   moi biet chuyen gi.

   Nhung khong duoc nhet thong bao vao `reason`: do la MA phan loai, duoc dem
   trong `reason_counts`. Moi ngoai le mot thong bao khac nhau se lam bang dem
   vo ra thanh rac. Nen ma va chi tiet di rieng.

2. KHONG CHAY DUOC CHO "MOI NICK CHUA DONG BO". Endpoint chi nhan danh sach
   id, ma luc do co 1652 account chua tung dong bo (69%) - cach duy nhat la
   tich tay 1652 dong trong bang. Nen chung cu nam do.
"""
from __future__ import annotations

import pytest

from app.interfaces.api.tasks_router import ANALYTICS_SELECTORS
from app.use_cases.health_check.quick_check_use_case import (
    QuickCheckResult,
    exception_detail,
)


class _Account:
    def __init__(self, status="NEVER", follower_count=None):
        self.analytics_sync_status = status
        self.follower_count = follower_count


# ---------------------------------------------------------------------------
# 1. Thong bao loi
# ---------------------------------------------------------------------------
def test_the_exception_message_is_kept():
    """⛔ 29 ca hong chi de lai ten loai ngoai le, khong gi de doc."""
    r = QuickCheckResult(None, "browser_profile_network_RuntimeError",
                         detail=exception_detail(RuntimeError("page closed")))
    assert r.detail == "page closed"
    assert r.described() == "browser_profile_network_RuntimeError: page closed"


def test_the_reason_code_stays_countable():
    """⛔ Nhet thong bao vao `reason` thi bang dem ly do thanh rac.

    Hai ngoai le cung loai nhung khac thong bao phai dem CHUNG mot muc.
    """
    a = QuickCheckResult(None, "browser_profile_network_RuntimeError",
                         detail=exception_detail(RuntimeError("may A")))
    b = QuickCheckResult(None, "browser_profile_network_RuntimeError",
                         detail=exception_detail(RuntimeError("may B")))
    assert a.reason == b.reason
    assert a.detail != b.detail


def test_a_result_with_no_exception_reads_as_before():
    r = QuickCheckResult(None, "tiktok_challenge")
    assert r.detail == ""
    assert r.described() == "tiktok_challenge"


def test_the_message_is_flattened_and_bounded():
    """Duong dan tep va dau dong lam vo dong nhat ky trong giao dien."""
    d = exception_detail(RuntimeError("dong mot\n   dong hai\tdong ba"))
    assert "\n" not in d and "\t" not in d
    assert d == "dong mot dong hai dong ba"
    assert len(exception_detail(RuntimeError("x" * 900))) <= 180


# ---------------------------------------------------------------------------
# 2. Chon account theo trang thai
# ---------------------------------------------------------------------------
def test_never_synced_accounts_can_be_selected_without_listing_them():
    """⛔ 1652 account chua dong bo; tich tay tung dong la khong lam duoc."""
    loc = ANALYTICS_SELECTORS["never"]
    assert loc(_Account("NEVER")) is True
    assert loc(_Account("SUCCESS")) is False
    assert loc(_Account("FAILED")) is False


def test_an_empty_status_counts_as_never():
    """Account nhap vao chua co cot nay thi van phai chay duoc."""
    assert ANALYTICS_SELECTORS["never"](_Account("")) is True


def test_failed_and_missing_metrics_are_separate_questions():
    """FAILED la 'da thu va hong'; thieu so lieu la 'khong co du lieu', khong
    cung nghia - mot account SUCCESS cu van co the chua co follower_count."""
    assert ANALYTICS_SELECTORS["failed"](_Account("FAILED")) is True
    assert ANALYTICS_SELECTORS["failed"](_Account("NEVER")) is False
    assert ANALYTICS_SELECTORS["chua_co_so_lieu"](_Account("SUCCESS", None)) is True
    assert ANALYTICS_SELECTORS["chua_co_so_lieu"](_Account("SUCCESS", 120)) is False


def test_adding_a_selector_is_one_line():
    """⛔ Yeu cau that: de mo rong. Them cach chon khong duoc dong vao endpoint."""
    ANALYTICS_SELECTORS["thu_nghiem"] = lambda a: a.follower_count == 7
    try:
        assert ANALYTICS_SELECTORS["thu_nghiem"](_Account("SUCCESS", 7)) is True
    finally:
        ANALYTICS_SELECTORS.pop("thu_nghiem")
