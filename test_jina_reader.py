"""
Level 0 of the fetch cascade: Jina Reader.

Runs offline. Jina is mocked at the HTTP call, and reaching the local cascade is detected by
the redirect walk it always starts with, so no test depends on a real site.

Run:  python test_jina_reader.py
"""

import asyncio
import json
import unittest
from unittest.mock import MagicMock, patch

import requests

import scraper
from scraper import SmartScraper

PAGE = "<html><head><title>Ordway</title></head><body><h1>Billing</h1></body></html>"


class _WentLocal(Exception):
    """Raised in place of the redirect walk, which is the first step of the local cascade."""


def _jina_response(status_code=200, http_status=200, html=PAGE):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = json.dumps({
        "code": 200,
        "data": {"url": "https://example.com/", "html": html, "httpStatus": http_status},
    }).encode()
    return resp


class TestJinaReader(unittest.TestCase):

    def setUp(self):
        cache = MagicMock()
        cache.get.return_value = None
        patches = [
            patch("scraper.get_content_cache", return_value=cache),
            patch("scraper.validate_url_for_fetch", return_value=None),
            patch("scraper._resolve_redirects", side_effect=_WentLocal),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.cache = cache
        self.scraper = SmartScraper(jina_reader=True)

    def test_page_comes_from_jina_without_touching_the_site(self):
        with patch("scraper.requests.post", return_value=_jina_response()) as post:
            html = self.scraper.fetch_html("https://example.com/customers/")
        self.assertEqual(html, PAGE)
        self.assertEqual(post.call_args.kwargs["json"], {"url": "https://example.com/customers/"})
        self.assertEqual(post.call_args.kwargs["headers"]["X-Return-Format"], "html")
        self.cache.set.assert_called_once_with("https://example.com/customers/", PAGE)

    def test_missing_page_raises_instead_of_returning_the_404_template(self):
        # The comparison-page probe treats any page that does not raise as found.
        with patch("scraper.requests.post", return_value=_jina_response(http_status=404)):
            with self.assertRaises(requests.HTTPError):
                self.scraper.fetch_html("https://example.com/vs/")
        self.cache.set.assert_not_called()

    def test_target_blocking_jina_falls_back_to_a_direct_fetch(self):
        with patch("scraper.requests.post", return_value=_jina_response(http_status=403)):
            with self.assertRaises(_WentLocal):
                self.scraper.fetch_html("https://example.com/")

    def test_challenge_page_from_jina_falls_back(self):
        challenge = "<html><title>Just a moment...</title></html>"
        with patch("scraper.requests.post", return_value=_jina_response(html=challenge)):
            with self.assertRaises(_WentLocal):
                self.scraper.fetch_html("https://example.com/")

    def test_rate_limit_pauses_the_reader(self):
        with patch("scraper.requests.post", return_value=_jina_response(status_code=429)) as post:
            with self.assertRaises(_WentLocal):
                self.scraper.fetch_html("https://example.com/a")
            with self.assertRaises(_WentLocal):
                self.scraper.fetch_html("https://example.com/b")
        self.assertEqual(post.call_count, 1)

    def test_network_error_falls_back(self):
        with patch("scraper.requests.post", side_effect=requests.ConnectionError()):
            with self.assertRaises(_WentLocal):
                self.scraper.fetch_html("https://example.com/")

    def test_machine_readable_documents_bypass_jina(self):
        for url in ("https://example.com/robots.txt",
                    "https://example.com/sitemap.xml",
                    "https://example.com/sitemap_index",
                    "https://example.com/openapi.json"):
            with patch("scraper.requests.post") as post:
                with self.assertRaises(_WentLocal):
                    self.scraper.fetch_html(url)
            post.assert_not_called()

    def test_a_stoplight_page_waits_for_its_article_to_render(self):
        """Without the wait, Stoplight pages came back as a 19-220 character loading shell."""
        rendered = '<html><body><div class="sl-markdown-viewer"><h1>Overview</h1></div></body></html>'
        with patch("scraper.requests.post", return_value=_jina_response(html=rendered)) as post:
            self.scraper.fetch_html("https://ordwaylabs.stoplight.io/docs/ordway/overview", timeout=8)
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["X-Wait-For-Selector"], ".sl-markdown-viewer")
        # The caller's 8s is shorter than a render takes, so the wait is raised, and the
        # client waits longer than Jina does.
        self.assertEqual(headers["X-Timeout"], str(scraper.JINA_RENDER_TIMEOUT))
        self.assertGreater(post.call_args.kwargs["timeout"], scraper.JINA_RENDER_TIMEOUT)

    def test_a_stoplight_shell_is_retried_and_never_kept(self):
        """A shell returned when the wait ran out would otherwise be cached as the page."""
        url = "https://ordwaylabs.stoplight.io/docs/ordway/authentication"
        rendered = '<html><body><div class="sl-markdown-viewer"><p>All requests need tokens.</p></div></body></html>'
        shell = "<html><body><div id='root'>Loading</div></body></html>"

        with patch("scraper.requests.post", side_effect=[_jina_response(html=shell),
                                                          _jina_response(html=rendered)]) as post:
            self.assertEqual(self.scraper.fetch_html(url), rendered)
        self.assertEqual(post.call_count, 2)

        self.cache.reset_mock()
        with patch("scraper.requests.post", return_value=_jina_response(html=shell)) as post:
            with self.assertRaises(_WentLocal):
                self.scraper.fetch_html(url)
        self.assertEqual(post.call_count, 2)
        self.cache.set.assert_not_called()

    def test_other_hosts_do_not_wait_for_a_selector(self):
        for url in ("https://example.com/docs", "https://notstoplight.io/docs",
                    "https://stoplight.io.example.com/docs"):
            with patch("scraper.requests.post", return_value=_jina_response()) as post:
                self.scraper.fetch_html(url, timeout=8)
            headers = post.call_args.kwargs["headers"]
            self.assertNotIn("X-Wait-For-Selector", headers, url)
            self.assertEqual(headers["X-Timeout"], "8", url)

    def test_disabled_reader_is_never_called(self):
        with patch.dict("os.environ", {"ONTOLEAP_JINA_READER": ""}):
            off = SmartScraper()
        with patch("scraper.requests.post") as post:
            with self.assertRaises(_WentLocal):
                off.fetch_html("https://example.com/")
        post.assert_not_called()

    def test_async_path_reads_through_jina_and_keeps_404s(self):
        client = MagicMock()
        client.__aenter__.return_value = client

        async def run(response):
            async def post(*args, **kwargs):
                return response
            client.post = post
            with patch("scraper.httpx.AsyncClient", return_value=client):
                return await self.scraper.fetch_html_async("https://example.com/")

        self.assertEqual(asyncio.run(run(_jina_response())), PAGE)
        with self.assertRaises(requests.HTTPError):
            asyncio.run(run(_jina_response(http_status=404)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
