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

    def __init__(self, *, json_data=None, content=b"", text="", status_code=200):
        self._json_data = json_data
        self.content = content
        self.text = text
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


class ExtractOgImageTests(unittest.TestCase):
    def test_finds_og_image_regardless_of_attribute_order(self):
        html_doc = (
            "<html><head>"
            '<meta content="https://example.com/a.jpg" property="og:image">'
            "</head><body></body></html>"
        )
        self.assertEqual(collect._extract_og_image(html_doc), "https://example.com/a.jpg")

    def test_falls_back_to_twitter_image_when_no_og_image(self):
        html_doc = (
            "<html><head>"
            '<meta name="twitter:image" content="https://example.com/b.jpg">'
            "</head><body></body></html>"
        )
        self.assertEqual(collect._extract_og_image(html_doc), "https://example.com/b.jpg")

    def test_prefers_og_image_over_twitter_image(self):
        html_doc = (
            "<html><head>"
            '<meta property="og:image" content="https://example.com/og.jpg">'
            '<meta name="twitter:image" content="https://example.com/tw.jpg">'
            "</head><body></body></html>"
        )
        self.assertEqual(collect._extract_og_image(html_doc), "https://example.com/og.jpg")

    def test_returns_empty_string_when_no_image_tag_present(self):
        html_doc = "<html><head><title>no image here</title></head><body></body></html>"
        self.assertEqual(collect._extract_og_image(html_doc), "")

    def test_does_not_raise_on_malformed_html(self):
        self.assertEqual(collect._extract_og_image("<html><head><meta property="), "")


class FetchArticleImageTests(unittest.TestCase):
    @patch("news_tracker.collect.requests.get")
    def test_returns_extracted_image_url(self, mock_get):
        mock_get.return_value = FakeResponse(
            text='<html><head><meta property="og:image" content="https://example.com/x.jpg">'
            "</head></html>"
        )

        self.assertEqual(
            collect.fetch_article_image("https://example.com/article"),
            "https://example.com/x.jpg",
        )

    def test_returns_empty_string_for_empty_url(self):
        self.assertEqual(collect.fetch_article_image(""), "")

    @patch("news_tracker.collect.requests.get")
    def test_returns_empty_string_on_request_failure(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("boom")

        self.assertEqual(collect.fetch_article_image("https://example.com/article"), "")

    @patch("news_tracker.collect.requests.get")
    def test_returns_empty_string_on_http_error(self, mock_get):
        mock_get.return_value = FakeResponse(text="", status_code=404)

        self.assertEqual(collect.fetch_article_image("https://example.com/article"), "")


class EnrichWithImagesTests(unittest.TestCase):
    @patch("news_tracker.collect.fetch_article_image")
    def test_attaches_image_key_to_every_article_preserving_order(self, mock_fetch):
        mock_fetch.side_effect = lambda link, timeout=None: f"img-for-{link}"
        articles = [
            {"title": "a", "link": "l1"},
            {"title": "b", "link": "l2"},
            {"title": "c", "link": "l3"},
        ]

        result = collect.enrich_with_images(articles)

        self.assertEqual([a["image"] for a in result], ["img-for-l1", "img-for-l2", "img-for-l3"])
        # mutates in place
        self.assertIs(result, articles)

    @patch("news_tracker.collect.fetch_article_image")
    def test_one_failed_fetch_does_not_affect_others(self, mock_fetch):
        def fake_fetch(link, timeout=None):
            if link == "l2":
                raise AssertionError("should never propagate")
            return "ok"

        # fetch_article_image itself never raises (it catches everything
        # internally); simulate that contract directly rather than a raise.
        mock_fetch.side_effect = lambda link, timeout=None: "" if link == "l2" else "ok"
        articles = [{"link": "l1"}, {"link": "l2"}, {"link": "l3"}]

        result = collect.enrich_with_images(articles)

        self.assertEqual([a["image"] for a in result], ["ok", "", "ok"])

    def test_empty_list_is_a_no_op(self):
        self.assertEqual(collect.enrich_with_images([]), [])


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
