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


def _markdown_response(status_code=200, content="# Billing\n\nAcme integrates with **Stripe**.",
                       links=(("Pricing", "https://example.com/pricing"),), body=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.content = body if body is not None else json.dumps({
        "code": 200,
        "data": {"url": "https://example.com/", "title": "Acme Billing", "description": "Billing",
                 "content": content, "links": [list(l) for l in links], "httpStatus": 200,
                 "usage": {"tokens": 1200}},
    }).encode()
    return resp


class TestLiteReads(unittest.TestCase):
    """Markdown reads for the crawl: a small fraction of the tokens, the same text and links.

    Measured 13 Sep 2026 on the same pages: rendered HTML cost 120,000-190,000 tokens a
    page, trimmed markdown 1,200-4,100, and extraction from the markdown kept 92% of the
    concepts the direct HTML yielded while finding more on pages built by script.
    """

    def setUp(self):
        cache = MagicMock()
        cache.get.return_value = None
        for p in (patch("scraper.get_content_cache", return_value=cache),
                  patch("scraper.validate_url_for_fetch", return_value=None),
                  patch("scraper._resolve_redirects", side_effect=_WentLocal)):
            p.start()
            self.addCleanup(p.stop)
        self.cache = cache
        self.scraper = SmartScraper(jina_reader=True)
        self.scraper.jina_api_key = None

    def test_a_trimmed_read_asks_for_markdown_without_the_chrome(self):
        with patch("scraper.requests.post", return_value=_markdown_response()) as post:
            html = self.scraper.fetch_html("https://example.com/features", lite="trimmed")
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["X-Return-Format"], "markdown")
        self.assertEqual(headers["X-Retain-Images"], "none")
        self.assertIn("nav", headers["X-Remove-Selector"])
        self.assertEqual(headers["X-With-Links-Summary"], "all")
        self.assertIn("<title>Acme Billing</title>", html)
        self.assertIn("<strong>Stripe</strong>", html)
        self.assertIn('<a href="https://example.com/pricing">Pricing</a>', html)
        # Cached apart from the full page, so a caller that needs the markup never gets this.
        self.cache.set.assert_called_once_with("https://example.com/features#jina-trimmed", html)

    def test_a_full_lite_read_keeps_the_navigation(self):
        with patch("scraper.requests.post", return_value=_markdown_response()) as post:
            self.scraper.fetch_html("https://example.com/", lite="full")
        self.assertNotIn("X-Remove-Selector", post.call_args.kwargs["headers"])

    def test_the_text_extraction_reads_is_the_page_not_its_link_list(self):
        import trafilatura
        with patch("scraper.requests.post", return_value=_markdown_response(
                content="Acme automates revenue recognition for subscription businesses. " * 8,
                links=[("Careers", "https://example.com/careers")] * 30)):
            html = self.scraper.fetch_html("https://example.com/rev-rec", lite="trimmed")
        text = trafilatura.extract(html) or ""
        self.assertIn("revenue recognition", text)
        self.assertNotIn("Careers", text)

    def test_markup_inside_a_page_cannot_reach_the_document(self):
        with patch("scraper.requests.post", return_value=_markdown_response(
                content='<script>alert(1)</script> text',
                links=[('"><script>x</script>', 'https://example.com/"onmouseover="x')])):
            html = self.scraper.fetch_html("https://example.com/x", lite="trimmed")
        self.assertNotIn("<script>", html)
        self.assertNotIn('"onmouseover="', html)

    def test_a_docs_viewer_page_is_always_read_in_full(self):
        rendered = '<html><body><div class="sl-markdown-viewer"><h1>Overview</h1></div></body></html>'
        with patch("scraper.requests.post", return_value=_jina_response(html=rendered)) as post:
            self.scraper.fetch_html("https://ordwaylabs.stoplight.io/docs/ordway/overview", lite="trimmed")
        self.assertEqual(post.call_args.kwargs["headers"]["X-Return-Format"], "html")

    def test_a_refused_key_is_dropped_and_the_read_continues_without_it(self):
        """13 Sep 2026: the configured key had no balance, and Jina itself still worked."""
        self.scraper.jina_api_key = "k"
        refused = _markdown_response(status_code=402, body=b'{"name":"InsufficientBalanceError"}')
        with patch("scraper.requests.post", side_effect=[refused, _markdown_response(), _markdown_response()]) as post:
            first = self.scraper.fetch_html("https://example.com/a", lite="trimmed")
            self.scraper.fetch_html("https://example.com/b", lite="trimmed")
        sent = [c.kwargs["headers"] for c in post.call_args_list]
        self.assertIn("Authorization", sent[0])
        self.assertNotIn("Authorization", sent[1], "The retry still sent the refused key")
        self.assertNotIn("Authorization", sent[2], "The next page went back to the refused key")
        self.assertIn("Stripe", first)
        self.assertEqual(self.scraper._jina_paused_until, 0.0, "A refused key paused the whole reader")

    def test_the_rate_limit_waits_then_gives_way_to_a_direct_fetch(self):
        limiter = scraper._RateLimiter(rpm=2)
        self.assertTrue(limiter.acquire(0))
        self.assertTrue(limiter.acquire(0))
        self.assertFalse(limiter.acquire(0.1), "A third request inside the minute was allowed")
        self.scraper._jina_limits = {True: limiter, False: limiter}
        with patch("scraper.requests.post") as post, patch.object(scraper, "JINA_RATE_WAIT_SECONDS", 0):
            with self.assertRaises(_WentLocal):
                self.scraper.fetch_html("https://example.com/c", lite="trimmed")
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
