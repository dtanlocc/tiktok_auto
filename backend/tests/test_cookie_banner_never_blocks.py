"""Popup xin phep cookie: dep duoc thi dep, khong co thi DI TIEP.

⛔ BAI TEST NAY CHAN MOT VONG LAP VO TAN DA QUAN SAT DUOC (24/10/2026).

Popup nay la custom element co shadow DOM, nen no chan dung nut Dang ma
`body.innerText` khong he thay. Ban dau ham dep popup bao "co chen ngang" moi
khi `querySelector` con thay phan tu - nhung TikTok KHONG go phan tu di, no chi
an. Nen lan goi nao cung bao "co chen ngang", va vong dang bai cu thu lai mai:

    "hien dang co don banner cookies, khong thay thi no tim de thay lien tuc,
     khong lam tiep"

Hai mat cua cung mot yeu cau:
  1. Lam MOT LAN - da dep roi thi thoi.
  2. KHONG BAO GIO CHAN - khong co popup, loi, hay het gio thi van dang binh
     thuong. Mot video da tai len 100% khong duoc chet vi mot popup khong co.
"""
from __future__ import annotations

import asyncio

from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter


class _Page:
    def __init__(self, *tra_ve, loi=None, treo=False):
        self.tra_ve = list(tra_ve)
        self.loi = loi
        self.treo = treo

    async def evaluate(self, _js):
        if self.loi is not None:
            raise self.loi
        if self.treo:
            await asyncio.sleep(3600)
        return self.tra_ve.pop(0) if self.tra_ve else False


def _goi(page, lan: int = 1) -> list[bool]:
    """Dung chinh ham that; `__init__` that dung ca mot browser nen bo qua."""
    adapter = object.__new__(InvisiblePlaywrightAdapter)
    adapter._page = page

    async def chay():
        return [await adapter._dismiss_cookie_banner() for _ in range(lan)]

    return asyncio.run(chay())


def test_no_popup_means_carry_on():
    """Truong hop thuong gap nhat."""
    assert _goi(_Page(False, False, False), 3) == [False, False, False]


def test_a_dismissed_popup_is_not_reported_again():
    """⛔ DAY LA VONG LAP VO TAN: TikTok giu lai phan tu, chi an di."""
    assert _goi(_Page(True, False, False, False), 4) == [True, False, False, False]


def test_an_error_does_not_stop_the_upload():
    """⛔ Khong tim thay popup la binh thuong, khong phai loi."""
    assert _goi(_Page(loi=RuntimeError("Execution context was destroyed"))) == [False]
    assert _goi(None) == [False]


def test_a_hanging_page_gives_up_instead_of_waiting_forever(monkeypatch):
    monkeypatch.setattr(InvisiblePlaywrightAdapter, "_BANNER_TIMEOUT", 0.05)
    assert _goi(_Page(treo=True)) == [False]


def test_the_script_marks_the_popup_before_it_does_anything_else():
    """⛔ Dau phai dat TRUOC moi nhanh, khong thi nhanh an-di khong duoc danh
    dau va vong lap quay lai. Va tu choi phai duoc thu truoc chap nhan."""
    js = InvisiblePlaywrightAdapter._COOKIE_BANNER_JS
    assert "if (!host || host.dataset.tkautoXong) return false;" in js
    assert js.index("dataset.tkautoXong = '1'") < js.index("shadowRoot")
    assert js.index("decline|reject") < js.index("allow|accept")
