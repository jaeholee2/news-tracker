import unittest

from news_tracker.dedupe import dedupe, normalize_title


class NormalizeTitleTests(unittest.TestCase):
    def test_strips_case_punctuation_and_whitespace(self):
        self.assertEqual(normalize_title("  Hello, World!!  "), "hello world")
        self.assertEqual(normalize_title("두두원, 5G 코어!"), normalize_title("두두원 5g 코어"))


class DedupeTests(unittest.TestCase):
    def test_collapses_same_story_across_sources(self):
        articles = [
            {
                "title": "두두원, 5G 코어 솔루션 공급 확대",
                "link": "https://a.example/1",
                "source": "Naver",
                "published_at": "2026-09-09T09:00:00+09:00",
                "keyword": "두두원",
            },
            {
                "title": "두두원 5g 코어 솔루션 공급 확대",  # same story, different casing/spacing
                "link": "https://b.example/1",
                "source": "Google News",
                "published_at": "2026-09-09T09:05:00+00:00",
                "keyword": "두두원",
            },
            {
                "title": "두두원 6G 공동개발 착수",
                "link": "https://a.example/2",
                "source": "Naver",
                "published_at": "2026-09-09T08:00:00+09:00",
                "keyword": "6G",
            },
        ]

        result = dedupe(articles)

        self.assertEqual(len(result), 2)
        first, second = result
        # first-seen title/link/published_at win
        self.assertEqual(first["title"], "두두원, 5G 코어 솔루션 공급 확대")
        self.assertEqual(first["link"], "https://a.example/1")
        self.assertEqual(first["sources"], ["Naver", "Google News"])
        self.assertEqual(first["keywords"], ["두두원"])

        self.assertEqual(second["title"], "두두원 6G 공동개발 착수")
        self.assertEqual(second["sources"], ["Naver"])
        self.assertEqual(second["keywords"], ["6G"])

    def test_keeps_distinct_empty_titled_articles_separate(self):
        articles = [
            {"title": "", "link": "https://a.example/1", "source": "Naver", "published_at": "", "keyword": "x"},
            {"title": "   ", "link": "https://a.example/2", "source": "Naver", "published_at": "", "keyword": "x"},
        ]

        self.assertEqual(len(dedupe(articles)), 2)

    def test_on_empty_list_returns_empty_list(self):
        self.assertEqual(dedupe([]), [])


if __name__ == "__main__":
    unittest.main()
