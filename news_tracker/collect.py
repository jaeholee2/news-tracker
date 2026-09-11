"""Fetchers for the two news sources: Naver News Search API and Google News RSS.

Both fetchers return a flat list of plain dicts:
    {"title": str, "link": str, "source": str, "published_at": str, "keyword": str}

`published_at` is normalized to an ISO 8601 string (or "" if it could not be
parsed) so downstream code never has to deal with source-specific date
formats.

Per the design: a failure fetching from either source is logged and treated
as "no results from that source", never raised -- one source outage should
not block the whole day's collection.

Only the standard library plus `requests` is used here (no `feedparser`)
so the whole tool installs from three common packages (requests, PyYAML,
Jinja2) -- see requirements.txt.
"""

from __future__ import annotations

import html
import logging
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

NAVER_NEWS_SEARCH_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"

_TAG_RE = re.compile(r"<[^>]+>")

REQUEST_TIMEOUT_SECONDS = 10


def _strip_html(text: str) -> str:
    """Naver titles/descriptions contain <b> tags around the matched query
    and HTML entities (&quot; etc). Strip both so we get plain text."""
    return html.unescape(_TAG_RE.sub("", text or "")).strip()


def _parse_rfc822(value: str) -> str:
    """Best-effort parse of an RFC 822 date string (used by both Naver and
    Google RSS) into ISO 8601. Returns "" if it can't be parsed."""
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).isoformat()
    except (TypeError, ValueError):
        return ""


def fetch_naver(
    keyword: str,
    client_id: str,
    client_secret: str,
    display: int = 100,
) -> list[dict]:
    """Query the Naver News Search API for a single keyword.

    On any request/parse failure, logs the error and returns an empty list
    rather than raising, per the design's error-handling policy.
    """
    headers = {
        "X-NCP-APIGW-API-KEY-ID": client_id,
        "X-NCP-APIGW-API-KEY": client_secret,
    }
    params = {"query": keyword, "display": display, "sort": "date"}

    try:
        response = requests.get(
            NAVER_NEWS_SEARCH_URL,
            headers=headers,
            params=params,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.error("Naver News API failed for keyword %r: %s", keyword, exc)
        return []

    articles = []
    for item in payload.get("items", []):
        articles.append(
            {
                "title": _strip_html(item.get("title", "")),
                # `link` is often a redirect through the publisher's own
                # syndication; `originallink` (when present) points straight
                # at the source article, which is what a reader wants.
                "link": item.get("originallink") or item.get("link", ""),
                "source": "Naver",
                "published_at": _parse_rfc822(item.get("pubDate", "")),
                "keyword": keyword,
            }
        )
    return articles


def _split_title_and_publisher(raw_title: str, known_publisher: str | None) -> tuple[str, str]:
    """Google News RSS entry titles are formatted "<headline> - <publisher>".

    If we already know the publisher (from the entry's <source> tag), just
    strip that exact suffix off the title. Otherwise fall back to splitting
    on the last " - ", and finally to "Google News" if neither is available.
    """
    title = (raw_title or "").strip()

    if known_publisher:
        suffix = f" - {known_publisher}"
        if title.endswith(suffix):
            return title[: -len(suffix)].strip(), known_publisher
        return title, known_publisher

    if " - " in title:
        headline, _, publisher = title.rpartition(" - ")
        if headline and publisher:
            return headline.strip(), publisher.strip()

    return title, "Google News"


def fetch_google_news_rss(keyword: str) -> list[dict]:
    """Fetch and parse the Google News RSS feed for a single keyword.

    On any fetch/parse failure, logs the error and returns an empty list
    rather than raising, per the design's error-handling policy.
    """
    url = GOOGLE_NEWS_RSS_URL.format(query=quote(keyword))

    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
    except requests.RequestException as exc:
        logger.error("Google News RSS fetch failed for keyword %r: %s", keyword, exc)
        return []

    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as exc:
        logger.error("Google News RSS parse failed for keyword %r: %s", keyword, exc)
        return []

    articles = []
    for item in root.findall("./channel/item"):
        raw_title = (item.findtext("title") or "").strip()
        source_el = item.find("source")
        known_publisher = source_el.text.strip() if source_el is not None and source_el.text else None
        headline, publisher = _split_title_and_publisher(raw_title, known_publisher)

        articles.append(
            {
                "title": headline,
                "link": (item.findtext("link") or "").strip(),
                "source": publisher,
                "published_at": _parse_rfc822(item.findtext("pubDate") or ""),
                "keyword": keyword,
            }
        )
    return articles


def collect(keywords: list[str], naver_client_id: str, naver_client_secret: str) -> list[dict]:
    """Collect articles for every keyword from both sources and merge them
    into one flat list. Individual source failures are logged (see the
    fetchers above) and simply contribute no articles."""
    articles: list[dict] = []
    for keyword in keywords:
        articles.extend(fetch_naver(keyword, naver_client_id, naver_client_secret))
        articles.extend(fetch_google_news_rss(keyword))
    return articles
