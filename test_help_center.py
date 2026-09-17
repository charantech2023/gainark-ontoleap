"""
A Zendesk help centre is read whole through its public API, each article cached as a
small page, so the crawl reads the articles it chooses without fetching them.
"""

import json
import unittest
from unittest.mock import patch

import trafilatura

import crawl_planner
import scraper
from help_center import read_zendesk, zendesk_locale

HOST = "support.acme.com"
API = "https://support.acme.com/api/v2/help_center/en-us/articles.json?per_page=100"
PAGE_2 = "https://support.acme.com/api/v2/help_center/en-us/articles.json?page=2&per_page=100"


def _article(n, **extra):
    article = {
        "id": n, "title": "Article %d" % n, "section_id": 700 + n % 2,
        "html_url": "https://support.acme.com/hc/en-us/articles/%d-Article-%d" % (n, n),
        "body": "<p>Acme posts revenue schedules to NetSuite for article %d.</p>"
                "<p>See <a href=\"/hc/en-us/articles/9\">related</a>.</p>" % n,
        "label_names": ["Revenue Recognition"], "draft": False, "user_segment_id": None,
    }
    article.update(extra)
    return article


class _Api:
    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def __call__(self, url, **kwargs):
        self.asked.append(url)
        if url not in self.pages:
            raise RuntimeError("404 Client Error for url: %s" % url)
        return json.dumps(self.pages[url])


class TestReadZendesk(unittest.TestCase):

    def setUp(self):
        self.store = {}
        cache = unittest.mock.MagicMock()
        cache.set.side_effect = lambda key, value: self.store.__setitem__(key, value)
        for p in (patch("help_center.get_content_cache", return_value=cache),
                  patch("help_center.validate_url_for_fetch", return_value=None)):
            p.start()
            self.addCleanup(p.stop)

    def test_every_page_of_articles_is_read_and_cached(self):
        api = _Api({
            API: {"articles": [_article(1), _article(2)], "next_page": PAGE_2},
            PAGE_2: {"articles": [_article(3)], "next_page": None},
        })
        articles = read_zendesk(HOST, fetch=api)
        self.assertEqual([a["title"] for a in articles], ["Article 1", "Article 2", "Article 3"])
        self.assertEqual(articles[0]["labels"], "Revenue Recognition")
        self.assertEqual(articles[0]["section"], "701")
        source = self.store[scraper.source_cache_key(articles[0]["url"])]
        self.assertIn("<title>Article 1</title>", source)
        page = scraper.lite_page(source, articles[0]["url"], "trimmed")
        self.assertIn("revenue schedules to NetSuite for article 1", trafilatura.extract(page))
        # Links in the body reach the crawl frontier, absolute.
        self.assertIn('href="https://support.acme.com/hc/en-us/articles/9"', page)

    def test_drafts_restricted_and_foreign_articles_are_skipped(self):
        api = _Api({API: {"articles": [
            _article(1, draft=True),
            _article(2, user_segment_id=123),
            _article(3, html_url="https://evil.example/hc/en-us/articles/3"),
            "not an article",
            _article(4),
        ], "next_page": None}})
        self.assertEqual([a["title"] for a in read_zendesk(HOST, fetch=api)], ["Article 4"])
        self.assertEqual(len(self.store), 1)

    def test_a_next_page_on_another_host_is_not_followed(self):
        api = _Api({API: {"articles": [_article(1)],
                          "next_page": "http://169.254.169.254/latest/meta-data"}})
        self.assertEqual(len(read_zendesk(HOST, fetch=api)), 1)
        self.assertEqual(api.asked, [API])

    def test_a_host_without_the_api_gives_nothing(self):
        for pages in ({}, {API: "<html>not json</html>"}, {API: {"sections": []}}):
            with self.subTest(pages=str(pages)[:30]):
                api = _Api(pages)
                if pages.get(API) == "<html>not json</html>":
                    api = lambda url, **kw: "<html>not json</html>"
                self.assertEqual(read_zendesk(HOST, fetch=api), [])
        self.assertEqual(self.store, {})

    def test_the_locale_comes_from_the_hosts_own_urls(self):
        urls = ["https://support.acme.com/hc/en-gb/articles/1",
                "https://support.acme.com/hc/en-gb/articles/2",
                "https://support.acme.com/hc/de/articles/3",
                "https://help.other.com/hc/fr/articles/4"]
        self.assertEqual(zendesk_locale(urls, HOST), "en-gb")
        self.assertEqual(zendesk_locale([], HOST), "en-us")


class TestAnArticleIsReadFromItsSource(unittest.TestCase):
    """A lite read of an article the API cached converts it; it never fetches the page."""

    def test_a_lite_read_converts_the_cached_article(self):
        url = "https://support.acme.com/hc/en-us/articles/1-Article-1"
        store = {scraper.source_cache_key(url): "<html><head><title>A</title></head><body>"
                 "<h1>A</h1><p>Acme posts revenue schedules to NetSuite.</p></body></html>"}
        cache = unittest.mock.MagicMock()
        cache.get.side_effect = lambda key, **kw: store.get(key)
        cache.set.side_effect = lambda key, value: store.__setitem__(key, value)
        with patch("scraper.get_content_cache", return_value=cache), \
                patch("scraper.validate_url_for_fetch", return_value=None), \
                patch("scraper._resolve_redirects", side_effect=AssertionError("fetched the page")):
            reader = scraper.SmartScraper(jina_reader=True)
            with patch("scraper.requests.post", side_effect=AssertionError("asked Jina")):
                page = reader.fetch_html(url, lite="trimmed")
                self.assertIn("revenue schedules", trafilatura.extract(page))
                self.assertEqual(store[scraper.lite_cache_key(url, "trimmed")], page)
                # A full read needs the page's own markup, which the API does not give.
                with self.assertRaises(AssertionError):
                    reader.fetch_html(url)


class TestCandidatesKeepWhatTheApiKnows(unittest.TestCase):

    def test_title_section_and_labels_are_kept_and_merged(self):
        plan = crawl_planner.new_plan("https://www.acme.com")
        url = "https://support.acme.com/hc/en-us/articles/1-Article-1"
        crawl_planner.add_candidates(plan, [url], "sitemap", set())
        crawl_planner.add_candidates(plan, [url], "help-center-api", set(),
                                     {url: {"url": url, "title": "Article 1", "section": "701", "labels": ""}})
        cand = plan["candidates"][crawl_planner.page_key(url)]
        self.assertEqual((cand["title"], cand["section"], cand["kind"]), ("Article 1", "701", "docs"))
        self.assertNotIn("labels", cand, "An empty value overwrote nothing and should not be stored")
        json.dumps(plan)


if __name__ == "__main__":
    unittest.main()
