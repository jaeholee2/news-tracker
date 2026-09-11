import tempfile
import unittest
from datetime import date
from pathlib import Path

from news_tracker import render

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

KEYWORDS = ["두두원", "5G 특화망"]

SAMPLE_ARTICLES = [
    {
        "title": "두두원, 5G 코어 솔루션 공급 확대",
        "link": "https://example.com/news/1",
        "sources": ["Naver", "Google News"],
        "published_at": "2026-09-09T09:00:00+09:00",
        "keywords": ["두두원"],
    },
    {
        "title": "두두원 6G 공동개발 착수",
        "link": "https://example.com/news/2",
        "sources": ["Naver"],
        "published_at": "2026-09-09T08:00:00+09:00",
        "keywords": ["두두원"],
    },
]


class RenderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.output_dir = Path(self._tmp.name) / "output"

    def test_writes_index_and_archive_with_expected_content(self):
        # Only "두두원" here (not the full KEYWORDS list) so every keyword
        # section has matches -- this test is about article content, not
        # the empty-state-per-keyword behavior (covered separately below).
        render.render(
            "2026-09-09",
            SAMPLE_ARTICLES,
            self.output_dir,
            TEMPLATES_DIR,
            retention_days=90,
            keywords=["두두원"],
        )

        index_html = (self.output_dir / "index.html").read_text(encoding="utf-8")
        archive_html = (self.output_dir / "archive" / "2026-09-09.html").read_text(encoding="utf-8")

        for html in (index_html, archive_html):
            self.assertIn("두두원, 5G 코어 솔루션 공급 확대", html)
            self.assertIn("https://example.com/news/1", html)
            self.assertIn("두두원 6G 공동개발 착수", html)
            self.assertIn("https://example.com/news/2", html)
            # The new template always emits a per-keyword-group empty-state
            # paragraph (hidden by default) so client-side JS filtering can
            # reveal it if a filter leaves zero visible articles in that
            # group -- so "기사 없음" text now legitimately exists in the
            # markup even when articles are present. What matters is that
            # it stays hidden when the group actually has matches.
            self.assertIn('class="empty group-empty" hidden', html)

        # today isn't linked to itself in its own nav
        self.assertNotIn('href="archive/2026-09-09.html"', index_html)
        # archive page links back up to the site root
        self.assertIn('href="../index.html"', archive_html)

    def test_shows_empty_state_when_no_articles(self):
        render.render("2026-09-09", [], self.output_dir, TEMPLATES_DIR, retention_days=90, keywords=KEYWORDS)

        index_html = (self.output_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn("기사 없음", index_html)
        # every configured keyword still gets a section, even with zero results
        self.assertIn("두두원", index_html)
        self.assertIn("5G 특화망", index_html)

    def test_index_nav_lists_previous_archive_days_within_retention(self):
        archive_dir = self.output_dir / "archive"
        archive_dir.mkdir(parents=True)
        (archive_dir / "2026-09-08.html").write_text("old page", encoding="utf-8")
        (archive_dir / "2026-01-01.html").write_text("too old", encoding="utf-8")  # outside retention

        render.render(
            "2026-09-09", SAMPLE_ARTICLES, self.output_dir, TEMPLATES_DIR, retention_days=30, keywords=KEYWORDS
        )

        index_html = (self.output_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="archive/2026-09-08.html"', index_html)
        self.assertNotIn("2026-01-01", index_html)


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
