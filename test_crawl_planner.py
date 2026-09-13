"""
Crawl planning: read the pages that carry the product, and stop when they stop adding.

Replaying the stored 40-page crawls page by page showed breadth-first crawling spending
its budget where nothing was left to find. On chargebee.com fifteen customer stories in a
row added one vertical concept; on ordwaylabs.com thirty pages after the product pages
added twelve, while the sitemap's comparison pages were never reached. These tests hold
the planner to what fixed that:

  * the sitemap and the brand's docs hosts are candidates, not only homepage links;
  * translations, other brands, careers and legal pages are never candidates;
  * product pages read before customer stories and blog posts;
  * a share of the budget is a ceiling only while other kinds still have pages;
  * a kind whose pages stop adding anything is set aside, and the crawl ends when no kind
    is still adding - with budget left;
  * the plan survives JSON, so a job resumes anywhere.

Offline: fetching and extraction are replaced.
"""

import json
import threading
import time

import crawl_planner as cp
import site_graph
from models import KGEdge, KGNode

SITE = "www.acme.com"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def test_pages_are_classified_by_what_they_are():
    print("\n[1] Paths classify by their leading section ...")
    cases = {
        "https://www.acme.com/": "home",
        "https://www.acme.com/products/integrations/": "integrations",
        "https://www.acme.com/products/billing-software/": "product",
        "https://www.acme.com/solutions/api": "product",
        "https://www.acme.com/docs/pricing-models": "docs",
        "https://www.acme.com/pricing": "pricing",
        "https://www.acme.com/alternatives/zuora": "comparison",
        "https://www.acme.com/blog/acme-vs-zuora": "editorial",
        "https://www.acme.com/resources/case-studies/globex/": "customers",
        "https://www.acme.com/resources/blog/post": "editorial",
        "https://www.acme.com/resources/guides/arr-guide/": "resources",
        "https://www.acme.com/trust": "security",
        "https://www.acme.com/cpq": "other",
        "https://docs.acme.com/getting-started": "docs",
        "https://apidocs.acme.com/api/v2": "docs",
        "https://support.acme.com/hc/en-us/articles/1": "docs",
    }
    never = [
        "https://www.acme.com/de/pricing", "https://support.acme.com/hc/fr/articles/1",
        "https://www.acme.com/careers/", "https://www.acme.com/company/terms",
        "https://www.acme.com/blog-author/jane", "https://www.acme.com/sitemap",
        "https://app.acme.com/login", "https://www.zuora.com/products", "https://www.acme.com/logo.svg",
        "mailto:hello@acme.com",
    ]
    wrong = {u: cp.classify(u, SITE) for u, want in cases.items() if cp.classify(u, SITE) != want}
    assert not wrong, wrong
    read = [u for u in never if cp.classify(u, SITE) is not None]
    assert not read, read
    print("    %d classified, %d refused" % (len(cases), len(never)))
    print("  PASS")


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def plan_with(urls, source="sitemap"):
    plan = cp.new_plan("https://www.acme.com")
    cp.add_candidates(plan, urls, source, set())
    return plan


def test_product_pages_come_before_stories_and_posts():
    print("\n[2] The batch order follows value, then inlinks ...")
    plan = plan_with(["https://www.acme.com/blog/a", "https://www.acme.com/customers/globex",
                      "https://www.acme.com/products/billing", "https://www.acme.com/pricing",
                      "https://www.acme.com/products/rev-rec"])
    # A page more of the site links to wins a tie within its kind.
    cp.add_candidates(plan, ["https://www.acme.com/products/rev-rec"] * 3, "link", set())
    batch = cp.next_batch(plan, max_pages=150, attempts=0, size=5)
    order = [b["url"].split("acme.com")[1] for b in batch]
    assert order == ["/products/rev-rec", "/products/billing", "/pricing", "/customers/globex", "/blog/a"], order
    print("    %s" % order)
    print("  PASS")


def test_a_ceiling_holds_only_while_other_kinds_have_pages():
    print("\n[3] A kind at its share waits for others, then reads on alone ...")
    stories = ["https://www.acme.com/customers/c%d" % i for i in range(30)]
    plan = plan_with(stories + ["https://www.acme.com/pricing"])
    ceiling = max(1, round(100 * dict(cp.KIND_ORDER)["customers"]))
    first = cp.next_batch(plan, 100, 0, 50)
    kinds = [b["kind"] for b in first]
    assert kinds.count("customers") == ceiling and "pricing" in kinds, kinds
    for b in first:
        cp.record_yield(plan, b["url"], b["kind"], ["new:%s" % b["url"]])
    second = cp.next_batch(plan, 100, len(first), 50)
    assert second and all(b["kind"] == "customers" for b in second), [b["kind"] for b in second]
    print("    first batch %d stories (ceiling %d) + pricing; then stories alone: %d"
          % (kinds.count("customers"), ceiling, len(second)))
    print("  PASS")


def test_a_kind_that_stops_adding_is_set_aside():
    print("\n[4] Pages that add nothing set their kind aside; the crawl then ends ...")
    plan = plan_with(["https://www.acme.com/resources/guides/g%d" % i for i in range(40)])
    reads = 0
    while not cp.exhausted(plan):
        batch = cp.next_batch(plan, 150, reads, 1)
        if not batch:
            break
        reads += 1
        # The first two guides add something; the rest repeat what is already known.
        cp.record_yield(plan, batch[0]["url"], batch[0]["kind"], ["concept:%d" % min(reads, 2)])
    assert reads == 2 + cp.YIELD_PATIENCE, reads
    assert plan["kinds"]["resources"]["set_aside"]
    assert cp.summary(plan)["resources"]["unread"] == 40 - reads
    print("    stopped after %d of 40 with 142 pages of budget left" % reads)
    print("  PASS")


def test_docs_hosts_are_found_once_and_bounded():
    print("\n[5] A linked docs host is reported once, and only a few are followed ...")
    plan = cp.new_plan("https://www.acme.com")
    hosts = cp.add_candidates(plan, ["https://docs.acme.com/a", "https://docs.acme.com/b",
                                     "https://support.acme.com/x", "https://help.acme.com/y",
                                     "https://developer.acme.com/z"], "link", set())
    assert hosts == ["docs.acme.com", "support.acme.com", "help.acme.com"], hosts
    plan["sitemaps"].extend(hosts)
    assert cp.add_candidates(plan, ["https://docs.acme.com/c"], "link", set()) == []
    print("    followed %s" % hosts)
    print("  PASS")


def test_the_plan_survives_json():
    print("\n[6] A plan written out and read back plans the same next batch ...")
    plan = plan_with(["https://www.acme.com/products/a", "https://www.acme.com/docs/b"])
    cp.record_yield(plan, "https://www.acme.com/", "home", ["entity:stripe"])
    copy = json.loads(json.dumps(plan))
    assert [b["url"] for b in cp.next_batch(plan, 10, 1, 5)] == [b["url"] for b in cp.next_batch(copy, 10, 1, 5)]
    print("  PASS")


# ---------------------------------------------------------------------------
# Crawl
# ---------------------------------------------------------------------------

class _PageKG:
    def __init__(self, nodes=(), edges=()):
        self.nodes, self.edges = list(nodes), list(edges)


class _Site:
    """A fake site: a sitemap, pages, and an extractor that finds something on some pages."""

    def __init__(self, sitemap, pages, finds, delay=0.0):
        self.sitemap, self.pages, self.finds, self.delay = sitemap, pages, finds, delay
        self.fetched, self.lite = [], {}
        self.active = self.peak = 0
        self.lock = threading.Lock()

    def fetch(self, url, *args, **kwargs):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(self.delay)
            key = site_graph._page_key(url)
            if key.endswith("sitemap.xml"):
                if not self.sitemap:
                    raise RuntimeError("404")
                return "<urlset>%s</urlset>" % "".join("<url><loc>%s</loc></url>" % u for u in self.sitemap)
            if key.endswith("robots.txt") or key.endswith("sitemap_index.xml"):
                raise RuntimeError("404")
            self.fetched.append(url)
            self.lite[url] = kwargs.get("lite")
            return "<html><body>%s</body></html>" % "".join('<a href="%s">x</a>' % h for h in self.pages.get(key, []))
        finally:
            with self.lock:
                self.active -= 1

    def extract(self, payload, url=None, **kwargs):
        concept = self.finds.get(site_graph._page_key(url))
        if concept is None:
            return _PageKG()
        return _PageKG(edges=[KGEdge(id="e", source="Acme", target=concept, predicate="hasFeature")])


def _run(site, max_pages, **kwargs):
    orig = (site_graph.smart_fetch, site_graph.build_page_kg, site_graph.validate_url_for_fetch)
    site_graph.smart_fetch, site_graph.build_page_kg = site.fetch, site.extract
    site_graph.validate_url_for_fetch = lambda url: None   # no DNS in an offline test
    try:
        state = site_graph.new_crawl_state("https://www.acme.com")
        while not site_graph.crawl_is_complete(state, max_pages):
            before = state["attempts"]
            site_graph.crawl_slice(state, "b2b_saas_fintech", max_pages, **kwargs)
            if state["attempts"] == before:
                break
        return state
    finally:
        site_graph.smart_fetch, site_graph.build_page_kg, site_graph.validate_url_for_fetch = orig


def test_a_crawl_reads_the_sitemap_and_stops_when_nothing_new_turns_up():
    print("\n[7] A crawl reads sitemap-only pages, product first, and stops by yield ...")
    sitemap = (["https://www.acme.com/products/p%d" % i for i in range(4)]
               + ["https://www.acme.com/customers/c%d" % i for i in range(20)]
               + ["https://www.acme.com/alternatives/zuora"])
    finds = {"acme.com/products/p%d" % i: "Feature %d" % i for i in range(4)}
    finds["acme.com/alternatives/zuora"] = "Comparison"
    site = _Site(sitemap, {"acme.com/": []}, finds)
    state = _run(site, max_pages=150)

    read = [u.split("acme.com")[1] for u in state["crawled"]]
    assert "/alternatives/zuora" in read, "A page only the sitemap lists was never read"
    assert read.index("/products/p3") < read.index("/customers/c0"), read
    stories = [r for r in read if r.startswith("/customers")]
    assert len(stories) == cp.YIELD_PATIENCE, stories
    assert state["attempts"] < 150
    assert site.lite["https://www.acme.com"] == "full" and site.lite["https://www.acme.com/products/p0"] == "trimmed"
    json.dumps(state)
    print("    read %d of %d candidates, %d customer stories, stopped with %d budget left"
          % (len(read), len(sitemap) + 1, len(stories), 150 - state["attempts"]))
    print("  PASS")


def test_pages_are_fetched_in_parallel():
    print("\n[8] A batch is fetched concurrently ...")
    sitemap = ["https://www.acme.com/products/p%d" % i for i in range(12)]
    site = _Site(sitemap, {}, {"acme.com/products/p%d" % i: "F%d" % i for i in range(12)}, delay=0.15)
    started = time.monotonic()
    _run(site, max_pages=13)
    elapsed = time.monotonic() - started
    assert site.peak > 1, "Pages were fetched one at a time"
    print("    13 pages at 0.15s each in %.2fs, peak %d at once" % (elapsed, site.peak))
    print("  PASS")


def test_a_page_is_never_read_twice_when_its_batch_links_to_it():
    print("\n[10] Pages in one batch linking to each other are each read once ...")
    sitemap = ["https://www.acme.com/products/p%d" % i for i in range(6)]
    # Every page links to every other, under both host spellings and a trailing slash.
    links = {"acme.com/products/p%d" % i: ["https://acme.com/products/p%d/" % j for j in range(6)]
             + ["https://www.acme.com/products/p%d" % j for j in range(6)] for i in range(6)}
    site = _Site(sitemap, links, {k: "F" + k for k in links})
    state = _run(site, max_pages=20)
    keys = [site_graph._page_key(u) for u in state["crawled"]]
    assert len(keys) == len(set(keys)), keys
    print("    %d pages, %d distinct" % (len(keys), len(set(keys))))
    print("  PASS")


def test_a_state_from_before_planning_resumes():
    print("\n[9] A queue-based state saved by the previous crawler resumes ...")
    site = _Site([], {}, {"acme.com/pricing": "Pricing"})
    old = {"start_url": "https://www.acme.com", "priority": ["https://www.acme.com/pricing"],
           "regular": ["https://www.acme.com/blog/x"], "seen": [], "crawled": ["https://www.acme.com"],
           "failed": [], "nodes": [], "edges": [], "attempts": 1}
    orig = (site_graph.smart_fetch, site_graph.build_page_kg)
    site_graph.smart_fetch, site_graph.build_page_kg = site.fetch, site.extract
    try:
        site_graph.crawl_slice(old, "b2b_saas_fintech", 10)
    finally:
        site_graph.smart_fetch, site_graph.build_page_kg = orig
    assert old["crawled"] == ["https://www.acme.com", "https://www.acme.com/pricing", "https://www.acme.com/blog/x"], old["crawled"]
    assert "priority" not in old and "plan" in old
    print("  PASS")


if __name__ == "__main__":
    print("=" * 78)
    print("CRAWL PLANNING")
    print("=" * 78)
    test_pages_are_classified_by_what_they_are()
    test_product_pages_come_before_stories_and_posts()
    test_a_ceiling_holds_only_while_other_kinds_have_pages()
    test_a_kind_that_stops_adding_is_set_aside()
    test_docs_hosts_are_found_once_and_bounded()
    test_the_plan_survives_json()
    test_a_crawl_reads_the_sitemap_and_stops_when_nothing_new_turns_up()
    test_pages_are_fetched_in_parallel()
    test_a_page_is_never_read_twice_when_its_batch_links_to_it()
    test_a_state_from_before_planning_resumes()
    print("\n" + "=" * 78)
    print("ALL CRAWL PLANNING TESTS PASSED")
    print("=" * 78)
