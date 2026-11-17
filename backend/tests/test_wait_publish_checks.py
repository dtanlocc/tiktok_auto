"""Cho hai muc kiem tra cua TikTok Studio dung yen roi moi bam Dang.

⛔ NUT POST SANG LEN KHONG PHAI LA DA SAN SANG. Do 23/09/2026: Post bat sang
ngay khi dong tep con ghi "70.95MB/79.55MB, 3 seconds left" va muc nhac van
ghi "Checking in progress". Nguoi that khong bam o do.

⛔ VA HAI LO HONG CUA CHINH HAM NAY, sua 17/11/2026:

1. XAC NHAN QUA NGAN. No doi 2 lan doc sach cach nhau 1.2s - tuc xac nhan vo
   ven 1.2 giay. Hai muc kiem tra co the trong "sach" trong mot nhip ngan
   giua luc chu "Checking" bien mat va luc ket qua hien ra.

2. "CHUA HIEN RA" BI DOC THANH "DA QUA". `checks_busy` chi dung khi CO chu bao
   dang chay; khoi kiem tra chua kip render thi danh sach rong, va vong lap
   coi nhu da xong - bam Post truoc ca khi Studio ve xong hai cai tich.
"""
from __future__ import annotations

import asyncio

import pytest

from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter


class _Page:
    """Trang gia: tra lan luot cac trang thai da dung san."""

    def __init__(self, khung: list[dict]):
        self.khung = list(khung)
        self.so_lan = 0

    async def evaluate(self, _js):
        self.so_lan += 1
        return self.khung[min(self.so_lan - 1, len(self.khung) - 1)]


def _adapter(page, on_dinh=0.3, cho_hien=0.6, nghi=(0.0, 0.0)):
    a = object.__new__(InvisiblePlaywrightAdapter)
    a._page = page
    a.XAC_NHAN_ON_DINH_GIAY = on_dinh
    a.CHO_KIEM_TRA_HIEN_GIAY = cho_hien
    a.NGHI_TRUOC_KHI_BAM = (0.0, 0.0)
    # Cac khoang rut tham bi ghim lai: mot bai test do THOI DIEM thi khong
    # the vua do vua de so lieu doi moi lan chay.
    a.NGHI_SAU_CAPTION = nghi
    a.NOI_THEM_ON_DINH_GIAY = (0.0, 0.0)
    a.NHIP_DOC_LECH = (1.0, 1.0)

    async def gate(): return None
    async def interruptions(step_logger=None): return 0
    a._wait_automation_gate = gate
    a._handle_upload_interruptions = interruptions
    return a


def _chay(a, timeout=8.0):
    return asyncio.run(a._wait_publish_checks(timeout_seconds=timeout))


DANG_CHAY = {"status": ["Uploaded (79.55MB)"], "checks": ["Checking in progress"]}
XONG = {"status": ["Uploaded (79.55MB)"], "checks": ["No issues found."]}
CHUA_HIEN = {"status": ["Uploaded (79.55MB)"], "checks": []}
DANG_TAI = {"status": ["70.95MB/79.55MB, 3 seconds left"], "checks": []}


def test_it_waits_while_the_checks_are_running():
    a = _adapter(_Page([DANG_CHAY] * 3 + [XONG] * 20))
    kq = _chay(a)
    assert kq["settled"] is True
    assert "No issues found." in kq["checks"]


def test_it_waits_while_the_file_is_still_uploading():
    """⛔ Dong tep con dang chay thi chua phai luc bam."""
    a = _adapter(_Page([DANG_TAI] * 3 + [XONG] * 20))
    assert _chay(a)["settled"] is True


def test_a_check_that_has_not_rendered_does_not_count_as_passed():
    """⛔ LO HONG 2. Danh sach rong khong phai la 'da qua'.

    Trang chi tra danh sach RONG, mai mai. Ham phai cho het
    `CHO_KIEM_TRA_HIEN_GIAY` roi moi di tiep, chu khong duoc xong ngay.
    """
    a = _adapter(_Page([CHUA_HIEN]), on_dinh=0.2, cho_hien=3.0)
    import time
    t0 = time.monotonic()
    kq = _chay(a)
    mat = time.monotonic() - t0
    assert kq["settled"] is True          # cuoi cung van di tiep
    # 3.0s -> 3 lan doc, tuc hai nhip ngu truoc khi di tiep.
    cho = 2 * InvisiblePlaywrightAdapter.NHIP_DOC_GIAY
    assert mat >= cho, f"di tiep qua som: {mat:.2f}s < {cho:.2f}s"


def test_a_brief_clean_blip_does_not_settle_it():
    """⛔ LO HONG 1. Mot nhip 'sach' ngan giua hai trang thai ban khong duoc tinh."""
    khung = [DANG_CHAY, XONG, DANG_CHAY, DANG_CHAY] + [XONG] * 20
    # ⛔ Phai lon hon MOT nhip doc, khong thi khong co lo hong nao de bat:
    # 3.0s = 3 lan doc sach lien tiep.
    a = _adapter(_Page(khung), on_dinh=3.0)
    kq = _chay(a)
    assert kq["settled"] is True
    # Phai doc qua ca doan nhap nhay roi moi xong.
    assert a._page.so_lan > 4


def test_it_never_raises_when_the_page_breaks():
    class _Vo:
        async def evaluate(self, _js):
            raise RuntimeError("page closed")

    a = _adapter(_Vo())
    kq = _chay(a, timeout=1.0)
    assert kq["settled"] is False


def test_no_page_is_not_an_error():
    a = _adapter(None)
    assert _chay(a)["settled"] is False


def test_the_defaults_are_longer_than_the_old_behaviour():
    """Mac dinh that phai dai hon han 1.2 giay cua ban cu."""
    assert InvisiblePlaywrightAdapter.XAC_NHAN_ON_DINH_GIAY >= 3.0
    assert InvisiblePlaywrightAdapter.CHO_KIEM_TRA_HIEN_GIAY >= 10.0
    assert min(InvisiblePlaywrightAdapter.NGHI_TRUOC_KHI_BAM) >= 2.0


def test_it_keeps_sitting_after_the_checks_go_clear():
    """⛔ HAI CAI TICH SANG KHONG PHAI LA LUC BAM.

    Trang sach ngay tu lan doc dau. Ham van phai ngoi het khoang
    `NGHI_SAU_CAPTION` - do la luc TikTok con doi chieu nhac va dong goi lai
    video - chu khong duoc bam Dang ngay.
    """
    a = _adapter(_Page([XONG]), on_dinh=0.2, nghi=(6.0, 6.0))
    # So LAN DOC moi la cai dang do; nhip ngu lam nho de bai test chay nhanh.
    a.NHIP_DOC_GIAY = 0.001
    kq = _chay(a, timeout=60.0)
    assert kq["settled"] is True
    can = InvisiblePlaywrightAdapter._so_lan_can(6.0)
    assert a._page.so_lan >= can, (
        f"bam Dang qua som: {a._page.so_lan} lan doc, can {can}"
    )
    assert kq["nghi_sau_caption"] == pytest.approx(6.0)


def test_no_two_posts_sit_for_the_same_length():
    """⛔ MOT KHOANG DUNG CO DINH LA MOT DAU VET.

    Hai lan dang lien tiep tren cung mot man hinh phai cho ra hai khoang
    khac nhau. Ban cu dung dung 5.0 giay moi bai - 2000 bai mot nhip.
    """
    do_dai = set()
    for _ in range(12):
        a = _adapter(_Page([XONG]), on_dinh=0.2)
        a.NGHI_SAU_CAPTION = InvisiblePlaywrightAdapter.NGHI_SAU_CAPTION
        a.NHIP_DOC_LECH = (1.0, 1.0)
        # ⛔ THOI HAN PHAI RONG. Chan tren la mot nua thoi han, nen mot
        # timeout nho se ep moi lan ve dung mot gia tri va bai test nay se
        # xanh ma khong kiem tra gi. So lan doc van la 12-27 nhu that; chi
        # nhip ngu bi lam nho de khong ngoi that 14-32 giay.
        a.NHIP_DOC_GIAY = 0.001
        do_dai.add(round(_chay(a, timeout=90.0)["nghi_sau_caption"], 4))
    assert len(do_dai) > 1, f"moi lan dang deu dung mot khoang: {do_dai}"


def test_the_post_caption_pause_is_long_and_is_a_range():
    thap, cao = InvisiblePlaywrightAdapter.NGHI_SAU_CAPTION
    assert thap >= 10.0, "khoang dung sau caption phai dai"
    assert cao > thap, "phai la mot khoang, khong phai mot so"
    assert InvisiblePlaywrightAdapter.NHIP_DOC_LECH[0] < 1.0 < (
        InvisiblePlaywrightAdapter.NHIP_DOC_LECH[1]
    ), "nhip doc phai lech hai phia, khong thanh cai dong ho"
