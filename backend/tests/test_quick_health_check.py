import asyncio
import json
import time

from app.use_cases.health_check import quick_check_use_case as quick_check_module
from app.use_cases.health_check.quick_check_use_case import (
    QuickCheckResult,
    QuickHealthCheckService,
    _build_tiktok_cookie_header,
    _classify_account_info_response,
    _classify_oembed_response,
    _classify_profile_html,
    _classify_profile_response,
    _extract_profile_metrics,
)


def _universal_html(detail: dict, extra_scope: dict | None = None) -> str:
    scope = {"webapp.user-detail": detail}
    if extra_scope:
        scope.update(extra_scope)
    payload = {"__DEFAULT_SCOPE__": scope}
    return (
        '<html><body><script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" '
        f'type="application/json">{json.dumps(payload)}</script></body></html>'
    )


def test_exact_tiktok_user_info_is_alive_and_interacted():
    html = _universal_html({
        "statusCode": 0,
        "userInfo": {
            "user": {"uniqueId": "target_user", "avatarLarger": "https://p16/avatar.jpeg"},
            "stats": {"videoCount": 3},
        },
    })

    result = _classify_profile_response(html, "target_user")

    assert result.classification == "SONG_DA_TUONG_TAC"
    assert result.reason == "tiktok_user_info"


def test_profile_metrics_only_store_values_returned_by_tiktok():
    metrics = _extract_profile_metrics({
        "videoCount": 12,
        "followerCount": 3456,
        "followingCount": 78,
        "heartCount": 9012,
    })

    assert metrics == {
        "video_count": 12,
        "follower_count": 3456,
        "following_count": 78,
        "likes_count": 9012,
    }
    assert "total_views" not in metrics


def test_tiktok_10221_is_the_only_embedded_not_found_signal():
    html = _universal_html({"statusCode": 10221, "statusMsg": "User doesn't exist"})

    result = _classify_profile_response(html, "missing_user")

    assert result.classification == "DIE"
    assert result.reason == "tiktok_status_10221"


def test_unrelated_suggested_account_does_not_create_false_alive():
    html = _universal_html(
        {"statusCode": 0, "userInfo": {}},
        extra_scope={
            "webapp.suggested-user": {
                "userInfo": {
                    "user": {"uniqueId": "target_user", "avatarLarger": "https://p16/avatar.jpeg"},
                    "stats": {"videoCount": 99},
                }
            }
        },
    )

    assert _classify_profile_html(html, "target_user") is None


def test_rate_limit_and_challenge_never_mark_account_dead():
    rate_limited = _classify_profile_response("", "target_user", http_status=429)
    challenged = _classify_profile_response("SlardarWAF", "target_user", http_status=200)

    assert rate_limited.classification is None
    assert rate_limited.reason == "tiktok_rate_limited"
    assert challenged.classification is None
    assert challenged.reason == "tiktok_challenge"


def test_official_http_not_found_is_dead_but_server_error_is_retryable():
    not_found = _classify_profile_response("", "target_user", http_status=404)
    server_error = _classify_profile_response("", "target_user", http_status=503)

    assert not_found.classification == "DIE"
    assert server_error.classification is None
    assert server_error.retryable is True


def test_account_info_exact_session_is_fast_alive_signal():
    body = json.dumps({
        "message": "success",
        "data": {
            "username": "target_user",
            "user_id": "123456789",
            "sec_user_id": "MS4wLjABAAAA",
        },
    })

    result = _classify_account_info_response(body, "target_user")

    assert result.classification == "ALIVE"
    assert result.reason == "tiktok_account_info"


def test_account_info_invalid_session_or_wrong_identity_never_marks_dead():
    invalid = _classify_account_info_response(
        json.dumps({"message": "error", "data": {"error_code": 8}}),
        "target_user",
    )
    mismatch = _classify_account_info_response(
        json.dumps({
            "message": "success",
            "data": {"username": "different_user", "user_id": "123"},
        }),
        "target_user",
    )

    assert invalid.classification is None
    assert mismatch.classification is None


def test_creator_oembed_exact_author_is_alive_but_4xx_is_inconclusive():
    alive = _classify_oembed_response(
        json.dumps({
            "type": "rich",
            "author_url": "https://www.tiktok.com/@target_user",
        }),
        "target_user",
    )
    unavailable = _classify_oembed_response(
        json.dumps({"code": 400, "message": "Something went wrong"}),
        "target_user",
        400,
    )

    assert alive.classification == "ALIVE"
    assert alive.reason == "tiktok_oembed"
    assert unavailable.classification is None
    assert unavailable.reason == "oembed_unavailable"


def test_cookie_header_filters_expired_and_foreign_domains_and_keeps_latest():
    cookies = [
        {"name": "sessionid", "value": "old", "domain": ".tiktok.com"},
        {"name": "foreign", "value": "secret", "domain": ".example.com"},
        {
            "name": "expired",
            "value": "gone",
            "domain": ".tiktok.com",
            "expires": time.time() - 10,
        },
        {"name": "sessionid", "value": "new", "domain": "www.tiktok.com"},
        {"name": "msToken", "value": "token", "domain": ".tiktok.com"},
    ]

    header = _build_tiktok_cookie_header(cookies)

    assert "sessionid=new" in header
    assert "msToken=token" in header
    assert "old" not in header
    assert "foreign" not in header
    assert "expired" not in header


def test_fast_account_info_stops_before_public_fallback(monkeypatch):
    service = QuickHealthCheckService()
    calls = []

    async def account_info(*_args):
        calls.append("account_info")
        return QuickCheckResult("ALIVE", "tiktok_account_info", http_status=200)

    async def should_not_run(*_args):
        calls.append("fallback")
        return QuickCheckResult(None, "unexpected")

    async def run_limited(factory):
        return await factory()

    monkeypatch.setattr(service, "_fetch_account_info", account_info)
    monkeypatch.setattr(service, "_fetch_oembed", should_not_run)
    monkeypatch.setattr(service, "_fetch_profile", should_not_run)

    result = asyncio.run(service._fetch_and_classify(
        None, "target_user", "sessionid=valid", run_limited
    ))

    assert result.classification == "ALIVE"
    assert calls == ["account_info"]


def test_public_fallback_waits_for_profile_die_when_oembed_is_unavailable(monkeypatch):
    service = QuickHealthCheckService()

    async def account_info(*_args):
        return QuickCheckResult(None, "account_info_session_invalid")

    async def oembed(*_args):
        return QuickCheckResult(None, "oembed_unavailable", http_status=400)

    async def profile(*_args):
        return QuickCheckResult("DIE", "tiktok_status_10221", http_status=200)

    async def run_limited(factory):
        return await factory()

    monkeypatch.setattr(service, "_fetch_account_info", account_info)
    monkeypatch.setattr(service, "_fetch_oembed", oembed)
    monkeypatch.setattr(service, "_fetch_profile", profile)

    result = asyncio.run(service._fetch_and_classify(
        None, "missing_user", "sessionid=expired", run_limited
    ))

    assert result.classification == "DIE"
    assert result.reason == "tiktok_status_10221"


def test_one_off_quick_check_broadcasts_completion(monkeypatch):
    service = QuickHealthCheckService()
    broadcasts = []

    async def capture_broadcast(payload):
        broadcasts.append(payload)

    monkeypatch.setattr(quick_check_module.ws_manager, "broadcast", capture_broadcast)

    asyncio.run(service.run_batch([]))

    assert [payload["event"] for payload in broadcasts] == ["QUICK_CHECK_FINISHED"]


def test_continuous_quick_check_cycle_suppresses_completion_popup_event(monkeypatch):
    service = QuickHealthCheckService()
    broadcasts = []

    async def capture_broadcast(payload):
        broadcasts.append(payload)

    monkeypatch.setattr(quick_check_module.ws_manager, "broadcast", capture_broadcast)

    asyncio.run(service.run_batch([], broadcast_finished=False))

    assert broadcasts == []
