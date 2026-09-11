"""A partial crawl must not read as a small site.

Every per-page failure is caught and logged, so without counts in the response a run that
lost most of its pages looks exactly like a site that only had a few: low page count, low
coverage. The reader draws the opposite conclusion from the right one - "thin content"
instead of "the crawl broke" - and acts on it.

Nothing here touches the network: fetching and page extraction are both replaced, so the
assertions are about the bookkeeping and not about any particular site staying up.
"""
import site_graph

PAGES = ["/pricing", "/product", "/integrations", "/about", "/blog", "/careers"]

HOME_HTML = "<html><body>" + "".join(
    '<a href="%s">%s</a>' % (p, p) for p in PAGES
) + "</body></html>"

# The two that will fail. One times out, one 404s - different causes, both invisible in
# the old response.
BROKEN = {
    "https://example.com/pricing": TimeoutError("read timed out after 30s"),
    "https://example.com/product": RuntimeError("HTTP 404"),
}


class _FakePageKG:
    nodes = []
    edges = []


def _fake_fetch(url, *args, **kwargs):
    return HOME_HTML


def _fake_build_page_kg(page_url, *args, **kwargs):
    if page_url in BROKEN:
        raise BROKEN[page_url]
    return _FakePageKG()


def test_partial_crawl_is_reported():
    print("\n[1] Testing that lost pages are counted, not just logged...")

    original_fetch = site_graph.smart_fetch
    original_build = site_graph.build_page_kg
    site_graph.smart_fetch = _fake_fetch
    site_graph.build_page_kg = _fake_build_page_kg
    try:
        kg = site_graph.build_site_kg(
            start_url="https://example.com",
            max_pages=4,
            vertical_id="b2b_saas_fintech",
            persist=False,
        )
    finally:
        site_graph.smart_fetch = original_fetch
        site_graph.build_page_kg = original_build

    print("  Discovered :", kg.pages_discovered)
    print("  Limit      :", kg.pages_requested)
    print("  Read       :", kg.pages_crawled)
    print("  Failed     :", kg.pages_failed)
    for f in kg.failed_pages:
        print("    -", f.url, "->", f.error)

    # Six links plus the start page, none of them dropped by the crawl limit. Discovery
    # used to return a capped slice, which made "how much was skipped" unanswerable.
    assert kg.pages_discovered == len(PAGES) + 1, kg.pages_discovered
    assert kg.pages_requested == 4

    # Four selected, two of them broken.
    assert kg.pages_crawled == 2, kg.pages_crawled
    assert kg.pages_failed == 2, kg.pages_failed
    assert kg.pages_crawled + kg.pages_failed == kg.pages_requested

    # page_urls holds what succeeded, so it must not include a page that failed.
    assert set(kg.page_urls) == {"https://example.com", "https://example.com/integrations"}, kg.page_urls

    # The reason travels with the failure. "2 failed" alone does not tell a subscriber
    # whether the site blocked us or the pages are gone.
    failed = {f.url: f.error for f in kg.failed_pages}
    assert set(failed) == set(BROKEN), failed
    assert "timed out" in failed["https://example.com/pricing"]
    assert "404" in failed["https://example.com/product"]
    print("  PASS - a partial crawl is distinguishable from a small site.")


def test_clean_crawl_reports_no_failures():
    print("\n[2] Testing that a clean crawl reports zero failures...")

    original_fetch = site_graph.smart_fetch
    original_build = site_graph.build_page_kg
    site_graph.smart_fetch = _fake_fetch
    site_graph.build_page_kg = lambda page_url, *a, **kw: _FakePageKG()
    try:
        kg = site_graph.build_site_kg(
            start_url="https://example.com",
            max_pages=3,
            vertical_id="b2b_saas_fintech",
            persist=False,
        )
    finally:
        site_graph.smart_fetch = original_fetch
        site_graph.build_page_kg = original_build

    print("  Discovered :", kg.pages_discovered)
    print("  Read       :", kg.pages_crawled)
    print("  Failed     :", kg.pages_failed)

    assert kg.pages_failed == 0
    assert kg.failed_pages == []
    assert kg.pages_crawled == 3
    # Seven found, three read. A caller that sees only "3 pages" would reasonably assume
    # that was the whole site.
    assert kg.pages_discovered > kg.pages_requested
    print("  PASS - the limit, not a failure, explains the unread pages.")


if __name__ == "__main__":
    print("=" * 55)
    print("CRAWL REPORTING TESTS")
    print("=" * 55)
    test_partial_crawl_is_reported()
    test_clean_crawl_reports_no_failures()
    print("\n" + "=" * 55)
    print("ALL CRAWL REPORTING TESTS PASSED")
    print("=" * 55)
