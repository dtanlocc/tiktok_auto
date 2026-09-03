"""Build conservative hashtag search queries from a post title.

The result is not published blindly. The browser adapter uses each query to
open TikTok Studio's own hashtag suggestions and removes it again when TikTok
does not return a matching suggestion.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable, Sequence


_HASHTAG_RE = re.compile(r"#[^\s]+", re.UNICODE)
_WORD_RE = re.compile(r"[a-z0-9]+")
_SUGGESTION_TOKEN_RE = re.compile(r"#?([^\s#]+)", re.UNICODE)
_USAGE_RE = re.compile(
    r"(?<![\w.])(\d+(?:[.,]\d+)?)\s*([kmb]|ngh[iì]n|tri[eệ]u|t[yỷ])?",
    re.IGNORECASE | re.UNICODE,
)

# Function words and filename noise are poor hashtag queries. Keep this list
# intentionally small: TikTok's live suggestion menu is still the final judge.
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "has", "have", "he", "her", "his", "how", "i", "in", "is", "it", "its",
    "me", "my", "of", "on", "or", "our", "she", "that", "the", "their", "them",
    "they", "this", "to", "was", "we", "were", "what", "when", "where", "who",
    "why", "will", "with", "you", "your",
    "anh", "ban", "bi", "bo", "cac", "cai", "cho", "co", "cua", "da", "dang",
    "day", "de", "den", "do", "duoc", "gi", "khi", "khong", "la", "lai", "mot",
    "nay", "nhung", "o", "qua", "se", "tai", "the", "thi", "trong", "tu", "va",
    "video", "clip", "short", "shorts", "final", "copy", "edit", "edited", "new",
}


def _ascii_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return "".join(_WORD_RE.findall(ascii_text))


def _usage_count(value: str) -> int:
    """Parse the optional usage metric shown after a Studio hashtag.

    TikTok localises both decimal separators and suffixes. Missing metrics are
    deliberately represented as zero; relevance still wins over popularity.
    """
    match = _USAGE_RE.search(value or "")
    if not match:
        return 0
    raw_number = match.group(1).replace(",", ".")
    try:
        number = float(raw_number)
    except ValueError:
        return 0
    suffix = (match.group(2) or "").casefold()
    multiplier = 1
    if suffix in {"k", "nghin", "nghìn"}:
        multiplier = 1_000
    elif suffix in {"m", "trieu", "triệu"}:
        multiplier = 1_000_000
    elif suffix in {"b", "ty", "tỷ"}:
        multiplier = 1_000_000_000
    return max(0, int(number * multiplier))


@dataclass(frozen=True)
class HashtagSuggestionChoice:
    token: str
    source_index: int
    relevance: int
    usage_count: int


def choose_stable_hashtag_suggestion(
    query: str,
    suggestion_texts: Sequence[str],
    *,
    excluded_tokens: Iterable[str] = (),
) -> HashtagSuggestionChoice | None:
    """Choose one relevant Studio suggestion with deterministic ordering.

    Exact matches always outrank broader variants. Within the same relevance
    tier, a visible usage metric is preferred, then the larger metric, then a
    lexical tie-break. Therefore DOM ordering cannot randomly change the tag.
    """
    query_slug = _ascii_slug((query or "").lstrip("#"))
    if not query_slug:
        return None
    excluded = {
        _ascii_slug(str(token).lstrip("#"))
        for token in excluded_tokens
        if str(token).strip()
    }
    choices: list[tuple[tuple[object, ...], HashtagSuggestionChoice]] = []
    for index, raw_text in enumerate(suggestion_texts):
        text = " ".join((raw_text or "").split())
        token_match = _SUGGESTION_TOKEN_RE.match(text)
        if not token_match:
            continue
        raw_token = token_match.group(1).rstrip(".,;:!?)\]}\"")
        slug = _ascii_slug(raw_token)
        if not slug or slug in excluded:
            continue
        if slug == query_slug:
            relevance = 3
        elif slug.startswith(query_slug) or query_slug.startswith(slug):
            relevance = 2
        elif len(query_slug) >= 4 and query_slug in slug:
            relevance = 1
        else:
            continue

        # Do not accidentally parse digits in a tag such as #45acp as usage.
        trailing_text = text[token_match.end():]
        usage = _usage_count(trailing_text)
        token = f"#{raw_token}"
        choice = HashtagSuggestionChoice(
            token=token,
            source_index=index,
            relevance=relevance,
            usage_count=usage,
        )
        sort_key = (
            -relevance,
            -(1 if usage > 0 else 0),
            -usage,
            abs(len(slug) - len(query_slug)),
            slug,
            index,
        )
        choices.append((sort_key, choice))
    if not choices:
        return None
    choices.sort(key=lambda item: item[0])
    return choices[0][1]


def hashtag_query_candidates(title: str, limit: int = 6) -> list[str]:
    """Return unique title keywords suitable for TikTok hashtag lookup.

    Existing hashtags disable auto lookup completely because an explicit user
    caption is authoritative. Generic reach tags are never invented here.
    """
    if not title or _HASHTAG_RE.search(title):
        return []

    # Treat common filename separators as spaces before tokenisation.
    plain = re.sub(r"[_\-.]+", " ", title)
    tokens = re.findall(r"[^\W_]+", plain, flags=re.UNICODE)
    words: list[str] = []
    word_seen: set[str] = set()
    for token in tokens:
        slug = _ascii_slug(token)
        if (
            not 3 <= len(slug) <= 24
            or slug.isdigit()
            or slug in _STOP_WORDS
            or slug in word_seen
        ):
            continue
        word_seen.add(slug)
        words.append(slug)

    # Titles commonly put the subject at the beginning and the specific topic
    # at the end. Try those two phrases first, then their component keywords;
    # TikTok Studio still has to return a matching live suggestion before any
    # query becomes a published hashtag.
    ranked: list[str] = []
    if len(words) >= 2:
        ranked.extend((words[0] + words[1], words[-2] + words[-1]))
    if words:
        ranked.extend((words[0],))
    if len(words) >= 2:
        ranked.extend((words[1], words[-1], words[-2]))
    ranked.extend(words[2:-2] if len(words) > 4 else words[2:])

    result: list[str] = []
    seen: set[str] = set()
    for slug in ranked:
        if not 3 <= len(slug) <= 24 or slug in seen:
            continue
        seen.add(slug)
        result.append(slug)
        if len(result) >= max(0, int(limit)):
            break
    return result
