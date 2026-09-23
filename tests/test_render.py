import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from news_tracker import render

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

KEYWORDS = ["두두원", "5G 특화망"]

SAMPLE_ARTICLES = [
    {
        "id": "aaa111",
        "title": "두두원, 5G 코어 솔루션 공급 확대",
        "link": "https://example.com/news/1",
        "sources": ["Naver", "Google News"],
        "published_at": "2026-09-09T09:00:00+09:00",
        "keywords": ["두두원"],
        "image": "",
    },
    {
        "id": "bbb222",
        "title": "두두원 6G 공동개발 착수",
        "link": "https://example.com/news/2",
        "sources": ["Naver"],
        "published_at": "2026-09-09T08:00:00+09:00",
        "keywords": ["두두원"],
        "image": "",
    },
]


def _write_day(data_dir: Path, date_str: str, articles: list[dict]) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / f"{date_str}.json").write_text(
        json.dumps(articles, ensure_ascii=False), encoding="utf-8"
    )


class RenderTests(unittest.TestCase):
    """render.render() now writes a single output/index.html (a
    client-side app -- see templates/page.html) plus output/data/
    articles.json, which is where actual article content lives. There is
    no more a per-day output/archive/*.html; history browsing happens
    client-side against the one JSON dataset. So these tests check the
    dataset's content/filtering rather than grepping server-rendered HTML
    for article titles."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.output_dir = Path(self._tmp.name) / "output"
        self.data_dir = Path(self._tmp.name) / "data"

    def _dataset(self):
        return json.loads((self.output_dir / "data" / "articles.json").read_text(encoding="utf-8"))

    def test_writes_index_html_and_articles_dataset(self):
        _write_day(self.data_dir, "2026-09-09", SAMPLE_ARTICLES)

        render.render(
            "2026-09-09",
            SAMPLE_ARTICLES,
            self.output_dir,
            TEMPLATES_DIR,
            retention_days=90,
            keywords=["두두원"],
            data_dir=self.data_dir,
        )

        index_html = (self.output_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn("<html", index_html)
        self.assertIn("News Tracker", index_html)
        # no more a separate per-day archive page -- history lives in the dataset
        self.assertFalse((self.output_dir / "archive").exists())

        dataset = self._dataset()
        titles = {a["title"] for a in dataset}
        self.assertEqual(titles, {a["title"] for a in SAMPLE_ARTICLES})
        links = {a["link"] for a in dataset}
        self.assertEqual(links, {a["link"] for a in SAMPLE_ARTICLES})
        # every article is annotated with its collection day and cluster fields
        for a in dataset:
            self.assertEqual(a["date"], "2026-09-09")
            self.assertIn("is_representative", a)

    def test_dataset_is_empty_when_no_articles_collected(self):
        render.render(
            "2026-09-09",
            [],
            self.output_dir,
            TEMPLATES_DIR,
            retention_days=90,
            keywords=KEYWORDS,
            data_dir=self.data_dir,
        )

        self.assertEqual(self._dataset(), [])
        index_html = (self.output_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn("<html", index_html)

    def test_dataset_includes_previous_days_within_retention_only(self):
        _write_day(self.data_dir, "2026-09-09", SAMPLE_ARTICLES)
        _write_day(
            self.data_dir,
            "2026-09-08",
            [
                {
                    "id": "ccc333",
                    "title": "어제 기사",
                    "link": "https://example.com/news/3",
                    "sources": ["Naver"],
                    "published_at": "2026-09-08T10:00:00+09:00",
                    "keywords": ["두두원"],
                    "image": "",
                }
            ],
        )
        _write_day(
            self.data_dir,
            "2026-01-01",
            [
                {
                    "id": "ddd444",
                    "title": "너무 오래된 기사",
                    "link": "https://example.com/news/4",
                    "sources": ["Naver"],
                    "published_at": "2026-01-01T10:00:00+09:00",
                    "keywords": ["두두원"],
                    "image": "",
                }
            ],
        )

        render.render(
            "2026-09-09",
            SAMPLE_ARTICLES,
            self.output_dir,
            TEMPLATES_DIR,
            retention_days=30,
            keywords=KEYWORDS,
            data_dir=self.data_dir,
        )

        dataset = self._dataset()
        titles = {a["title"] for a in dataset}
        self.assertIn("어제 기사", titles)
        self.assertNotIn("너무 오래된 기사", titles)

    def test_keyword_specs_with_today_counts_are_embedded(self):
        _write_day(self.data_dir, "2026-09-09", SAMPLE_ARTICLES)
        specs = [
            {"name": "두두원", "exclude": [], "naver": True, "google": True, "active": True},
            {"name": "5G 특화망", "exclude": [], "naver": True, "google": True, "active": False},
        ]

        render.render(
            "2026-09-09",
            SAMPLE_ARTICLES,
            self.output_dir,
            TEMPLATES_DIR,
            retention_days=90,
            keywords=["두두원"],
            keyword_specs=specs,
            data_dir=self.data_dir,
        )

        index_html = (self.output_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="nt-keyword-specs"', index_html)
        # today's per-keyword article count is embedded for the keyword
        # management screen's initial (pre-live-fetch) render
        self.assertIn('"today_count": 2', index_html)


class GroupByKeywordTests(unittest.TestCase):
    def test_groups_in_configured_keyword_order(self):
        articles = [
            {"title": "a", "link": "l1", "sources": ["Naver"], "published_at": "", "keywords": ["P5G"]},
            {"title": "b", "link": "l2", "sources": ["Naver"], "published_at": "", "keywords": ["두두원"]},
        ]

        groups = render.group_by_keyword(articles, ["두두원", "P5G"])

        self.assertEqual([g["keyword"] for g in groups], ["두두원", "P5G"])
        self.assertEqual([a["link"] for a in groups[0]["articles"]], ["l2"])
        self.assertEqual([a["link"] for a in groups[1]["articles"]], ["l1"])

    def test_keyword_with_no_matches_still_included_with_empty_list(self):
        groups = render.group_by_keyword([], ["두두원", "P5G"])

        self.assertEqual([g["keyword"] for g in groups], ["두두원", "P5G"])
        self.assertEqual(groups[0]["articles"], [])
        self.assertEqual(groups[1]["articles"], [])

    def test_article_matching_multiple_keywords_appears_in_each_group(self):
        articles = [
            {
                "title": "a",
                "link": "l1",
                "sources": ["Naver"],
                "published_at": "",
                "keywords": ["두두원", "P5G"],
            }
        ]

        groups = render.group_by_keyword(articles, ["두두원", "P5G"])

        self.assertEqual([a["link"] for a in groups[0]["articles"]], ["l1"])
        self.assertEqual([a["link"] for a in groups[1]["articles"]], ["l1"])


class DiscoverArchiveDatesTests(unittest.TestCase):
    def test_filters_by_retention_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive_dir = Path(tmp) / "archive"
            archive_dir.mkdir()
            (archive_dir / "2026-09-09.html").touch()
            (archive_dir / "2026-08-01.html").touch()
            (archive_dir / "not-a-date.html").touch()

            dates = render.discover_archive_dates(archive_dir, date(2026, 9, 9), retention_days=10)

            self.assertEqual(dates, ["2026-09-09"])

    def test_missing_directory_returns_empty_list(self):
        dates = render.discover_archive_dates(Path("/nonexistent/path"), date(2026, 9, 9), retention_days=10)
        self.assertEqual(dates, [])


if __name__ == "__main__":
    unittest.main()
