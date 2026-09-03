"""Canonical TikTok web URLs used by browser and public-web requests."""

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def ensure_tiktok_english_url(url: str) -> str:
    """Force ``lang=en`` on TikTok web URLs without corrupting query strings.

    TikTok CDN hosts and the official ``open.tiktokapis.com`` API are not web
    pages under ``tiktok.com`` and are intentionally left unchanged.
    """
    value = str(url or "")
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value

    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if hostname != "tiktok.com" and not hostname.endswith(".tiktok.com"):
        return value

    query_items = []
    language_added = False
    for key, item_value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.casefold() == "lang":
            if not language_added:
                query_items.append(("lang", "en"))
                language_added = True
            continue
        query_items.append((key, item_value))
    if not language_added:
        query_items.append(("lang", "en"))

    return urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urlencode(query_items, doseq=True),
            parsed.fragment,
        )
    )
