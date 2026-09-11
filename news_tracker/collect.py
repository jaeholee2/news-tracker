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
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

NAVER_NEWS_SEARCH_URL = "https://naverapihub.apigw.ntruss.com/search/v1/news"
GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search?q={query}&hl=ko&gl=KR&ceid=KR:ko"

_TAG_RE = re.compile(r"<[^>]+>")

REQUEST_TIMEOUT_SECONDS = 10
# Fetching each article's own page (for its thumbnail, see
# enrich_with_images below) is a per-article best-effort extra -- kept
# short so one slow/unresponsive publisher can't stall the whole run.
IMAGE_FETCH_TIMEOUT_SECONDS = 4
IMAGE_FETCH_MAX_WORKERS = 15
# A generic browser UA: some publishers 403 obviously-bot requests (no UA,
# or a bare "python-requests/..." UA) even for a page they'd otherwise
# serve to anyone.
IMAGE_FETCH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


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


class _OgImageParser(HTMLParser):
    """Pulls the first og:image (falling back to twitter:image) meta tag's
    `content` out of an HTML document.

    Uses the stdlib parser rather than a regex so it isn't tripped up by
    attribute order/quoting, which varies a lot across the many different
    Korean news publisher CMSes this will see.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.og_image = ""
        self.twitter_image = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta":
            return
        attr_dict = {k: (v or "") for k, v in attrs}
        prop = (attr_dict.get("property") or attr_dict.get("name") or "").strip().lower()
        content = attr_dict.get("content", "").strip()
        if not content:
            return
        if prop == "og:image" and not self.og_image:
            self.og_image = content
        elif prop == "twitter:image" and not self.twitter_image:
            self.twitter_image = content


def _extract_og_image(html_text: str) -> str:
    """Best-effort: returns "" if no usable tag is found or the document
    can't be parsed at all (HTMLParser is lenient, but never guaranteed)."""
    # og:image/twitter:image can only validly appear in <head>, which for
    # a real article page is normally well within the first ~150KB even
    # when the page is heavy -- no need to parse (or even keep in memory
    # for parsing) the full article body just to find one meta tag.
    head_end = html_text.lower().find("</head>")
    snippet = html_text if head_end == -1 else html_text[: head_end + len("</head>")]

    parser = _OgImageParser()
    try:
        parser.feed(snippet)
    except Exception:  # noqa: BLE001 - never let a malformed page break collection
        return ""
    return parser.og_image or parser.twitter_image


def fetch_article_image(url: str, timeout: int = IMAGE_FETCH_TIMEOUT_SECONDS) -> str:
    """Fetch `url` and return its og:image/twitter:image URL, or "" on any
    failure (network error, non-200, no such tag, etc). Never raises."""
    if not url:
        return ""
    try:
        response = requests.get(
            url,
            timeout=timeout,
            headers={"User-Agent": IMAGE_FETCH_USER_AGENT},
        )
        response.raise_for_status()
    except requests.RequestException:
        return ""
    try:
        return _extract_og_image(response.text)
    except Exception:  # noqa: BLE001 - one bad page should never fail the run
        return ""


def enrich_with_images(
    articles: list[dict],
    timeout: int = IMAGE_FETCH_TIMEOUT_SECONDS,
    max_workers: int = IMAGE_FETCH_MAX_WORKERS,
) -> list[dict]:
    """Attach an "image" key (og:image URL, or "" if none could be found)
    to each article dict, mutating them in place. Meant to be called once,
    after dedup/date-filtering, so this only pays for one extra HTTP
    request per *story actually shown* rather than per raw search hit.

    Fetches run concurrently (each article's page fetch is independent and
    the bottleneck is publisher response time, not CPU), bounded by
    max_workers so a slow run doesn't open unbounded connections. A single
    article's fetch failing never affects any other article's result.
    """
    if not articles:
        return articles

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        images = list(
            pool.map(lambda article: fetch_article_image(article.get("link", ""), timeout), articles)
        )

    for article, image in zip(articles, images):
        article["image"] = image
    return articles


def _title_has_all_terms(title: str, terms: list[str]) -> bool:
    """Case-insensitive check that every term appears somewhere in title."""
    lowered = title.lower()
    return all(term.lower() in lowered for term in terms)


def collect(keywords: list[str], naver_client_id: str, naver_client_secret: str) -> list[dict]:
    """Collect articles for every keyword from both sources and merge them
    into one flat list. Individual source failures are logged (see the
    fetchers above) and simply contribute no articles.

    A keyword made of several space-separated terms (e.g. "방사청 5G") is
    meant as "articles mentioning all of these", not just one of them. The
    two upstream APIs don't reliably guarantee that: Google News RSS does
    document space-separated terms as an implicit AND, but Naver's News
    Search API does not document its matching behavior at all, so its
    results for a multi-term query may include articles that only match
    one term. To make the "all terms" guarantee hold regardless of what
    either API actually did internally, results for any multi-term keyword
    are filtered here to keep only articles whose title contains every
    term (case-insensitive substring match). Single-term keywords are
    unaffected -- there's nothing to filter.
    """
    articles: list[dict] = []
    for keyword in keywords:
        keyword_articles = fetch_naver(keyword, naver_client_id, naver_client_secret)
        keyword_articles += fetch_google_news_rss(keyword)

        terms = keyword.split()
        if len(terms) > 1:
            keyword_articles = [
                a for a in keyword_articles if _title_has_all_terms(a["title"], terms)
            ]

        articles.extend(keyword_articles)
    return articles
