"""Dong trinh duyet hai luot song song: khong duoc bao loi, khong duoc lam hai lan.

⛔ BAI TEST NAY CHAN MOT DONG ERROR GIA TREN DUONG DI BINH THUONG.

`close()` chay HAI LUOT song song (nut Dong goi mot luot, bo theo doi cua so
goi luot nua - trong log that thay `[+] Da dong phien trinh duyet` in hai lan).
Phan don dep ho so tam co nhieu `await`, va moi `await` la mot cho nhuong event
loop. Truoc 14/10/2026 ca hai luot deu qua duoc `if self._temp_profile_path`:
luot A nhuong loop trong `persist_external_storage`, luot B vao theo, roi luot
ve sau goi `shutil.rmtree(None)`:

    INFO : [*] Dang don dep ho so tam thoi: C:\...\temp_ef4ef264-...
    INFO : [*] Dang don dep ho so tam thoi: None
    ERROR: [-] Loi phat sinh khi dong trinh duyet va don dep:
           lstat: path should be string, bytes or os.PathLike, not NoneType

`ignore_errors=True` khong do duoc, vi no chi bat `OSError` con day la
`TypeError`. Ba dong tren la copy tu log cua ban 0.1.15 DA BIEN DICH, ngay sau
mot lan mo trinh duyet trang hoan toan thanh cong - nen nguoi dung doc duoc bao
loi o dung luc moi thu vua chay dung, va mot dong ERROR gia tren duong di binh
thuong thi lam che mat loi that.

Dieu thu hai, nang hon mot dong log: hai luot cung goi
`persist_external_storage` tren CUNG MOT ho so, trong hai thread.

⛔ HAI DO TRE DUOI DAY LA DIEU KIEN DE BAI TEST CO NGHIA. Khong co chung thi
luot A xong truoc khi luot B kip cham vao `_temp_profile_path`, va bai test di
qua KE CA KHI LOI CON NGUYEN - da do dung the.
"""
from __future__ import annotations

import asyncio
import logging
import time

import pytest

from app.infrastructure.automation.playwright_adapter import InvisiblePlaywrightAdapter

#: Driver that cua phien mat vai giay de dong; 50ms la du de ca hai luot
#: `close()` cung vao duoc khoi `if self._invisible_pw:`.
_DRIVER_CLOSE = 0.05

#: `persist_external_storage` that doc/ghi tep tren o. 400ms rong hon moi do
#: xe dich cua scheduler, nen cua so dua la chac chan chu khong may rui.
_PERSIST_WORK = 0.4


class _Driver:
    """Phien trinh duyet gia: `close()` await `instance.__aexit__`."""

    _session_token = None

    async def __aexit__(self, *exc_info) -> None:
        await asyncio.sleep(_DRIVER_CLOSE)


class _Builder:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def persist_external_storage(self, profile_path: str) -> list[str]:
        self.calls.append(profile_path)
        time.sleep(_PERSIST_WORK)
        return []


@pytest.fixture
def adapter(tmp_path):
    """Dung chinh `close()` that, khong goi `__init__` (no dung ca mot browser).

    Chi dat nhung thuoc tinh ma `close()` doc tren duong di nay.
    """
    profile = tmp_path / "profile"
    profile.mkdir()
    (profile / "prefs.js").write_text("user_pref('x', 1);", encoding="utf-8")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "clip.mp4").write_bytes(b"\x00")

    instance = object.__new__(InvisiblePlaywrightAdapter)
    instance._hwnd = None
    instance._window_visible = True
    instance._invisible_pw = _Driver()
    instance._temp_profile_path = str(profile)
    builder = _Builder()
    instance._extension_profile_builder = builder
    # `close()` xoa tham chieu builder, nen giu san chinh cai list dem.
    instance._builder_calls = builder.calls
    instance._native_upload_staging_dirs = [str(staging)]
    instance._profile_dir = profile
    instance._staging_dir = staging
    return instance


async def _close_twice(instance) -> None:
    await asyncio.gather(instance.close(), instance.close())


def test_two_concurrent_closes_report_no_error(adapter, caplog):
    with caplog.at_level(logging.ERROR, logger="PlaywrightAdapter"):
        asyncio.run(_close_twice(adapter))

    assert [record.getMessage() for record in caplog.records] == []


def test_two_concurrent_closes_still_clean_everything_up(adapter):
    asyncio.run(_close_twice(adapter))

    assert not adapter._profile_dir.exists(), "Ho so tam phai bi xoa"
    assert not adapter._staging_dir.exists(), "Thu muc staging phai bi xoa"


def test_the_profile_is_persisted_exactly_once(adapter):
    """⛔ Hai thread ghi cung mot ho so la mot phep ghi dua nhau."""
    asyncio.run(_close_twice(adapter))

    assert adapter._extension_profile_builder is None
    assert len(adapter._builder_calls) == 1, adapter._builder_calls


def test_closing_again_later_is_harmless(adapter, caplog):
    """Goi lai sau khi da dong xong thi khong duoc nem gi, khong duoc bao loi."""
    with caplog.at_level(logging.ERROR, logger="PlaywrightAdapter"):
        asyncio.run(adapter.close())
        asyncio.run(adapter.close())

    assert [record.getMessage() for record in caplog.records] == []
    assert adapter._temp_profile_path is None
    assert adapter._extension_profile_builder is None
    assert list(adapter._native_upload_staging_dirs) == []
