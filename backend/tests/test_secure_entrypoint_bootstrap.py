"""Bootstrap của launcher: cái gì bắt buộc, cái gì được phép rỗng.

⛔ BÀI TEST NÀY TỒN TẠI VÌ MỘT LỖI CHỈ HIỆN TRONG BẢN BIÊN DỊCH. Validator
trước đây đòi **mọi** trường không rỗng, nên khi khoá CAPTCHA trở thành tuỳ
chọn và launcher truyền chuỗi rỗng xuống, backend chết ngay khi khởi động với
`Secure launcher bootstrap value is invalid.` — một thông báo không nói trường
nào. Và nó chỉ xảy ra trong binary (dev chạy uvicorn trực tiếp, không qua
entrypoint này), nên không lần chạy dev nào thấy được.

Hai chiều đều phải canh: trường tuỳ chọn được rỗng, và **mọi trường còn lại thì
không** — thiếu `lease_path` hay `license_public_keys_json` là mất đường kiểm
license, thiếu `local_session_secret` là mở cổng local cho bất kỳ tiến trình nào
trên máy.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

_ENTRYPOINT = Path(__file__).resolve().parents[1] / "secure_entrypoint.py"


@pytest.fixture(scope="module")
def entrypoint():
    """Nạp secure_entrypoint.py như một module, không chạy `main()`."""
    spec = importlib.util.spec_from_file_location("tkauto_secure_entrypoint", _ENTRYPOINT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _bootstrap(**overrides) -> dict:
    payload = {
        "device_id": "device_" + "ab" * 20,
        "lease_path": r"C:\x\current.lease",
        "license_public_keys_json": '{"lease-dev-2026-01":"-----BEGIN PUBLIC KEY-----"}',
        "local_session_secret": "c2VjcmV0",
        "app_version": "0.1.14",
        "database_url": "sqlite:///C:/x/database.db",
        "omocaptcha_key": "khoa-cua-khach",
        "port": 51826,
    }
    payload.update(overrides)
    return payload


def _feed(monkeypatch, entrypoint, payload: dict):
    line = (json.dumps(payload) + "\n").encode("utf-8")
    stream = type("S", (), {"buffer": io.BytesIO(line)})()
    monkeypatch.setattr(entrypoint.sys, "stdin", stream, raising=False)
    return entrypoint._read_bootstrap()


def test_a_full_bootstrap_is_accepted(monkeypatch, entrypoint):
    fields, port = _feed(monkeypatch, entrypoint, _bootstrap())
    assert port == 51826
    assert fields["omocaptcha_key"] == "khoa-cua-khach"


def test_an_empty_captcha_key_is_accepted(monkeypatch, entrypoint):
    """⛔ Đây là trường hợp đã làm backend 0.1.14 không khởi động được."""
    fields, _ = _feed(monkeypatch, entrypoint, _bootstrap(omocaptcha_key=""))
    assert fields["omocaptcha_key"] == ""


@pytest.mark.parametrize(
    "field",
    [
        "device_id",
        "lease_path",
        "license_public_keys_json",
        "local_session_secret",
        "app_version",
        "database_url",
    ],
)
def test_every_other_field_must_still_be_present(monkeypatch, entrypoint, field):
    """Nới một trường không được phép nới cả nhóm."""
    with pytest.raises(RuntimeError, match="empty"):
        _feed(monkeypatch, entrypoint, _bootstrap(**{field: ""}))


def test_only_the_captcha_key_is_optional(entrypoint):
    """Danh sách tuỳ chọn phải đọc được ở một chỗ, và chỉ có đúng một tên."""
    assert entrypoint._BOOTSTRAP_OPTIONAL == frozenset({"omocaptcha_key"})


def test_an_unknown_field_is_refused(monkeypatch, entrypoint):
    payload = _bootstrap()
    payload["truong_la"] = "x"
    with pytest.raises(RuntimeError, match="fields are invalid"):
        _feed(monkeypatch, entrypoint, payload)


def test_a_missing_field_is_refused(monkeypatch, entrypoint):
    payload = _bootstrap()
    del payload["database_url"]
    with pytest.raises(RuntimeError, match="fields are invalid"):
        _feed(monkeypatch, entrypoint, payload)


@pytest.mark.parametrize("port", [0, 80, 1023, 65536, "51826", None])
def test_a_bad_port_is_refused(monkeypatch, entrypoint, port):
    with pytest.raises(RuntimeError, match="port is invalid"):
        _feed(monkeypatch, entrypoint, _bootstrap(port=port))


def test_the_error_message_names_the_offending_field(monkeypatch, entrypoint):
    """⛔ Thông báo cũ không nói trường nào, và đó là lý do lỗi này tốn thời gian.

    Một backend chết lúc khởi động không để lại gì ngoài dòng này; nó phải nói
    đủ để sửa được.
    """
    with pytest.raises(RuntimeError, match="lease_path"):
        _feed(monkeypatch, entrypoint, _bootstrap(lease_path=""))
    with pytest.raises(RuntimeError, match="app_version"):
        _feed(monkeypatch, entrypoint, _bootstrap(app_version=123))
