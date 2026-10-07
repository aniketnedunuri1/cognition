"""verify_quote: a quote must appear verbatim in the cached text of the source it cites.

Normalization only removes formatting noise (case, whitespace, curly quotes,
markdown). No fuzzy matching: a paraphrase is not a quote.
"""

from __future__ import annotations

import html
import re

from .cache import get_doc
from .exa import fetch_page

MIN_WORDS_PER_PART = 3

_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"',
                            "–": "-", "—": "-", " ": " "})


def normalize(s: str) -> str:
    s = html.unescape(s).translate(_TRANSLATE)
    s = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", s)  # [text](url) -> text
    s = re.sub(r"[*_#>`|\"]", " ", s)  # formatting + double quotes (punctuation, not content)
    return re.sub(r"\s+", " ", s).strip().lower()


def _contains_in_order(haystack: str, parts: list[str]) -> bool:
    pos = 0
    for part in parts:
        i = haystack.find(part, pos)
        if i < 0:
            return False
        pos = i + len(part)
    return True


def verify_quote(source_id: str, quote: str) -> dict:
    """Check `quote` against the cached document `source_id`.

    "..." / "…" may join parts that appear in order. Checks full page text;
    fetches it if only search highlights are cached. Falls back to highlights
    (verbatim excerpts of the same page) only when the page can't be fetched.
    """
    result = {"source_id": source_id, "verified": False, "matched_in": None}
    doc = get_doc(source_id)
    if not doc:
        return {**result, "reason": "unknown source_id"}
    if doc["kind"] == "apollo_org":
        return {**result, "reason": "Apollo records are structured data: cite the source_id, don't quote it"}

    parts = [normalize(p) for p in re.split(r"\.\.\.|…", quote)]
    parts = [p for p in parts if p]
    if not parts or any(len(p.split()) < MIN_WORDS_PER_PART for p in parts):
        return {**result, "reason": f"each quoted part needs at least {MIN_WORDS_PER_PART} words"}

    if not doc.get("text") and doc.get("url") and doc["kind"] in ("web", "job"):
        fetch_page(source_id)  # caches full text into the doc; failure leaves it unset
        doc = get_doc(source_id)

    title = normalize(doc.get("title") or "")
    if doc.get("text"):
        if _contains_in_order(title + " " + normalize(doc["text"]), parts):
            return {**result, "verified": True, "matched_in": "text"}
        return {**result, "reason": "quote not found in the page text"}

    for h in doc.get("highlights") or []:
        if _contains_in_order(title + " " + normalize(h), parts):
            return {**result, "verified": True, "matched_in": "highlights"}
    if doc["kind"] == "job" and _contains_in_order(title, parts):
        return {**result, "verified": True, "matched_in": "job title"}
    return {**result, "reason": "quote not found (page text unavailable; checked cached highlights)"}
