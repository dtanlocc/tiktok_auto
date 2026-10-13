"""Giao diện quản lí key chạy local, nhưng "local" không có nghĩa là an toàn.

⛔ MỘT CỔNG TRÊN 127.0.0.1 KHÔNG PHẢI MỘT CỔNG RIÊNG TƯ. Bất kỳ trang web nào
đang mở trong trình duyệt của operator đều gửi được request tới nó (CSRF), và
một tên miền do kẻ khác sở hữu trỏ về 127.0.0.1 còn vượt được cả kiểm tra
same-origin (DNS rebinding) — lúc đó trang của họ **cùng origin** với cổng này.
Mà tiến trình này đang giữ mật khẩu database và pepper.

Nên có ba lớp, và bài test này canh cả ba:
  1. token ngẫu nhiên sinh mỗi lần chạy, mọi request phải mang;
  2. header `Host` phải là 127.0.0.1/localhost — đây là lớp chặn DNS rebinding,
     vì token nằm trong trang nên một trang same-origin đọc được nó;
  3. chỉ bind 127.0.0.1 (kiểm bằng mắt trong `main()`, không test được ở đây).

⛔ VÀ SO TOKEN BẰNG `compare_digest`. Một vòng `==` thoát sớm ở byte đầu khác
nhau, và token này là thứ duy nhất đứng giữa một trang web bất kỳ và quyền phát
key.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

_TOKEN = "token-dung-cho-bai-test-0123456789"


class _FakeCursor:
    def __init__(self, recorder: list):
        self.recorder = recorder
        self.rowcount = 1
        self.description = []
        self._rows: list = []

    #: Đúng những cột mà `/api/licenses` đọc. Một fake thiếu cột chỉ làm bài
    #: test vỡ ở `KeyError` thay vì nói được gì về lớp bảo vệ.
    _OVERVIEW = (
        ("id", "lic_x"),
        ("customer_reference", "ban-A"),
        ("plan", "nhom"),
        ("channel", "stable"),
        ("group_feature", "group.hn01"),
        ("role_feature", "role.member"),
        ("features", ["accounts.manage", "group.hn01"]),
        ("max_accounts", 200),
        ("max_tabs", 4),
        ("max_devices", 2),
        ("active_devices", 1),
        ("expires_at", 1_800_000_000),
        ("days_left", 30),
        ("revoked", False),
        ("revoked_reason", None),
    )

    def execute(self, sql, params=()):
        self.recorder.append((" ".join(sql.split()), params))
        if "from license.overview" in sql:
            self.description = [
                type("C", (), {"name": name})() for name, _ in self._OVERVIEW
            ]
            self._rows = [tuple(value for _, value in self._OVERVIEW)]
        else:
            self.description = []
            self._rows = []

    def fetchall(self):
        return self._rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeConnection:
    def __init__(self, recorder: list):
        self.recorder = recorder
        self.committed = False

    def cursor(self):
        return _FakeCursor(self.recorder)

    def commit(self):
        self.committed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """App thật, nhưng database và bí mật là đồ thay."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    import key_manager
    import supabase_license_admin as cli

    secrets_dir = tmp_path / "secrets"
    secrets_dir.mkdir()
    (secrets_dir / "license-key-pepper").write_text("p" * 48, encoding="utf-8")
    (secrets_dir / "supabase-database-url").write_text(
        "postgresql://u:p@localhost:5432/d", encoding="utf-8"
    )

    recorder: list = []
    monkeypatch.setattr(cli, "_connect", lambda operator: _FakeConnection(recorder))

    app = key_manager._build_app(tmp_path, _TOKEN)
    test_client = TestClient(app, base_url="http://127.0.0.1")
    test_client.recorder = recorder
    test_client.operator = tmp_path
    return test_client


def test_the_page_needs_the_token(client):
    assert client.get("/").status_code == 401
    assert client.get("/?t=sai").status_code == 401
    assert client.get(f"/?t={_TOKEN}").status_code == 200


def test_every_api_route_needs_the_token(client):
    """⛔ Thân request phải HỢP LỆ trong bài test này, có lý do.

    FastAPI kiểm body TRƯỚC khi vào thân hàm, nên một POST không token kèm body
    sai trả 422 chứ không phải 401 - và lúc đó bài test không chứng minh được
    gì về lớp token. Gửi body đúng hình dạng để thứ duy nhất còn từ chối được
    là `_guard`.
    """
    routes = [
        ("get", "/api/licenses", None),
        ("get", "/api/devices", None),
        ("get", "/api/releases", None),
        ("post", "/api/licenses", {"customer_reference": "x"}),
        ("post", "/api/licenses/lic_x/revoke", {"reason": "x"}),
        ("post", "/api/devices/device_x/revoke", {"reason": "x"}),
        ("post", "/api/releases/artifact_x/deactivate", None),
    ]
    for method, path, body in routes:
        response = (
            client.post(path, json=body) if method == "post" else client.get(path)
        )
        assert response.status_code == 401, (method, path, response.status_code, response.text)
        assert client.recorder == [], (
            f"{method} {path} cham vao database truoc khi kiem token"
        )


def test_a_rebinding_host_is_refused_even_with_the_right_token(client):
    """⛔ Lớp chặn DNS rebinding.

    Nếu một tên miền của kẻ khác trỏ về 127.0.0.1 thì trang của họ là
    same-origin với cổng này và đọc được token trong URL. Kiểm `Host` là thứ
    duy nhất còn chặn được.
    """
    for host in ("ke-tan-cong.example", "127.0.0.1.evil.test", "0.0.0.0"):
        response = client.get(
            "/api/licenses",
            headers={"x-tkauto-token": _TOKEN, "host": host},
        )
        assert response.status_code == 403, (host, response.status_code)
    assert client.recorder == []


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.1:9000", "localhost:8080"])
def test_the_allowed_hosts_still_work(client, host):
    response = client.get(
        "/api/licenses", headers={"x-tkauto-token": _TOKEN, "host": host}
    )
    assert response.status_code == 200, (host, response.text)


def test_creating_a_key_stores_only_the_digest_and_reveals_the_key_once(client):
    from control_plane.app.security import digest_license_key

    response = client.post(
        "/api/licenses",
        headers={"x-tkauto-token": _TOKEN},
        json={
            "customer_reference": "ban-A",
            "plan": "nhom",
            "group": "hn01",
            "role": "member",
            "features": ["accounts.manage"],
            "max_accounts": 200,
            "max_tabs": 4,
            "max_devices": 2,
            "channel": "stable",
            "expires_days": 30,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()

    # Nhóm và quyền phải thành feature `group.` / `role.`, và loại trùng.
    assert body["features"] == [
        "accounts.manage",
        "group.hn01",
        "role.member",
    ]

    inserts = [sql for sql, _ in client.recorder if "insert into license.licenses" in sql]
    assert len(inserts) == 1
    params = next(p for sql, p in client.recorder if "insert into license.licenses" in sql)
    # ⛔ KEY THẬT KHÔNG ĐƯỢC CÓ TRONG BẤT KỲ THAM SỐ SQL NÀO.
    assert body["license_key"] not in [str(value) for value in params]
    assert digest_license_key(body["license_key"], "p" * 48) in params

    # Và phải được ghi ra file, vì database chỉ có digest.
    saved = Path(body["saved_to"])
    assert saved.is_file()
    assert json.loads(saved.read_text(encoding="utf-8"))["license_key"] == body["license_key"]


def test_an_unknown_field_in_the_create_form_is_refused(client):
    response = client.post(
        "/api/licenses",
        headers={"x-tkauto-token": _TOKEN},
        json={"customer_reference": "x", "khong_co_truong_nay": 1},
    )
    assert response.status_code == 422, response.text
    assert client.recorder == []


def test_revoking_says_how_long_the_delay_is(client):
    """Thu hồi KHÔNG tức thì; giao diện phải nói ra chứ không để người dùng đoán."""
    response = client.post(
        "/api/licenses/lic_x/revoke",
        headers={"x-tkauto-token": _TOKEN},
        json={"reason": "thu"},
    )
    assert response.status_code == 200, response.text
    note = response.json()["note"]
    assert "1 gio" in note and "60 giay" in note


def test_the_page_loads_no_external_resource():
    """⛔ Trang này mở ra trong lúc tiến trình giữ mật khẩu database và pepper.

    Một thẻ script/style trỏ ra Internet là mời bên thứ ba vào cùng origin với
    thứ đó.
    """
    html = (_REPO_ROOT / "scripts" / "key_manager.html").read_text(encoding="utf-8")
    for needle in ("src=\"http", "src='http", "href=\"http", "href='http", "@import"):
        assert needle not in html, f"trang tai tai nguyen ngoai: {needle}"
