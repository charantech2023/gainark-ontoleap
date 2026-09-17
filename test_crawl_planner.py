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
import help_center
import scraper
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
    # Two hits earn patience: after 8 reads the rate is 2/8 and 6 misses are not enough
    # (patience 9); after 10 it is 2/10, patience 8, and the 8 misses since set it aside.
    assert reads == 10, reads
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


class _LiteSite(_Site):
    """Pages as a lite read returns them: every product page carries the site's call to
    action, two carry a shared sentence, and each has a line of its own."""

    CTA = "Schedule a 30-minute call with one of our billing and revenue consultants."
    PAIR = "Ordway posts invoices to NetSuite and QuickBooks Online."

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.payloads = {}

    def fetch(self, url, *args, **kwargs):
        page = super().fetch(url, *args, **kwargs)
        key = site_graph._page_key(url)
        if "/products/" not in key:
            return page
        n = int(key.rsplit("p", 1)[1])
        lines = ["Product %d automates revenue recognition for usage plans." % n, self.CTA]
        if n in (0, 1):
            lines.append(self.PAIR)
        return scraper._markdown_page({"title": "P%d" % n, "content": "\n\n".join(lines)})

    def extract(self, payload, url=None, **kwargs):
        self.payloads[site_graph._page_key(url)] = payload
        return super().extract(payload, url, **kwargs)


def test_blocks_a_site_repeats_are_read_once():
    print("\n[11] A block on most of a site's pages reaches extraction once ...")
    sitemap = ["https://www.acme.com/products/p%d" % i for i in range(10)]
    site = _LiteSite(sitemap, {}, {"acme.com/products/p%d" % i: "F%d" % i for i in range(10)})
    state = _run(site, max_pages=11)

    products = [site_graph._page_key(u) for u in state["crawled"] if "/products/" in u]
    assert len(products) == 10, products
    with_cta = [k for k in products if _LiteSite.CTA in site.payloads[k]]
    assert with_cta == products[:1], "The call to action reached extraction on %s" % with_cta
    for k in products:
        n = int(k.rsplit("p", 1)[1])
        assert "Product %d automates" % n in site.payloads[k], "A page lost its own text: %s" % k
    # On two pages only, under the threshold: both keep it.
    assert sum(_LiteSite.PAIR in site.payloads[k] for k in products) == 2
    assert state["repeated_blocks_removed"] == 9, state["repeated_blocks_removed"]
    assert _LiteSite.CTA not in json.dumps(state), "The state holds a client's text, not only hashes"
    print("    call to action kept on 1 of 10 pages, %d repeats dropped" % state["repeated_blocks_removed"])
    print("  PASS")


class _HelpCenterSite(_Site):
    """A site whose homepage links to a Zendesk help centre with a public article API."""

    API_KEY = "support.acme.com/api/v2/help_center/en-us/articles.json"

    def fetch(self, url, *args, **kwargs):
        if site_graph._page_key(url) == self.API_KEY:
            self.fetched.append(url)
            return json.dumps({"next_page": None, "articles": [
                {"title": "Revenue schedules", "section_id": 7, "label_names": ["Rev Rec"],
                 "html_url": "https://support.acme.com/hc/en-us/articles/%d-Revenue" % i,
                 "body": "<p>Article %d</p>" % i, "draft": False, "user_segment_id": None}
                for i in range(3)]})
        return super().fetch(url, *args, **kwargs)


def test_a_help_centre_is_listed_through_its_api():
    print("\n[12] A linked Zendesk help centre is read through its API ...")
    site = _HelpCenterSite([], {"acme.com/": ["https://support.acme.com/hc/en-us"]}, {})
    orig = (help_center.get_content_cache, help_center.validate_url_for_fetch)
    help_center.get_content_cache = lambda: type("C", (), {"set": lambda self, k, v: None})()
    help_center.validate_url_for_fetch = lambda url: None   # no DNS in an offline test
    try:
        state = _run(site, max_pages=1)
    finally:
        help_center.get_content_cache, help_center.validate_url_for_fetch = orig
    plan = state["plan"]
    assert plan["help_centers"] == {"support.acme.com": 3}, plan.get("help_centers")
    articles = [c for c in plan["candidates"].values() if "/articles/" in c["url"]]
    assert len(articles) == 3 and all(c["title"] == "Revenue schedules" for c in articles), articles
    assert all(c["kind"] == "docs" and c["section"] == "7" for c in articles)
    json.dumps(state)
    print("    %d articles listed with titles, before any was read" % len(articles))
    print("  PASS")


def test_pages_naming_missing_concepts_come_first_then_sections_spread():
    print("\n[13] Within a kind: missing vocabulary first, then a spread of sections ...")
    plan = plan_with(
        # Sitemap order puts five release notes first.
        ["https://support.acme.com/hc/en-us/articles/%d-Release-Notes-%d" % (i, i) for i in range(5)]
        + ["https://support.acme.com/hc/en-us/articles/10-Configure-ASC-606-Schedules",
           "https://support.acme.com/hc/en-us/articles/11-Invoice-Templates",
           "https://support.acme.com/hc/en-us/articles/12-Usage-Imports"])
    sections = {0: "notes", 1: "notes", 2: "notes", 3: "notes", 4: "notes", 10: "revrec", 11: "billing", 12: "usage"}
    for cand in plan["candidates"].values():
        n = int(cand["url"].rsplit("/", 1)[1].split("-")[0])
        cp.add_candidates(plan, [cand["url"]], "help-center-api", set(),
                          {cand["url"]: {"section": sections[n]}})
    cp.set_vocabulary(plan, {"Revenue Recognition": "concept:rr", "ASC 606": "concept:rr",
                             "Usage-Based Billing": "concept:ubb", "Invoicing": "concept:inv"})
    # A link's words count: the usage article is linked as "Usage-Based Billing".
    url = "https://support.acme.com/hc/en-us/articles/12-Usage-Imports"
    cp.add_candidates(plan, [url], "link", set(), {url: {"anchor": "Usage-Based Billing"}})
    assert plan["candidates"][cp.page_key(url)]["terms"] == ["concept:ubb"]

    # The graph already has Usage-Based Billing.
    cp.record_yield(plan, "https://www.acme.com/", "home", ["concept:ubb"])
    batch = cp.next_batch(plan, max_pages=150, attempts=0, size=4)
    order = [b["url"].rsplit("/", 1)[1] for b in batch]
    # ASC 606 names a missing concept; then one page from each section not yet read -
    # the usage page before the invoice page because it names a concept the graph has -
    # and only then a second release note.
    assert order == ["10-Configure-ASC-606-Schedules", "12-Usage-Imports",
                     "0-Release-Notes-0", "11-Invoice-Templates"], order
    nxt = [b["url"].rsplit("/", 1)[1] for b in cp.next_batch(plan, 150, 4, 2)]
    assert nxt == ["1-Release-Notes-1", "2-Release-Notes-2"], nxt
    json.dumps(plan)
    print("    %s" % order)
    print("  PASS")


def test_a_state_from_before_scent_still_plans():
    print("\n[14] A plan saved before scent plans without it ...")
    plan = plan_with(["https://www.acme.com/products/a", "https://www.acme.com/products/b"])
    for key in ("vocabulary", "sections_read"):
        plan.pop(key)
    batch = cp.next_batch(plan, 150, 0, 2)
    assert [b["url"].rsplit("/", 1)[1] for b in batch] == ["a", "b"]
    print("  PASS")


def test_patience_grows_with_what_a_kind_has_added():
    print("\n[15] A kind that has been adding gets more patience ...")
    assert cp.patience({"read": 0}) == cp.YIELD_PATIENCE
    assert cp.patience({"read": 10, "hits": 0}) == cp.YIELD_PATIENCE
    assert cp.patience({"read": 18, "hits": 7}) == 11      # the help centre of 17 Sep 2026
    assert cp.patience({"read": 5, "hits": 5}) == 3 * cp.YIELD_PATIENCE
    # A state saved before hits were counted reads as a rate of 0.
    assert cp.patience({"read": 5, "streak": 2, "yield": 1, "set_aside": False}) == cp.YIELD_PATIENCE
    print("  PASS")


def test_a_set_aside_kind_still_reads_pages_naming_what_is_missing():
    print("\n[16] A set-aside kind reads on scent until that stops paying ...")
    plain = ["https://www.acme.com/resources/guides/g%d" % i for i in range(20)]
    named = ["https://www.acme.com/resources/guides/asc-606-part-%d" % i for i in range(5)]
    plan = plan_with(plain + named)
    cp.set_vocabulary(plan, {"ASC 606": "concept:asc606"})
    ks = plan["kinds"].setdefault("resources", {"read": 0, "streak": 0, "yield": 0, "set_aside": False})
    ks.update(read=6, streak=6, set_aside=True)

    batch = cp.next_batch(plan, 150, 6, 10)
    assert batch and all("asc-606" in b["url"] for b in batch), [b["url"] for b in batch]
    assert len(batch) == 5, "Plain guides were read from a set-aside kind"
    # Each named page adds nothing: three in a row close the scent reads.
    for b in batch[:cp.SCENT_PATIENCE]:
        cp.record_yield(plan, b["url"], b["kind"], [])
    assert ks.get("scent_closed"), ks
    for b in batch[cp.SCENT_PATIENCE:]:
        plan["candidates"][cp.page_key(b["url"])] = {k: v for k, v in b.items() if k != "key"}
    assert cp.next_batch(plan, 150, 20, 10) == []
    assert cp.exhausted(plan)
    print("  PASS")


def test_a_scent_page_that_finds_the_concept_stops_the_rest_looking_promising():
    print("\n[17] Once the graph has a concept, pages naming it are no longer scent reads ...")
    named = ["https://www.acme.com/resources/guides/asc-606-part-%d" % i for i in range(4)]
    plan = plan_with(named)
    cp.set_vocabulary(plan, {"ASC 606": "concept:asc606"})
    plan["kinds"]["resources"] = {"read": 6, "streak": 6, "yield": 0, "set_aside": True}
    first = cp.next_batch(plan, 150, 6, 1)
    cp.record_yield(plan, first[0]["url"], "resources", ["concept:asc606"])
    assert cp.next_batch(plan, 150, 7, 5) == [] and cp.exhausted(plan)
    print("  PASS")


def test_scent_knows_registry_entities_as_well_as_concepts():
    print("\n[18] Scent vocabulary: concepts, then registry entities ...")
    from types import SimpleNamespace as NS
    from entity_resolver import normalise_key

    industry = NS(concepts=[NS(id="rev-rec", pref_label="Revenue Recognition", alt_labels=["ASC 606"])])
    entities = {
        "slack": NS(id="slack", status="active", forms=lambda: ["Slack", "Slack Technologies"]),
        "taxjar": NS(id="taxjar", status="active", forms=lambda: ["TaxJar"]),
        "old": NS(id="old", status="merged", forms=lambda: ["Old Name"]),
        # A registry entity sharing a concept's form does not take it over.
        "rr": NS(id="rr", status="active", forms=lambda: ["Revenue Recognition"]),
    }
    index = {normalise_key(f): [e] for e in entities.values() if e.status == "active" for f in e.forms()}
    registry = NS(entities=entities, lookup=lambda key: index.get(key, []))
    forms = site_graph._scent_forms(industry, registry)
    assert forms["Revenue Recognition"] == "concept:rev-rec", forms
    assert forms["ASC 606"] == "concept:rev-rec"
    assert forms["Slack Technologies"] == "entity:slack" and forms["TaxJar"] == "entity:taxjar"
    assert "Old Name" not in forms

    plan = plan_with(["https://www.acme.com/blog/connect-slack-alerts", "https://www.acme.com/blog/q3-update"])
    cp.set_vocabulary(plan, forms)
    batch = cp.next_batch(plan, 150, 0, 1)
    assert batch[0]["url"].endswith("connect-slack-alerts"), batch
    assert site_graph._scent_forms(None, None) == {}
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
    test_blocks_a_site_repeats_are_read_once()
    test_a_help_centre_is_listed_through_its_api()
    test_pages_naming_missing_concepts_come_first_then_sections_spread()
    test_a_state_from_before_scent_still_plans()
    test_patience_grows_with_what_a_kind_has_added()
    test_a_set_aside_kind_still_reads_pages_naming_what_is_missing()
    test_a_scent_page_that_finds_the_concept_stops_the_rest_looking_promising()
    test_scent_knows_registry_entities_as_well_as_concepts()
    test_a_state_from_before_planning_resumes()
    print("\n" + "=" * 78)
    print("ALL CRAWL PLANNING TESTS PASSED")
    print("=" * 78)
