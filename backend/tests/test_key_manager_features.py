"""Quyen ghi vao key phai dung voi quyen backend thuc su kiem tra.

⛔ BAI TEST NAY RA DOI TU MOT LOI DA PHAT RA NGOAI, TREN MOI KEY. Giao dien
phat key go san mot danh sach mac dinh trong HTML:

    <input name="features" value="accounts.manage,upload.video">

`accounts.manage` KHONG TON TAI - khong mot cho nao trong backend kiem tra cai
ten do - va bon quyen that (`accounts.login`, `profiles.update`,
`interaction.run`, `analytics.sync`) thi khong co trong danh sach. Ket qua la
**ca muoi mot key tung phat deu chi dang duoc video**.

Va lo ra rat muon:
  - luc phat key: khong bao gi, ten la di thang vao database;
  - luc khach mo app: van chay, vi may chu LUON tu them `app.start`
    (`effectiveFeatures` o supabase/functions/_shared/service.ts);
  - mai den luc khach bam nut dang nhap moi hien
        Feature is not licensed: accounts.login

Nen hai dieu duoc canh o day: danh sach mac dinh phai khop voi thuc te, va mot
ten quyen la phai bi chan NGAY LUC PHAT chu khong phai o may khach.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from app.security.runtime import GRANTABLE_FEATURES, _TASK_FEATURES, feature_for_task

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PAGE = _REPO_ROOT / "scripts" / "key_manager.html"
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

_TOKEN = "token-dung-cho-bai-test-0123456789"


def _default_features_in_the_page() -> list[str]:
    found = re.search(
        r'<input name="features" value="([^"]*)"', _PAGE.read_text(encoding="utf-8")
    )
    assert found, "Khong thay o 'features' trong key_manager.html"
    return [item.strip() for item in found.group(1).split(",") if item.strip()]


def test_the_page_offers_exactly_the_features_the_backend_enforces():
    """⛔ Day la bai test se do ngay tu ngay dau neu da co."""
    assert _default_features_in_the_page() == sorted(GRANTABLE_FEATURES)


def test_accounts_manage_is_not_a_real_feature():
    """Chinh cai ten da song qua muoi mot key."""
    assert "accounts.manage" not in GRANTABLE_FEATURES


def test_every_task_the_app_can_run_has_a_grantable_feature():
    """Them mot loai tac vu ma quen quyen cua no -> do o day, khong do o khach."""
    for task_type in _TASK_FEATURES:
        assert feature_for_task(task_type) in GRANTABLE_FEATURES, task_type
    assert feature_for_task("LOGIN_EMAIL") in GRANTABLE_FEATURES


def test_app_start_is_not_granted_by_hand():
    """May chu tu them; de no trong danh sach la moi nguoi go thua mot lan."""
    assert "app.start" not in GRANTABLE_FEATURES


class _ConnectionMustNotBeUsed:
    def __enter__(self):
        raise AssertionError(
            "Ten quyen la phai bi chan TRUOC khi cham vao database."
        )

    def __exit__(self, *exc_info):
        return False


@pytest.fixture()
def client(tmp_path, monkeypatch):
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
    monkeypatch.setattr(cli, "_connect", lambda operator: _ConnectionMustNotBeUsed())

    return TestClient(
        key_manager._build_app(tmp_path, _TOKEN), base_url="http://127.0.0.1"
    )


def _create(client, features: list[str]):
    return client.post(
        "/api/licenses",
        headers={"X-TKAuto-Token": _TOKEN},
        json={"customer_reference": "ban-A", "features": features},
    )


def test_an_unknown_feature_is_refused_before_the_database(client):
    response = _create(client, ["accounts.manage"])

    assert response.status_code == 400
    assert "accounts.manage" in response.json()["detail"]


def test_the_refusal_says_which_names_are_allowed(client):
    """Mot loi khong noi phai go gi thi doi nguoi ta doan - va ho doan sai."""
    detail = _create(client, ["accounts.loginn"]).json()["detail"]

    for feature in GRANTABLE_FEATURES:
        assert feature in detail
