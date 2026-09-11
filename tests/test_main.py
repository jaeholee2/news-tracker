import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import yaml

from news_tracker import main as news_main


def write_config(path, **overrides):
    config = {
        "keywords": ["두두원"],
        "naver": {"client_id": "id", "client_secret": "secret"},
        "retention_days": 90,
    }
    config.update(overrides)
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    return path


class LoadConfigTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    def test_valid_config(self):
        config_path = write_config(self.tmp_path / "config.yaml")

        config = news_main.load_config(config_path)

        self.assertEqual(config["keywords"], ["두두원"])
        self.assertEqual(config["naver_client_id"], "id")
        self.assertEqual(config["naver_client_secret"], "secret")
        self.assertEqual(config["retention_days"], 90)

    def test_missing_file_is_fatal(self):
        with self.assertRaises(news_main.ConfigError):
            news_main.load_config(self.tmp_path / "does-not-exist.yaml")

    def test_malformed_yaml_is_fatal(self):
        path = self.tmp_path / "config.yaml"
        path.write_text("keywords: [unclosed", encoding="utf-8")

        with self.assertRaises(news_main.ConfigError):
            news_main.load_config(path)

    def test_missing_keywords_is_fatal(self):
        path = write_config(self.tmp_path / "config.yaml", keywords=[])

        with self.assertRaises(news_main.ConfigError):
            news_main.load_config(path)

    def test_missing_naver_credentials_is_fatal(self):
        path = write_config(
            self.tmp_path / "config.yaml", naver={"client_id": "", "client_secret": ""}
        )

        with self.assertRaises(news_main.ConfigError):
            news_main.load_config(path)

    def test_defaults_retention_days_when_absent(self):
        path = self.tmp_path / "config.yaml"
        path.write_text(
            yaml.safe_dump(
                {"keywords": ["x"], "naver": {"client_id": "id", "client_secret": "secret"}},
                allow_unicode=True,
            ),
            encoding="utf-8",
        )

        config = news_main.load_config(path)

        self.assertEqual(config["retention_days"], news_main.DEFAULT_RETENTION_DAYS)

    def test_defaults_max_article_age_days_when_absent(self):
        path = self.tmp_path / "config.yaml"
        path.write_text(
            yaml.safe_dump(
                {"keywords": ["x"], "naver": {"client_id": "id", "client_secret": "secret"}},
                allow_unicode=True,
            ),
            encoding="utf-8",
        )

        config = news_main.load_config(path)

        self.assertEqual(config["max_article_age_days"], news_main.DEFAULT_MAX_ARTICLE_AGE_DAYS)

    def test_invalid_max_article_age_days_is_fatal(self):
        path = write_config(self.tmp_path / "config.yaml", max_article_age_days=0)

        with self.assertRaises(news_main.ConfigError):
            news_main.load_config(path)


class NaverCredentialEnvOverrideTests(unittest.TestCase):
    """Env vars exist so a public (secrets-free) config.yaml can be used in
    CI -- see config.ci.yaml and .github/workflows/daily.yml."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)

    @patch.dict(
        "os.environ",
        {"NAVER_CLIENT_ID": "env-id", "NAVER_CLIENT_SECRET": "env-secret"},
    )
    def test_env_vars_take_priority_over_config_file(self):
        path = write_config(self.tmp_path / "config.yaml")  # has naver: id/secret

        config = news_main.load_config(path)

        self.assertEqual(config["naver_client_id"], "env-id")
        self.assertEqual(config["naver_client_secret"], "env-secret")

    @patch.dict(
        "os.environ",
        {"NAVER_CLIENT_ID": "env-id", "NAVER_CLIENT_SECRET": "env-secret"},
    )
    def test_env_vars_suffice_when_config_has_no_naver_section(self):
        path = self.tmp_path / "config.ci.yaml"
        path.write_text(yaml.safe_dump({"keywords": ["두두원"]}, allow_unicode=True), encoding="utf-8")

        config = news_main.load_config(path)

        self.assertEqual(config["naver_client_id"], "env-id")
        self.assertEqual(config["naver_client_secret"], "env-secret")

    def test_missing_credentials_in_both_config_and_env_is_fatal(self):
        path = self.tmp_path / "config.ci.yaml"
        path.write_text(yaml.safe_dump({"keywords": ["두두원"]}, allow_unicode=True), encoding="utf-8")

        with self.assertRaises(news_main.ConfigError):
            news_main.load_config(path)


class ResolveConfigPathTests(unittest.TestCase):
    def test_defaults_to_config_yaml_in_project_root(self):
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("NEWS_TRACKER_CONFIG_PATH", None)
            self.assertEqual(news_main.resolve_config_path(), news_main.CONFIG_PATH)

    @patch.dict("os.environ", {"NEWS_TRACKER_CONFIG_PATH": "config.ci.yaml"})
    def test_relative_override_resolves_against_project_root(self):
        self.assertEqual(
            news_main.resolve_config_path(), news_main.PROJECT_ROOT / "config.ci.yaml"
        )

    @patch.dict("os.environ", {"NEWS_TRACKER_CONFIG_PATH": "/etc/somewhere/config.yaml"})
    def test_absolute_override_used_as_is(self):
        self.assertEqual(news_main.resolve_config_path(), Path("/etc/somewhere/config.yaml"))


class FilterRecentTests(unittest.TestCase):
    TODAY = date(2026, 9, 11)

    def test_drops_articles_older_than_max_age(self):
        articles = [
            {"title": "old", "published_at": "2020-01-01T09:00:00+09:00"},
            {"title": "recent", "published_at": "2026-09-10T09:00:00+09:00"},
        ]

        kept = news_main.filter_recent(articles, self.TODAY, max_age_days=3)

        self.assertEqual([a["title"] for a in kept], ["recent"])

    def test_keeps_articles_exactly_at_the_cutoff(self):
        articles = [{"title": "edge", "published_at": "2026-09-08T00:00:00+09:00"}]

        kept = news_main.filter_recent(articles, self.TODAY, max_age_days=3)

        self.assertEqual([a["title"] for a in kept], ["edge"])

    def test_keeps_articles_with_unparseable_or_missing_date(self):
        articles = [
            {"title": "no date", "published_at": ""},
            {"title": "missing key"},
        ]

        kept = news_main.filter_recent(articles, self.TODAY, max_age_days=3)

        self.assertEqual(len(kept), 2)


class PruneOldFilesTests(unittest.TestCase):
    def test_removes_only_files_older_than_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"
            output_dir = Path(tmp) / "output"
            archive_dir = output_dir / "archive"
            data_dir.mkdir()
            archive_dir.mkdir(parents=True)

            (data_dir / "2026-09-09.json").write_text("[]", encoding="utf-8")
            (data_dir / "2026-01-01.json").write_text("[]", encoding="utf-8")
            (archive_dir / "2026-09-09.html").write_text("x", encoding="utf-8")
            (archive_dir / "2026-01-01.html").write_text("x", encoding="utf-8")

            news_main.prune_old_files(
                date(2026, 9, 9), retention_days=30, data_dir=data_dir, output_dir=output_dir
            )

            self.assertTrue((data_dir / "2026-09-09.json").exists())
            self.assertFalse((data_dir / "2026-01-01.json").exists())
            self.assertTrue((archive_dir / "2026-09-09.html").exists())
            self.assertFalse((archive_dir / "2026-01-01.html").exists())


class SaveArticlesTests(unittest.TestCase):
    def test_overwrites_same_day_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp) / "data"

            news_main.save_articles([{"title": "first run"}], "2026-09-09", data_dir=data_dir)
            news_main.save_articles([{"title": "second run"}], "2026-09-09", data_dir=data_dir)

            saved = (data_dir / "2026-09-09.json").read_text(encoding="utf-8")
            self.assertIn("second run", saved)
            self.assertNotIn("first run", saved)


if __name__ == "__main__":
    unittest.main()
