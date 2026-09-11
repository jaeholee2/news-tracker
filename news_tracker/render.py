"""Render deduped articles into the static HTML site.

Writes two files per run:
    output/index.html              -- today's articles (always overwritten)
    output/archive/YYYY-MM-DD.html -- a permanent copy of today's page

Both are rendered from the same `templates/page.html` template and both
carry a nav of recent archive days (limited to `retention_days`), so a page
opened from the archive is just as navigable as index.html.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import jinja2

DATE_FORMAT = "%Y-%m-%d"


def _build_env(templates_dir: Path) -> jinja2.Environment:
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(templates_dir)),
        autoescape=jinja2.select_autoescape(["html"]),
    )


def discover_archive_dates(archive_dir: Path, today: date, retention_days: int) -> list[str]:
    """Return ISO date strings for every archive page that exists on disk
    within the retention window (today included), sorted most-recent-first.

    This only filters what's already on disk -- it does not delete
    anything. Pruning files older than the window is main.py's job.
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


def _render_page(
    env: jinja2.Environment,
    date_str: str,
    articles: list[dict],
    archive_dates: list[str],
    keywords: list[str],
    *,
    is_index: bool,
    password_hash: str | None,
) -> str:
    template = env.get_template("page.html")
    return template.render(
        date=date_str,
        keyword_groups=group_by_keyword(articles, keywords),
        archive_dates=archive_dates,
        # index.html links into archive/<date>.html; an archive page links
        # to its siblings directly and back up to ../index.html.
        archive_link_prefix="archive/" if is_index else "",
        home_link="index.html" if is_index else "../index.html",
        # When set, the template shows a client-side password gate. See
        # main.resolve_site_password_hash -- this is a deterrent against
        # casual visitors on a public URL, not real access control.
        password_hash=password_hash,
    )


def render(
    date_str: str,
    articles: list[dict],
    output_dir: Path,
    templates_dir: Path,
    retention_days: int,
    keywords: list[str],
    password_hash: str | None = None,
) -> None:
    """Render today's index page and its permanent archive copy."""
    output_dir = Path(output_dir)
    templates_dir = Path(templates_dir)
    archive_dir = output_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)

    today = datetime.strptime(date_str, DATE_FORMAT).date()
    env = _build_env(templates_dir)

    # Write the archive copy first so today's date is included when we
    # recompute the nav list for index.html below.
    archive_page = archive_dir / f"{date_str}.html"
    archive_page.write_text(
        _render_page(
            env,
            date_str,
            articles,
            discover_archive_dates(archive_dir, today, retention_days),
            keywords,
            is_index=False,
            password_hash=password_hash,
        ),
        encoding="utf-8",
    )

    archive_dates = discover_archive_dates(archive_dir, today, retention_days)
    index_page = output_dir / "index.html"
    index_page.write_text(
        _render_page(
            env, date_str, articles, archive_dates, keywords, is_index=True, password_hash=password_hash
        ),
        encoding="utf-8",
    )
