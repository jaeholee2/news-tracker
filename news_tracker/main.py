"""Entry point: python -m news_tracker.main

Orchestrates one day's run: load config -> collect -> dedupe -> save ->
render -> prune. Intended to be invoked once daily by cron.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import yaml

from news_tracker import collect, dedupe, render

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
TEMPLATES_DIR = PROJECT_ROOT / "templates"

DEFAULT_RETENTION_DAYS = 90
# Google News RSS search matches by keyword relevance, not recency -- a
# search for "두두원" can return a story from years ago that merely contains
# "두두". Since this is meant to be checked daily for *new* news, articles
# published more than this many days before the collection day are dropped
# before saving/rendering. See filter_recent().
DEFAULT_MAX_ARTICLE_AGE_DAYS = 3
DATE_FORMAT = "%Y-%m-%d"

# Env var names. Naver credentials given this way take priority over
# config.yaml, so a public repo (e.g. for GitHub Pages / Actions) can ship a
# config file with no secrets in it at all -- see NEWS_TRACKER_CONFIG_PATH
# below for pointing at that public config file in CI.
ENV_NAVER_CLIENT_ID = "NAVER_CLIENT_ID"
ENV_NAVER_CLIENT_SECRET = "NAVER_CLIENT_SECRET"
# Optional: if set, the rendered site is gated behind this password
# (client-side only -- see templates/page.html). Unset by default, which
# renders the site with no gate, same as before this existed.
ENV_SITE_PASSWORD = "SITE_PASSWORD"
# Optional: overrides which config file to load (relative paths resolve
# against PROJECT_ROOT). Used in CI to point at a secrets-free config file.
ENV_CONFIG_PATH = "NEWS_TRACKER_CONFIG_PATH"

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Missing or malformed config.yaml. Always fatal -- a broken config
    should not silently produce an empty page."""


def resolve_config_path() -> Path:
    """Which config file to load: NEWS_TRACKER_CONFIG_PATH env var if set
    (relative to PROJECT_ROOT), otherwise the default config.yaml."""
    override = os.environ.get(ENV_CONFIG_PATH)
    if not override:
        return CONFIG_PATH
    override_path = Path(override)
    return override_path if override_path.is_absolute() else PROJECT_ROOT / override_path


def resolve_site_password_hash() -> str | None:
    """SHA-256 hex digest of SITE_PASSWORD, or None if it's not set (no
    password gate). Only the hash ever leaves this function -- the plain
    password is never written to disk or embedded in the rendered page."""
    password = os.environ.get(ENV_SITE_PASSWORD)
    return hashlib.sha256(password.encode("utf-8")).hexdigest() if password else None


def load_config(path: Path | None = None) -> dict:
    path = path or resolve_config_path()
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")

    try:
        with path.open(encoding="utf-8") as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"could not parse {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a YAML mapping at the top level")

    keywords = raw.get("keywords")
    if (
        not isinstance(keywords, list)
        or not keywords
        or not all(isinstance(k, str) and k.strip() for k in keywords)
    ):
        raise ConfigError("'keywords' must be a non-empty list of strings")

    # Naver credentials: environment variables take priority over the
    # config file (so a committed, secrets-free config works in CI); if
    # neither provides both values, that's a hard stop.
    naver = raw.get("naver") if isinstance(raw.get("naver"), dict) else {}
    client_id = os.environ.get(ENV_NAVER_CLIENT_ID) or naver.get("client_id")
    client_secret = os.environ.get(ENV_NAVER_CLIENT_SECRET) or naver.get("client_secret")
    if not client_id or not client_secret:
        raise ConfigError(
            f"Naver credentials are required: set {ENV_NAVER_CLIENT_ID}/"
            f"{ENV_NAVER_CLIENT_SECRET} env vars, or 'naver.client_id'/"
            "'naver.client_secret' in the config file"
        )

    retention_days = raw.get("retention_days", DEFAULT_RETENTION_DAYS)
    if not isinstance(retention_days, int) or retention_days <= 0:
        raise ConfigError("'retention_days' must be a positive integer")

    max_article_age_days = raw.get("max_article_age_days", DEFAULT_MAX_ARTICLE_AGE_DAYS)
    if not isinstance(max_article_age_days, int) or max_article_age_days <= 0:
        raise ConfigError("'max_article_age_days' must be a positive integer")

    return {
        "keywords": keywords,
        "naver_client_id": client_id,
        "naver_client_secret": client_secret,
        "retention_days": retention_days,
        "max_article_age_days": max_article_age_days,
    }


def save_articles(articles: list[dict], date_str: str, data_dir: Path = DATA_DIR) -> Path:
    """Write the deduped article list for `date_str`, overwriting any
    existing file for the same day (one run per day is the expected use)."""
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / f"{date_str}.json"
    path.write_text(json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def filter_recent(articles: list[dict], today: date, max_age_days: int) -> list[dict]:
    """Drop articles published more than `max_age_days` before `today`.

    Google News RSS search ranks by keyword match, not recency, so it
    regularly surfaces stories from years ago that merely contain the
    search term -- not useful on a page meant to show *today's* news. An
    article whose `published_at` couldn't be parsed (empty string) is kept
    rather than dropped, since there's no date to judge it by.
    """
    cutoff = today - timedelta(days=max_age_days)
    kept = []
    for article in articles:
        published_at = article.get("published_at", "")
        if not published_at:
            kept.append(article)
            continue
        try:
            published_date = datetime.fromisoformat(published_at).date()
        except ValueError:
            kept.append(article)
            continue
        if published_date >= cutoff:
            kept.append(article)
    return kept


def prune_old_files(
    today: date,
    retention_days: int,
    data_dir: Path = DATA_DIR,
    output_dir: Path = OUTPUT_DIR,
) -> None:
    """Delete data/*.json and output/archive/*.html files older than
    retention_days, so both directories stay bounded in size."""
    cutoff = today - timedelta(days=retention_days)
    archive_dir = output_dir / "archive"

    for directory, suffix in ((data_dir, ".json"), (archive_dir, ".html")):
        if not directory.exists():
            continue
        for path in directory.glob(f"*{suffix}"):
            try:
                file_date = datetime.strptime(path.stem, DATE_FORMAT).date()
            except ValueError:
                continue  # not a dated file we manage (e.g. .gitkeep)
            if file_date < cutoff:
                path.unlink()
                logger.info("Pruned %s (older than retention_days=%d)", path, retention_days)


def run(today: date | None = None) -> int:
    """Runs one full daily cycle. Returns a process exit code."""
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"news-tracker: fatal: {exc}", file=sys.stderr)
        return 1

    today = today or date.today()
    date_str = today.strftime(DATE_FORMAT)

    logger.info("Collecting articles for %d keyword(s) on %s", len(config["keywords"]), date_str)
    articles = collect.collect(
        config["keywords"], config["naver_client_id"], config["naver_client_secret"]
    )
    deduped = dedupe.dedupe(articles)
    recent = filter_recent(deduped, today, config["max_article_age_days"])
    logger.info(
        "Collected %d article(s), %d after dedup, %d after dropping items older than %d day(s)",
        len(articles),
        len(deduped),
        len(recent),
        config["max_article_age_days"],
    )

    save_articles(recent, date_str)
    render.render(
        date_str,
        recent,
        OUTPUT_DIR,
        TEMPLATES_DIR,
        config["retention_days"],
        config["keywords"],
        password_hash=resolve_site_password_hash(),
    )
    prune_old_files(today, config["retention_days"])

    logger.info("Done. Open %s in a browser.", OUTPUT_DIR / "index.html")
    return 0


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    sys.exit(run())


if __name__ == "__main__":
    main()
