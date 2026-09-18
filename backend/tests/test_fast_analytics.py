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


# ---------------------------------------------------------------------------
# One profile load: counts from the grid, pages only for what the grid lacks.
# ---------------------------------------------------------------------------

from types import SimpleNamespace as _NS

from app.use_cases.analytics.tiktok_fast_analytics_sync import _page_fresh_video_ids
from app.use_cases.analytics.tiktok_public_video_client import (
    PAGE_ONLY_ROW_FIELDS,
    grid_video_row_from_item,
)


def _item(video_id, plays=10, **extra):
    item = {
        "id": video_id,
        "desc": f"video {video_id}",
        "createTime": int(video_id[-3:]),
        "stats": {"playCount": plays, "diggCount": 2, "commentCount": 1,
                  "shareCount": 0, "collectCount": 0},
        "video": {"duration": 30, "bitrateInfo": []},
    }
    item.update(extra)
    return item


def test_a_grid_row_carries_counts_but_never_the_page_only_fields():
    """Writing "" / None / "YES" for fields the grid cannot know would erase
    the region and shadow-ban last read from the video's own page."""
    row = grid_video_row_from_item(_item("7000000000000000123", plays=99,
                                         locationCreated="ID", indexEnabled=True))

    assert row["view_count"] == 99
    assert row["detail_available"] is True
    for name in PAGE_ONLY_ROW_FIELDS:
        assert name not in row
    assert "detail_source" not in row


def test_page_fields_are_fresh_only_when_known_and_recent():
    now = datetime.now()
    recent = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    old = (now - timedelta(hours=48)).isoformat(timespec="seconds")
    rows = [
        _NS(video_id="1", region="ID", detail_synced_at=recent, synced_at=recent),
        _NS(video_id="2", region="", detail_synced_at=recent, synced_at=recent),
        _NS(video_id="3", region="ID", detail_synced_at=old, synced_at=recent),
        # Written before detail_synced_at existed: dated by synced_at.
        _NS(video_id="4", region="VN", detail_synced_at="", synced_at=recent),
    ]
    assert _page_fresh_video_ids(rows, 24 * 3600) == {"1", "4"}


def _grid_client(monkeypatch, ids, items, page_rows):
    client = TikTokPublicVideoClient()
    calls = {"http_links": [], "profile_loads": 0}

    async def http_details(links, **_kw):
        calls["http_links"] = list(links)
        return [page_rows.get(link.split("/video/")[1].split("?")[0]) for link in links]

    async def must_not_launch(*_a, **_kw):
        calls["profile_loads"] += 1
        raise AssertionError("a cached grid must not load the profile again")

    client._fetch_video_details_http = http_details
    client._ensure_page = must_not_launch
    # Read "in the future": always within the cache window.
    client._grid_cache[client._grid_key("user")] = (float("inf"), ids, items)
    return client, calls


def test_grid_sync_opens_no_page_for_videos_already_known(monkeypatch):
    ids = ["7000000000000000001", "7000000000000000002"]
    items = {vid: _item(vid) for vid in ids}
    client, calls = _grid_client(monkeypatch, ids, items, {})

    rows, complete = asyncio.run(client.fetch_videos(
        "user", "", max_videos=60, expected_video_count=2,
        known_video_urls=[], page_fresh_video_ids=set(ids)))

    assert complete is True
    assert len(rows) == 2
    assert calls["http_links"] == []


def test_grid_sync_reads_the_page_only_for_a_new_video(monkeypatch):
    ids = ["7000000000000000001", "7000000000000000002"]
    items = {vid: _item(vid) for vid in ids}
    page_row = {"video_id": ids[1], "view_count": 5, "region": "ID",
                "shadow_ban": "NO", "index_enabled": True, "detail_available": True,
                "create_time": 2}
    client, calls = _grid_client(monkeypatch, ids, items, {ids[1]: page_row})

    rows, complete = asyncio.run(client.fetch_videos(
        "user", "", max_videos=60, expected_video_count=2,
        known_video_urls=[], page_fresh_video_ids={ids[0]}))

    assert [link.split("/video/")[1].split("?")[0] for link in calls["http_links"]] == [ids[1]]
    by_id = {row["video_id"]: row for row in rows}
    assert by_id[ids[1]]["region"] == "ID"          # the page row replaced the grid row
    assert "region" not in by_id[ids[0]]            # the known video kept its stored region
    assert complete is True


def test_an_unreadable_grid_falls_back_to_the_page_crawl(monkeypatch):
    client = TikTokPublicVideoClient()

    async def grid_unreadable(*_a, **_kw):
        return None

    async def page_crawl_http(links, **_kw):
        return [{"video_id": "7000000000000000001", "create_time": 1,
                 "detail_available": True}]

    client._fetch_videos_via_grid = grid_unreadable
    client._fetch_video_details_http = page_crawl_http
    rows, complete = asyncio.run(client.fetch_videos(
        "user", "", max_videos=60, expected_video_count=1,
        known_video_urls=["https://www.tiktok.com/@user/video/7000000000000000001"],
        page_fresh_video_ids=set()))

    assert complete is True
    assert rows[0]["video_id"] == "7000000000000000001"


def test_a_video_refused_by_moderation_is_named_not_counted_as_a_gap():
    """All six 'chưa đủ (n-1/n)' accounts in the full reg web batch were a
    video TikTok answered with statusCode 10231 - there was nothing to read."""
    reason = "Video không vượt qua kiểm duyệt (statusCode 10231)"
    status, error = _merge_video_completeness(
        "SUCCESS", "", 4, 3, False, rows=4, restricted=[reason])

    assert status == "PARTIAL"
    assert "chưa đủ" not in error
    assert "10231" in error
    assert "Đã đọc đủ 3 video" in error


def test_restricted_and_hidden_videos_are_both_reported():
    reason = "Video không vượt qua kiểm duyệt (statusCode 10231)"
    status, error = _merge_video_completeness(
        "SUCCESS", "", 6, 3, False, rows=4, restricted=[reason])

    assert status == "PARTIAL"
    assert "1 video bị TikTok hạn chế" in error
    assert "thêm 2 video không hiện công khai" in error


# ---------------------------------------------------------------------------
# Fewer page reads, and accounts read from item_list instead of a page load.
# ---------------------------------------------------------------------------

from app.use_cases.analytics.tiktok_fast_analytics_sync import TikTokFastAnalyticsSyncService
from app.use_cases.analytics.tiktok_public_video_client import api_profile_result_from_items


def _read_row(video_id, *, read_hours_ago, age_when_read_days, region="ID"):
    read_at = datetime.now() - timedelta(hours=read_hours_ago)
    return _NS(
        video_id=video_id,
        region=region,
        detail_synced_at=read_at.isoformat(timespec="seconds"),
        synced_at=read_at.isoformat(timespec="seconds"),
        create_time=int(read_at.timestamp() - age_when_read_days * 86400),
    )


def test_a_settled_video_keeps_its_page_fields_for_a_week_a_young_one_for_a_day():
    rows = [
        _read_row("young", read_hours_ago=30, age_when_read_days=1),
        _read_row("settled", read_hours_ago=72, age_when_read_days=5),
        _read_row("settled_long_ago", read_hours_ago=8 * 24, age_when_read_days=5),
        _read_row("no_region", read_hours_ago=1, age_when_read_days=5, region=""),
        _NS(video_id="no_create_time", region="ID", create_time=None,
            detail_synced_at=(datetime.now() - timedelta(hours=30)).isoformat(timespec="seconds"),
            synced_at=""),
    ]

    fresh = _page_fresh_video_ids(
        rows, 24 * 3600, settled_ttl_seconds=168 * 3600, settled_age_seconds=3 * 86400)

    assert fresh == {"settled"}


def test_a_guest_read_keeps_the_session_tiktok_gave_after_its_challenge():
    client = TikTokPublicVideoClient()
    calls = []

    class Context:
        async def clear_cookies(self):
            calls.append("clear")

        async def add_cookies(self, cookies):
            calls.append("add")

    class Page:
        context = Context()

    asyncio.run(client._apply_cookie_header(Page(), ""))
    assert calls == []                              # nothing seeded: nothing to clear

    asyncio.run(client._apply_cookie_header(Page(), "sessionid=abc"))
    asyncio.run(client._apply_cookie_header(Page(), ""))
    assert calls == ["clear", "add", "clear"]       # an account's session is still removed


def _api_item(video_id, username="owner"):
    item = _item(video_id)
    item["author"] = {"id": "42", "uniqueId": username, "secUid": "sec-owner",
                      "nickname": "Owner", "avatarLarger": "https://x/a.jpg"}
    item["authorStats"] = {"videoCount": 2, "followerCount": 5, "followingCount": 0,
                           "heartCount": 29}
    return item


def test_item_list_is_a_profile_only_when_every_item_is_this_users():
    result = api_profile_result_from_items(
        [_api_item("7000000000000000001"), _api_item("7000000000000000002")], "@Owner")

    assert result.profile_metrics == {"video_count": 2, "follower_count": 5,
                                      "following_count": 0, "likes_count": 29}
    assert result.profile_identity == {"user_id": "42", "sec_uid": "sec-owner",
                                       "username": "owner"}
    assert result.profile_data["display_name"] == "Owner"

    renamed = [_api_item("7000000000000000001", username="someone_else")]
    assert api_profile_result_from_items(renamed, "owner") is None
    assert api_profile_result_from_items([], "owner") is None
    missing_count = _api_item("7000000000000000001")
    del missing_count["authorStats"]["followerCount"]
    assert api_profile_result_from_items([missing_count], "owner") is None


def _api_client(pages):
    client = TikTokPublicVideoClient()
    sent = []

    async def api_tab(_username, _proxy):
        return object(), None

    async def api_get(_page, _path, params):
        sent.append(dict(params))
        return pages.pop(0)

    client._ensure_api_page = api_tab
    client._api_get = api_get
    return client, sent


def test_an_api_read_caches_every_page_of_videos_for_fetch_videos():
    first = [_api_item(f"70000000000000000{n:02d}") for n in range(1, 36)]
    second = [_api_item("7000000000000000099")]
    client, sent = _api_client([
        {"statusCode": 0, "itemList": first, "hasMore": True, "cursor": "1700"},
        {"statusCode": 0, "itemList": second, "hasMore": False, "cursor": "-1"},
    ])

    result = asyncio.run(client.fetch_profile_via_api("owner", "sec-owner", max_videos=60))

    assert result.profile_metrics["follower_count"] == 5
    assert [call["cursor"] for call in sent] == ["0", "1700"]
    assert all(call["secUid"] == "sec-owner" for call in sent)
    _read_at, ids, items = client._grid_cache[client._grid_key("owner")]
    assert len(ids) == 36 and ids[-1] == "7000000000000000099"
    assert set(items) == set(ids)
    assert client.get_stats()["api_account_reads"] == 1


def test_an_api_read_that_breaks_midway_leaves_the_account_to_the_page_load():
    first = [_api_item(f"70000000000000000{n:02d}") for n in range(1, 36)]
    client, _sent = _api_client([
        {"statusCode": 0, "itemList": first, "hasMore": True, "cursor": "1700"},
        None,
    ])

    assert asyncio.run(client.fetch_profile_via_api("owner", "sec-owner")) is None
    assert client._grid_key("owner") not in client._grid_cache


def test_the_api_template_never_replays_the_tokens_tiktok_signs_each_call_with():
    client = TikTokPublicVideoClient()
    handlers = []

    class Page:
        def on(self, event, handler):
            handlers.append((event, handler))

    client._watch_for_api_template(Page())
    event, capture = handlers[0]
    capture(_NS(url="https://www.tiktok.com/api/user/playlist/?aid=1988"))
    assert client._api_params is None
    capture(_NS(url="https://www.tiktok.com/api/post/item_list/?aid=1988&device_id=77"
                    "&secUid=abc&cursor=0&count=35&msToken=t&X-Bogus=b&X-Gnarly=g&X-Dynosaur=d"))

    assert event == "request"
    assert client._api_params == {"aid": "1988", "device_id": "77"}


def test_video_pages_http_missed_are_read_side_by_side_in_a_bounded_set_of_tabs(monkeypatch):
    client = TikTokPublicVideoClient()
    client._detail_tab_gate = asyncio.Semaphore(3)
    state = {"open": 0, "most": 0, "tabs": 0}

    class Tab:
        def is_closed(self):
            return False

    class Browser:
        async def new_page(self):
            state["tabs"] += 1
            return Tab()

    async def browser_ready(_proxy=None):
        client._browser = Browser()

    async def open_page(cls, _page, url, _ready_js, **_kw):
        state["open"] += 1
        state["most"] = max(state["most"], state["open"])
        await asyncio.sleep(0.01)
        state["open"] -= 1
        return url, True

    async def http_missed_all(links, **_kw):
        return [None] * len(links)

    monkeypatch.setattr(TikTokPublicVideoClient, "_open_until_ready", classmethod(open_page))
    monkeypatch.setattr(video_client_module, "extract_public_video_detail_html",
                        lambda html, video_id, share_url="": {
                            "video_id": video_id, "region": "ID", "detail_available": True,
                            "create_time": 1})
    client._ensure_page = browser_ready
    client._fetch_video_details_http = http_missed_all
    ids = [f"70000000000000000{n:02d}" for n in range(8)]
    client._grid_cache[client._grid_key("user")] = (float("inf"), ids, {vid: _item(vid) for vid in ids})

    rows, complete = asyncio.run(client.fetch_videos(
        "user", "", max_videos=60, expected_video_count=8,
        known_video_urls=[], page_fresh_video_ids=set()))

    assert complete is True
    assert all(row["region"] == "ID" for row in rows)
    assert state["most"] == 3          # side by side, never past the tab limit
    assert state["tabs"] == 3          # tabs are reused, not opened per video


def test_plain_http_profile_reads_pause_when_almost_none_of_the_last_ones_worked():
    service = TikTokFastAnalyticsSyncService()
    challenge = video_client_module.QuickCheckResult(None, "tiktok_challenge")
    success = video_client_module.QuickCheckResult(
        "SONG_TRANG", "tiktok_user_info", profile_metrics={"video_count": 0})

    service._note_http_profile_result(success)          # one rare hit...
    for _ in range(8):
        service._note_http_profile_result(challenge)
    assert not service._http_profile_paused()           # ...window not yet full

    service._note_http_profile_result(challenge)
    assert service._http_profile_paused()               # 1 hit in 10: paused anyway


def test_plain_http_profile_reads_keep_going_while_they_work():
    service = TikTokFastAnalyticsSyncService()
    challenge = video_client_module.QuickCheckResult(None, "tiktok_challenge")
    success = video_client_module.QuickCheckResult(
        "SONG_TRANG", "tiktok_user_info", profile_metrics={"video_count": 0})

    for _ in range(4):
        service._note_http_profile_result(success)
        service._note_http_profile_result(challenge)
        service._note_http_profile_result(challenge)
    assert not service._http_profile_paused()


def test_plain_http_video_reads_pause_after_a_run_of_misses(monkeypatch):
    requests = []

    class Response:
        text = "<html>Please wait...</html>"
        status_code = 200
        headers = {}

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, headers=None):
            requests.append(url)
            return Response()

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(video_client_module.httpx, "AsyncClient", Client)
    monkeypatch.setattr(video_client_module.asyncio, "sleep", no_sleep)
    client = TikTokPublicVideoClient()
    # One rare hit already in the window must not keep plain HTTP going.
    client._http_detail_outcomes.append(True)
    links = [f"https://www.tiktok.com/@u/video/70000000000000000{n:02d}" for n in range(30)]

    async def run_twice():
        first = await client._fetch_video_details_http(links, profile_url="p", proxy_url=None)
        sent = len(requests)
        second = await client._fetch_video_details_http(links[:5], profile_url="p", proxy_url=None)
        return first, sent, second

    first, sent_first, second = asyncio.run(run_twice())

    assert first == [None] * 30
    assert sent_first < 30 * 2          # reads still queued stopped once the pause began
    assert second == [None] * 5 and len(requests) == sent_first   # later reads send nothing
