"""Collapse the same story appearing under multiple sources/keywords into a
single entry.

Naver and Google News frequently carry the exact same wire story with
identical (or near-identical) headlines. We normalize each title (case,
whitespace, punctuation) to a dedup key so those collapse into one entry,
while remembering every source and keyword the story matched under.

Input: a flat list of {title, link, source, published_at, keyword} dicts,
as produced by collect.collect().

Output: a list of deduped entries:
    {title, link, published_at, sources: [str, ...], keywords: [str, ...]}
The first-seen title/link/published_at win; `sources` and `keywords`
accumulate every distinct value seen for that story, in first-seen order.
"""

from __future__ import annotations

import hashlib
import re

_PUNCTUATION_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def _make_id(link: str, title: str) -> str:
    """A short, stable id for an article -- used as its localStorage
    read/bookmark key and as its cluster id, so it must stay the same
    across runs for the same story. Keyed off the link (falling back to
    the normalized title for the rare article with no link at all)."""
    basis = link or normalize_title(title)
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, and collapse whitespace so that titles
    differing only in casing/punctuation/spacing dedup together."""
    text = (title or "").lower()
    text = _PUNCTUATION_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def dedupe(articles: list[dict]) -> list[dict]:
    """Dedupe a flat article list by normalized title, preserving the
    first-seen order of stories."""
    entries_by_key: dict[str, dict] = {}
    order: list[str] = []

    for article in articles:
        normalized = normalize_title(article.get("title", ""))
        # An article with an unusably empty title (after normalization)
        # can't be safely merged with anything else by title, so key it by
        # link instead to avoid accidentally collapsing unrelated articles.
        key = normalized or f"__link__:{article.get('link', '')}"

        if key in entries_by_key:
            entry = entries_by_key[key]
            source = article.get("source", "")
            if source and source not in entry["sources"]:
                entry["sources"].append(source)
            keyword = article.get("keyword", "")
            if keyword and keyword not in entry["keywords"]:
                entry["keywords"].append(keyword)
        else:
            entry = {
                "id": _make_id(article.get("link", ""), article.get("title", "")),
                "title": article.get("title", ""),
                "link": article.get("link", ""),
                "published_at": article.get("published_at", ""),
                "sources": [article["source"]] if article.get("source") else [],
                "keywords": [article["keyword"]] if article.get("keyword") else [],
            }
            entries_by_key[key] = entry
            order.append(key)

    return [entries_by_key[key] for key in order]
