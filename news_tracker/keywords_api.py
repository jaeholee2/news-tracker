"""Fetches the live keyword list from the keywords API (worker/ -- a small
Cloudflare Worker backing the "키워드 관리" screen) at the start of each
collection run, so a keyword added/edited/disabled from the browser takes
effect on the very next hourly run without touching config.yaml or
redeploying anything.

Falls back to the static list in config.yaml/config.ci.yaml whenever the
API isn't configured or can't be reached -- a live-editing feature failing
open to "collect the same keywords as last time" is much safer than it
silently collecting nothing.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 8


def fetch_live_keywords(api_base: str | None, fallback: list[str]) -> list[dict]:
    """Returns a list of keyword-spec dicts ({name, exclude, naver, google,
    active}) to pass to collect.collect().

    `api_base` is the keywords API's origin (e.g.
    "https://news-tracker-api.<subdomain>.workers.dev"), or falsy to skip
    the live fetch entirely (e.g. running locally without the worker set
    up). `fallback` is config["keywords"] -- used verbatim, as plain
    strings, whenever the live fetch is skipped or fails.
    """
    if not api_base:
        logger.info("No keywords API configured; using the static keyword list from config")
        return [_static_spec(k) for k in fallback]

    url = api_base.rstrip("/") + "/api/keywords"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        specs = response.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning(
            "Could not fetch live keywords from %s (%s) -- falling back to config.yaml's list",
            url,
            exc,
        )
        return [_static_spec(k) for k in fallback]

    if not isinstance(specs, list) or not specs:
        logger.warning(
            "Keywords API returned an empty/invalid list -- falling back to config.yaml's list"
        )
        return [_static_spec(k) for k in fallback]

    normalized = []
    for spec in specs:
        if not isinstance(spec, dict) or not spec.get("name"):
            continue
        normalized.append(
            {
                "name": str(spec["name"]),
                "exclude": [str(x) for x in spec.get("exclude", []) if str(x).strip()],
                "naver": spec.get("naver", True) is not False,
                "google": spec.get("google", True) is not False,
                "active": spec.get("active", True) is not False,
            }
        )

    if not normalized:
        logger.warning("Keywords API returned no usable entries -- falling back to config.yaml")
        return [_static_spec(k) for k in fallback]

    logger.info("Loaded %d keyword(s) from the live keywords API", len(normalized))
    return normalized


def _static_spec(name: str) -> dict:
    return {"name": name, "exclude": [], "naver": True, "google": True, "active": True}
