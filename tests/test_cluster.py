import unittest

from news_tracker import cluster


class AssignClustersTests(unittest.TestCase):
    def test_near_duplicate_titles_within_24h_form_one_cluster(self):
        articles = [
            {
                "id": "a",
                "title": "두두원, 5G 코어 솔루션 공급 확대",
                "sources": ["Naver"],
                "published_at": "2026-09-09T09:00:00+09:00",
            },
            {
                "id": "b",
                "title": "두두원 5G 코어 솔루션 공급 확대 나서",
                "sources": ["조선일보"],
                "published_at": "2026-09-09T10:00:00+09:00",
            },
        ]
        cluster.assign_clusters(articles)

        self.assertTrue(articles[0]["is_representative"])
        self.assertFalse(articles[1]["is_representative"])
        self.assertEqual(articles[0]["cluster_size"], 2)
        self.assertEqual(articles[0]["cluster_id"], articles[1]["cluster_id"])
        self.assertEqual([r["id"] for r in articles[0]["related"]], ["b"])

    def test_unrelated_titles_stay_in_separate_singleton_clusters(self):
        articles = [
            {"id": "a", "title": "두두원 신사업 발표", "sources": ["Naver"], "published_at": "2026-09-09T09:00:00+09:00"},
            {"id": "b", "title": "P5G 국방 사업 수주", "sources": ["Naver"], "published_at": "2026-09-09T09:30:00+09:00"},
        ]
        cluster.assign_clusters(articles)

        self.assertTrue(articles[0]["is_representative"])
        self.assertTrue(articles[1]["is_representative"])
        self.assertEqual(articles[0]["cluster_size"], 1)
        self.assertEqual(articles[1]["cluster_size"], 1)
        self.assertNotEqual(articles[0]["cluster_id"], articles[1]["cluster_id"])

    def test_similar_titles_more_than_24h_apart_are_not_clustered(self):
        articles = [
            {
                "id": "a",
                "title": "두두원, 5G 코어 솔루션 공급 확대",
                "sources": ["Naver"],
                "published_at": "2026-09-08T09:00:00+09:00",
            },
            {
                "id": "b",
                "title": "두두원 5G 코어 솔루션 공급 확대 나서",
                "sources": ["조선일보"],
                "published_at": "2026-09-09T10:00:01+09:00",
            },
        ]
        cluster.assign_clusters(articles)

        self.assertNotEqual(articles[0]["cluster_id"], articles[1]["cluster_id"])
        self.assertEqual(articles[0]["cluster_size"], 1)
        self.assertEqual(articles[1]["cluster_size"], 1)

    def test_articles_without_parseable_dates_are_left_as_singletons(self):
        articles = [
            {"id": "a", "title": "제목", "sources": [], "published_at": ""},
            {"id": "b", "title": "제목", "sources": [], "published_at": "not-a-date"},
        ]
        cluster.assign_clusters(articles)

        self.assertEqual(articles[0]["cluster_size"], 1)
        self.assertEqual(articles[1]["cluster_size"], 1)

    def test_empty_list_is_a_no_op(self):
        self.assertIsNone(cluster.assign_clusters([]))


if __name__ == "__main__":
    unittest.main()
