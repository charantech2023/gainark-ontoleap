"""Evidence-backed industry discovery.

Discovery used to infer a customer profile from a single homepage hero section, and the
prompt handed the model 'Enterprise', 'Mid-Market', 'SMB' as examples of what to return -
so that is what came back, for every domain, with nothing tying it to the site. These
tests cover the replacement: read the pages that carry buyer evidence, make every buyer
claim cite one of them, and keep only the claims whose quote is actually on the page cited.

Fetching and the LLM are both replaced here, so nothing touches the network.
"""
import contextlib
import json
import os
import shutil
import tempfile

import industry_ontology
import industry_profiler as ip


HOME_HTML = """
<html><head><title>Acme Billing</title>
<meta name="description" content="Billing for B2B SaaS"></head>
<body>
<h1>Billing that scales</h1>
<a href="/pricing">Pricing</a>
<a href="/customers/globex-case-study">Globex case study</a>
<a href="/blog/hello-world">Blog</a>
<a href="/compare/vs-zuora">Acme vs Zuora</a>
<a href="https://twitter.com/acme">Twitter</a>
<a href="/brochure.pdf">Brochure</a>
<a href="#top">Top</a>
<p>Acme automates invoicing and revenue recognition.</p>
</body></html>
"""

CASE_STUDY_HTML = """
<html><head><title>Globex case study</title></head><body>
<h1>Globex moves off spreadsheets</h1>
<p>Globex is a 400-person medical devices manufacturer running three subsidiaries.
Before Acme, the finance team maintained revenue schedules in Excel.</p>
</body></html>
"""

COMPARE_HTML = """
<html><head><title>Acme vs Zuora</title></head><body>
<h1>Acme vs Zuora</h1>
<p>Teams migrating from Zuora choose Acme for multi-entity consolidation.</p>
</body></html>
"""

PRICING_HTML = "<html><head><title>Pricing</title></head><body><h1>Pricing</h1></body></html>"

# A marketing site whose mega-menu sits in <div>s rather than <nav>. Stripping tags by
# name leaves the menu in the text, and it is long enough to fill the whole window before
# the page's own prose begins - which is how a live run against chargebee.com came back
# with six case studies read and nothing quotable on any of them.
MEGA_MENU = " ".join(
    "<a href='/p/%d'>Billing for Agents Price agent actions Usage-based Provisioning "
    "Revenue Recognition Explore MCP Browse Recipes</a>" % i for i in range(40)
)

BOILERPLATE_HEAVY_HTML = """
<html><head><title>How Whereby navigates AI pricing</title></head><body>
<div class="mega-menu"><h2>Products</h2><h2>Solutions</h2>%s</div>
<article>
<h1>How Whereby navigates AI pricing</h1>
<p>Whereby is a video API company of about 90 people serving embedded developers.
Automating revenue recognition eliminated the spreadsheet work that consumed the finance
team every month, and month-end close now runs in days rather than weeks.</p>
<p>Before Chargebee, billing ownership sat entirely with finance, and every usage-based
plan change required a manual reconciliation across three separate systems.</p>
</article>
</body></html>
""" % MEGA_MENU


DEFAULT_SITE = {
    "https://acme.com": HOME_HTML,
    "https://acme.com/pricing": PRICING_HTML,
    "https://acme.com/customers/globex-case-study": CASE_STUDY_HTML,
    "https://acme.com/compare/vs-zuora": COMPARE_HTML,
}


@contextlib.contextmanager
def patched(*swaps):
    """Swap (module, attribute, replacement) for the duration of the block."""
    saved = [(mod, name, getattr(mod, name)) for mod, name, _ in swaps]
    try:
        for mod, name, value in swaps:
            setattr(mod, name, value)
        yield
    finally:
        for mod, name, value in saved:
            setattr(mod, name, value)


def fake_site(pages=None, fail=(), fetched=None):
    """Serve canned HTML for known paths; raise for anything in `fail` or unknown.

    Pass a list as `fetched` to record every URL the code under test asked for.
    """
    served = DEFAULT_SITE if pages is None else pages

    def fake_fetch(url, timeout=None):
        if fetched is not None:
            fetched.append(url)
        if url in fail:
            raise RuntimeError("boom")
        if url not in served:
            raise RuntimeError("404 %s" % url)
        return served[url]

    # validate_url_for_fetch resolves hostnames against real DNS otherwise.
    return patched((ip, "smart_fetch", fake_fetch),
                   (ip, "validate_url_for_fetch", lambda url: None))


def sample_pages():
    """Two evidence pages, in the shape the gatherer produces."""
    return [
        {"url": "https://acme.com/customers/globex-case-study",
         "title": "Globex case study", "meta_description": "",
         "headings": ["Globex moves off spreadsheets"],
         "body_snippet": "Globex is a 400-person medical devices manufacturer running three "
                         "subsidiaries. Before Acme, the finance team maintained revenue "
                         "schedules in Excel."},
        {"url": "https://acme.com/compare/vs-zuora",
         "title": "Acme vs Zuora", "meta_description": "", "headings": ["Acme vs Zuora"],
         "body_snippet": "Teams migrating from Zuora choose Acme for multi-entity consolidation."},
        {"url": "https://acme.com/customers/fmg",
         "title": "FMG case study", "meta_description": "", "headings": [],
         "body_snippet": "Discover FMG's transformational journey of switching from a legacy "
                         "subscription billing platform to Acme. A five-person finance team "
                         "invoiced 4,500+ merchants manually every two weeks."},
    ]


# ---------------------------------------------------------------------- link ranking

def test_evidence_links_rank_buyer_pages_above_generic_ones():
    links = ip._rank_evidence_links("https://acme.com", HOME_HTML, limit=10)
    assert links, "homepage links should be discovered"
    # A case study outranks a comparison page, which outranks pricing, which outranks
    # an unclassified blog post.
    assert links[0].endswith("/customers/globex-case-study"), links
    assert links.index("https://acme.com/compare/vs-zuora") < links.index("https://acme.com/pricing")
    assert links[-1].endswith("/blog/hello-world"), links
    print("  Ranked:", [u.replace("https://acme.com", "") for u in links])


def test_evidence_links_exclude_offsite_assets_and_anchors():
    links = ip._rank_evidence_links("https://acme.com", HOME_HTML, limit=10)
    assert not any("twitter.com" in u for u in links), links
    assert not any(u.endswith(".pdf") for u in links), links
    assert not any("#" in u for u in links), links
    assert "https://acme.com" not in links, "the start page is not its own evidence link"


def test_evidence_links_respect_the_limit():
    assert len(ip._rank_evidence_links("https://acme.com", HOME_HTML, limit=2)) == 2
    assert ip._rank_evidence_links("https://acme.com", HOME_HTML, limit=0) == []


# ------------------------------------------------------------------ evidence gather

def test_gather_reads_homepage_plus_linked_evidence_pages():
    with fake_site():
        evidence = ip.gather_discovery_evidence("https://acme.com", max_pages=4)

    urls = [p["url"] for p in evidence["pages"]]
    assert urls[0] == "https://acme.com", "homepage first"
    assert "https://acme.com/customers/globex-case-study" in urls, urls
    assert evidence["pages_read"] == 4, urls
    assert evidence["pages_failed"] == 0

    case_study = next(p for p in evidence["pages"] if "globex" in p["url"])
    assert "400-person medical devices manufacturer" in case_study["body_snippet"]
    print("  Read %d pages: %s" % (evidence["pages_read"],
                                   [u.replace("https://acme.com", "") or "/" for u in urls]))


def test_gather_survives_a_failing_sub_page():
    with fake_site(fail={"https://acme.com/customers/globex-case-study"}):
        evidence = ip.gather_discovery_evidence("https://acme.com", max_pages=4)

    assert evidence["pages_failed"] == 1
    assert evidence["pages_read"] == 3
    assert all("globex" not in p["url"] for p in evidence["pages"])


def test_gather_degrades_when_the_homepage_is_unreachable():
    with fake_site(pages={}):
        evidence = ip.gather_discovery_evidence("https://acme.com")

    assert evidence["pages_read"] == 0
    assert evidence["pages_failed"] == 1
    assert len(evidence["pages"]) == 1, "a stub page keeps the prompt well-formed"


def test_gather_adds_a_scheme_when_the_caller_omits_one():
    with fake_site():
        evidence = ip.gather_discovery_evidence("acme.com", max_pages=1)
    assert evidence["url"] == "https://acme.com"
    assert evidence["domain"] == "acme.com"



def test_main_content_is_read_instead_of_the_navigation_menu():
    """The live failure this guards: a case study arriving as a list of menu items."""
    page = ip._summarize_html("https://acme.com/customers/whereby",
                              "acme.com", BOILERPLATE_HEAVY_HTML, word_cap=350)

    assert "eliminated the spreadsheet work" in page["body_snippet"], page["body_snippet"][:200]
    assert "Browse Recipes" not in page["body_snippet"], "the mega-menu is boilerplate"
    assert "Products" not in page["headings"] and "Solutions" not in page["headings"], page["headings"]
    print("  Snippet opens: %s..." % page["body_snippet"][:70])


def test_thin_pages_fall_back_to_raw_text_rather_than_returning_nothing():
    thin = "<html><head><title>Pricing</title></head><body><h1>Pricing</h1>"            "<p>Plans start at $299.</p></body></html>"
    page = ip._summarize_html("https://acme.com/pricing", "acme.com", thin)
    assert "299" in page["body_snippet"], page["body_snippet"]


# ------------------------------------------------------------------------- prompt

def test_prompt_carries_every_page_url():
    with fake_site():
        evidence = ip.gather_discovery_evidence("https://acme.com", max_pages=4)
    prompt = ip.build_discovery_prompt(evidence, brand_hint="Acme")

    for page in evidence["pages"]:
        assert page["url"] in prompt, "a claim can only cite a URL the prompt showed"
    assert "Acme" in prompt


def test_prompt_no_longer_supplies_the_generic_buyer_answers():
    """The regression guard. Offering these as examples is what produced them as output."""
    with fake_site():
        evidence = ip.gather_discovery_evidence("https://acme.com", max_pages=2)
    prompt = ip.build_discovery_prompt(evidence)

    for leading_example in ("Mid-Market", "SMB", "High-Growth Startups", "Manual Spreadsheets",
                            "Excel-Based Reporting", "Manual Approvals"):
        assert leading_example not in prompt, (
            "%r is handed back verbatim by the model when the prompt suggests it"
            % leading_example
        )
    assert "empty list is a CORRECT answer" in prompt


def test_prompt_asks_for_one_object_with_the_buyer_keys_inside_it():
    """A live run returned only the category keys.

    The prompt had shown two separate JSON objects, one per group, so the model answered
    the first schema and stopped - the buyer keys came back absent rather than empty, and
    every downstream check saw a site with no buyer evidence on it.
    """
    with fake_site():
        evidence = ip.gather_discovery_evidence("https://acme.com", max_pages=2)
    prompt = ip.build_discovery_prompt(evidence)

    assert "ONE valid, raw JSON object" in prompt
    assert "do not omit" in prompt.lower()

    # Every buyer key sits between the first and last category key, so there is one object.
    first_category = prompt.index('"brand_name"')
    last_category = prompt.rindex('"summary"')
    for field in ip.ICP_FIELDS:
        where = prompt.index('"%s"' % field)
        assert first_category < where < last_category, (
            "%s is outside the object the model is asked to return" % field
        )

    # And each is shown in the cite-and-quote shape, not as a bare list of strings.
    buyer_block = prompt[prompt.index('"known_segments"'):prompt.rindex('"summary"')]
    assert buyer_block.count('"source_url"') >= len(ip.ICP_FIELDS)
    assert buyer_block.count('"quote"') >= len(ip.ICP_FIELDS)


# ------------------------------------------------------------- claim verification

def test_verified_claim_keeps_its_evidence():
    values, evidence, stats = ip.split_evidence_claims([
        {"value": "400-person multi-entity manufacturer",
         "source_url": "https://acme.com/customers/globex-case-study",
         "quote": "Globex is a 400-person medical devices manufacturer running three subsidiaries."}
    ], sample_pages())

    assert values == ["400-person multi-entity manufacturer"]
    assert evidence["400-person multi-entity manufacturer"]["source_url"].endswith("globex-case-study")
    assert stats == {"proposed": 1, "evidenced": 1, "bad_url": 0,
                     "quote_not_found": 0, "unsupported": 0}, stats


def test_claim_citing_a_page_we_never_read_loses_its_evidence():
    values, evidence, stats = ip.split_evidence_claims([
        {"value": "Enterprise", "source_url": "https://acme.com/invented-page",
         "quote": "Acme serves enterprises."}
    ], sample_pages())

    assert values == ["Enterprise"], "the proposal survives, so the caller can see it"
    assert evidence == {}, "but nothing backs it"
    assert stats["bad_url"] == 1 and stats["evidenced"] == 0, stats


def test_claim_quoting_text_absent_from_the_cited_page_loses_its_evidence():
    values, evidence, stats = ip.split_evidence_claims([
        {"value": "Fortune 500", "source_url": "https://acme.com/compare/vs-zuora",
         "quote": "Acme is trusted by hundreds of Fortune 500 companies."}
    ], sample_pages())

    assert values == ["Fortune 500"]
    assert evidence == {}
    assert stats["quote_not_found"] == 1, stats


def test_quote_matching_survives_whitespace_and_curly_quotes():
    _, evidence, _ = ip.split_evidence_claims([
        {"value": "moved off Zuora", "source_url": "https://acme.com/compare/vs-zuora",
         "quote": "Teams   migrating from Zuora\nchoose Acme"}
    ], sample_pages())
    assert evidence.get("moved off Zuora"), "whitespace differences are not a forgery"


def test_bare_strings_are_kept_as_unevidenced_values():
    values, evidence, stats = ip.split_evidence_claims(["Healthcare", "Manufacturing"], sample_pages())
    assert values == ["Healthcare", "Manufacturing"]
    assert evidence == {}
    assert stats["proposed"] == 2 and stats["evidenced"] == 0


def test_duplicate_claims_are_collapsed():
    values, _, stats = ip.split_evidence_claims(["Healthcare", "healthcare", "Healthcare"], sample_pages())
    assert values == ["Healthcare"]
    assert stats["proposed"] == 1


def test_malformed_items_are_ignored():
    values, evidence, stats = ip.split_evidence_claims(
        [None, 42, {}, {"value": ""}, {"source_url": "x"}], sample_pages())
    assert values == [] and evidence == {} and stats["proposed"] == 0


def test_resolve_buyer_fields_totals_across_every_field():
    discovered = {
        "known_segments": [{"value": "400-person multi-entity manufacturer",
                            "source_url": "https://acme.com/customers/globex-case-study",
                            "quote": "Globex is a 400-person medical devices manufacturer"}],
        "known_industries": ["Medical Devices"],
        "known_competitors": [{"value": "Zuora", "source_url": "https://acme.com/compare/vs-zuora",
                               "quote": "Teams migrating from Zuora"}],
        "known_replaces": [{"value": "revenue schedules in Excel",
                            "source_url": "https://acme.com/customers/globex-case-study",
                            "quote": "maintained revenue schedules in Excel"}],
    }
    values, evidence, stats = ip.resolve_buyer_fields(discovered, sample_pages())

    assert values["known_industries"] == ["Medical Devices"]
    assert set(evidence) == {"known_segments", "known_competitors", "known_replaces"}
    assert stats["proposed"] == 4 and stats["evidenced"] == 3, stats
    assert stats["evidence_ratio"] == 0.75
    print("  %d of %d buyer claims verified (ratio %.2f)"
          % (stats["evidenced"], stats["proposed"], stats["evidence_ratio"]))



def test_a_real_quote_that_does_not_support_the_claim_is_rejected():
    """Straight from a live run: the model named Zuora and cited a sentence that does not.

    The sentence is genuinely on the page, so the quote check passes it. Only comparing the
    claim against its own quote catches an inferred answer wearing a real citation.
    """
    values, evidence, stats = ip.split_evidence_claims([
        {"value": "Zuora", "source_url": "https://acme.com/customers/fmg",
         "quote": "Discover FMG's transformational journey of switching from a legacy "
                  "subscription billing platform to Acme."}
    ], sample_pages())

    assert values == ["Zuora"], "still visible as a proposal"
    assert evidence == {}, "but the quote never names Zuora"
    assert stats["unsupported"] == 1 and stats["quote_not_found"] == 0, stats


def test_a_value_restated_from_its_quote_is_accepted():
    """The same check must not reject honest compression of the sentence it came from."""
    for value, quote in [
        ("manual invoicing",
         "A five-person finance team invoiced 4,500+ merchants manually every two weeks."),
        ("legacy subscription billing platform",
         "Discover FMG's transformational journey of switching from a legacy "
         "subscription billing platform to Acme."),
    ]:
        _, evidence, stats = ip.split_evidence_claims(
            [{"value": value, "source_url": "https://acme.com/customers/fmg", "quote": quote}],
            sample_pages())
        assert evidence.get(value), "%r should be supported by %r (stats=%s)" % (value, quote[:40], stats)


def test_short_values_must_appear_verbatim_in_their_quote():
    """An acronym has no stem to match on, so nothing less than the word itself will do."""
    _, absent, _ = ip.split_evidence_claims([
        {"value": "SAP", "source_url": "https://acme.com/compare/vs-zuora",
         "quote": "Teams migrating from Zuora choose Acme for multi-entity consolidation."}
    ], sample_pages())
    assert absent == {}

    _, present, _ = ip.split_evidence_claims([
        {"value": "Zuora", "source_url": "https://acme.com/compare/vs-zuora",
         "quote": "Teams migrating from Zuora choose Acme"}
    ], sample_pages())
    assert present.get("Zuora")


def test_reading_buyer_pages_and_extracting_nothing_scores_at_the_floor():
    """The boilerplate bug scored 0.85 while producing an empty buyer profile."""
    silent_failure = ip.discovery_confidence(
        {"proposed": 0, "evidenced": 0, "evidence_ratio": 0.0}, pages_read=8, icp_pages_read=6)
    no_buyer_pages_to_read = ip.discovery_confidence(
        {"proposed": 0, "evidenced": 0, "evidence_ratio": 0.0}, pages_read=8, icp_pages_read=0)

    assert silent_failure < no_buyer_pages_to_read, (silent_failure, no_buyer_pages_to_read)
    print("  6 buyer pages, nothing extracted: %.2f   no buyer pages at all: %.2f"
          % (silent_failure, no_buyer_pages_to_read))


def test_gather_counts_the_buyer_pages_it_read():
    with fake_site():
        evidence = ip.gather_discovery_evidence("https://acme.com", max_pages=4)
    # /customers/..., /compare/... and /pricing all carry buyer evidence; the homepage does not.
    assert evidence["icp_pages_read"] == 3, [p["url"] for p in evidence["pages"]]




# ------------------------------------------------- category keys stay about the product

def test_prompt_forbids_taking_category_facts_from_customer_stories():
    """A live run listed Amazon, Walmart and eBay as integrations.

    They were a customer's sales channels, named in that customer's case study. Reading
    more pages sharpened the buyer half and handed the category half more to be misled by.
    """
    with fake_site():
        evidence = ip.gather_discovery_evidence("https://acme.com", max_pages=2)
    prompt = ip.build_discovery_prompt(evidence)

    category_half = prompt[prompt.index("GROUP 1"):prompt.index("GROUP 2")]
    assert "sales channels" in category_half

    # The general rule alone did not hold: a live run still listed Amazon and Walmart as
    # integrations. The constraint has to sit on the key the model is filling in, not only
    # in the preamble above it.
    integrations_spec = prompt[prompt.index('"known_integrations"'):prompt.index('"known_pricing"')]
    assert "must not appear here" in integrations_spec
    assert "Amazon" in integrations_spec and "sales channels" in integrations_spec


# ------------------------------------------------------------------- segment level

def test_prompt_asks_for_a_segment_shape_not_one_customer_description():
    with fake_site():
        evidence = ip.gather_discovery_evidence("https://acme.com", max_pages=2)
    prompt = ip.build_discovery_prompt(evidence)

    segment_spec = prompt[prompt.index('"known_segments"'):prompt.index('"known_industries"')]
    assert "Abstract one step up" in segment_spec
    assert "vendor's own total customer count" in segment_spec
    # The worked example is deliberately from another industry, so it cannot be copied
    # into the answer for a billing or security vendor.
    assert "dialysis" in segment_spec


def test_an_abstracted_segment_is_accepted_though_its_words_are_not_in_the_quote():
    """Abstraction is the point of this field, so it cannot be held to lexical presence."""
    pages = [{"url": "https://acme.com/customers/mercy", "title": "MercyCare",
              "meta_description": "", "headings": [],
              "body_snippet": "MercyCare is a 60-clinic dialysis network staffing 400 "
                              "travelling nurses across four states."}]
    discovered = {
        "known_segments": [{"value": "multi-site healthcare provider with a contingent workforce",
                            "source_url": "https://acme.com/customers/mercy",
                            "quote": "MercyCare is a 60-clinic dialysis network staffing 400 travelling nurses"}],
        "known_industries": [], "known_competitors": [], "known_replaces": [],
    }
    _, evidence, stats = ip.resolve_buyer_fields(discovered, pages)
    assert evidence.get("known_segments"), stats


def test_an_abstracted_value_in_any_other_buyer_field_is_still_rejected():
    """The looser rule is for segments only: a competitor is named or it is not."""
    pages = [{"url": "https://acme.com/customers/mercy", "title": "MercyCare",
              "meta_description": "", "headings": [],
              "body_snippet": "MercyCare switched from a legacy scheduling system."}]
    discovered = {
        "known_segments": [],
        "known_competitors": [{"value": "Kronos", "source_url": "https://acme.com/customers/mercy",
                               "quote": "MercyCare switched from a legacy scheduling system."}],
        "known_industries": [], "known_replaces": [],
    }
    _, evidence, stats = ip.resolve_buyer_fields(discovered, pages)
    assert evidence == {}, "the quote never names Kronos"
    assert stats["unsupported"] == 1


def test_a_segment_quote_must_still_be_on_the_page_it_cites():
    """Relaxing support does not relax provenance."""
    pages = [{"url": "https://acme.com/customers/mercy", "title": "MercyCare",
              "meta_description": "", "headings": [],
              "body_snippet": "MercyCare is a 60-clinic dialysis network."}]
    discovered = {
        "known_segments": [{"value": "multi-site healthcare provider",
                            "source_url": "https://acme.com/customers/mercy",
                            "quote": "MercyCare operates 900 hospitals in twelve countries."}],
        "known_industries": [], "known_competitors": [], "known_replaces": [],
    }
    _, evidence, stats = ip.resolve_buyer_fields(discovered, pages)
    assert evidence == {}
    assert stats["quote_not_found"] == 1


# ------------------------------------------------------------- competitor starvation

COMPARISON_SITE = {
    "https://acme.com": HOME_HTML.replace('<a href="/compare/vs-zuora">Acme vs Zuora</a>', ""),
    "https://acme.com/pricing": PRICING_HTML,
    "https://acme.com/customers/globex-case-study": CASE_STUDY_HTML,
    "https://acme.com/alternatives": "<html><head><title>Acme alternatives</title></head>"
                                     "<body><h1>Alternatives</h1><p>Teams comparing Acme and "
                                     "Zuora usually weigh multi-entity support.</p></body></html>",
}


def test_comparison_pages_are_probed_when_the_homepage_links_none():
    """Seven case studies and no competitor evidence: vendors rarely link 'us vs them'."""
    fetched = []
    with fake_site(pages=COMPARISON_SITE, fetched=fetched):
        evidence = ip.gather_discovery_evidence("https://acme.com", max_pages=4)

    urls = [p["url"] for p in evidence["pages"]]
    assert "https://acme.com/alternatives" in urls, urls
    assert "https://acme.com/alternatives" in fetched
    print("  Probed and found: %s" % [u for u in urls if "alternativ" in u])


def test_no_probing_when_the_homepage_already_links_a_comparison_page():
    fetched = []
    with fake_site(fetched=fetched):
        ip.gather_discovery_evidence("https://acme.com", max_pages=4)

    for path in ip.COMPARISON_PROBE_PATHS:
        assert "https://acme.com" + path not in fetched, "wasted a request on %s" % path


def test_probing_does_not_exceed_the_page_budget():
    fetched = []
    with fake_site(pages=COMPARISON_SITE, fetched=fetched):
        evidence = ip.gather_discovery_evidence("https://acme.com", max_pages=3)
    assert evidence["pages_read"] <= 3, [p["url"] for p in evidence["pages"]]





# ----------------------------------------------------------------------- sitemap

def sitemap_xml(*urls):
    locs = "".join("<url><loc>%s</loc></url>" % u for u in urls)
    return '<?xml version="1.0" encoding="UTF-8"?><urlset>%s</urlset>' % locs


def sitemap_index(*documents):
    locs = "".join("<sitemap><loc>%s</loc></sitemap>" % d for d in documents)
    return '<?xml version="1.0" encoding="UTF-8"?><sitemapindex>%s</sitemapindex>' % locs


def test_sitemap_urls_are_read():
    site = {"https://acme.com/sitemap.xml": sitemap_xml(
        "https://acme.com/compare/vs-zuora", "https://acme.com/customers/pret")}
    with fake_site(pages=site):
        urls = ip.fetch_sitemap_urls("https://acme.com")
    assert urls == ["https://acme.com/compare/vs-zuora", "https://acme.com/customers/pret"], urls


def test_a_sitemap_index_is_followed_one_level():
    site = {
        "https://acme.com/sitemap.xml": sitemap_index("https://acme.com/sitemap-pages.xml"),
        "https://acme.com/sitemap-pages.xml": sitemap_xml("https://acme.com/alternatives"),
    }
    with fake_site(pages=site):
        urls = ip.fetch_sitemap_urls("https://acme.com")
    assert urls == ["https://acme.com/alternatives"], urls


def test_the_sitemap_named_in_robots_txt_is_used():
    """The site's own statement of where its index lives beats guessing at paths."""
    site = {
        "https://acme.com/robots.txt": "User-agent: *\nSitemap: https://acme.com/custom-map.xml\n",
        "https://acme.com/custom-map.xml": sitemap_xml("https://acme.com/customers/globex"),
    }
    with fake_site(pages=site):
        urls = ip.fetch_sitemap_urls("https://acme.com")
    assert urls == ["https://acme.com/customers/globex"], urls


def test_compressed_sitemaps_are_skipped_rather_than_read_as_text():
    site = {
        "https://acme.com/sitemap.xml": sitemap_index("https://acme.com/pages.xml.gz"),
        "https://acme.com/pages.xml.gz": "\x1f\x8b binary noise",
    }
    with fake_site(pages=site):
        urls = ip.fetch_sitemap_urls("https://acme.com")
    assert urls == [], urls


def test_sitemap_document_count_is_capped():
    children = ["https://acme.com/s%d.xml" % i for i in range(12)]
    site = {"https://acme.com/sitemap.xml": sitemap_index(*children)}
    for i, child in enumerate(children):
        site[child] = sitemap_xml("https://acme.com/page%d" % i)

    fetched = []
    with fake_site(pages=site, fetched=fetched):
        ip.fetch_sitemap_urls("https://acme.com")

    xml_fetches = [u for u in fetched if u.endswith(".xml")]
    assert len(xml_fetches) <= ip._MAX_SITEMAP_DOCS, xml_fetches


def test_a_missing_sitemap_is_not_an_error():
    with fake_site(pages={}):
        assert ip.fetch_sitemap_urls("https://acme.com") == []


def test_offsite_urls_in_a_sitemap_are_ignored():
    """A sitemap may list other hosts; those are not this company's own evidence."""
    urls = ip._rank_urls("https://acme.com", [
        "https://acme.com/customers/globex",
        "https://cdn.othersite.com/customers/someone-else",
    ], limit=10)
    assert urls == ["https://acme.com/customers/globex"], urls


def test_a_comparison_page_only_in_the_sitemap_is_still_read():
    """The live failure: seven case studies linked, no comparison page, competitors empty.

    chargebee.com does not link its comparison pages from the homepage and returns 404 for
    every conventional path, so the only way to find one is the site's own index.
    """
    home_without_comparison = HOME_HTML.replace(
        '<a href="/compare/vs-zuora">Acme vs Zuora</a>', "")
    site = {
        "https://acme.com": home_without_comparison,
        "https://acme.com/pricing": PRICING_HTML,
        "https://acme.com/customers/globex-case-study": CASE_STUDY_HTML,
        "https://acme.com/sitemap.xml": sitemap_xml(
            "https://acme.com/blog/unrelated-post",
            "https://acme.com/acme-vs-zuora"),
        "https://acme.com/acme-vs-zuora": COMPARE_HTML,
    }
    with fake_site(pages=site):
        evidence = ip.gather_discovery_evidence("https://acme.com", max_pages=4)

    urls = [p["url"] for p in evidence["pages"]]
    assert "https://acme.com/acme-vs-zuora" in urls, urls
    print("  Found via sitemap: /acme-vs-zuora")


def test_a_linked_page_outranks_a_sitemap_only_page_of_the_same_kind():
    """The homepage links what the company considers important; ties go to it."""
    site = {
        "https://acme.com": HOME_HTML,
        "https://acme.com/pricing": PRICING_HTML,
        "https://acme.com/customers/globex-case-study": CASE_STUDY_HTML,
        "https://acme.com/compare/vs-zuora": COMPARE_HTML,
        "https://acme.com/sitemap.xml": sitemap_xml("https://acme.com/customers/archived-story"),
        "https://acme.com/customers/archived-story": CASE_STUDY_HTML,
    }
    # One slot beyond the homepage, so the tie between two /customers pages is what
    # decides which is read.
    with fake_site(pages=site):
        evidence = ip.gather_discovery_evidence("https://acme.com", max_pages=2)

    urls = [p["url"] for p in evidence["pages"]]
    assert "https://acme.com/customers/globex-case-study" in urls, urls
    assert "https://acme.com/customers/archived-story" not in urls, urls




# --------------------------------------------------------------- budget diversity

CB = "https://vendor.com"


def lopsided_candidates(customers=300, comparisons=14):
    """The real shape of a large vendor's sitemap: case studies dwarf everything else."""
    urls = ["%s/customers/c%d" % (CB, i) for i in range(customers)]
    urls += ["%s/compare-competitors/" % CB]
    urls += ["%s/compare-competitors/rival%d" % (CB, i) for i in range(comparisons - 1)]
    urls += ["%s/pricing" % CB, "%s/security" % CB, "%s/solutions/saas" % CB]
    return urls


def kinds(urls):
    counts = {}
    for u in urls:
        counts[ip._evidence_group(u)] = counts.get(ip._evidence_group(u), 0) + 1
    return counts


def test_a_scarce_page_kind_still_gets_read():
    """The live failure: fourteen comparison pages found, none read, competitors empty."""
    selected = ip._select_evidence_urls(CB, lopsided_candidates(), 7)

    assert len(selected) == 7
    counts = kinds(selected)
    assert counts.get("comparison", 0) >= 1, counts
    assert counts.get("customer", 0) >= 3, "case studies still carry three of the four fields"
    print("  %d slots -> %s" % (len(selected), counts))


def test_ranking_alone_would_have_starved_it():
    """Pins the behaviour this replaced, so the regression is visible if it returns."""
    by_rank = ip._rank_urls(CB, lopsided_candidates(), 7)
    assert kinds(by_rank) == {"customer": 7}, kinds(by_rank)


def test_an_absent_kind_returns_its_slots_rather_than_wasting_them():
    """Reserves are floors, not quotas: this must never read less than before."""
    only_customers = ["%s/customers/c%d" % (CB, i) for i in range(20)]
    selected = ip._select_evidence_urls(CB, only_customers, 6)
    assert len(selected) == 6, selected
    assert kinds(selected) == {"customer": 6}


def test_the_budget_is_never_exceeded():
    for limit in (1, 2, 3, 7, 40):
        selected = ip._select_evidence_urls(CB, lopsided_candidates(), limit)
        assert len(selected) <= limit, (limit, len(selected))
    assert ip._select_evidence_urls(CB, lopsided_candidates(), 0) == []


def test_one_slot_goes_to_the_strongest_evidence():
    """With a single page to spend, a case study beats a pricing page."""
    selected = ip._select_evidence_urls(CB, lopsided_candidates(), 1)
    assert ip._evidence_group(selected[0]) == "customer", selected


def test_selection_comes_back_in_rank_order():
    selected = ip._select_evidence_urls(CB, lopsided_candidates(), 7)
    ranks = [ip._rank_urls(CB, lopsided_candidates()).index(u) for u in selected]
    assert ranks == sorted(ranks), ranks


def test_a_comparison_page_from_the_sitemap_survives_a_crowd_of_case_studies():
    """End to end: the homepage links only case studies, the sitemap adds the comparison."""
    home = HOME_HTML.replace('<a href="/compare/vs-zuora">Acme vs Zuora</a>', "")
    site = {
        "https://acme.com": home,
        "https://acme.com/pricing": PRICING_HTML,
        "https://acme.com/sitemap.xml": sitemap_xml(
            *(["https://acme.com/customers/story%d" % i for i in range(30)]
              + ["https://acme.com/compare-competitors/zuora"])),
        "https://acme.com/compare-competitors/zuora": COMPARE_HTML,
        "https://acme.com/customers/globex-case-study": CASE_STUDY_HTML,
    }
    for i in range(30):
        site["https://acme.com/customers/story%d" % i] = CASE_STUDY_HTML

    with fake_site(pages=site):
        evidence = ip.gather_discovery_evidence("https://acme.com", max_pages=5)

    urls = [p["url"] for p in evidence["pages"]]
    assert "https://acme.com/compare-competitors/zuora" in urls, urls
    print("  Comparison page survived %d case studies" % 30)




def test_a_glossary_comparison_does_not_take_the_competitor_slot():
    """Live failure: "/resources/glossaries/acv-vs-arr/" won the comparison slot.

    It matches a comparison hint and compares two metrics, not two vendors. Reserving a
    slot for competitor evidence achieves nothing if content marketing can spend it.
    """
    urls = ["%s/resources/glossaries/acv-vs-arr" % CB,
            "%s/blog/chargebee-vs-zuora" % CB,
            "%s/compare-competitors/maxio" % CB]
    assert ip._evidence_group(urls[0]) is None
    assert ip._evidence_group(urls[1]) is None, "a blog post naming a rival is not a comparison page"
    assert ip._evidence_group(urls[2]) == "comparison"

    selected = ip._select_evidence_urls(CB, urls + lopsided_candidates(), 7)
    comparisons = [u for u in selected if ip._evidence_group(u) == "comparison"]
    assert comparisons, selected
    assert not any("glossar" in u or "/blog" in u for u in selected), selected
    print("  Comparison slot went to %s" % comparisons[0].replace(CB, ""))


def test_editorial_pages_rank_last_rather_than_being_dropped():
    """Still readable when there is nothing better, just never preferred."""
    ranked = ip._rank_urls(CB, ["%s/resources/glossaries/acv-vs-arr" % CB,
                                "%s/customers/pret" % CB])
    assert ranked[0].endswith("/customers/pret"), ranked
    assert len(ranked) == 2, ranked



def test_a_section_leaf_is_preferred_over_the_section_index():
    """Live failure: /compare-competitors/ was read and named no competitor.

    A section index is a landing page full of links; the claims are on the pages it links
    to. Reading the hub spends the reserved slot and yields nothing quotable.
    """
    ranked = ip._rank_urls(CB, ["%s/compare-competitors/" % CB,
                                "%s/compare-competitors/maxio" % CB])
    assert ranked[0].endswith("/maxio"), ranked

    ranked = ip._rank_urls(CB, ["%s/customers/" % CB, "%s/customers/pret" % CB])
    assert ranked[0].endswith("/pret"), ranked



def test_a_sub_page_of_a_customer_story_does_not_outrank_the_story():
    """Live failure: two /customers/<name>/user-roles/ pages displaced real case studies.

    Depth was a goal rather than a tie-break, so the deepest page always won.
    """
    ranked = ip._rank_urls(CB, ["%s/customers/freshdesk/user-roles" % CB,
                                "%s/customers/whereby" % CB,
                                "%s/customers/" % CB])
    assert ranked[0].endswith("/customers/whereby"), ranked
    assert ranked[-1].endswith("/user-roles"), ranked


def test_language_variants_are_skipped():
    """A German page is deeper than its English original and was winning the tie."""
    ranked = ip._rank_urls(CB, ["%s/de/solutions/industry" % CB, "%s/solutions/industry" % CB])
    assert ranked == ["%s/solutions/industry" % CB], ranked


def test_the_locale_test_does_not_swallow_ordinary_paths():
    """Only a leading two-letter segment counts, so real sections survive."""
    for path in ("/devops/pricing", "/es-money-movement/pricing", "/ai/pricing",
                 "/id/verification", "/my/account"):
        assert ip._rank_urls(CB, [CB + path]) == [CB + path], path

    # Language-region forms are variants too.
    assert ip._rank_urls(CB, ["%s/pt-br/pricing" % CB]) == [], "pt-br is a translation"


# ------------------------------------------------------------- match before mint

BILLING_VERTICAL = {
    "vertical_id": "billing_ops",
    "display_name": "Billing Operations",
    "gliner_labels": ["Billing Model"],
    "mandatory_schema_types": ["SoftwareApplication"],
    "core_seed_concepts": ["Subscription Billing", "Revenue Recognition", "Dunning",
                           "Invoicing", "Proration", "Usage-Based Pricing"],
    "known_compliance": ["ASC 606", "IFRS 15"],
    "known_integrations": ["NetSuite", "Salesforce"],
    "known_features": ["Credit Notes"],
    "known_segments": ["Multi-entity SaaS"],
    "concepts": [{"id": "dunning", "prefLabel": "Dunning", "definition": "Chasing failed payments."}],
    "alt_labels": {"Dunning": ["Dunning Management"]},
}

SECURITY_VERTICAL = {
    "vertical_id": "appsec",
    "display_name": "Application Security",
    "gliner_labels": ["Security Platform"],
    "mandatory_schema_types": ["SoftwareApplication"],
    "core_seed_concepts": ["Vulnerability Scanning", "Threat Detection", "Penetration Testing",
                           "Static Analysis", "Container Security", "Secrets Detection"],
    "known_compliance": ["ISO 27001"],
    "known_integrations": ["GitHub"],
}

BILLING_TEXT = ("Automated invoicing and revenue recognition for subscription billing. "
                "Dunning, proration and usage-based pricing, with ASC 606 schedules "
                "exported to NetSuite.")


def with_verticals(profiles):
    """Point every reader and writer at a throwaway directory holding `profiles`."""
    tmpdir = tempfile.mkdtemp(prefix="ontoleap_verticals_test_")
    for profile in profiles:
        with open(os.path.join(tmpdir, profile["vertical_id"] + ".json"), "w", encoding="utf-8") as fh:
            json.dump(profile, fh)
    return tmpdir, patched((industry_ontology, "verticals_dir", lambda: tmpdir),
                           (ip, "verticals_dir", lambda: tmpdir))


def test_a_site_joins_the_vertical_that_already_describes_its_category():
    """Six runs over one domain minted six verticals. The category already had a home."""
    tmpdir, scope = with_verticals([BILLING_VERTICAL, SECURITY_VERTICAL])
    try:
        with scope:
            match = industry_ontology.match_existing_vertical(BILLING_TEXT)
        assert match["vertical_id"] == "billing_ops", match
        assert match["decision"] in ("matched", "resolved-ambiguous")
        print("  Matched %s: %s" % (match["vertical_id"], match["reason"][:60]))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_a_site_in_an_unknown_category_still_mints():
    """Matching must not drag every site into the nearest vertical."""
    tmpdir, scope = with_verticals([BILLING_VERTICAL])
    try:
        with scope:
            match = industry_ontology.match_existing_vertical(
                "Veterinary practice management software for scheduling and patient records.")
        assert match["vertical_id"] is None, match
        assert match["decision"] == "no-match"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_ambiguity_between_near_duplicates_resolves_to_the_one_with_concepts():
    """Refusing here mints a third copy of a category that already has two."""
    twin = dict(BILLING_VERTICAL, vertical_id="billing_ops_twin",
                display_name="Billing Ops Twin", concepts=[], alt_labels={})
    tmpdir, scope = with_verticals([BILLING_VERTICAL, twin])
    try:
        with scope:
            match = industry_ontology.match_existing_vertical(BILLING_TEXT)
        assert match["vertical_id"] == "billing_ops", match
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_matching_reads_the_same_directory_that_discovery_writes():
    """These used to disagree: writes honoured the override, reads never did."""
    tmpdir, scope = with_verticals([SECURITY_VERTICAL])
    try:
        with scope:
            visible = [m["vertical_id"] for m
                       in industry_ontology.list_available_industries(include_unusable=True)]
        assert visible == ["appsec"], visible
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ------------------------------------------------------------------ accumulation

def test_a_second_site_adds_vocabulary_instead_of_replacing_it():
    """Without this, matching is pointless: the newcomer overwrites what it joined."""
    incoming = {
        "vertical_id": "something_the_model_made_up",
        "display_name": "Monetization & Revenue Ops",
        "core_seed_concepts": ["Revenue Recognition", "Cash Application"],
        "known_compliance": ["SOC 2"],
        "known_segments": ["Order fulfillment provider"],
    }
    merged, mode = ip.merge_profile(dict(BILLING_VERTICAL, concepts=[], alt_labels={}),
                                    incoming, matched_existing=True)

    assert mode == "accumulated"
    # Identity belongs to the category, not to whichever site was read last.
    assert merged["vertical_id"] == "billing_ops"
    assert merged["display_name"] == "Billing Operations"
    # Existing vocabulary kept, new vocabulary added, no duplicates.
    assert "Subscription Billing" in merged["core_seed_concepts"]
    assert "Cash Application" in merged["core_seed_concepts"]
    assert merged["core_seed_concepts"].count("Revenue Recognition") == 1
    assert merged["known_compliance"] == ["ASC 606", "IFRS 15", "SOC 2"]
    assert merged["known_segments"] == ["Multi-entity SaaS", "Order fulfillment provider"]
    print("  Seeds grew %d -> %d" % (len(BILLING_VERTICAL["core_seed_concepts"]),
                                     len(merged["core_seed_concepts"])))


def test_accumulation_is_case_insensitive_about_duplicates():
    merged, _ = ip.merge_profile(
        {"vertical_id": "v", "known_pricing": ["Usage-Based Pricing"]},
        {"known_pricing": ["usage-based pricing", "Tiered Pricing"]},
        matched_existing=True)
    assert merged["known_pricing"] == ["Usage-Based Pricing", "Tiered Pricing"]


def test_rediscovering_a_minted_vertical_still_replaces_rather_than_accumulates():
    """The same site read twice should not pile its own older words back on."""
    merged, mode = ip.merge_profile(
        {"vertical_id": "v", "display_name": "Old", "core_seed_concepts": ["Stale Term"]},
        {"vertical_id": "v", "display_name": "New", "core_seed_concepts": ["Fresh Term"]},
        matched_existing=False)
    assert mode == "refreshed"
    assert merged["core_seed_concepts"] == ["Fresh Term"]
    assert merged["display_name"] == "New"


def test_a_curated_vertical_is_still_protected_when_matched_into():
    """Accumulation must not become a back door into the hand-built vocabulary."""
    merged, mode = ip.merge_profile(BILLING_VERTICAL,
                                    {"core_seed_concepts": ["Nonsense"], "display_name": "Hijack"},
                                    matched_existing=True)
    assert mode == "merged-into-curated"
    assert merged["core_seed_concepts"] == BILLING_VERTICAL["core_seed_concepts"]
    assert merged["display_name"] == "Billing Operations"


def test_discovery_joins_an_existing_vertical_end_to_end():
    """The whole point, through the real orchestrator: no new vertical for a known category."""
    tmpdir, scope = with_verticals([BILLING_VERTICAL])

    async def no_grounding(compliance, integrations):
        return []

    # The homepage decides the match, so it has to read like a real billing vendor's.
    # A four-line fixture matched two vocabulary terms and minted a duplicate - which is
    # exactly the failure mode in production, where a thin homepage looks like a new
    # category.
    billing_home = HOME_HTML.replace(
        "<p>Acme automates invoicing and revenue recognition.</p>",
        "<p>Acme automates subscription billing, invoicing and revenue recognition. "
        "Dunning, proration and usage-based pricing, with ASC 606 and IFRS 15 revenue "
        "schedules exported to NetSuite and Salesforce.</p>")
    billing_site = {
        "https://acme.com": billing_home,
        "https://acme.com/pricing": PRICING_HTML,
        "https://acme.com/customers/globex-case-study": CASE_STUDY_HTML,
        "https://acme.com/compare/vs-zuora": COMPARE_HTML,
    }
    llm = dict(LLM_OUTPUT, vertical_id="a_brand_new_billing_category",
               display_name="A Brand New Billing Category",
               core_seed_concepts=["Subscription Billing", "Revenue Recognition", "Dunning",
                                   "Invoicing", "Proration", "Usage-Based Pricing"],
               known_compliance=["ASC 606", "IFRS 15"])

    try:
        with scope, fake_site(pages=billing_site), patched(
            (ip, "ground_discovered_entities", no_grounding),
            (ip.vertex_ai_client, "_call_gemini", lambda **kwargs: json.dumps(llm)),
        ):
            res = ip.discover_industry_profile("https://acme.com")

        assert res.vertical_match_mode == "matched-existing", res.vertical_match_reason
        assert res.vertical_id == "billing_ops"
        assert res.minted_vertical_id == "a_brand_new_billing_category", \
            "what would have been created is still reported"
        assert not os.path.exists(os.path.join(tmpdir, "a_brand_new_billing_category.json")), \
            "no duplicate vertical on disk"
        assert res.profile_write_mode == "merged-into-curated"
        print("  Joined %s instead of minting %s" % (res.vertical_id, res.minted_vertical_id))
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)



# ---------------------------------------------------------------------- confidence

def test_confidence_rises_with_pages_read_and_with_verified_evidence():
    thin = ip.discovery_confidence({"proposed": 4, "evidenced": 0, "evidence_ratio": 0.0}, pages_read=1)
    solid = ip.discovery_confidence({"proposed": 4, "evidenced": 4, "evidence_ratio": 1.0},
                                    pages_read=ip.DISCOVERY_EVIDENCE_PAGES)
    assert solid > thin, (thin, solid)
    assert 0.0 <= thin <= 1.0 and 0.0 <= solid <= 1.0
    print("  1 page / nothing verified: %.2f   full read / all verified: %.2f" % (thin, solid))


def test_fabricated_buyer_claims_score_below_none_at_all():
    """Claiming four customer segments and proving none is worse than claiming nothing."""
    fabricated = ip.discovery_confidence({"proposed": 4, "evidenced": 0, "evidence_ratio": 0.0}, pages_read=4)
    silent = ip.discovery_confidence({"proposed": 0, "evidenced": 0, "evidence_ratio": 0.0}, pages_read=4)
    assert fabricated < silent, (fabricated, silent)


# --------------------------------------------------------------------- persistence

def test_saved_profile_keeps_industries_competitors_and_evidence():
    """These two fields were generated and then dropped, so no profile could carry them."""
    tmpdir = tempfile.mkdtemp(prefix="ontoleap_discovery_test_")
    try:
        with patched((ip, "verticals_dir", lambda: tmpdir)):
            path, write_mode = ip.save_vertical_configuration(
                vertical_id="acme_billing",
                display_name="Acme Billing",
                gliner_labels=["Billing Model"],
                core_seed_concepts=["Invoicing"],
                known_integrations=["NetSuite"],
                known_compliance=["ASC 606"],
                known_pricing=["Usage-Based Pricing"],
                known_segments=["400-person multi-entity manufacturer"],
                known_industries=["Medical Devices"],
                known_competitors=["Zuora"],
                known_replaces=["revenue schedules in Excel"],
                icp_evidence={"known_competitors": {"Zuora": {
                    "source_url": "https://acme.com/compare/vs-zuora",
                    "quote": "Teams migrating from Zuora"}}},
            )

        with open(path, encoding="utf-8") as fh:
            saved = json.load(fh)
        assert write_mode == "created"
        assert saved["known_industries"] == ["Medical Devices"]
        assert saved["known_competitors"] == ["Zuora"]
        assert saved["icp_evidence"]["known_competitors"]["Zuora"]["source_url"].endswith("vs-zuora")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)



# --------------------------------------------------- a curated profile is not clobbered

def curated_profile():
    """A hand-built vertical, shaped like verticals/b2b_saas_fintech.json.

    The two keys discovery cannot regenerate are `concepts` (definitions) and `alt_labels`
    (the register-correct surface forms). It also carries a far richer known_features list
    than any discovery run produces, and an empty buyer half.
    """
    return {
        "vertical_id": "acme_billing",
        "display_name": "Curated Billing",
        "gliner_labels": ["Billing Model", "Revenue Stream"],
        "mandatory_schema_types": ["SoftwareApplication"],
        "core_seed_concepts": ["Subscription Billing", "Revenue Recognition", "Dunning"],
        "known_features": ["Proration", "Revenue Waterfall", "Contract Modification",
                           "Usage Rating", "Credit Notes", "Aging Report"],
        "known_integrations": ["NetSuite ERP", "Salesforce CRM"],
        "known_compliance": ["ASC 606", "IFRS 15"],
        "known_pricing": ["Usage-Based Pricing"],
        "known_segments": [],
        "known_industries": [],
        "known_competitors": [],
        "known_replaces": [],
        "concepts": [{"id": "asc-606", "prefLabel": "ASC 606",
                      "definition": "Revenue from contracts with customers.",
                      "governs": ["revenue-recognition"]}],
        "alt_labels": {"ASC 606": ["ASC606", "Revenue Standard ASC 606"]},
        "concept_hierarchy": {"Dunning": "Accounts Receivable"},
        "a_key_discovery_has_never_heard_of": {"kept": True},
    }


def discovered_profile():
    """What a discovery run would write for the same slug."""
    return {
        "vertical_id": "acme_billing",
        "display_name": "Billing & Monetization",
        "gliner_labels": ["Subscription Platform"],
        "mandatory_schema_types": ["SoftwareApplication", "Organization", "Offer"],
        "core_seed_concepts": ["Recurring Billing", "Payment Processing"],
        "known_features": ["Automated Invoicing", "Self-Service Portal"],
        "known_integrations": ["Stripe", "Xero"],
        "known_compliance": ["PCI-DSS"],
        "known_pricing": ["Tiered Pricing"],
        "known_segments": ["Order fulfillment provider for eCommerce merchants"],
        "known_industries": ["eCommerce"],
        "known_competitors": ["Zuora"],
        "known_replaces": ["Excel-dependent billing"],
        "icp_evidence": {"known_replaces": {"Excel-dependent billing": {
            "source_url": "https://acme.com/customers/sesame", "quote": "Excel-dependent billing"}}},
        "concept_hierarchy": {"Recurring Billing": "Billing"},
    }


def test_discovery_never_destroys_hand_built_concepts_and_alt_labels():
    """The hazard this exists for: 111 defined concepts and 85 alt-label sets, gone silently."""
    merged, mode = ip.merge_profile(curated_profile(), discovered_profile())

    assert mode == "merged-into-curated"
    assert merged["concepts"][0]["definition"] == "Revenue from contracts with customers."
    assert merged["alt_labels"]["ASC 606"] == ["ASC606", "Revenue Standard ASC 606"]


def test_a_curated_profile_keeps_its_own_richer_vocabulary():
    """Overwriting these is quieter than losing the concepts, and just as destructive."""
    merged, _ = ip.merge_profile(curated_profile(), discovered_profile())

    assert merged["known_features"] == curated_profile()["known_features"]
    assert merged["known_integrations"] == ["NetSuite ERP", "Salesforce CRM"]
    assert merged["core_seed_concepts"] == curated_profile()["core_seed_concepts"]
    assert merged["display_name"] == "Curated Billing"
    assert merged["concept_hierarchy"] == {"Dunning": "Accounts Receivable"}


def test_a_curated_profile_gains_the_buyer_half_it_was_missing():
    """The point of the exercise: an evidence-backed ICP without risking the vocabulary."""
    merged, _ = ip.merge_profile(curated_profile(), discovered_profile())

    assert merged["known_segments"] == ["Order fulfillment provider for eCommerce merchants"]
    assert merged["known_industries"] == ["eCommerce"]
    assert merged["known_competitors"] == ["Zuora"]
    assert merged["known_replaces"] == ["Excel-dependent billing"]
    assert merged["icp_evidence"]["known_replaces"]["Excel-dependent billing"]["source_url"]
    print("  Curated vocabulary kept, buyer half filled: %s"
          % merged["known_segments"])


def test_keys_the_merge_has_never_heard_of_survive():
    merged, _ = ip.merge_profile(curated_profile(), discovered_profile())
    assert merged["a_key_discovery_has_never_heard_of"] == {"kept": True}


def test_an_auto_discovered_profile_is_refreshed_wholesale():
    """Nothing in it was curated, so a newer reading should replace it."""
    previous = {k: v for k, v in curated_profile().items()
                if k not in ("concepts", "alt_labels")}
    merged, mode = ip.merge_profile(previous, discovered_profile())

    assert mode == "refreshed"
    assert merged["display_name"] == "Billing & Monetization"
    assert merged["known_features"] == ["Automated Invoicing", "Self-Service Portal"]
    assert merged["a_key_discovery_has_never_heard_of"] == {"kept": True}, "still not ours to drop"


def test_a_new_profile_is_written_as_is():
    merged, mode = ip.merge_profile(None, discovered_profile())
    assert mode == "created"
    assert merged == discovered_profile()


def test_saving_over_a_curated_file_on_disk_preserves_it():
    """End to end through the writer, not just the merge function."""
    tmpdir = tempfile.mkdtemp(prefix="ontoleap_discovery_test_")
    try:
        path = os.path.join(tmpdir, "acme_billing.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(curated_profile(), fh)

        with patched((ip, "verticals_dir", lambda: tmpdir)):
            saved_path, mode = ip.save_vertical_configuration(
                vertical_id="acme_billing",
                display_name="Billing & Monetization",
                gliner_labels=["Subscription Platform"],
                core_seed_concepts=["Recurring Billing"],
                known_integrations=["Stripe"],
                known_compliance=["PCI-DSS"],
                known_pricing=["Tiered Pricing"],
                known_segments=["Order fulfillment provider for eCommerce merchants"],
                known_industries=["eCommerce"],
                known_competitors=["Zuora"],
                known_replaces=["Excel-dependent billing"],
            )

        assert mode == "merged-into-curated"
        with open(saved_path, encoding="utf-8") as fh:
            on_disk = json.load(fh)
        assert on_disk["alt_labels"]["ASC 606"], "the curated identity layer survived a write"
        assert on_disk["concepts"][0]["id"] == "asc-606"
        assert on_disk["known_segments"] == ["Order fulfillment provider for eCommerce merchants"]
        assert on_disk["known_integrations"] == ["NetSuite ERP", "Salesforce CRM"]
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_an_unreadable_existing_profile_does_not_stop_the_write():
    tmpdir = tempfile.mkdtemp(prefix="ontoleap_discovery_test_")
    try:
        path = os.path.join(tmpdir, "acme_billing.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ not json at all")

        with patched((ip, "verticals_dir", lambda: tmpdir)):
            saved_path, mode = ip.save_vertical_configuration(
                vertical_id="acme_billing",
                display_name="Billing",
                gliner_labels=["Subscription Platform"],
                core_seed_concepts=["Recurring Billing"],
                known_integrations=["Stripe"],
                known_compliance=["PCI-DSS"],
                known_pricing=["Tiered Pricing"],
            )
        assert mode == "created"
        with open(saved_path, encoding="utf-8") as fh:
            assert json.load(fh)["display_name"] == "Billing"
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)



# ------------------------------------------------------------------------ fallback

def test_unreachable_llm_returns_no_buyer_profile_rather_than_a_generic_one():
    with patched((ip.vertex_ai_client, "_call_gemini", lambda **kwargs: None)):
        out = ip.call_gemini_industry_discovery({"domain": "acme.com", "pages": []}, brand_hint="Acme")

    for field in ip.ICP_FIELDS:
        assert out[field] == [], "%s must be empty, not guessed" % field
    assert out["discovery_degraded"], "a degraded profile has to say so"
    assert out["core_seed_concepts"], "the category half still has defaults"


def test_unparseable_llm_output_also_returns_no_buyer_profile():
    with patched((ip.vertex_ai_client, "_call_gemini", lambda **kwargs: "not json at all")):
        out = ip.call_gemini_industry_discovery({"domain": "acme.com", "pages": []})

    for field in ip.ICP_FIELDS:
        assert out[field] == []
    assert out["discovery_degraded"]


def test_fenced_json_is_parsed():
    payload = '```json\n{"brand_name": "Acme", "known_segments": []}\n```'
    with patched((ip.vertex_ai_client, "_call_gemini", lambda **kwargs: payload)):
        out = ip.call_gemini_industry_discovery({"domain": "acme.com", "pages": []})
    assert out["brand_name"] == "Acme"


# --------------------------------------------------------------- end to end, faked

LLM_OUTPUT = {
    "brand_name": "Acme",
    "vertical_id": "billing_automation",
    "display_name": "Billing Automation",
    "category": "Fintech",
    "core_seed_concepts": ["Invoicing", "Revenue Recognition"],
    "known_compliance": ["ASC 606"],
    "known_integrations": ["NetSuite"],
    "suggested_competitors": ["Chargebee"],
    "known_segments": [
        {"value": "400-person multi-entity manufacturer",
         "source_url": "https://acme.com/customers/globex-case-study",
         "quote": "Globex is a 400-person medical devices manufacturer running three subsidiaries."},
        {"value": "Enterprise", "source_url": "https://acme.com/not-a-real-page",
         "quote": "Acme serves the enterprise."},
    ],
    "known_replaces": [
        {"value": "revenue schedules in Excel",
         "source_url": "https://acme.com/customers/globex-case-study",
         "quote": "the finance team maintained revenue schedules in Excel"},
    ],
    "known_competitors": [
        {"value": "Zuora", "source_url": "https://acme.com/compare/vs-zuora",
         "quote": "Teams migrating from Zuora choose Acme"},
    ],
    "known_industries": [],
    "summary": "Billing automation for B2B SaaS.",
}


def test_discovery_end_to_end_verifies_claims_against_the_pages_it_read():
    tmpdir = tempfile.mkdtemp(prefix="ontoleap_discovery_test_")

    async def no_grounding(compliance, integrations):
        return []

    try:
        with fake_site(), patched(
            (ip, "verticals_dir", lambda: tmpdir),
            (ip, "ground_discovered_entities", no_grounding),
            (ip.vertex_ai_client, "_call_gemini", lambda **kwargs: json.dumps(LLM_OUTPUT)),
        ):
            res = ip.discover_industry_profile("https://acme.com", brand_hint="Acme")

        # Both proposals survive as values; only the one that checks out carries evidence.
        assert "400-person multi-entity manufacturer" in res.known_segments
        assert "Enterprise" in res.known_segments
        assert "400-person multi-entity manufacturer" in res.icp_evidence["known_segments"]
        assert "Enterprise" not in res.icp_evidence.get("known_segments", {})

        assert res.known_competitors == ["Zuora"], "named on the site"
        assert res.suggested_competitors == ["Chargebee"], "model knowledge, kept separate"

        assert res.buyer_claims_proposed == 4, res.buyer_claims_proposed
        assert res.buyer_claims_evidenced == 3, res.buyer_claims_evidenced
        assert res.pages_read == 4
        assert len(res.evidence_urls) == 4
        assert res.confidence_score != 0.96, "no longer a constant"

        with open(res.config_file, encoding="utf-8") as fh:
            saved = json.load(fh)
        assert saved["known_competitors"] == ["Zuora"]
        assert saved["icp_evidence"]["known_replaces"]["revenue schedules in Excel"]["source_url"]

        print("  Segments proposed: %s" % res.known_segments)
        print("  Segments evidenced: %s" % list(res.icp_evidence.get("known_segments", {})))
        print("  Confidence: %.2f (was a hardcoded 0.96)" % res.confidence_score)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


TESTS = [
    test_evidence_links_rank_buyer_pages_above_generic_ones,
    test_evidence_links_exclude_offsite_assets_and_anchors,
    test_evidence_links_respect_the_limit,
    test_gather_reads_homepage_plus_linked_evidence_pages,
    test_gather_survives_a_failing_sub_page,
    test_gather_degrades_when_the_homepage_is_unreachable,
    test_gather_adds_a_scheme_when_the_caller_omits_one,
    test_main_content_is_read_instead_of_the_navigation_menu,
    test_thin_pages_fall_back_to_raw_text_rather_than_returning_nothing,
    test_prompt_carries_every_page_url,
    test_prompt_no_longer_supplies_the_generic_buyer_answers,
    test_prompt_asks_for_one_object_with_the_buyer_keys_inside_it,
    test_verified_claim_keeps_its_evidence,
    test_claim_citing_a_page_we_never_read_loses_its_evidence,
    test_claim_quoting_text_absent_from_the_cited_page_loses_its_evidence,
    test_quote_matching_survives_whitespace_and_curly_quotes,
    test_bare_strings_are_kept_as_unevidenced_values,
    test_duplicate_claims_are_collapsed,
    test_malformed_items_are_ignored,
    test_resolve_buyer_fields_totals_across_every_field,
    test_a_real_quote_that_does_not_support_the_claim_is_rejected,
    test_a_value_restated_from_its_quote_is_accepted,
    test_short_values_must_appear_verbatim_in_their_quote,
    test_reading_buyer_pages_and_extracting_nothing_scores_at_the_floor,
    test_gather_counts_the_buyer_pages_it_read,
    test_prompt_forbids_taking_category_facts_from_customer_stories,
    test_prompt_asks_for_a_segment_shape_not_one_customer_description,
    test_an_abstracted_segment_is_accepted_though_its_words_are_not_in_the_quote,
    test_an_abstracted_value_in_any_other_buyer_field_is_still_rejected,
    test_a_segment_quote_must_still_be_on_the_page_it_cites,
    test_comparison_pages_are_probed_when_the_homepage_links_none,
    test_no_probing_when_the_homepage_already_links_a_comparison_page,
    test_probing_does_not_exceed_the_page_budget,
    test_sitemap_urls_are_read,
    test_a_sitemap_index_is_followed_one_level,
    test_the_sitemap_named_in_robots_txt_is_used,
    test_compressed_sitemaps_are_skipped_rather_than_read_as_text,
    test_sitemap_document_count_is_capped,
    test_a_missing_sitemap_is_not_an_error,
    test_offsite_urls_in_a_sitemap_are_ignored,
    test_a_comparison_page_only_in_the_sitemap_is_still_read,
    test_a_linked_page_outranks_a_sitemap_only_page_of_the_same_kind,
    test_a_scarce_page_kind_still_gets_read,
    test_ranking_alone_would_have_starved_it,
    test_an_absent_kind_returns_its_slots_rather_than_wasting_them,
    test_the_budget_is_never_exceeded,
    test_one_slot_goes_to_the_strongest_evidence,
    test_selection_comes_back_in_rank_order,
    test_a_comparison_page_from_the_sitemap_survives_a_crowd_of_case_studies,
    test_a_glossary_comparison_does_not_take_the_competitor_slot,
    test_editorial_pages_rank_last_rather_than_being_dropped,
    test_a_section_leaf_is_preferred_over_the_section_index,
    test_a_sub_page_of_a_customer_story_does_not_outrank_the_story,
    test_language_variants_are_skipped,
    test_the_locale_test_does_not_swallow_ordinary_paths,
    test_confidence_rises_with_pages_read_and_with_verified_evidence,
    test_fabricated_buyer_claims_score_below_none_at_all,
    test_saved_profile_keeps_industries_competitors_and_evidence,
    test_discovery_never_destroys_hand_built_concepts_and_alt_labels,
    test_a_curated_profile_keeps_its_own_richer_vocabulary,
    test_a_curated_profile_gains_the_buyer_half_it_was_missing,
    test_keys_the_merge_has_never_heard_of_survive,
    test_an_auto_discovered_profile_is_refreshed_wholesale,
    test_a_new_profile_is_written_as_is,
    test_saving_over_a_curated_file_on_disk_preserves_it,
    test_an_unreadable_existing_profile_does_not_stop_the_write,
    test_a_site_joins_the_vertical_that_already_describes_its_category,
    test_a_site_in_an_unknown_category_still_mints,
    test_ambiguity_between_near_duplicates_resolves_to_the_one_with_concepts,
    test_matching_reads_the_same_directory_that_discovery_writes,
    test_a_second_site_adds_vocabulary_instead_of_replacing_it,
    test_accumulation_is_case_insensitive_about_duplicates,
    test_rediscovering_a_minted_vertical_still_replaces_rather_than_accumulates,
    test_a_curated_vertical_is_still_protected_when_matched_into,
    test_discovery_joins_an_existing_vertical_end_to_end,
    test_unreachable_llm_returns_no_buyer_profile_rather_than_a_generic_one,
    test_unparseable_llm_output_also_returns_no_buyer_profile,
    test_fenced_json_is_parsed,
    test_discovery_end_to_end_verifies_claims_against_the_pages_it_read,
]


if __name__ == "__main__":
    for test in TESTS:
        print("[%s]" % test.__name__)
        test()
    print("\n" + "=" * 55)
    print("ALL %d DISCOVERY EVIDENCE TESTS PASSED" % len(TESTS))
    print("=" * 55)
