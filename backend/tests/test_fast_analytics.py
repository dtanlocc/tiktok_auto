import asyncio
from datetime import datetime, timedelta

from app.use_cases.analytics import tiktok_public_video_client as video_client_module
from app.use_cases.analytics.tiktok_fast_analytics_sync import (
    _is_cache_fresh,
    _is_sync_cache_usable,
    _is_video_cache_usable,
    _merge_video_completeness,
    _stale_video_ids_to_remove,
    profile_metric_sync_result,
)
from app.use_cases.analytics.tiktok_public_video_client import (
    TikTokPublicVideoClient,
    _playwright_proxy_options,
    extract_public_user_identity,
    extract_public_video_detail_html,
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


def test_failed_sync_is_never_treated_as_usable_cache():
    fresh = (datetime.now() - timedelta(seconds=10)).isoformat(timespec="seconds")

    assert _is_sync_cache_usable(
        "SUCCESS", "TIKTOK_PUBLIC_PROFILE", fresh, 120
    ) is True
    assert _is_sync_cache_usable(
        "FAILED", "TIKTOK_PUBLIC_PROFILE", fresh, 120
    ) is False


def test_video_cache_requires_every_expected_row_to_be_recent():
    fresh = (datetime.now() - timedelta(seconds=10)).isoformat(timespec="seconds")
    stale = (datetime.now() - timedelta(seconds=600)).isoformat(timespec="seconds")

    class Row:
        def __init__(self, synced_at):
            self.synced_at = synced_at

    assert _is_video_cache_usable([Row(fresh), Row(fresh)], 2, 300) is True
    assert _is_video_cache_usable([Row(fresh)], 2, 300) is False
    assert _is_video_cache_usable([Row(fresh), Row(fresh)], 1, 300) is False
    assert _is_video_cache_usable([Row(fresh), Row(stale)], 2, 300) is False


def test_stale_video_rows_are_removed_only_after_complete_full_crawl():
    existing = {"1", "2", "3"}
    current = {"1", "3"}

    assert _stale_video_ids_to_remove(
        existing,
        current,
        complete=True,
        profile_video_count=2,
        max_videos=60,
    ) == {"2"}
    assert _stale_video_ids_to_remove(
        existing,
        current,
        complete=False,
        profile_video_count=2,
        max_videos=60,
    ) == set()
    assert _stale_video_ids_to_remove(
        existing,
        current,
        complete=True,
        profile_video_count=100,
        max_videos=60,
    ) == set()


def test_incomplete_video_collection_is_partial_not_success():
    status, error = _merge_video_completeness("SUCCESS", "", 31, 14, False)

    assert status == "PARTIAL"
    assert "14/31" in error
    assert _merge_video_completeness("SUCCESS", "", 31, 31, True) == (
        "SUCCESS",
        "",
    )


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


def test_public_video_detail_reports_quality_region_favorites_and_shadow_ban():
    html = '''<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
    {"__DEFAULT_SCOPE__":{"webapp.video-detail":{"statusCode":0,"itemInfo":{"itemStruct":{
      "id":"7176222902134508827","desc":"caption","createTime":1670844603,
      "locationCreated":"ID","isReviewing":false,"takeDown":0,
      "stats":{"playCount":"8","diggCount":"1","commentCount":"2",
               "shareCount":"3","collectCount":"4","repostCount":"5"},
      "video":{"duration":17,"width":720,"height":1280,"bitrateInfo":[
        {"Bitrate":500000,"BitrateFPS":30,"PlayAddr":{"Width":720,"Height":1280}},
        {"Bitrate":900000,"BitrateFPS":59,"PlayAddr":{"Width":1080,"Height":1920}}
      ]}
    }}}}}
    </script>'''

    row = extract_public_video_detail_html(
        html,
        "7176222902134508827",
        "https://www.tiktok.com/@target/video/7176222902134508827",
    )

    assert row is not None
    assert row["max_quality"] == "1080p60"
    assert row["region"] == "ID"
    assert row["favorite_count"] == 4
    assert row["repost_count"] == 5
    assert row["download_count"] is None
    assert row["shadow_ban"] == "YES"
    assert row["index_enabled"] is None
    assert "Thiếu indexEnabled" in row["shadow_ban_reason"]
    assert row["detail_source"] == "Browser"


def test_index_enabled_true_is_not_shadow_banned():
    html = '''<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
    {"__DEFAULT_SCOPE__":{"webapp.video-detail":{"itemInfo":{"itemStruct":{
      "id":"7176222902134508827","indexEnabled":true,
      "stats":{"playCount":0},"video":{"width":720,"height":1280}
    }}}}}
    </script>'''

    row = extract_public_video_detail_html(html, "7176222902134508827")

    assert row is not None
    assert row["shadow_ban"] == "NO"
    assert row["index_enabled"] is True
    assert row["max_quality"] == "720p"


def test_self_only_video_status_is_preserved_as_shadow_ban_evidence():
    html = '''<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
    {"__DEFAULT_SCOPE__":{"webapp.video-detail":{"statusCode":10204,"statusMsg":""}}}
    </script>'''

    row = extract_public_video_detail_html(
        html,
        "7176222902134508827",
        "https://www.tiktok.com/@target/video/7176222902134508827",
    )

    assert row is not None
    assert row["detail_available"] is False
    assert row["shadow_ban"] == "YES"
    assert "10204" in row["shadow_ban_reason"]


def test_playwright_proxy_options_keep_auth_separate_from_server():
    assert _playwright_proxy_options(
        "socks5://user%40mail.test:p%40ss@127.0.0.1:1080"
    ) == {
        "server": "socks5://127.0.0.1:1080",
        "username": "user@mail.test",
        "password": "p@ss",
    }


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


def test_known_video_urls_refresh_without_opening_browser():
    client = TikTokPublicVideoClient()
    observed = {}

    async def http_details(links, **kwargs):
        observed["links"] = links
        observed.update(kwargs)
        return [
            {
                "video_id": link.rstrip("/").split("/")[-1].split("?")[0],
                "create_time": index + 1,
                "detail_available": True,
            }
            for index, link in enumerate(links)
        ]

    async def browser_must_not_open(*_args, **_kwargs):
        raise AssertionError("known complete video URLs must stay on HTTP fast path")

    client._fetch_video_details_http = http_details
    client._ensure_page = browser_must_not_open
    rows, complete = asyncio.run(client.fetch_videos(
        "target_user",
        "sec-target",
        expected_video_count=2,
        known_video_urls=[
            "https://www.tiktok.com/@target_user/video/7176222902134508828",
            "https://www.tiktok.com/@target_user/video/7176222902134508827",
        ],
        proxy_url="socks5://127.0.0.1:1080",
        cookie_header="sessionid=secret",
    ))

    assert complete is True
    assert [row["video_id"] for row in rows] == [
        "7176222902134508827",
        "7176222902134508828",
    ]
    assert observed["proxy_url"] == "socks5://127.0.0.1:1080"
    assert observed["cookie_header"] == "sessionid=secret"


def test_known_video_http_failure_opens_browser_for_missing_only():
    client = TikTokPublicVideoClient()
    page = object()
    calls = {"browser": 0, "missing": []}

    async def http_details(links, **_kwargs):
        return [
            {
                "video_id": "7176222902134508828",
                "create_time": 2,
                "detail_available": True,
            },
            None,
        ]

    async def ensure_page(*_args, **_kwargs):
        calls["browser"] += 1
        return page

    async def no_cookie_seed(*_args, **_kwargs):
        return None

    async def fill_missing(actual_page, links, results):
        assert actual_page is page
        calls["missing"] = [index for index, row in enumerate(results) if row is None]
        results[1] = {
            "video_id": "7176222902134508827",
            "create_time": 1,
            "detail_available": True,
        }

    client._fetch_video_details_http = http_details
    client._ensure_page = ensure_page
    client._apply_cookie_header = no_cookie_seed
    client._fill_missing_details_with_browser = fill_missing
    rows, complete = asyncio.run(client.fetch_videos(
        "target_user",
        "sec-target",
        expected_video_count=2,
        known_video_urls=[
            "https://www.tiktok.com/@target_user/video/7176222902134508828",
            "https://www.tiktok.com/@target_user/video/7176222902134508827",
        ],
    ))

    assert complete is True
    assert len(rows) == 2
    assert calls == {"browser": 1, "missing": [1]}


def test_browser_cookie_seed_clears_previous_account_first():
    client = TikTokPublicVideoClient()
    calls = []

    class Context:
        async def clear_cookies(self):
            calls.append("clear")

        async def add_cookies(self, cookies):
            calls.append(cookies)

    class Page:
        context = Context()

    asyncio.run(client._apply_cookie_header(
        Page(), "sessionid=abc=123; msToken=xyz"
    ))

    assert calls[0] == "clear"
    assert calls[1] == [
        {
            "name": "sessionid",
            "value": "abc=123",
            "url": "https://www.tiktok.com/",
        },
        {
            "name": "msToken",
            "value": "xyz",
            "url": "https://www.tiktok.com/",
        },
    ]


def test_video_http_retries_invalid_200_with_account_cookie(monkeypatch):
    video_id = "7176222902134508827"
    valid_html = '''<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__" type="application/json">
    {"__DEFAULT_SCOPE__":{"webapp.video-detail":{"itemInfo":{"itemStruct":{
      "id":"7176222902134508827","createTime":1670844603,"indexEnabled":true,
      "stats":{"playCount":8},"video":{"width":720,"height":1280}
    }}}}}</script>'''
    requests = []

    class Response:
        def __init__(self, text):
            self.text = text
            self.status_code = 200
            self.headers = {}

    class Client:
        def __init__(self, **_kwargs):
            self.responses = [Response("<html>WAF shell</html>"), Response(valid_html)]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, headers=None):
            requests.append((url, headers))
            return self.responses.pop(0)

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(video_client_module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(video_client_module.asyncio, "sleep", no_sleep)
    client = TikTokPublicVideoClient()

    rows = asyncio.run(client._fetch_video_details_http(
        [f"https://www.tiktok.com/@target/video/{video_id}"],
        profile_url="https://www.tiktok.com/@target",
        proxy_url=None,
        cookie_header="sessionid=abc",
    ))

    assert rows[0]["video_id"] == video_id
    assert rows[0]["detail_source"] == "HTTP"
    assert requests[0][1] == {"Cookie": ""}
    assert requests[1][1] == {"Cookie": "sessionid=abc"}
    assert client.get_stats()["video_http_requests"] == 2
    assert client.get_stats()["video_http_retries"] == 1


# ---------------------------------------------------------------------------
# TikTok's "Please wait..." challenge, and what the sync reports around it.
# ---------------------------------------------------------------------------

class _ChallengePage:
    """A tab that shows the challenge, navigates itself, then shows the page.

    Evaluate raises while the challenge replaces the document, which is what
    made `locator.wait_for` give up at once in the real browser.
    """

    def __init__(self, ready_after=3, raise_on=(1,)):
        self.ready_after = ready_after
        self.raise_on = set(raise_on)
        self.polls = 0
        self.gotos = 0

    async def evaluate(self, _script):
        self.polls += 1
        if self.polls in self.raise_on:
            raise RuntimeError("Execution context was destroyed")
        return self.polls >= self.ready_after

    async def goto(self, *_args, **_kwargs):
        self.gotos += 1

    async def content(self):
        ready = self.polls >= self.ready_after
        return "<html>" + ("x" * 500) + (
            '<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__">{}</script>' if ready else ""
        ) + "</html>"


def _no_sleep(monkeypatch):
    async def instant(_seconds):
        return None
    monkeypatch.setattr(video_client_module.asyncio, "sleep", instant)


def test_the_challenge_is_waited_out_in_place_not_reopened(monkeypatch):
    """Re-opening restarts the challenge (recovered 1 of 3 grids); waiting in
    place recovered 4 of 4. A navigation mid-challenge is a reason to look
    again, not a verdict."""
    _no_sleep(monkeypatch)
    page = _ChallengePage(ready_after=4, raise_on=(1, 2))

    html, ready = asyncio.run(TikTokPublicVideoClient._open_until_ready(
        page, "https://www.tiktok.com/@user", "() => true"))

    assert ready is True
    assert page.gotos == 1
    assert "__UNIVERSAL_DATA_FOR_REHYDRATION__" in html


def test_a_page_that_never_settles_is_opened_again_then_reported(monkeypatch):
    _no_sleep(monkeypatch)
    page = _ChallengePage(ready_after=10**9, raise_on=())
    clock = [0.0]

    class Loop:
        def time(self):
            clock[0] += 1.0
            return clock[0]

    monkeypatch.setattr(video_client_module.asyncio, "get_running_loop", lambda: Loop())

    html, ready = asyncio.run(TikTokPublicVideoClient._open_until_ready(
        page, "https://www.tiktok.com/@user", "() => true",
        navigations=2, settle_seconds=5))

    assert ready is False
    assert page.gotos == 2
    assert html  # the caller still gets the last HTML to judge for itself


def test_a_counted_but_unshown_video_is_named_as_such_not_as_a_crawl_gap():
    """All videos the profile showed were read; TikTok still counts one more."""
    status, error = _merge_video_completeness("SUCCESS", "", 4, 3, False, rows=3)

    assert status == "PARTIAL"
    assert "không hiện công khai" in error
    assert "chưa đủ" not in error


def test_a_real_crawl_gap_keeps_the_crawl_gap_message():
    # 4 counted, 3 found, only 2 of those read: that IS the crawler.
    status, error = _merge_video_completeness("SUCCESS", "", 4, 2, False, rows=3)

    assert status == "PARTIAL"
    assert "chưa đủ (2/4)" in error
