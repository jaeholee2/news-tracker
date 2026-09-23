"""Render collected articles into the static Nocturne-design site.

Unlike the old per-day-archive-page design, this renders a single
output/index.html (a small client-side app) plus one JSON dataset,
output/data/articles.json, that the page fetches on load. History
browsing (the feed's infinite scroll, and the date popover) happens
entirely client-side against that one dataset -- there is no more a
separate output/archive/YYYY-MM-DD.html per day; a build simply re-writes
the same two files every run with whatever is currently in data/*.json
within the retention window.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import jinja2

from news_tracker import cluster
from news_tracker.dedupe import _make_id

DATE_FORMAT = "%Y-%m-%d"


def _build_env(templates_dir: Path) -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(templates_dir)),
        autoescape=jinja2.select_autoescape(["html"]),
    )


def discover_archive_dates(archive_dir: Path, today: date, retention_days: int) -> list[str]:
    """Return ISO date strings for every archive page that exists on disk
    within the retention window (today included), sorted most-recent-first.

    Kept for the data-file discovery it's still used for (see
    load_dataset below, which globs data/*.json the same way); no longer
    used to build a per-day HTML nav, since there's only one HTML page now.
    """
    if not archive_dir.exists():
        return []

    cutoff = today - timedelta(days=retention_days)
    found: list[date] = []
    for path in archive_dir.glob("*.html"):
        try:
            parsed = datetime.strptime(path.stem, DATE_FORMAT).date()
        except ValueError:
            continue
        if cutoff <= parsed <= today:
            found.append(parsed)

    found.sort(reverse=True)
    return [d.strftime(DATE_FORMAT) for d in found]


def group_by_keyword(articles: list[dict], keywords: list[str]) -> list[dict]:
    """Group deduped articles (each carrying a `keywords` list, see
    dedupe.dedupe) into per-keyword sections, in the order `keywords`
    lists them -- so the page always matches the configured search-keyword
    order, regardless of which keyword happened to collect more today.

    A story that matched more than one keyword search appears under each
    of them (it's still one dedup entry -- just listed twice). A keyword
    with zero matches today still gets an entry with an empty article
    list, so the top-of-page keyword list always shows every configured
    keyword, not just the ones that found something.
    """
    groups: dict[str, list[dict]] = {keyword: [] for keyword in keywords}
    for article in articles:
        for keyword in article.get("keywords", []):
            if keyword in groups:
                groups[keyword].append(article)
            # A keyword on the article but not in the current `keywords`
            # list (e.g. config changed since collection) has no
            # top-level section to show it under -- drop it from grouping
            # rather than inventing a new section for stale data.
    return [{"keyword": keyword, "articles": groups[keyword]} for keyword in keywords]


def load_dataset(data_dir: Path, today: date, retention_days: int) -> list[dict]:
    """Reads every data/YYYY-MM-DD.json file within the retention window
    (including today's, which main.py has already written by the time
    this runs) and concatenates them into one flat, newest-first,
    deduped-by-id list.

    Each data/YYYY-MM-DD.json file is a full snapshot of "everything still
    within max_article_age_days" as of that collection day -- not a diff --
    so the same story routinely appears in several consecutive days' files
    (it's still recent enough to be re-collected each day). The same id
    (see dedupe._make_id) showing up in more than one file is that, not
    data corruption: keep only the first occurrence found (arbitrary file
    read order doesn't matter -- kept vs. dropped copies are the same
    article) so it doesn't render as several duplicate cards.

    Each kept article is annotated with "date" -- its published_at
    converted to a KST calendar day, which is what the feed's date
    sections and the date popover group by (matching the spec's "날짜
    (KST)별 섹션"). An article whose published_at can't be parsed falls
    back to the collection file's own date instead, so it still lands
    somewhere sane rather than being dropped.
    """
    if not data_dir.exists():
        return []

    cutoff = today - timedelta(days=retention_days)
    seen_ids: set[str] = set()
    all_articles: list[dict] = []
    for path in data_dir.glob("*.json"):
        try:
            file_date = datetime.strptime(path.stem, DATE_FORMAT).date()
        except ValueError:
            continue
        if not (cutoff <= file_date <= today):
            continue
        try:
            day_articles = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if not isinstance(day_articles, list):
            continue
        for article in day_articles:
            article = dict(article)
            # data/*.json files written before dedupe.dedupe() started
            # assigning "id" (see dedupe.py) don't have one -- backfill it
            # the same way, deterministically, so re-running the build
            # doesn't shuffle ids for articles already shown to the user.
            if not article.get("id"):
                article["id"] = _make_id(article.get("link", ""), article.get("title", ""))
            if article["id"] in seen_ids:
                continue
            seen_ids.add(article["id"])
            article["date"] = _kst_day(article.get("published_at", "")) or path.stem
            all_articles.append(article)

    def sort_key(article: dict) -> str:
        # Falls back to the collection date (+ midnight) when published_at
        # couldn't be parsed, so undated articles still land in a sane
        # place (the end of their collection day) instead of sorting
        # first/last arbitrarily.
        return article.get("published_at") or f"{article.get('date', '')}T00:00:00"

    all_articles.sort(key=sort_key, reverse=True)
    return all_articles


_KST = timezone(timedelta(hours=9))


def _kst_day(published_at: str) -> str:
    """published_at converted to its KST calendar day ("YYYY-MM-DD"), or
    "" if it can't be parsed. A naive (no-offset) timestamp is assumed to
    already be KST rather than reinterpreted as UTC."""
    if not published_at:
        return ""
    try:
        dt = datetime.fromisoformat(published_at)
    except ValueError:
        return ""
    if dt.tzinfo is not None:
        dt = dt.astimezone(_KST)
    return dt.strftime(DATE_FORMAT)


def build_dataset(data_dir: Path, today: date, retention_days: int) -> list[dict]:
    """load_dataset() + clustering, ready to serialize as
    output/data/articles.json."""
    articles = load_dataset(data_dir, today, retention_days)
    cluster.assign_clusters(articles)
    return articles


def render(
    date_str: str,
    articles: list[dict],
    output_dir: Path,
    templates_dir: Path,
    retention_days: int,
    keywords: list[str],
    *,
    keyword_specs: list[dict] | None = None,
    data_dir: Path | None = None,
    keywords_api_base: str = "",
) -> None:
    """Writes output/index.html and output/data/articles.json.

    `articles` is today's already-deduped/date-filtered/image-enriched
    list (used only to compute each keyword's "오늘" count for the
    keyword-management screen's initial render -- the page's actual
    article data all comes from output/data/articles.json). `data_dir`
    defaults to output_dir's sibling "data" directory when not given
    (matching main.py's layout) so existing callers/tests that don't pass
    it still work against an empty dataset rather than erroring.
    """
    output_dir = Path(output_dir)
    templates_dir = Path(templates_dir)
    today = datetime.strptime(date_str, DATE_FORMAT).date()
    if data_dir is None:
        data_dir = output_dir.parent / "data"
    else:
        data_dir = Path(data_dir)

    dataset = build_dataset(data_dir, today, retention_days)

    data_out_dir = output_dir / "data"
    data_out_dir.mkdir(parents=True, exist_ok=True)
    (data_out_dir / "articles.json").write_text(
        json.dumps(dataset, ensure_ascii=False), encoding="utf-8"
    )

    today_groups = group_by_keyword(articles, keywords)
    today_counts = {g["keyword"]: len(g["articles"]) for g in today_groups}

    if keyword_specs is None:
        keyword_specs = [
            {"name": k, "exclude": [], "naver": True, "google": True, "active": True}
            for k in keywords
        ]
    specs_for_page = [
        {**spec, "today_count": today_counts.get(spec["name"], 0)} for spec in keyword_specs
    ]

    env = _build_env(templates_dir)
    template = env.get_template("page.html")
    html = template.render(
        date=date_str,
        article_count=len(articles),
        keywords=keywords,
        # Passed as data (not pre-serialized) so the template's |tojson
        # filter can escape it correctly for a <script> context -- doing
        # the json.dumps() here and interpolating the string directly
        # would get HTML-autoescaped by Jinja (quotes turned into
        # &#34; entities), which breaks JSON.parse() in the browser.
        keyword_specs=specs_for_page,
        keywords_api_base=keywords_api_base,
        oldest_date=dataset[-1]["date"] if dataset else date_str,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "index.html").write_text(html, encoding="utf-8")
