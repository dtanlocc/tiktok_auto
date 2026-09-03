from app.core.tiktok_urls import ensure_tiktok_english_url


def test_tiktok_url_without_query_gets_english_language():
    assert ensure_tiktok_english_url("https://www.tiktok.com/foryou") == (
        "https://www.tiktok.com/foryou?lang=en"
    )


def test_tiktok_url_preserves_query_fragment_and_replaces_language():
    assert ensure_tiktok_english_url(
        "https://developers.tiktok.com/apps/?tab=active&lang=vi#settings"
    ) == "https://developers.tiktok.com/apps/?tab=active&lang=en#settings"


def test_tiktok_url_deduplicates_language_parameter():
    assert ensure_tiktok_english_url(
        "https://www.tiktok.com/@name?lang=vi&lang=th"
    ) == "https://www.tiktok.com/@name?lang=en"


def test_non_web_tiktok_hosts_are_not_modified():
    assert ensure_tiktok_english_url("https://open.tiktokapis.com/v2/user/info/") == (
        "https://open.tiktokapis.com/v2/user/info/"
    )
    assert ensure_tiktok_english_url("https://p16-sign.tiktokcdn-us.com/image.jpeg") == (
        "https://p16-sign.tiktokcdn-us.com/image.jpeg"
    )
