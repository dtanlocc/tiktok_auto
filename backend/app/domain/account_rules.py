"""Business rules that must be enforced independently from the UI."""

import re
import unicodedata
from typing import Any


def _canonical_label(value: Any) -> str:
    # Unicode does not decompose Vietnamese Đ/đ under NFKD, so normalize it
    # explicitly before stripping the remaining combining accents.
    text = str(value or "").replace("Đ", "D").replace("đ", "d")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"[^A-Z0-9]+", " ", text.upper()).strip()


def is_sold_batch(batch_tag: Any) -> bool:
    """Return True only for the explicit archival group named DA BAN / SOLD."""
    return _canonical_label(batch_tag) in {"DA BAN", "SOLD"}


def is_sold_account(account: Any) -> bool:
    return bool(account and is_sold_batch(getattr(account, "batch_tag", "")))
