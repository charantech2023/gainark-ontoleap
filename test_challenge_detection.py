"""
Bot-protection pages are recognised by vendor, and a crawl that only ever gets one fails
with that vendor's name instead of keeping the challenge as the page's content.
"""

import unittest
from unittest.mock import MagicMock, patch

import scraper
from scraper import BotProtectionBlocked, SmartScraper, challenge_vendor, is_challenge_page

CLOUDFLARE = ('<html><head><title>Just a moment...</title></head><body>'
              '<script>window._cf_chl_opt={cvId:"3"}</script></body></html>')
AWS_WAF = ('<html><head><script>window.gokuProps={"key":"x"}</script>'
           '<script src="https://abc.token.awswaf.com/abc/challenge.js"></script></head><body></body></html>')
DATADOME = '<html><body><iframe src="https://geo.captcha-delivery.com/captcha/?initialCid=x"></iframe></body></html>'
AKAMAI = '<html><body>Access blocked. See https://errors.edgesuite.net/18.abc</body></html>'
HUMAN = '<html><body><div id="px-captcha"></div><p>Press &amp; Hold to confirm</p></body></html>'
IMPERVA = '<html><body><iframe src="/_Incapsula_Resource?SWUDNSAI=31"></iframe></body></html>'
REAL = "<html><head><title>Ordway</title></head><body><h1>Billing</h1></body></html>"


class TestChallengeVendor(unittest.TestCase):

    def test_each_vendor_is_named(self):
        for html, vendor in [(CLOUDFLARE, "Cloudflare"), (AWS_WAF, "AWS WAF"), (DATADOME, "DataDome"),
                             (AKAMAI, "Akamai"), (HUMAN, "HUMAN (PerimeterX)"), (IMPERVA, "Imperva")]:
            with self.subTest(vendor=vendor):
                self.assertEqual(challenge_vendor(html), vendor)
                self.assertTrue(is_challenge_page(200, html))

    def test_a_real_page_is_not_a_challenge(self):
        self.assertIsNone(challenge_vendor(REAL))
        self.assertFalse(is_challenge_page(200, REAL))

    def test_a_large_page_carrying_the_vendor_script_is_not_a_challenge(self):
        # A contact form with a Turnstile box, or a site loading the AWS WAF SDK.
        page = ('<html><body><div class="cf-turnstile"></div>'
                '<script src="https://abc.token.awswaf.com/x/jsapi.js"></script>'
                + "<p>Subscription billing and revenue recognition.</p>" * 2000 + "</body></html>")
        self.assertGreater(len(page), scraper.CHALLENGE_PAGE_MAX_BYTES)
        self.assertIsNone(challenge_vendor(page))
        self.assertFalse(is_challenge_page(200, page))

    def test_blocking_statuses_still_count_without_a_marker(self):
        for status in (403, 429, 503):
            self.assertTrue(is_challenge_page(status, REAL))


def _streamed(status_code, html):
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = {"Content-Type": "text/html; charset=utf-8"}
    resp.encoding = "utf-8"
    resp.iter_content.return_value = [html.encode()]
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False

    def raise_for_status():
        if status_code >= 400:
            import requests
            raise requests.HTTPError("%d Client Error" % status_code)
    resp.raise_for_status.side_effect = raise_for_status
    return resp


class TestLastLevelFailsWithTheVendor(unittest.TestCase):
    """curl_cffi and Crawl4AI are switched off, so the plain requests level decides."""

    def setUp(self):
        cache = MagicMock()
        cache.get.return_value = None
        for p in [
            patch("scraper.get_content_cache", return_value=cache),
            patch("scraper.validate_url_for_fetch", return_value=None),
            patch("scraper._resolve_redirects", side_effect=lambda url, *a, **k: url),
            patch("scraper.CURL_CFFI_AVAILABLE", False),
            patch("scraper.CRAWL4AI_AVAILABLE", False),
        ]:
            p.start()
            self.addCleanup(p.stop)
        self.cache = cache
        self.scraper = SmartScraper(jina_reader=False)

    def fetch(self, status_code, html):
        with patch("scraper.requests.Session") as session:
            session.return_value.get.return_value = _streamed(status_code, html)
            return self.scraper.fetch_html("https://example.com/pricing/")

    def test_challenge_served_with_202_is_not_kept_as_content(self):
        with self.assertRaises(BotProtectionBlocked) as ctx:
            self.fetch(202, AWS_WAF)
        self.assertEqual(ctx.exception.vendor, "AWS WAF")
        self.assertIn("Blocked by AWS WAF (HTTP 202)", str(ctx.exception))
        self.cache.set.assert_not_called()

    def test_challenge_served_with_403_names_the_vendor(self):
        with self.assertRaises(BotProtectionBlocked) as ctx:
            self.fetch(403, CLOUDFLARE)
        self.assertEqual(ctx.exception.vendor, "Cloudflare")

    def test_plain_403_is_still_an_http_error(self):
        import requests
        with self.assertRaises(requests.HTTPError) as ctx:
            self.fetch(403, REAL)
        self.assertNotIsInstance(ctx.exception, BotProtectionBlocked)

    def test_a_page_without_a_charset_is_decoded(self):
        """A streamed body cannot be read again through resp.apparent_encoding."""
        import requests
        resp = requests.Response()
        resp.status_code = 200
        resp.headers["Content-Type"] = "text/html"
        resp.raw = __import__("io").BytesIO("<html><body>Réconciliation Zürich</body></html>".encode("utf-8"))
        with patch("scraper.requests.Session") as session:
            session.return_value.get.return_value = resp
            html = self.scraper.fetch_html("https://example.com/de/")
        self.assertIn("Zürich", html)

    def test_an_error_page_without_a_charset_fails_as_an_http_error(self):
        import requests
        resp = requests.Response()
        resp.status_code = 400
        resp.url = "https://example.com/hc/related/click"
        resp.headers["Content-Type"] = "text/html"
        resp.raw = __import__("io").BytesIO(b"<html><body>Bad request</body></html>")
        with patch("scraper.requests.Session") as session:
            session.return_value.get.return_value = resp
            with self.assertRaises(requests.HTTPError) as ctx:
                self.scraper.fetch_html("https://example.com/hc/related/click")
        self.assertIn("400", str(ctx.exception))

    def test_real_page_is_returned_and_cached(self):
        self.assertEqual(self.fetch(200, REAL), REAL)
        self.cache.set.assert_called_once_with("https://example.com/pricing/", REAL)


if __name__ == "__main__":
    unittest.main()
