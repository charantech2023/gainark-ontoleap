"""
A directly fetched page, read lite, reaches extraction in the same shape a Jina markdown
read does - so trafilatura cannot keep one block of a page-builder layout and drop the rest.
"""

import unittest
from unittest.mock import MagicMock, patch

import trafilatura

import scraper
from scraper import SmartScraper, lite_page

URL = "https://example.com/ai-llm/"

# The shape of ordwaylabs.com/ai-llm/ on 17 Sep 2026: twenty sibling page-builder rows, the
# security list in one and the largest, an FAQ, last. trafilatura kept only the FAQ row.
_ROW = ('<div class="wpb_row vc_row-fluid vc_row"><div class="row-bg-wrap"><div class="inner-wrap row-bg-layer">'
        '<div class="row-bg viewport-desktop"></div></div></div>'
        '<div class="row_col_wrap_12 col span_12 dark left"><div class="vc_col-sm-12 wpb_column column_container col">'
        '<div class="vc_column-inner"><div class="wpb_wrapper"><div class="wpb_text_column wpb_content_element">'
        '<div class="wpb_wrapper">%s</div></div></div></div></div></div></div>')


def _section(i):
    return ("<h2><strong>%d. Section</strong></h2><h3>Topic %d</h3><p>%s</p>"
            % (i, i, "Ordway handles billing topic %d with care and detail. " % i * 12))


_SECURITY = ('<h2><strong>9. Security</strong></h2><h3>Security and Compliance</h3><ul>'
             '<li><span style="font-weight: 400;">SOC report available on request via the SOC 2 request form</span></li>'
             '<li><span style="font-weight: 400;">GDPR and CCPA</span></li></ul>')
_FAQ = "<h2><strong>Frequently Asked Questions</strong></h2>" + "".join(
    "<h3>Question %d?</h3><p>%s</p>" % (i, "Ordway automates billing and revenue recognition, answer %d. " % i * 6)
    for i in range(25))
PAGE = (
    '<html><head><title>Ordway AI</title><meta name="description" content="AI billing">'
    '<script>var tracking = "Salesforce";</script><style>.x{color:red}</style></head><body>'
    '<header><a href="/pricing">Pricing</a> Top banner</header>'
    '<nav><a href="/customers">Customers</a> Menu</nav>'
    '<div class="container main-content" role="main"><div class="row">'
    + "".join(_ROW % r for r in [_section(0), _section(1), _SECURITY, _section(2), _FAQ])
    + '<img src="/logo.png" alt="Logo alt text"><svg><text>svg label</text></svg>'
    + '</div></div><footer><a href="https://example.com/careers">Careers</a> Footer text</footer>'
    '</body></html>'
)


class TestLitePage(unittest.TestCase):

    def test_extraction_keeps_every_section(self):
        self.assertNotIn("GDPR", trafilatura.extract(PAGE) or "",
                         "Fixture no longer reproduces the dropped section; the test proves nothing")
        text = trafilatura.extract(lite_page(PAGE, URL, "trimmed")) or ""
        self.assertIn("GDPR and CCPA", text)
        self.assertIn("SOC report available", text)
        self.assertIn("Topic 0", text)
        self.assertIn("Question 24", text)

    def test_trimmed_drops_the_chrome_and_non_text(self):
        page = lite_page(PAGE, URL, "trimmed")
        main = page.split(scraper.LITE_MAIN)[1].split("</main>")[0]
        for gone in ("Top banner", "Menu", "Footer text", "tracking", "color:red", "svg label", "logo.png"):
            self.assertNotIn(gone, main)

    def test_trimmed_drops_page_furniture_the_markup_names(self):
        furniture = (
            '<a class="nectar-skip-to-content" href="#main">Skip to main content</a>'
            '<div id="slide-out-widget-area" role="dialog"><p>1707 L St. NW Suite 850</p>'
            '<a href="/about">About us</a></div>'
            '<div id="search-outer"><form role="search"><span>Hit enter to search</span></form></div>'
            '<span class="screen-reader-text">Close Menu</span>'
            '<ol class="breadcrumbs"><li>Home</li><li>Blog</li></ol>'
            '<div class="row related-post-wrap"><h3>You May Also Like</h3><p>Another post title</p></div>'
            '<div id="article-comments"><p>Please sign in to leave a comment.</p></div>'
            '<div class="cookie-consent-banner">We use cookies</div>'
            '<aside class="article-sidebar">Articles in this section</aside>'
        )
        html = PAGE.replace('<div class="row">', '<div class="row">' + furniture, 1)
        page = lite_page(html, URL, "trimmed")
        main = page.split(scraper.LITE_MAIN)[1].split("</main>")[0]
        for gone in ("Skip to main", "1707 L St", "Hit enter", "Close Menu", "Blog", "Another post title",
                     "sign in to leave", "We use cookies", "Articles in this section"):
            self.assertNotIn(gone, main)
        self.assertIn("GDPR and CCPA", main)
        # A link inside removed furniture still reaches the crawl frontier.
        self.assertIn('href="https://example.com/about"', page.split("<nav>")[1])

    def test_furniture_names_do_not_match_inside_words(self):
        html = ('<html><body><div class="research-results"><p>Ordway research on usage-based billing.</p></div>'
                '<div class="social-proof"><p>Trusted by Acme Corp</p></div>'
                '<div class="elementor-widget-text-editor"><p>Revenue recognition for SaaS</p></div>'
                '</body></html>')
        main = lite_page(html, URL, "trimmed").split(scraper.LITE_MAIN)[1]
        for kept in ("Ordway research", "Trusted by Acme", "Revenue recognition"):
            self.assertIn(kept, main)

    def test_a_wrapper_holding_most_of_the_page_is_never_removed(self):
        # ASP.NET WebForms wraps the whole page in a <form>; some themes call it "search-page".
        for wrapper in ('<form id="aspnetForm">%s</form>', '<div class="search-page">%s</div>'):
            html = "<html><body>" + wrapper % "<p>%s</p>" % ("Ordway automates billing. " * 40) + "</body></html>"
            with self.subTest(wrapper=wrapper[:20]):
                self.assertIn("Ordway automates billing", lite_page(html, URL, "trimmed"))

    def test_full_drops_the_chrome_but_keeps_its_links(self):
        """The start page is read full for its menu links, which a direct read keeps anyway."""
        page = lite_page(PAGE, URL, "full")
        main = page.split(scraper.LITE_MAIN)[1].split("</main>")[0]
        self.assertNotIn("Menu", main)
        self.assertNotIn("Footer text", main)
        self.assertEqual(page.split("<nav>")[1], lite_page(PAGE, URL, "trimmed").split("<nav>")[1])

    def test_every_link_survives_absolute_even_when_trimmed(self):
        page = lite_page(PAGE, URL, "trimmed")
        nav = page.split("<nav>")[1]
        self.assertIn('href="https://example.com/pricing"', nav)
        self.assertIn('href="https://example.com/customers"', nav)
        self.assertIn('href="https://example.com/careers"', nav)

    def test_title_and_description_are_kept(self):
        page = lite_page(PAGE, URL, "trimmed")
        self.assertIn("<title>Ordway AI</title>", page)
        self.assertIn('content="AI billing"', page)

    def test_markup_in_the_page_cannot_reach_the_document(self):
        page = lite_page('<html><body><p>&lt;script&gt;alert(1)&lt;/script&gt; text</p>'
                         '<a href=\'x"onmouseover="y\'>go</a></body></html>', URL, "trimmed")
        self.assertNotIn("<script>", page)
        self.assertNotIn('"onmouseover="', page)


class TestDirectLiteRead(unittest.TestCase):
    """Jina off: the page comes from the last level and is converted before it is cached."""

    def setUp(self):
        self.store = {}
        cache = MagicMock()
        cache.get.side_effect = lambda key, **kw: self.store.get(key)
        cache.set.side_effect = lambda key, value: self.store.__setitem__(key, value)
        for p in [
            patch("scraper.get_content_cache", return_value=cache),
            patch("scraper.validate_url_for_fetch", return_value=None),
            patch("scraper._resolve_redirects", side_effect=lambda url, *a, **k: url),
            patch("scraper.CURL_CFFI_AVAILABLE", False),
            patch("scraper.CRAWL4AI_AVAILABLE", False),
        ]:
            p.start()
            self.addCleanup(p.stop)
        self.scraper = SmartScraper(jina_reader=False)

    def _serve(self, html):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {"Content-Type": "text/html; charset=utf-8"}
        resp.encoding = "utf-8"
        resp.iter_content.return_value = [html.encode()]
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        session = patch("scraper.requests.Session")
        mock = session.start()
        self.addCleanup(session.stop)
        mock.return_value.get.return_value = resp
        return mock

    def test_lite_read_is_converted_and_both_forms_are_cached(self):
        self._serve(PAGE)
        page = self.scraper.fetch_html(URL, lite="trimmed")
        self.assertIn(scraper.LITE_MAIN, page)
        self.assertIn("GDPR", trafilatura.extract(page) or "")
        self.assertEqual(self.store[URL], PAGE, "The full page must stay cached for markup callers")
        self.assertEqual(self.store[URL + "#jina-trimmed"], page)

    def test_a_full_read_is_untouched(self):
        self._serve(PAGE)
        self.assertEqual(self.scraper.fetch_html(URL), PAGE)

    def test_a_cached_full_page_is_converted_for_a_lite_caller(self):
        self.store[URL] = PAGE
        session = self._serve("unused")
        page = self.scraper.fetch_html(URL, lite="trimmed")
        session.assert_not_called()
        self.assertIn("GDPR", trafilatura.extract(page) or "")
        self.assertEqual(self.store[URL + "#jina-trimmed"], page)


if __name__ == "__main__":
    unittest.main()
