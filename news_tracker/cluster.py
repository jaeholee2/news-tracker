"""Groups articles that are almost certainly the same story (e.g. the same
wire report carried by several outlets) into clusters, for the "같은 소식
묶기" (group same story) toggle on the feed.

This is separate from dedupe.dedupe(): that function collapses articles
that are *exact* title matches (after normalizing case/punctuation/
whitespace) regardless of publish time, and is meant to merge the same
Naver + Google News hit for one story. This module additionally catches
*near*-duplicate titles across different articles (e.g. several outlets'
own headlines for the same event) published within a short window of each
other -- a much fuzzier, time-bounded match, appropriate for a UI grouping
toggle rather than for silently discarding one of the two entries.

Algorithm (as suggested in the redesign handoff): normalize each title,
token-Jaccard similarity >= 0.5 AND published within 24h of each other =>
same cluster. Clustering never removes or merges article dicts -- it only
annotates each one, so turning the "grouped" toggle off in the UI can
still show every article as its own card.
"""

from __future__ import annotations

from datetime import datetime

from news_tracker.dedupe import normalize_title

CLUSTER_WINDOW_HOURS = 24
JACCARD_THRESHOLD = 0.5


def _parse_dt(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    if intersection == 0:
        return 0.0
    return intersection / len(a | b)


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def assign_clusters(articles: list[dict]) -> None:
    """Mutates each article dict in place, adding:
        cluster_id:     a stable id shared by every article in the cluster
        is_representative: True for exactly one article per cluster (the
                            earliest-published one)
        related:        (representative only) the other articles in the
                         cluster, each as {id, title, sources, published_at}
                         sorted oldest-first
        cluster_size:   (representative only) total articles in the cluster

    Articles without a parseable published_at, or whose id is missing, are
    left alone in their own singleton cluster -- there's no reliable way to
    time-bound-compare them, and a wrong merge is worse than none.
    """
    n = len(articles)
    if n == 0:
        return

    # Precompute per-article (parsed time, token set); skip anything that
    # can't be time-compared.
    parsed = [_parse_dt(a.get("published_at", "")) for a in articles]
    tokens = [set(normalize_title(a.get("title", "")).split()) for a in articles]

    order = sorted(
        (i for i in range(n) if parsed[i] is not None),
        key=lambda i: parsed[i],
    )

    uf = _UnionFind(n)
    for pos, i in enumerate(order):
        for j in order[pos + 1 :]:
            delta = (parsed[j] - parsed[i]).total_seconds()
            if delta > CLUSTER_WINDOW_HOURS * 3600:
                break  # order is time-sorted -- nothing further can be in-window either
            if _jaccard(tokens[i], tokens[j]) >= JACCARD_THRESHOLD:
                uf.union(i, j)

    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    for root, members in groups.items():
        # Representative = earliest publish time among members with a
        # parseable date; if none parse, just take the first member as-is.
        dated = [m for m in members if parsed[m] is not None]
        rep = min(dated, key=lambda m: parsed[m]) if dated else members[0]
        cluster_id = articles[rep].get("id") or f"cluster-{root}"

        for m in members:
            articles[m]["cluster_id"] = cluster_id
            articles[m]["is_representative"] = m == rep

        if len(members) > 1:
            others = sorted(
                (m for m in members if m != rep),
                key=lambda m: parsed[m] or datetime.min.replace(tzinfo=None),
            )
            articles[rep]["related"] = [
                {
                    "id": articles[m].get("id"),
                    "title": articles[m].get("title", ""),
                    "sources": articles[m].get("sources", []),
                    "published_at": articles[m].get("published_at", ""),
                    "link": articles[m].get("link", ""),
                }
                for m in others
            ]
            articles[rep]["cluster_size"] = len(members)
        else:
            articles[rep]["related"] = []
            articles[rep]["cluster_size"] = 1
