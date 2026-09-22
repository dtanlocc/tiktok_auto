import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.infrastructure.automation.egress_stability import (
    proxy_url_from_config,
    rotating_addresses,
)
from app.use_cases.upload.tiktok_upload_video import (
    TikTokUploadMediaUseCase,
    UnstableEgressError,
)


def test_a_vpn_that_changes_address_per_connection_is_rotating():
    # 2026-09-19, the machine VPN: every request left from a different 14.102.84.x
    ips = ["14.102.84.83", "14.102.84.55", "14.102.84.99", "14.102.84.44", "14.102.84.24"]
    assert rotating_addresses(ips) == sorted(ips)


def test_a_fixed_proxy_is_not_rotating():
    assert rotating_addresses(["151.244.238.42"] * 5) == []
    assert rotating_addresses([]) == []


def test_the_adapter_proxy_dict_becomes_one_url():
    assert proxy_url_from_config(None) is None
    assert proxy_url_from_config({"server": "socks5://151.244.238.42:50101",
                                  "username": "nguyenvo027", "password": "p@ss"}) == \
        "socks5://nguyenvo027:p%40ss@151.244.238.42:50101"
    assert proxy_url_from_config({"server": "socks5://1.2.3.4:5"}) == "socks5://1.2.3.4:5"


def _account():
    return SimpleNamespace(
        id="a", username="thai.nguyen.nng", cookies=[{"name": "sessionid", "value": "live"}],
        health_status="ALIVE", status="IDLE", current_step="", note="",
        upload_success_count=3, upload_failure_count=0,
        last_upload_status="SUCCESS", last_upload_error="", last_upload_at="",
    )


class _Repo:
    def __init__(self, account):
        self.account = account

    def get_by_id(self, _id):
        return self.account

    def save(self, _account):
        return None


class _Login:
    calls = 0

    async def login(self, *_a, **_kw):
        _Login.calls += 1
        return True


class _Browser:
    def __init__(self, ips, server_after_post=None):
        self.ips = ips
        self.server_after_post = server_after_post or {"state": "alive", "username": "thai.nguyen.nng"}
        self.published = 0

    async def sample_egress_ips(self, _samples=5):
        return list(self.ips)

    async def prepare_foryou_home(self, **_kw):
        return True

    async def publish_media(self, **_kw):
        self.published += 1
        return True

    async def read_session_account(self):
        return dict(self.server_after_post)

    async def validate_authenticated_identity(self, _username):
        return True

    async def extract_cookies(self):
        return [{"name": "sessionid", "value": "live"}]


def _batch(tmp_path: Path, browser, messages):
    video = tmp_path / "16. Cari rumah murah.mp4"
    video.write_bytes(b"video")
    _Login.calls = 0

    async def log(message):
        messages.append(message)

    use_case = TikTokUploadMediaUseCase(_Repo(_account()), browser, _Login(), None, step_logger=log)
    return asyncio.run(use_case.execute_video_batch("a", [str(video)]))


_ROTATING = ["14.102.84.83", "14.102.84.55", "14.102.84.99"]


def test_a_rotating_route_is_only_a_warning_by_default(tmp_path: Path):
    """The operator turned the block off (2026-09-19): warn, then post."""
    browser = _Browser(ips=_ROTATING)
    messages = []

    assert _batch(tmp_path, browser, messages) is True
    assert _Login.calls == 1 and browser.published == 1
    assert any("Cảnh báo" in m and "đổi IP" in m for m in messages)


def test_a_rotating_route_stops_before_login_when_the_block_is_on(tmp_path: Path, monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "UPLOAD_REQUIRE_STABLE_EGRESS", True)
    browser = _Browser(ips=_ROTATING)

    with pytest.raises(UnstableEgressError, match="đổi IP"):
        _batch(tmp_path, browser, [])
    assert _Login.calls == 0 and browser.published == 0


def test_a_fixed_route_posts(tmp_path: Path):
    browser = _Browser(ips=["151.244.238.42"] * 5)
    messages = []

    assert _batch(tmp_path, browser, messages) is True
    assert browser.published == 1
    assert any("ổn định 1 IP (151.244.238.42)" in m for m in messages)


def test_a_session_tiktok_ended_after_the_post_is_named_as_such(tmp_path: Path):
    browser = _Browser(ips=["151.244.238.42"] * 5,
                       server_after_post={"state": "signed_out", "detail": "session expired, please sign in again"})
    messages = []

    assert _batch(tmp_path, browser, messages) is True      # the video itself was published
    assert any("HỦY phiên đăng nhập ngay sau khi Post" in m for m in messages)
    assert not any("chưa xác minh đúng username" in m for m in messages)
