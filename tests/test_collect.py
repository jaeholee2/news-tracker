"""Tests for collect.py, run with the standard library's unittest.

HTTP is mocked by patching `requests.get` directly (no third-party mocking
library required), against fixture JSON/XML that mirrors real Naver News
API and Google News RSS responses.
"""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from news_tracker import collect

FIXTURES = Path(__file__).parent / "fixtures"
KEYWORD = "두두원"


class FakeResponse:
    """Minimal stand-in for requests.Response."""

    def __init__(self, *, json_data=None, content=b"", status_code=200):
        self._json_data = json_data
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"status {self.status_code}")

    def json(self):
        if self._json_data is None:
            raise ValueError("response body is not valid JSON")
        return self._json_data


class FetchNaverTests(unittest.TestCase):
    def setUp(self):
        self.payload = json.loads((FIXTURES / "naver_response.json").read_text(encoding="utf-8"))

    @patch("news_tracker.collect.requests.get")
    def test_parses_items_and_strips_html(self, mock_get):
        mock_get.return_value = FakeResponse(json_data=self.payload)

        articles = collect.fetch_naver(KEYWORD, "id", "secret")

        self.assertEqual(len(articles), 2)
        first = articles[0]
        self.assertEqual(first["title"], "두두원, 5G 코어 솔루션 공급 확대")  # <b> tags stripped
        self.assertEqual(first["link"], "https://example.com/news/1")  # prefers originallink
        self.assertEqual(first["source"], "Naver")
        self.assertEqual(first["keyword"], KEYWORD)
        self.assertEqual(first["published_at"], "2026-09-09T09:00:00+09:00")

        # second item has no originallink -> falls back to `link`
        self.assertEqual(articles[1]["link"], "https://news.naver.com/link/2")

    @patch("news_tracker.collect.requests.get")
    def test_returns_empty_list_on_request_failure(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("boom")

        self.assertEqual(collect.fetch_naver(KEYWORD, "id", "secret"), [])

    @patch("news_tracker.collect.requests.get")
    def test_returns_empty_list_on_bad_json(self, mock_get):
        mock_get.return_value = FakeResponse(json_data=None)

        self.assertEqual(collect.fetch_naver(KEYWORD, "id", "secret"), [])

    @patch("news_tracker.collect.requests.get")
    def test_returns_empty_list_on_http_error(self, mock_get):
        mock_get.return_value = FakeResponse(json_data=self.payload, status_code=401)

        self.assertEqual(collect.fetch_naver(KEYWORD, "id", "secret"), [])


class FetchGoogleNewsRssTests(unittest.TestCase):
    def setUp(self):
        self.xml_body = (FIXTURES / "google_news.xml").read_text(encoding="utf-8").encode("utf-8")

    @patch("news_tracker.collect.requests.get")
    def test_parses_entries_and_resolves_publisher(self, mock_get):
        mock_get.return_value = FakeResponse(content=self.xml_body)

        articles = collect.fetch_google_news_rss(KEYWORD)

        self.assertEqual(len(articles), 2)
        first, second = articles

        # has a <source> tag -> publisher comes from there, stripped off the title
        self.assertEqual(first["title"], "두두원, 5G 코어 솔루션 공급 확대")
        self.assertEqual(first["source"], "예시일보")
        self.assertEqual(first["link"], "https://news.google.com/rss/articles/xyz1?oc=5")
        self.assertEqual(first["published_at"], "2026-09-09T09:00:00+00:00")

        # no <source> tag and no " - Publisher" suffix -> falls back to "Google News"
        self.assertEqual(second["title"], "두두원 신규 계약 체결")
        self.assertEqual(second["source"], "Google News")

    @patch("news_tracker.collect.requests.get")
    def test_returns_empty_list_on_request_failure(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("boom")

        self.assertEqual(collect.fetch_google_news_rss(KEYWORD), [])

    @patch("news_tracker.collect.requests.get")
    def test_returns_empty_list_on_malformed_xml(self, mock_get):
        mock_get.return_value = FakeResponse(content=b"<rss><channel><item><oops")

        self.assertEqual(collect.fetch_google_news_rss(KEYWORD), [])


class CollectTests(unittest.TestCase):
    @patch("news_tracker.collect.fetch_google_news_rss")
    @patch("news_tracker.collect.fetch_naver")
    def test_merges_both_sources_and_survives_one_source_failing(self, mock_naver, mock_google):
        mock_naver.return_value = [
            {"title": "a", "link": "l1", "source": "Naver", "published_at": "", "keyword": KEYWORD},
            {"title": "b", "link": "l2", "source": "Naver", "published_at": "", "keyword": KEYWORD},
        ]
        mock_google.return_value = []  # simulates the RSS fetch having failed internally

        articles = collect.collect([KEYWORD], "id", "secret")

        self.assertEqual(len(articles), 2)
        self.assertTrue(all(a["keyword"] == KEYWORD for a in articles))

    @patch("news_tracker.collect.fetch_google_news_rss")
    @patch("news_tracker.collect.fetch_naver")
    def test_single_term_keyword_is_not_filtered(self, mock_naver, mock_google):
        # Neither source is documented to guarantee anything for a plain
        # single-word keyword, so collect() must not touch these results.
        mock_naver.return_value = [
            {"title": "관련 없는 제목", "link": "l1", "source": "Naver", "published_at": "", "keyword": KEYWORD}
        ]
        mock_google.return_value = []

        articles = collect.collect([KEYWORD], "id", "secret")

        self.assertEqual(len(articles), 1)

    @patch("news_tracker.collect.fetch_google_news_rss")
    @patch("news_tracker.collect.fetch_naver")
    def test_multi_term_keyword_requires_all_terms_in_title(self, mock_naver, mock_google):
        # Naver's News Search API doesn't document how it matches a
        # multi-word query (it may be relevance-ranked rather than a
        # strict AND), so a keyword like "방사청 5G" must be enforced here:
        # only articles whose title contains every term survive, regardless
        # of what either upstream source actually returned.
        keyword = "방사청 5G"
        mock_naver.return_value = [
            {"title": "방사청, 5G 국방망 사업 발주", "link": "both", "source": "Naver", "published_at": "", "keyword": keyword},
            {"title": "방사청 예산안 국회 통과", "link": "only-first-term", "source": "Naver", "published_at": "", "keyword": keyword},
        ]
        mock_google.return_value = [
            {"title": "5G 특화망 확산 전망", "link": "only-second-term", "source": "Google News", "published_at": "", "keyword": keyword},
        ]

        articles = collect.collect([keyword], "id", "secret")

        self.assertEqual([a["link"] for a in articles], ["both"])

    def test_title_has_all_terms_is_case_insensitive(self):
        self.assertTrue(collect._title_has_all_terms("Hanwha 5g network launch", ["hanwha", "5G"]))
        self.assertFalse(collect._title_has_all_terms("Hanwha network launch", ["hanwha", "5G"]))


if __name__ == "__main__":
    unittest.main()
