import tempfile
import unittest
from datetime import date
from pathlib import Path

from news_tracker import render

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

SAMPLE_ARTICLES = [
    {
        "title": "두두원, 5G 코어 솔루션 공급 확대",
        "link": "https://example.com/news/1",
        "sources": ["Naver", "Google News"],
        "published_at": "2026-09-09T09:00:00+09:00",
    },
    {
        "title": "두두원 6G 공동개발 착수",
        "link": "https://example.com/news/2",
        "sources": ["Naver"],
        "published_at": "2026-09-09T08:00:00+09:00",
    },
]


class RenderTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.output_dir = Path(self._tmp.name) / "output"

    def test_writes_index_and_archive_with_expected_content(self):
        render.render("2026-09-09", SAMPLE_ARTICLES, self.output_dir, TEMPLATES_DIR, retention_days=90)

        index_html = (self.output_dir / "index.html").read_text(encoding="utf-8")
        archive_html = (self.output_dir / "archive" / "2026-09-09.html").read_text(encoding="utf-8")

        for html in (index_html, archive_html):
            self.assertIn("두두원, 5G 코어 솔루션 공급 확대", html)
            self.assertIn("https://example.com/news/1", html)
            self.assertIn("두두원 6G 공동개발 착수", html)
            self.assertIn("https://example.com/news/2", html)
            self.assertNotIn("기사 없음", html)

        # today isn't linked to itself in its own nav
        self.assertNotIn('href="archive/2026-09-09.html"', index_html)
        # archive page links back up to the site root
        self.assertIn('href="../index.html"', archive_html)

    def test_shows_empty_state_when_no_articles(self):
        render.render("2026-09-09", [], self.output_dir, TEMPLATES_DIR, retention_days=90)

        index_html = (self.output_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn("기사 없음", index_html)

    def test_no_password_gate_by_default(self):
        render.render("2026-09-09", SAMPLE_ARTICLES, self.output_dir, TEMPLATES_DIR, retention_days=90)

        index_html = (self.output_dir / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("gate-overlay", index_html)

    def test_password_gate_rendered_when_hash_given(self):
        fake_hash = "a" * 64
        render.render(
            "2026-09-09",
            SAMPLE_ARTICLES,
            self.output_dir,
            TEMPLATES_DIR,
            retention_days=90,
            password_hash=fake_hash,
        )

        index_html = (self.output_dir / "index.html").read_text(encoding="utf-8")
        archive_html = (self.output_dir / "archive" / "2026-09-09.html").read_text(encoding="utf-8")

        for html in (index_html, archive_html):
            self.assertIn("gate-overlay", html)
            self.assertIn(fake_hash, html)
            # content is hidden by default until the gate script unlocks it
            self.assertIn('id="site-content" style="display: none;"', html)

    def test_index_nav_lists_previous_archive_days_within_retention(self):
        archive_dir = self.output_dir / "archive"
        archive_dir.mkdir(parents=True)
        (archive_dir / "2026-09-08.html").write_text("old page", encoding="utf-8")
        (archive_dir / "2026-01-01.html").write_text("too old", encoding="utf-8")  # outside retention

        render.render("2026-09-09", SAMPLE_ARTICLES, self.output_dir, TEMPLATES_DIR, retention_days=30)

        index_html = (self.output_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="archive/2026-09-08.html"', index_html)
        self.assertNotIn("2026-01-01", index_html)


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
