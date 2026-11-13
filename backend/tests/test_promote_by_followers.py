"""Gom nick dat nguong follow sang lo rieng, tach theo nuoc va ngay.

Ten lo mang ca NUOC va NGAY vi hai cau hoi hay di cung nhau: "nick 1k cua Duc
dau?" va "no len 1k hoi nao?".

⛔ BA DIEU DE LAM SAI, VA CA BA DEU MAT DU LIEU HOAC NIEM TIN:

1. Chay lan hai dong dau NGAY MOI len nick cu -> "len 1k tu bao gio" bi ghi de
   thanh "hom nay", va mat han sau lan chay thu hai.
2. Coi nick CHUA CO so lieu follower la "chua dat" -> 1528/2386 account hom
   13/11/2026 khong co so lieu; gop chung vao dau cung la noi sai.
3. Chuyen that ngay lan dau -> dao lon cach to chuc cua 2000 account, khong co
   nut hoan lai. Mac dinh phai la XEM TRUOC.
"""
from __future__ import annotations

import pytest

from app.interfaces.api.accounts_router import _already_promoted, _promoted_batch


def test_the_batch_name_carries_the_country_and_the_date():
    assert _promoted_batch(1000, "us", "2026-11-13") == "1K-US-2026-11-13"
    assert _promoted_batch(5000, "DE", "2026-12-01") == "5K-DE-2026-12-01"


def test_an_odd_threshold_keeps_its_number():
    """100 khong phai 0K."""
    assert _promoted_batch(100, "US", "2026-11-13") == "100-US-2026-11-13"
    assert _promoted_batch(1500, "US", "2026-11-13") == "1500-US-2026-11-13"


def test_an_account_with_no_country_is_still_named_something_readable():
    assert _promoted_batch(1000, "", "2026-11-13") == "1K-??-2026-11-13"


def test_running_twice_does_not_restamp_the_date():
    """⛔ DAY LA DIEU DE MAT NHAT. Ngay trong ten lo la ngay nick DAT NGUONG.

    Chay lai moi ngay ma khong co phep kiem nay thi moi nick deu mang ngay hom
    nay, va lich su "len 1k tu bao gio" bi xoa sach tu lan chay thu hai.
    """
    assert _already_promoted("1K-US-2026-11-13", 1000) is True
    assert _already_promoted("1K-DE-2026-01-02", 1000) is True


def test_a_different_threshold_is_a_different_question():
    """Nick da o lo 100 van phai duoc gom khi hoi nguong 1000."""
    assert _already_promoted("100-US-2026-11-13", 1000) is False
    assert _already_promoted("1K-US-2026-11-13", 100) is False


def test_an_ordinary_batch_is_not_mistaken_for_a_promoted_one():
    for ten in ("us sdt 9/11", "die", "LÔ_20261018", "", "test 31-10"):
        assert _already_promoted(ten, 1000) is False


def test_the_check_ignores_letter_case():
    assert _already_promoted("1k-us-2026-11-13", 1000) is True
