"""Dịch vụ giải CAPTCHA là **tuỳ chọn**, không phải điều kiện để chạy.

⛔ TRƯỚC 14/10/2026 NÓ LÀ BẮT BUỘC, VÀ ĐÓ LÀ MỘT RÀNG BUỘC SAI. `launch_backend`
từ chối khởi động khi không có khoá, nên mọi người dùng đều buộc phải có tài
khoản OmoCaptcha dù không cần tới — và bản đóng gói phải mang theo khoá của ai
đó. Giờ: không có khoá thì app vẫn chạy, chỉ là add-on không được nạp; ai có
khoá thì nhập trong app bất cứ lúc nào.

Bài test này canh phần backend của thoả thuận đó: **không khoá thì không nạp
add-on**. Để nó lại với `api_key: ""` là một add-on nằm trong mọi phiên trình
duyệt mà không làm được gì — thêm một thứ Firefox phải kích hoạt, thêm một chữ
ký phải kiểm, và thêm một dấu vết nhận diện trên mọi trang khách mở.
"""
from __future__ import annotations

import pytest

from app.infrastructure.automation import configured_extensions
from app.infrastructure.automation.configured_extensions import (
    OMOCAPTCHA_ADDON_ID,
    configured_extension_builder,
)


@pytest.fixture()
def settings_with(monkeypatch):
    """Đổi `settings` mà không chạm vào file cấu hình thật."""

    def apply(**values):
        for name, value in values.items():
            monkeypatch.setattr(
                configured_extensions.settings, name, value, raising=False
            )

    return apply


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_without_a_key_the_captcha_addon_is_not_loaded_at_all(settings_with, empty):
    settings_with(OMOCAPTCHA_KEY=empty)
    _, excluded = configured_extension_builder()
    assert OMOCAPTCHA_ADDON_ID in excluded, (
        "Không có khoá thì add-on OmoCaptcha phải bị loại khỏi profile, "
        "không phải nạp vào với api_key rỗng."
    )


def test_with_a_key_the_addon_is_loaded_and_carries_that_key(settings_with):
    settings_with(OMOCAPTCHA_KEY="khoa-cua-khach-hang")
    builder, excluded = configured_extension_builder()
    assert OMOCAPTCHA_ADDON_ID not in excluded
    seeded = builder.storage_local_overrides[OMOCAPTCHA_ADDON_ID]
    assert seeded["api_key"] == "khoa-cua-khach-hang"
    assert seeded["initialized"] is True


def test_the_runtime_check_accepts_an_empty_key(settings_with):
    """⛔ Phép kiểm lúc chạy không được biến "không có khoá" thành lỗi.

    `validate_configured_extensions` chỉ so khoá KHI có khoá để so. Nếu nó đòi
    khớp vô điều kiện thì một máy không dùng OmoCaptcha sẽ chết ngay khi mở
    trình duyệt — và thông báo sẽ nói về chữ ký extension, không nói gì về khoá.
    """
    settings_with(OMOCAPTCHA_KEY="")
    # Không có extension nào được cài -> vòng lặp không chạy, không ném lỗi.
    configured_extensions.validate_configured_extensions([])


def test_the_bundled_extension_carries_no_credential():
    """⛔ XPI đi kèm bản phát hành không được mang khoá của bất kỳ ai.

    Khoá được tiêm vào `browser.storage.local` lúc chạy, nên `configs.json`
    trong XPI phải trống chỗ đó. `sanitize_extensions_for_release.py` lo việc
    này lúc build; bài test canh nguồn trong repo cũng không có sẵn khoá.
    """
    import json
    import re
    import zipfile
    from pathlib import Path

    repo_root = Path(__file__).resolve().parents[2]
    xpis = sorted((repo_root / "backend" / "extensions").glob("omocaptcha*.xpi"))
    if not xpis:
        pytest.skip("Không thấy XPI OmoCaptcha trong backend/extensions.")

    for xpi in xpis:
        with zipfile.ZipFile(xpi) as archive:
            try:
                raw = archive.read("configs.json").decode("utf-8-sig")
            except KeyError:
                continue
        config = json.loads(raw)
        for name, value in config.items():
            if "key" not in name.lower() and "token" not in name.lower():
                continue
            text = str(value).strip()
            # ⛔ RỖNG HOẶC PLACEHOLDER, không phải "rỗng" một cách cứng nhắc.
            # XPI gốc của nhà cung cấp mang sẵn `api_key: "YOUR_CLIEN_KEY"` —
            # đó là chỗ trống để người dùng điền, không phải khoá của ai. Đòi
            # rỗng tuyệt đối sẽ bắt nhầm nó và làm bài test này bị tắt đi,
            # trong khi thứ thật sự cần chặn là một khoá THẬT lọt vào repo.
            placeholder = bool(re.fullmatch(r"[A-Z0-9_\- ]+", text))
            assert not text or placeholder, (
                f"{xpi.name} mang sẵn {name!r} = {text[:12]}... — trông như một "
                "khoá thật. Khoá phải được tiêm lúc chạy, không đóng gói theo."
            )
