"""What the crawl reached, and what it lost.

Two failures this covers, both silent in the response before:

A partial crawl read as a small site. Every per-page failure is caught and logged, so a
run that lost most of its pages returned a low page count and a low coverage score -
indistinguishable from a site that is genuinely thin, and calling for the opposite
conclusion.

A deep page read as absent content. Coverage is computed from the crawled graph, so an
industry concept on a page the crawl never reached is reported as a gap the subscriber
does not have. Discovery followed links from the start page only, which made everything
below the second level invisible.

Nothing here touches the network: fetching and page extraction are both replaced, so the
assertions are about crawl behaviour and not about any particular site staying up.
"""
import site_graph

HOME = "https://example.com"

# A site whose interesting pages are NOT all linked from the homepage. /product/billing is
# reachable only via /product, so a crawl that reads links from the start page alone can
# never see it - and would report everything on it as missing content.
LINKS = {
    "example.com/": ["/pricing", "/product", "/about", "/blog"],
    "example.com/product": ["/product/billing", "/product/revenue"],
    "example.com/pricing": ["/pricing/enterprise"],
    "example.com/product/billing": ["/product"],          # back-link, already seen
    "example.com/about": [],
    "example.com/blog": [],
    "example.com/product/revenue": [],
    "example.com/pricing/enterprise": [],
}

# One fails to fetch, one fetches and fails to extract. Different causes, and before this
# the response showed neither.
UNFETCHABLE = {"https://example.com/about": TimeoutError("read timed out after 30s")}
UNEXTRACTABLE = {"https://example.com/blog": RuntimeError("no parseable content")}


class _FakePageKG:
    nodes = []
    edges = []


def _html_for(url):
    hrefs = LINKS.get(site_graph._page_key(url), [])
    return "<html><body>" + "".join('<a href="%s">x</a>' % h for h in hrefs) + "</body></html>"


def _fake_fetch(url, *args, **kwargs):
    if url in UNFETCHABLE:
        raise UNFETCHABLE[url]
    return _html_for(url)


def _fake_build_page_kg(payload, url=None, **kwargs):
    # The crawler must hand over HTML it already holds. Passing the URL would make this
    # refetch the page, doubling every crawl.
    assert not payload.startswith("http"), (
        "crawler passed a URL to build_page_kg; it should pass the HTML it fetched")
    if url in UNEXTRACTABLE:
        raise UNEXTRACTABLE[url]
    return _FakePageKG()


def _crawl(max_pages):
    original_fetch = site_graph.smart_fetch
    original_build = site_graph.build_page_kg
    site_graph.smart_fetch = _fake_fetch
    site_graph.build_page_kg = _fake_build_page_kg
    try:
        return site_graph.build_site_kg(
            start_url=HOME, max_pages=max_pages,
            vertical_id="b2b_saas_fintech", persist=False,
        )
    finally:
        site_graph.smart_fetch = original_fetch
        site_graph.build_page_kg = original_build


def test_crawl_reaches_pages_below_the_start_page():
    print("\n[1] Testing that the crawl follows links from pages it reads...")
    kg = _crawl(max_pages=8)

    print("  Read      :", kg.pages_crawled)
    for u in kg.page_urls:
        print("    -", u)

    # The point of the change. Neither of these is linked from the homepage; both are
    # reachable only through /product, and both were unreachable before.
    assert "https://example.com/product/billing" in kg.page_urls, kg.page_urls
    assert "https://example.com/product/revenue" in kg.page_urls, kg.page_urls
    assert "https://example.com/pricing/enterprise" in kg.page_urls, kg.page_urls

    # High-value paths keep their head start: /pricing and /product are read before
    # /about and /blog, so a truncated crawl still sees the pages that carry the product.
    assert kg.page_urls[0] == HOME
    assert set(kg.page_urls[1:3]) == {"https://example.com/pricing", "https://example.com/product"}, kg.page_urls

    # A back-link to an already-read page must not be read twice.
    assert len(kg.page_urls) == len(set(kg.page_urls)), kg.page_urls
    print("  PASS - pages below the start page are reachable.")


def test_partial_crawl_is_reported():
    print("\n[2] Testing that lost pages are counted, not just logged...")
    # Large enough to outlast the priority queue: /about and /blog are ordinary paths, so
    # a smaller budget is spent on product pages before either is ever attempted.
    kg = _crawl(max_pages=8)

    print("  Discovered :", kg.pages_discovered)
    print("  Budget     :", kg.pages_requested)
    print("  Read       :", kg.pages_crawled)
    print("  Failed     :", kg.pages_failed)
    for f in kg.failed_pages:
        print("    -", f.url, "->", f.error)

    # The budget is spent on attempts, so a failure costs a page. Otherwise a site that
    # fails half its fetches would quietly fetch twice what was asked for.
    assert kg.pages_crawled + kg.pages_failed == kg.pages_requested == 8
    assert kg.pages_failed == 2, kg.pages_failed

    # page_urls holds what succeeded, so a failed page must not appear in it.
    failed_urls = {f.url for f in kg.failed_pages}
    assert failed_urls == set(UNFETCHABLE) | set(UNEXTRACTABLE), failed_urls
    assert not failed_urls & set(kg.page_urls)

    # The reason travels with the failure, and says which half of the job broke. "2 failed"
    # alone does not tell a subscriber whether the site blocked us or the pages are junk.
    reasons = {f.url: f.error for f in kg.failed_pages}
    assert reasons["https://example.com/about"].startswith("fetch failed"), reasons
    assert "timed out" in reasons["https://example.com/about"]
    assert reasons["https://example.com/blog"].startswith("extraction failed"), reasons
    print("  PASS - a partial crawl is distinguishable from a small site.")


def test_discovery_counts_more_than_it_reads():
    print("\n[3] Testing that the unread remainder is visible...")
    kg = _crawl(max_pages=3)

    print("  Discovered :", kg.pages_discovered)
    print("  Read       :", kg.pages_crawled)

    # Three read, but the crawl saw further than it went. A caller shown only "3 pages"
    # would reasonably assume that was the whole site.
    assert kg.pages_crawled == 3
    assert kg.pages_discovered > kg.pages_crawled, kg.pages_discovered
    print("  PASS - the limit, not the site, explains the unread pages.")


def test_query_strings_do_not_multiply_a_page():
    print("\n[4] Testing that one page with many query strings is read once...")
    original = LINKS["example.com/"]
    LINKS["example.com/"] = ["/pricing?utm=a", "/pricing?utm=b", "/pricing#plans", "/pricing/"]
    try:
        kg = _crawl(max_pages=6)
    finally:
        LINKS["example.com/"] = original

    print("  Read      :", kg.page_urls)
    # Tracking parameters and fragments address the same page. Treating them as distinct
    # would let a crawl spend its whole budget going nowhere.
    pricing = [u for u in kg.page_urls if site_graph._page_key(u) == "example.com/pricing"]
    assert len(pricing) == 1, pricing
    assert len(kg.page_urls) == len(set(kg.page_urls)), kg.page_urls
    print("  PASS - query strings and fragments do not multiply a page.")


if __name__ == "__main__":
    print("=" * 55)
    print("CRAWL REPORTING TESTS")
    print("=" * 55)
    test_crawl_reaches_pages_below_the_start_page()
    test_partial_crawl_is_reported()
    test_discovery_counts_more_than_it_reads()
    test_query_strings_do_not_multiply_a_page()
    print("\n" + "=" * 55)
    print("ALL CRAWL REPORTING TESTS PASSED")
    print("=" * 55)
