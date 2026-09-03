import asyncio
from datetime import datetime, timedelta

from app.use_cases.analytics.tiktok_fast_analytics_sync import (
    _is_cache_fresh,
    profile_metric_sync_result,
)
from app.use_cases.analytics.tiktok_public_video_client import (
    TikTokPublicVideoClient,
    extract_public_user_identity,
    extract_video_detail_html,
    normalize_profile_video_links,
    resolve_profile_video_links,
)


def test_profile_sync_never_invents_missing_metrics():
    assert profile_metric_sync_result({"video_count": 1})[0] == "PARTIAL"
    assert profile_metric_sync_result({})[0] == "FAILED"
    assert profile_metric_sync_result({
        "video_count": 1,
        "follower_count": 0,
        "following_count": 0,
        "likes_count": 0,
    }) == ("SUCCESS", "")


def test_profile_cache_window_is_bounded():
    fresh = (datetime.now() - timedelta(seconds=10)).isoformat(timespec="seconds")
    stale = (datetime.now() - timedelta(seconds=180)).isoformat(timespec="seconds")
    assert _is_cache_fresh(fresh, 120) is True
    assert _is_cache_fresh(stale, 120) is False
    assert _is_cache_fresh(fresh, 0) is False


def test_identity_extractor_ignores_suggested_users():
    html = '''<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
    {"__DEFAULT_SCOPE__":{"webapp.user-detail":{"userInfo":{"user":
    {"uniqueId":"target_user","id":"42","secUid":"sec-target"}}},
    "webapp.suggested-user":{"userInfo":{"user":{"uniqueId":"other","secUid":"sec-other"}}}}}
    </script>'''
    assert extract_public_user_identity(html, "target_user") == {
        "username": "target_user",
        "user_id": "42",
        "sec_uid": "sec-target",
    }
    assert extract_public_user_identity(html, "other") is None


def test_public_profile_data_is_normalized_from_user_hydration():
    from app.use_cases.health_check.quick_check_use_case import _extract_public_profile_data

    data = _extract_public_profile_data(
        {
            "id": "42",
            "secUid": "sec-target",
            "nickname": "Target",
            "signature": "hello",
            "avatarLarger": "https://example.test/a.jpg",
            "verified": True,
            "privateAccount": False,
            "bioLink": {"link": "https://example.test"},
        },
        {},
    )
    assert data["display_name"] == "Target"
    assert data["verified"] is True
    assert data["website_url"] == "https://example.test"


def test_profile_video_links_require_exact_owner_and_are_deduplicated():
    links = normalize_profile_video_links(
        [
            "https://www.tiktok.com/@target_user/video/7176222902134508827",
            "https://www.tiktok.com/@target_user/video/7176222902134508827?lang=en",
            "https://www.tiktok.com/@other/video/7176190120381467931",
            "https://example.test/@target_user/video/123456",
        ],
        "target_user",
        10,
    )
    assert links == [
        "https://www.tiktok.com/@target_user/video/7176222902134508827?lang=en"
    ]


def test_known_video_url_fills_temporarily_empty_profile_grid():
    assert resolve_profile_video_links(
        [],
        ["https://www.tiktok.com/@target_user/video/7176222902134508827"],
        "target_user",
        10,
    ) == ["https://www.tiktok.com/@target_user/video/7176222902134508827?lang=en"]


def test_rendered_profile_links_stay_ahead_of_known_fallback_links():
    assert resolve_profile_video_links(
        ["https://www.tiktok.com/@target_user/video/7176222902134508828"],
        ["https://www.tiktok.com/@target_user/video/7176222902134508827"],
        "target_user",
        10,
    ) == [
        "https://www.tiktok.com/@target_user/video/7176222902134508828?lang=en",
        "https://www.tiktok.com/@target_user/video/7176222902134508827?lang=en",
    ]


def test_video_detail_html_returns_only_requested_video():
    html = '''<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
    {"__DEFAULT_SCOPE__":{"webapp.video-detail":{"itemInfo":{"itemStruct":{
      "id":"7176222902134508827","desc":"caption","createTime":1670844603,
      "stats":{"playCount":8,"diggCount":1,"commentCount":2,"shareCount":3}
    }}}}}
    </script>'''
    row = extract_video_detail_html(
        html,
        "7176222902134508827",
        "https://www.tiktok.com/@target/video/7176222902134508827",
    )
    assert row is not None
    assert row["view_count"] == 8
    assert row["like_count"] == 1
    assert row["comment_count"] == 2
    assert row["share_count"] == 3
    assert row["share_url"].endswith("/7176222902134508827")
    assert extract_video_detail_html(html, "9999999999999999999") is None


def test_zero_video_profile_skips_browser_initialization():
    client = TikTokPublicVideoClient()

    async def fail_if_called():
        raise AssertionError("browser must not start for an account with zero videos")

    client._ensure_page = fail_if_called
    assert asyncio.run(
        client.fetch_videos(
            "empty_profile",
            "sec-empty",
            max_videos=30,
            expected_video_count=0,
        )
    ) == ([], True)
