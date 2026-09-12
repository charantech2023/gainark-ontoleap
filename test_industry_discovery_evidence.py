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
            path = ip.save_vertical_configuration(
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
        assert saved["known_industries"] == ["Medical Devices"]
        assert saved["known_competitors"] == ["Zuora"]
        assert saved["icp_evidence"]["known_competitors"]["Zuora"]["source_url"].endswith("vs-zuora")
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
    test_confidence_rises_with_pages_read_and_with_verified_evidence,
    test_fabricated_buyer_claims_score_below_none_at_all,
    test_saved_profile_keeps_industries_competitors_and_evidence,
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
