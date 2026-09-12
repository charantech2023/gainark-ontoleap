"""
GainARK OntoLeap — Autonomous Industry Profiler & Zero-Shot Taxonomy Discovery
Converts any B2B SaaS domain into a complete Industry Ontology:
1. Reads the homepage plus the pages that carry buyer evidence (case studies,
   customer stories, comparison and pricing pages)
2. Prompts Google Gemini 2.5 Flash for industry taxonomy, compliance, integrations, and competitors
3. Asynchronously grounds entities against Wikidata and Google Knowledge Graph
4. Persists the vertical profile into verticals/ so OntologyPipeline can immediately use it
"""

import os
import json
import re
import asyncio
import logging
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import trafilatura

import vertex_ai_client
from entity_grounding import resolve_wikidata
from scraper import smart_fetch, validate_url_for_fetch
from models import (
    IndustryDiscoveryResponse,
    GroundedConcept
)
from constants import (ICP_EVIDENCE_PATHS, ICP_EVIDENCE_GROUPS, ICP_EVIDENCE_RESERVE,
                       ICP_EDITORIAL_PATHS)
from industry_ontology import match_existing_vertical

from security import verticals_dir

logger = logging.getLogger("gainark.industry_profiler")


# Ceiling on auto-discovered vertical profiles held on disk. Discovery is an
# unauthenticated write path in the default configuration.
MAX_VERTICAL_PROFILES = int(os.environ.get("MAX_VERTICAL_PROFILES", "200") or 200)


# Pages read per discovery call. Each is one fetch (about a second), so this is the
# latency budget: the homepage plus seven pages of buyer evidence.
DISCOVERY_EVIDENCE_PAGES = int(os.environ.get("DISCOVERY_EVIDENCE_PAGES", "8") or 8)

# Words kept per evidence page. A case study has to carry enough prose to name the
# customer's size, industry and what they moved off; the homepage cap stays lower
# because its copy is repetitive.
DISCOVERY_PAGE_WORDS = 350

# Below this, the main-content extractor is treated as having failed and the raw page
# text is used instead. A real page of prose clears it easily; a thin one is better
# read whole, boilerplate included, than not at all.
_MIN_MAIN_TEXT_WORDS = 40

# Buyer-evidence pages that have to be read before an empty buyer profile is treated as a
# failure rather than as a site that simply does not publish one.
_SILENT_FAILURE_MIN_ICP_PAGES = 2

# Paths that name competitors, and the conventional spellings worth probing directly when
# the homepage links none of them.
_COMPARISON_HINTS = ("/vs-", "/vs/", "/compare", "/comparison", "/alternative", "/migrate", "/switch")
COMPARISON_PROBE_PATHS = ["/alternatives", "/compare", "/comparison", "/competitors"]
_MAX_COMPARISON_PAGES = 2

# Sitemap reading. A sitemap is the site's own published index, so it lists the pages the
# homepage never links - which is where comparison pages and older case studies live. The
# caps exist because a large site's sitemap indexes tens of thousands of URLs and none of
# this is worth an unbounded read.
SITEMAP_PATHS = ["/sitemap.xml", "/sitemap_index.xml"]
_MAX_SITEMAP_DOCS = 5
_MAX_SITEMAP_URLS = 2000
# Matches the page-fetch timeout. At 8s this timed out during DNS resolution on a site
# whose pages fetched fine, and a timeout is indistinguishable from an absent sitemap in
# the result: both produce no URLs.
_SITEMAP_TIMEOUT = 12

_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
_SITEMAP_INDEX_RE = re.compile(r"<sitemapindex", re.I)
_ROBOTS_SITEMAP_RE = re.compile(r"^\s*sitemap:\s*(\S+)", re.I | re.M)

_ASSET_SUFFIXES = re.compile(r'\.(pdf|png|jpg|jpeg|svg|css|js|webp|gif|zip|xml|ico|mp4|woff2?)$')


def _sanitize_slug(text: str) -> str:
    """Generate safe identifier string for file paths and vertical IDs."""
    clean = re.sub(r'[^a-zA-Z0-9_]+', '_', text.strip().lower())
    return clean.strip('_')[:50] or "custom_vertical"


def _summarize_html(url: str, domain: str, html: str, word_cap: int = 250) -> Dict[str, Any]:
    """High-signal structure from markup already fetched: title, meta, headings, prose."""
    soup = BeautifulSoup(html, "html.parser")

    # Clean non-content elements
    for el in soup(["script", "style", "nav", "footer", "noscript", "svg"]):
        el.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    meta_desc = ""
    m_tag = soup.find("meta", attrs={"name": re.compile(r"description", re.I)}) or             soup.find("meta", attrs={"property": "og:description"})
    if m_tag and m_tag.get("content"):
        meta_desc = m_tag["content"].strip()

    raw_headings = []
    for h in soup.find_all(["h1", "h2"]):
        h_text = h.get_text(strip=True)
        if h_text and len(h_text) > 3:
            raw_headings.append(h_text)

    # A modern marketing site puts its mega-menu in <div>s, not <nav>, so stripping tags
    # by name leaves it in the text - and it is long enough to fill the whole window
    # before any of the page's own prose is reached. A case study read this way arrives
    # as a list of product menu items, which is why nothing on it could be quoted.
    main_text = ""
    try:
        main_text = trafilatura.extract(html) or ""
    except Exception as err:
        logger.debug("trafilatura could not extract %s: %s", url, err)

    if len(main_text.split()) >= _MIN_MAIN_TEXT_WORDS:
        body_text = main_text
        # Menu labels do not appear in the extracted main content, so this drops them
        # while keeping the page's real headings.
        # No fallback if nothing matches: an empty heading list costs the prompt little,
        # while a fallback to the raw list puts the menu labels back in.
        headings = [h for h in raw_headings if h in main_text]
    else:
        body_text = soup.get_text(separator=" ", strip=True)
        headings = raw_headings

    body_snippet = " ".join(body_text.split()[:word_cap])

    return {
        "url": url,
        "domain": domain,
        "title": title,
        "meta_description": meta_desc,
        "headings": headings[:8],
        "body_snippet": body_snippet
    }


def extract_page_summary(url: str, timeout: float = 12.0) -> Dict[str, Any]:
    """
    Crawls the target URL and extracts high-signal structural elements:
    title, meta description, H1/H2 headings, and hero body copy.
    """
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path

    try:
        html = smart_fetch(url, timeout=int(timeout))
    except Exception as e:
        logger.warning("Failed to fetch %s: %s. Using URL fallback.", url, e)
        return {
            "url": url,
            "domain": domain,
            "title": domain,
            "meta_description": "",
            "headings": [],
            "body_snippet": f"B2B SaaS software platform operating at {domain}"
        }

    return _summarize_html(url, domain, html)


def _is_comparison_page(url: str) -> bool:
    """Is this the kind of page that names a competitor?"""
    path = urlparse(url).path.rstrip("/").lower()
    return any(hint in path for hint in _COMPARISON_HINTS)


def _probe_comparison_pages(base_url: str) -> List[Tuple[str, str]]:
    """Try the conventional comparison URLs directly, for sites that do not link them.

    Ranking only sees what the homepage links, and a vendor rarely links "Us vs Them" from
    its own front page - which is how a run that read seven case studies came back with no
    competitor evidence at all. These paths are conventional enough to be worth four
    requests when nothing else on the page offers one.
    """
    parsed = urlparse(base_url)
    origin = "%s://%s" % (parsed.scheme, parsed.netloc)

    found: List[Tuple[str, str]] = []
    for path in COMPARISON_PROBE_PATHS:
        candidate = origin + path
        try:
            validate_url_for_fetch(candidate)
            html = smart_fetch(candidate, timeout=12)
        except Exception as err:
            logger.debug("[Discovery] No comparison page at %s: %s", candidate, err)
            continue
        found.append((candidate, html))
        if len(found) >= _MAX_COMPARISON_PAGES:
            break
    return found


def _is_icp_evidence_page(url: str) -> bool:
    """Is this one of the pages a buyer profile is supposed to be readable from?"""
    path = urlparse(url).path.rstrip("/").lower()
    return any(hint in path for hint in ICP_EVIDENCE_PATHS)


def _is_editorial(path: str) -> bool:
    """Content marketing rather than a statement about the business.

    These carry the same words as the pages worth reading - a glossary compares two
    metrics, a blog post names a competitor in passing - without being evidence of who
    buys or who is competed against.
    """
    return any(hint in path for hint in ICP_EDITORIAL_PATHS)


def _rank_urls(base_url: str, urls: List[str], limit: Optional[int] = None) -> List[str]:
    """Same-site candidate pages, ICP-bearing ones first.

    Ranked rather than filtered: a site that names none of the expected paths should still
    contribute some pages rather than none. Ordering follows `ICP_EVIDENCE_PATHS`, so a
    case study outranks a pricing page, which outranks an unclassified page. The sort is
    stable, so within one rank the caller's order decides - which is how a page the
    homepage links stays ahead of one only the sitemap knows about.
    """
    parsed = urlparse(base_url)
    base_netloc = parsed.netloc.replace("www.", "").lower()

    scored: List[Tuple[int, str]] = []
    seen = {(parsed.path.rstrip("/").lower() or "/")}

    for candidate in urls:
        parsed_full = urlparse(candidate)
        if parsed_full.scheme not in ("http", "https"):
            continue
        # A sitemap may list other hosts, and a link may point off-site. Neither is this
        # company's own evidence.
        if parsed_full.netloc.replace("www.", "").lower() != base_netloc:
            continue

        path = parsed_full.path.rstrip("/").lower()
        if not path or path in seen or _ASSET_SUFFIXES.search(path):
            continue
        seen.add(path)

        rank = len(ICP_EVIDENCE_PATHS)
        if not _is_editorial(path):
            for i, hint in enumerate(ICP_EVIDENCE_PATHS):
                if hint in path:
                    rank = i
                    break
        scored.append((rank, parsed_full._replace(fragment="", query="").geturl()))

    scored.sort(key=lambda item: item[0])
    ranked = [url for _, url in scored]
    return ranked if limit is None else ranked[:limit]


def _evidence_group(url: str) -> Optional[str]:
    """Which buyer field this page is likely to feed, by its path."""
    path = urlparse(url).path.rstrip("/").lower()
    if _is_editorial(path):
        # No group, so it can never claim a slot reserved for real evidence.
        return None
    for group, hints in ICP_EVIDENCE_GROUPS.items():
        if any(hint in path for hint in hints):
            return group
    return None


def _select_evidence_urls(base_url: str, urls: List[str], limit: int) -> List[str]:
    """Choose the pages to read, reserving slots for each kind of evidence.

    Taking the top `limit` by rank looked reasonable and starved the scarce kinds. A
    vendor with hundreds of case studies and a dozen comparison pages gave every slot to
    case studies, so `known_competitors` came back empty on a site that had named its
    competitors fourteen times - the pages were found and then never read.

    Reserves are floors. A kind with nothing to offer hands its slots back, and whatever
    is left over is filled in rank order, so this never reads less than before.
    """
    if limit <= 0:
        return []

    ranked = _rank_urls(base_url, urls)

    buckets: Dict[str, List[str]] = {group: [] for group in ICP_EVIDENCE_GROUPS}
    for url in ranked:
        group = _evidence_group(url)
        if group:
            buckets[group].append(url)

    selected: List[str] = []
    for group, share in ICP_EVIDENCE_RESERVE.items():
        # At least one slot for any kind the site actually offers: one comparison page
        # typically names several competitors, so the first is worth far more than the
        # second.
        reserve = max(1, int(limit * share))
        selected.extend(buckets.get(group, [])[:reserve])

    chosen = set(selected)
    for url in ranked:
        if len(selected) >= limit:
            break
        if url not in chosen:
            selected.append(url)
            chosen.add(url)

    # Back into rank order, so the strongest evidence leads the prompt.
    order = {url: i for i, url in enumerate(ranked)}
    selected.sort(key=lambda u: order.get(u, len(order)))
    return selected[:limit]


def _rank_evidence_links(page_url: str, html: str, limit: Optional[int] = None) -> List[str]:
    """Internal links from one page, ICP-bearing ones first."""
    soup = BeautifulSoup(html, "html.parser")
    hrefs = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        hrefs.append(urljoin(page_url, href))
    return _rank_urls(page_url, hrefs, limit)


def _sitemap_documents(base_url: str) -> List[str]:
    """Where this site's sitemap might be: the conventional paths, then robots.txt.

    robots.txt is read for its `Sitemap:` directive only. That line exists to be read by
    crawlers and is the site's own statement of where its index lives, which beats guessing
    at paths.
    """
    parsed = urlparse(base_url)
    origin = "%s://%s" % (parsed.scheme, parsed.netloc)
    documents = [origin + path for path in SITEMAP_PATHS]

    try:
        validate_url_for_fetch(origin + "/robots.txt")
        robots = smart_fetch(origin + "/robots.txt", timeout=_SITEMAP_TIMEOUT)
    except Exception as err:
        logger.debug("[Discovery] No robots.txt for %s: %s", origin, err)
        return documents

    for declared in _ROBOTS_SITEMAP_RE.findall(robots or ""):
        if declared not in documents:
            documents.append(declared)
    return documents


def fetch_sitemap_urls(base_url: str) -> List[str]:
    """Page URLs the site publishes in its own sitemap. Never raises.

    Follows a sitemap index one level down to the sitemaps it names. Compressed sitemaps
    are skipped: `smart_fetch` returns text, so a .gz would arrive as binary noise, and
    handling it properly is a separate job from finding evidence pages.
    """
    found: List[str] = []
    pending = _sitemap_documents(base_url)
    fetched = 0
    seen_docs = set()

    while pending and fetched < _MAX_SITEMAP_DOCS and len(found) < _MAX_SITEMAP_URLS:
        document = pending.pop(0)
        if document in seen_docs or document.endswith(".gz"):
            continue
        seen_docs.add(document)

        # Counted before the attempt, not after: a miss costs a request just as a hit does,
        # and a site offering many dead candidates should not get unlimited tries.
        fetched += 1
        try:
            validate_url_for_fetch(document)
            body = smart_fetch(document, timeout=_SITEMAP_TIMEOUT)
        except Exception as err:
            logger.debug("[Discovery] No sitemap at %s: %s", document, err)
            continue

        locations = _LOC_RE.findall(body or "")
        if _SITEMAP_INDEX_RE.search(body or ""):
            # An index names other sitemaps, not pages. Queue them instead of reading
            # their URLs as content.
            pending.extend(loc for loc in locations if loc not in seen_docs)
            continue

        found.extend(locations)

    if found:
        logger.info("[Discovery] Sitemap offered %d URLs for %s", len(found), base_url)
    return found[:_MAX_SITEMAP_URLS]


def gather_discovery_evidence(
    url: str,
    max_pages: int = DISCOVERY_EVIDENCE_PAGES,
    timeout: float = 12.0
) -> Dict[str, Any]:
    """Read the homepage and the buyer-evidence pages it links to.

    Discovery used to infer an entire customer profile from one hero section, which is
    why it returned the generic segment bands: there was nothing on the page that could
    have told it anything sharper. Who buys is stated on case studies, customer stories
    and comparison pages, so those are what this reads.

    The site's own sitemap is read too, because the homepage does not link everything: on
    chargebee.com the homepage offered seven case studies and no comparison page at all,
    and the competitor field came back empty as a result.

    Comparison pages are the last exception: when neither the links nor the sitemap offers
    one, the conventional URLs are probed directly.

    Only fetches, never the extractor - about a second a page against roughly sixteen for
    a GLiNER pass - so widening the evidence this way costs seconds, not the eleven
    minutes a full crawl takes.
    """
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path

    pages: List[Dict[str, Any]] = []
    failed = 0

    try:
        home_html = smart_fetch(url, timeout=int(timeout))
    except Exception as e:
        logger.warning("Failed to fetch %s: %s. Discovery falls back to the URL alone.", url, e)
        return {
            "url": url,
            "domain": domain,
            "pages": [{
                "url": url,
                "title": domain,
                "meta_description": "",
                "headings": [],
                "body_snippet": f"B2B SaaS software platform operating at {domain}"
            }],
            "pages_read": 0,
            "pages_failed": 1,
            "icp_pages_read": 0,
        }

    pages.append(_summarize_html(url, domain, home_html))

    budget = max(0, max_pages - 1)

    # Homepage links first, then whatever the sitemap adds. Both go through one ranking so
    # a case study only the sitemap knows about still outranks a linked pricing page, while
    # a linked page wins ties: the homepage links what the company considers important.
    # Every candidate goes into one selection. Truncating the homepage links to the budget
    # first threw away comparison pages before the sitemap could add more of them.
    candidates = _rank_evidence_links(url, home_html)
    candidates.extend(fetch_sitemap_urls(url))
    ranked = _select_evidence_urls(url, candidates, budget)

    # A comparison page is where competitors are named, and it is the one kind of evidence
    # page a vendor tends not to link from its homepage. Probe for it rather than let the
    # competitor field go empty by default.
    probed: List[Tuple[str, str]] = []
    if budget and not any(_is_comparison_page(u) for u in ranked):
        probed = _probe_comparison_pages(url)
        if probed:
            ranked = ranked[:max(0, budget - len(probed))]

    for probe_url, probe_html in probed:
        pages.append(_summarize_html(probe_url, domain, probe_html, word_cap=DISCOVERY_PAGE_WORDS))

    for link in ranked:
        try:
            validate_url_for_fetch(link)
            html = smart_fetch(link, timeout=int(timeout))
        except Exception as e:
            logger.info("[Discovery] Skipped evidence page %s: %s", link, e)
            failed += 1
            continue
        pages.append(_summarize_html(link, domain, html, word_cap=DISCOVERY_PAGE_WORDS))

    icp_pages = sum(1 for p in pages if _is_icp_evidence_page(p["url"]))
    logger.info("[Discovery] Read %d evidence pages for %s (%d of them buyer pages, %d failed)",
                len(pages), domain, icp_pages, failed)
    return {
        "url": url,
        "domain": domain,
        "pages": pages,
        "pages_read": len(pages),
        "pages_failed": failed,
        # Read several of these and still find no buyer claims, and the extractor is the
        # likelier explanation - which is what the confidence score has to reflect.
        "icp_pages_read": icp_pages,
    }


# Two kinds of field, answered two different ways. Category vocabulary may be inferred
# from knowledge of the market; who buys may not, and has to cite a page that was read.
PROMPT_TEMPLATE = """
You are an expert enterprise B2B SaaS ontology architect and market taxonomist.
Below are {page_count} pages read from the website of a B2B company at {domain}.
Each page is marked with the URL it came from.
{brand_clause}

{evidence_block}

Synthesize an Industry Ontology configuration for this company.
Return ONLY ONE valid, raw JSON object containing EVERY key listed below (without markdown
fences, or with a standard json fence). Do not return more than one object, and do not omit
any key.

The keys fall into two groups, and they are answered differently.

GROUP 1 - CATEGORY KEYS. What this class of product does. Answer from the evidence above
together with your knowledge of the category. Each is a plain list of strings.

These describe the product being sold, and the pages include customer stories. A customer's
own suppliers, sales channels, tools, metrics and markets are that customer's, not this
product's: never put them in a category key. If a case study says a customer sells through
Amazon and Walmart, those are the customer's sales channels - they are not integrations of
the product this profile is about.

GROUP 2 - BUYER KEYS, exactly these four: known_segments, known_industries,
known_competitors, known_replaces. Who actually buys this product and what they leave
behind. These are NOT answered from your knowledge of the market. Each entry is an object:

  {{"value": "...", "source_url": "one of the PAGE URLs above", "quote": "text copied verbatim from that page"}}

Rules for the four buyer keys, which override anything else here:
- The quote must appear word-for-word on the page named by source_url. Do not paraphrase.
- source_url must be one of the PAGE URLs listed above. Do not cite any other page.
- If the pages do not establish one of these keys, return an empty list for that key. The
  key must still be present.
- An empty list is a CORRECT answer. A plausible guess is a WRONG answer.
- Prefer what the page actually says over the category-standard phrasing. If a customer is
  described as "a 400-person company running three subsidiaries", that specificity is the
  answer; do not compress it into a generic market band.

The single object to return:

{{
  "brand_name": "Official brand or company name (e.g. Snyk, Gusto, Snowflake)",
  "vertical_id": "slug_format_vertical_name (e.g. cybersecurity_devsecops, hr_payroll, cloud_data_warehouse)",
  "display_name": "Formal Category Display Name (e.g. Developer Security & DevSecOps)",
  "category": "High level industry category (e.g. Cybersecurity, Human Resources, Cloud Infrastructure, Fintech)",
  "gliner_labels": [
    "List of 6 distinct, high-signal NER entity labels tailored to this vertical (e.g. 'Security Platform', 'Threat Vector', 'Security Standard', 'Integration Partner', 'Deployment Model', 'Pricing Model')"
  ],
  "core_seed_concepts": [
    "List of 10 to 12 essential domain concepts, technical standards, or core functional capabilities that define this category"
  ],
  "known_compliance": [
    "List of 6 to 8 standard regulatory, compliance, or security frameworks expected in this vertical (e.g. SOC 2, ISO 27001, FedRAMP, HIPAA, GDPR, PCI-DSS, NIST)"
  ],
  "known_integrations": [
    "List of 8 to 12 prominent software platforms or ecosystem tools that solutions in this space typically integrate with. Systems THIS product connects to. A marketplace, channel, supplier or tool that belongs to a customer described in a case study is not one of them: if a page says a customer sells through Amazon, Walmart or eBay, those are that customer's sales channels and must not appear here."
  ],
  "known_pricing": [
    "List of 3 to 5 common B2B pricing models for this vertical (e.g. 'Per-Developer Pricing', 'Usage-Based Ingestion', 'Tiered Enterprise')"
  ],
  "known_features": [
    "List of 6 to 10 discrete functional features standard in this vertical (e.g. 'Role-Based Access Control', 'Single Sign-On', 'Audit Logging', 'Automated Reporting')"
  ],
  "known_deployment": [
    "List of 3 to 5 hosting/deployment architectures common in this space (e.g. 'Cloud-Native', 'Multi-Tenant SaaS', 'Private Cloud', 'On-Premise')"
  ],
  "known_sla": [
    "List of 2 to 4 standard reliability or uptime SLA guarantees (e.g. '99.9% Uptime', '99.99% Uptime', '24/7 Support')"
  ],
  "concept_hierarchy": {{
    "Object mapping each narrower concept to the broader one it sits under, for example mapping 'Prior Authorization' to 'Revenue Cycle Management'. Add an entry for every core_seed_concept and known_automation item that belongs under a broader umbrella; a parent may be a term not otherwise listed. Never create cycles. Omit concepts with no natural parent."
  }},
  "known_automation": [
    "List of 8 to 12 core operational capabilities that products in this vertical automate, phrased as the buyer would name them (e.g. 'Vulnerability Scanning', 'Clinical Documentation', 'Payroll Runs', 'Revenue Recognition'). These are what marketing claims are checked against, so favour concrete workflows over abstract benefits."
  ],
  "suggested_competitors": [
    "List of 3 to 5 real-world direct market competitors in this exact vertical. Your own market knowledge, not read from the pages."
  ],
  "known_segments": [
    {{"value": "A KIND of organisation this product is sold to, at a level that would fit more than one customer. Abstract one step up from the page: a page reading 'MercyCare, a 60-clinic dialysis network staffing 400 travelling nurses' supports the value 'multi-site healthcare provider with a contingent workforce'. Not one customer's own description, not a headcount or usage figure, and never the vendor's own total customer count.", "source_url": "PAGE URL", "quote": "verbatim text from that page describing that customer"}}
  ],
  "known_industries": [
    {{"value": "Industry or sector of a customer these pages actually name.", "source_url": "PAGE URL", "quote": "verbatim text from that page"}}
  ],
  "known_competitors": [
    {{"value": "A vendor these pages name as an alternative, a comparison, or a product a customer moved off.", "source_url": "PAGE URL", "quote": "verbatim text from that page"}}
  ],
  "known_replaces": [
    {{"value": "The manual practice, spreadsheet, or legacy system a customer is described as leaving behind.", "source_url": "PAGE URL", "quote": "verbatim text from that page"}}
  ],
  "summary": "2-sentence executive summary of the discovered market category and ontological scope."
}}
"""


def _render_evidence(evidence: Dict[str, Any]) -> str:
    """The pages, each labelled with the URL it came from.

    The URL is not decoration: buyer claims have to cite one, and a citation is only
    accepted if it names a page actually in this block.
    """
    blocks: List[str] = []
    for i, page in enumerate(evidence.get("pages") or [], start=1):
        headings = "; ".join(page.get("headings") or [])
        blocks.append(
            "=== PAGE %d - %s ===\n"
            "Title: %s\n"
            "Meta: %s\n"
            "Headings: %s\n"
            "Copy: %s" % (
                i, page.get("url", ""), page.get("title", ""),
                page.get("meta_description", ""), headings, page.get("body_snippet", "")
            )
        )
    return "\n\n".join(blocks)


def build_discovery_prompt(evidence: Dict[str, Any], brand_hint: Optional[str] = None) -> str:
    """The discovery prompt: category fields from knowledge, buyer fields from evidence.

    The split is the point. Category vocabulary is safe to infer - a billing platform has
    invoices whether or not the homepage says so. Who buys is not: inferred, it comes back
    as the generic bands every vendor would return, which is exactly what the previous
    single-page prompt produced by offering 'Enterprise', 'Mid-Market', 'SMB' as examples
    and getting them back. Buyer fields now have to cite a page and quote it.
    """
    domain = evidence.get("domain", "")
    page_count = len(evidence.get("pages") or [])
    brand_clause = ("The user indicates the brand name is '%s'." % brand_hint) if brand_hint else ""

    return PROMPT_TEMPLATE.format(
        page_count=page_count,
        domain=domain,
        brand_clause=brand_clause,
        evidence_block=_render_evidence(evidence),
    )


def call_gemini_industry_discovery(
    evidence: Dict[str, Any],
    brand_hint: Optional[str] = None
) -> Dict[str, Any]:
    """
    Prompts Google Gemini 2.5 Flash to synthesize the Industry Ontology
    and competitive market taxonomy from the pages read for this domain.
    """
    domain = evidence.get("domain", "")
    prompt = build_discovery_prompt(evidence, brand_hint=brand_hint)

    response_text = vertex_ai_client._call_gemini(
        prompt=prompt,
        system_instruction="You are an autonomous enterprise B2B ontology extraction system. Output valid JSON only.",
        temperature=0.1,
        # Buyer claims now carry a verbatim quote each, so the response is far longer than
        # it was when every field was a bare string. Truncated JSON does not fail loudly:
        # it fails to parse, and the fallback returns a profile with no buyer half at all.
        max_output_tokens=8192,
        timeout=60
    )

    if not response_text:
        # Fallback if Gemini is unavailable. The category fields are generic on purpose;
        # the buyer fields are absent on purpose. A fabricated ICP is worse than none,
        # because nothing downstream can tell a fabricated one from a real one.
        brand = brand_hint or domain.split(".")[0].capitalize()
        slug = _sanitize_slug(domain.split(".")[0])
        return {
            "brand_name": brand,
            "vertical_id": "saas_%s" % slug,
            "display_name": "%s Enterprise SaaS" % brand,
            "category": "Enterprise B2B SaaS",
            "gliner_labels": ["Software Platform", "Feature", "Integration Partner", "Compliance Standard", "Pricing Model", "Deployment Model"],
            "core_seed_concepts": ["API Integration", "Workflow Automation", "Analytics", "Role-Based Access Control", "Data Security", "Cloud Architecture"],
            "known_compliance": ["SOC 2 Type II", "ISO 27001", "GDPR", "CCPA"],
            "known_integrations": ["Salesforce", "Slack", "AWS", "Google Cloud", "Microsoft Azure"],
            "known_pricing": ["Subscription Pricing", "Usage-Based Pricing", "Tiered Enterprise"],
            "known_features": ["Role-Based Access Control", "Single Sign-On", "Audit Trail", "Custom Reporting", "API Access"],
            "known_deployment": ["Cloud-Native", "Multi-Tenant SaaS"],
            "known_sla": ["99.9% Uptime", "24/7 Support"],
            "known_segments": [],
            "known_industries": [],
            "known_competitors": [],
            "known_replaces": [],
            "known_automation": ["Workflow Automation", "Reporting", "User Provisioning", "Data Sync", "Alerting"],
            "suggested_competitors": [],
            "summary": "Autonomous ontology profile generated for %s based on domain structure." % brand,
            "discovery_degraded": "Gemini unavailable: category defaults only, no buyer profile."
        }

    # Clean JSON
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except Exception as ex:
        logger.error("Failed to parse Gemini industry JSON: %s. Text was:\n%s", ex, response_text)
        brand = brand_hint or domain.split(".")[0].capitalize()
        return {
            "brand_name": brand,
            "vertical_id": "saas_%s" % _sanitize_slug(brand),
            "display_name": "%s SaaS Platform" % brand,
            "category": "B2B Software",
            "gliner_labels": ["Software Platform", "Feature", "Integration Partner", "Compliance Standard", "Pricing Model"],
            "core_seed_concepts": ["Automation", "Integration", "Security", "Scalability"],
            "known_compliance": ["SOC 2", "ISO 27001", "GDPR"],
            "known_integrations": ["Slack", "Salesforce"],
            "known_pricing": ["Tiered Pricing", "Usage-Based"],
            "known_automation": ["Workflow Automation", "Reporting", "Data Sync"],
            "known_segments": [],
            "known_industries": [],
            "known_competitors": [],
            "known_replaces": [],
            "suggested_competitors": [],
            "summary": "Fallback profile for %s." % brand,
            "discovery_degraded": "Discovery output was not valid JSON: category defaults only, no buyer profile."
        }


ICP_FIELDS = ("known_segments", "known_industries", "known_competitors", "known_replaces")

# How much of a quote has to be found on the cited page. A model reproducing the opening
# of a sentence it actually read is the signal; requiring the whole quote would fail on
# trailing punctuation and whitespace the extractor already normalised away.
_QUOTE_MATCH_CHARS = 60


# How much of a claimed value has to turn up in its own quote. Words are compared on a
# five-character prefix so "invoicing" matches "invoiced", and half the content words is
# enough because a value is usually a compressed restatement rather than a substring.
_ENTAILMENT_PREFIX = 5
_ENTAILMENT_MIN_RATIO = 0.5

# known_segments is the exception, and the exception is structural rather than a loosened
# standard. Every other buyer field names something the page names - a competitor, an
# industry, a displaced practice - so the value should be lexically present in the sentence
# that proves it. A segment is a deliberate generalisation over one customer's description,
# so requiring it to appear in its own quote would reject exactly the answers worth having.
# What still holds for it: the quote is real, and it is on the page cited.
_FIELD_SUPPORT_RATIO = {"known_segments": 0.0}


def _quote_supports_value(value: str, quote: str, min_ratio: float = _ENTAILMENT_MIN_RATIO) -> bool:
    """Does this quote actually say this, or merely sit on the same page?

    A live run produced the competitor "Zuora" cited to the sentence "switching from a
    legacy subscription billing platform to Chargebee" - a real sentence, really on that
    page, which never names Zuora. Checking that the quote exists catches invented
    evidence; it does not catch real evidence attached to an inferred answer, which is the
    same fabrication wearing a citation.
    """
    if min_ratio <= 0:
        return True

    normalized_value = _normalize_for_match(value)
    normalized_quote = _normalize_for_match(quote)
    if not normalized_value or not normalized_quote:
        return False

    value_words = re.findall(r"[a-z0-9]+", normalized_value)
    content_words = [w for w in value_words if len(w) >= 4]
    if not content_words:
        # An acronym or short vendor name carries no matchable stem, so it has to appear
        # as written: "SAP" is either in the sentence or it is not.
        return normalized_value in normalized_quote

    quote_prefixes = {w[:_ENTAILMENT_PREFIX] for w in re.findall(r"[a-z0-9]+", normalized_quote)}
    hits = sum(1 for w in content_words if w[:_ENTAILMENT_PREFIX] in quote_prefixes)
    return (hits / len(content_words)) >= min_ratio


def _normalize_for_match(text: Optional[str]) -> str:
    """Lowercase, collapse whitespace, and flatten the quote marks HTML likes to curl."""
    flattened = (text or "").replace("\u2018", "'").replace("\u2019", "'")
    flattened = flattened.replace("\u201c", '"').replace("\u201d", '"')
    flattened = flattened.replace("\u2013", "-").replace("\u2014", "-")
    return " ".join(flattened.lower().split())


def split_evidence_claims(
    raw_items: Any,
    pages: List[Dict[str, Any]],
    min_support_ratio: float = _ENTAILMENT_MIN_RATIO
) -> Tuple[List[str], Dict[str, Dict[str, str]], Dict[str, Any]]:
    """Separate buyer claims into values and verified evidence.

    A cited page has to be one we actually read, and the quote has to appear on it. The
    model is shown page text and asked to quote it; a quote that is nowhere in that text
    was composed, not read, and composed ICP data is the exact failure this change exists
    to stop. Such a claim is not discarded - it is kept as an unevidenced value, so the
    caller can see what was proposed and what was proven, and tell them apart.

    Returns (values, evidence, stats).
    """
    page_text: Dict[str, str] = {}
    for page in pages or []:
        url = page.get("url") or ""
        if not url:
            continue
        combined = " ".join([
            page.get("title") or "",
            page.get("meta_description") or "",
            " ".join(page.get("headings") or []),
            page.get("body_snippet") or "",
        ])
        page_text[url] = _normalize_for_match(combined)

    values: List[str] = []
    evidence: Dict[str, Dict[str, str]] = {}
    seen = set()
    stats = {"proposed": 0, "evidenced": 0, "bad_url": 0, "quote_not_found": 0, "unsupported": 0}

    for item in raw_items or []:
        if isinstance(item, str):
            value, source_url, quote = item.strip(), "", ""
        elif isinstance(item, dict):
            value = str(item.get("value") or "").strip()
            source_url = str(item.get("source_url") or "").strip()
            quote = str(item.get("quote") or "").strip()
        else:
            continue

        if not value:
            continue

        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        values.append(value)
        stats["proposed"] += 1

        if not source_url or not quote:
            continue
        if source_url not in page_text:
            # A citation to a page this run never read proves nothing, whether it is a
            # hallucinated URL or a real page we did not open.
            stats["bad_url"] += 1
            continue

        needle = _normalize_for_match(quote)[:_QUOTE_MATCH_CHARS]
        if not needle or needle not in page_text[source_url]:
            stats["quote_not_found"] += 1
            continue

        if not _quote_supports_value(value, quote, min_support_ratio):
            # On the page, but about something else. Counted apart from a missing quote
            # because it is a different failure: the page was read correctly and the
            # answer was still inferred.
            stats["unsupported"] += 1
            continue

        evidence[value] = {"source_url": source_url, "quote": quote[:300]}
        stats["evidenced"] += 1

    return values, evidence, stats


def resolve_buyer_fields(
    discovered: Dict[str, Any],
    pages: List[Dict[str, Any]]
) -> Tuple[Dict[str, List[str]], Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Verify every buyer field at once.

    Returns (values_by_field, evidence_by_field, stats) where stats totals across fields
    and carries the ratio the confidence score is built from.
    """
    values_by_field: Dict[str, List[str]] = {}
    evidence_by_field: Dict[str, Dict[str, Any]] = {}
    totals = {"proposed": 0, "evidenced": 0, "bad_url": 0, "quote_not_found": 0, "unsupported": 0}

    for field in ICP_FIELDS:
        values, evidence, stats = split_evidence_claims(
            discovered.get(field), pages,
            min_support_ratio=_FIELD_SUPPORT_RATIO.get(field, _ENTAILMENT_MIN_RATIO))
        values_by_field[field] = values
        if evidence:
            evidence_by_field[field] = evidence
        for k in totals:
            totals[k] += stats[k]

    totals["evidence_ratio"] = (
        round(totals["evidenced"] / totals["proposed"], 3) if totals["proposed"] else 0.0
    )
    return values_by_field, evidence_by_field, totals


def discovery_confidence(
    buyer_stats: Dict[str, Any],
    pages_read: int,
    icp_pages_read: int = 0
) -> float:
    """How much of this profile rests on pages that were actually read.

    Replaces a hardcoded 0.96. Two things move it: how many pages the run managed to read,
    and what share of the buyer claims carried a quote that held up.

    Proposing nothing is read in the light of what was available to read. A run that saw no
    case studies and returned no buyer profile is simply a site that does not publish one.
    A run that read several case studies and still returned nothing is far more often a
    broken extractor than a site with no customers - that is exactly what a boilerplate bug
    produced here, reporting 0.85 while the buyer profile was empty - so it scores at the
    floor rather than in the middle.
    """
    page_component = min(pages_read, DISCOVERY_EVIDENCE_PAGES) / float(DISCOVERY_EVIDENCE_PAGES)

    if buyer_stats.get("proposed"):
        evidence_component = buyer_stats.get("evidence_ratio", 0.0)
    elif icp_pages_read >= _SILENT_FAILURE_MIN_ICP_PAGES:
        evidence_component = 0.0
    else:
        # Nothing proposed and nothing that should have carried a proposal. Neither
        # rewarded nor punished: the category half still stands on the pages read.
        evidence_component = 0.5

    return round(0.4 + (0.3 * page_component) + (0.3 * evidence_component), 3)


def evidence_text(evidence: Dict[str, Any]) -> str:
    """Everything read this run, as one blob for vocabulary matching."""
    parts = []
    for page in evidence.get("pages") or []:
        parts.extend([
            page.get("title") or "",
            page.get("meta_description") or "",
            " ".join(page.get("headings") or []),
            page.get("body_snippet") or "",
        ])
    return "\n".join(p for p in parts if p)


async def ground_discovered_entities(
    compliance_list: List[str],
    integration_list: List[str]
) -> List[GroundedConcept]:
    """
    Asynchronously queries Wikidata to ground top compliance and integration entities
    with canonical Q-IDs and descriptions.
    """
    grounded: List[GroundedConcept] = []
    candidates = (compliance_list[:4] + integration_list[:4])

    tasks = [resolve_wikidata(c) for c in candidates]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for name, res in zip(candidates, results):
        if isinstance(res, dict) and res:
            # resolve_wikidata returns id/sameAs, not wikidata_id/wikidata_url. Reading
            # the wrong keys made every entity here come back with a description and no
            # Q-ID at all - grounding that reported itself as ungrounded.
            grounded.append(GroundedConcept(
                name=name,
                wikidata_id=res.get("id"),
                wikidata_url=res.get("sameAs"),
                description=res.get("description")
            ))
        else:
            grounded.append(GroundedConcept(name=name))

    return grounded


# A profile nobody curated holds only what discovery generates, so rediscovering it should
# refresh it. A curated profile holds work discovery cannot produce - concept definitions,
# alt-label sets - and these two keys are how one is recognised.
_CURATION_MARKERS = ("concepts", "alt_labels")

# What a vertical is called is a property of the category, not of whichever site was read
# most recently. Left mutable, six runs over one domain renamed the category six times.
_IDENTITY_KEYS = ("vertical_id", "display_name")


def _profile_is_curated(profile: Dict[str, Any]) -> bool:
    """Does this profile contain work that discovery could not have written?"""
    return any(profile.get(marker) for marker in _CURATION_MARKERS)


def _union(existing: Any, incoming: Any) -> Any:
    """Add what is new without dropping what is there.

    Lists keep their existing order and gain unseen values, compared case-insensitively so
    "Usage-Based Pricing" and "usage-based pricing" do not both survive. Dicts take new
    keys and keep the values already recorded.
    """
    if isinstance(existing, list) and isinstance(incoming, list):
        merged = list(existing)
        seen = {str(v).strip().lower() for v in existing}
        for value in incoming:
            key = str(value).strip().lower()
            if key and key not in seen:
                merged.append(value)
                seen.add(key)
        return merged
    if isinstance(existing, dict) and isinstance(incoming, dict):
        merged = dict(incoming)
        merged.update(existing)
        return merged
    return existing if existing else incoming


def merge_profile(
    existing: Optional[Dict[str, Any]],
    discovered: Dict[str, Any],
    matched_existing: bool = False
) -> Tuple[Dict[str, Any], str]:
    """Fold a discovered profile into whatever is already on disk.

    `save_vertical_configuration` used to build the file from scratch and write it whole,
    and overwriting an existing profile was explicitly allowed. Point discovery at a slug
    that matches a curated vertical and its 111 defined concepts and 85 alt-label sets were
    gone, silently - the two things discovery cannot regenerate and the prompt work depends
    on entirely.

    Four outcomes, because these are four different events:
      created           - nothing was there.
      refreshed         - the same site's own minted vertical, replaced with the newer
                          reading. Nothing accumulates because there is nothing to keep.
      accumulated       - a *different* site was matched into this vertical, so its
                          vocabulary is added rather than substituted. This is what lets a
                          category deepen with each customer instead of forking: without
                          it, the second billing site overwrites the first one's words and
                          the library never grows.
      merged-into-curated - a curated profile keeps everything it has and gains only the
                          keys it was missing or had empty.

    Keys discovery does not generate survive in every case.
    """
    if not existing:
        return discovered, "created"

    curated = _profile_is_curated(existing)
    # Starting from `existing` rather than from `discovered` is what preserves keys this
    # function has never heard of.
    merged = dict(existing)

    for key, value in discovered.items():
        if key not in merged:
            merged[key] = value
        elif curated:
            if not merged.get(key):
                merged[key] = value
        elif matched_existing:
            # Identity is the vertical's own, not the newcomer's: a site joining a
            # category does not get to rename it.
            if key not in _IDENTITY_KEYS:
                merged[key] = _union(merged.get(key), value)
        else:
            merged[key] = value

    if curated:
        return merged, "merged-into-curated"
    return merged, "accumulated" if matched_existing else "refreshed"


def save_vertical_configuration(
    vertical_id: str,
    display_name: str,
    gliner_labels: List[str],
    core_seed_concepts: List[str],
    known_integrations: List[str],
    known_compliance: List[str],
    known_pricing: List[str],
    known_automation: Optional[List[str]] = None,
    concept_hierarchy: Optional[Dict[str, str]] = None,
    known_features: Optional[List[str]] = None,
    known_segments: Optional[List[str]] = None,
    known_deployment: Optional[List[str]] = None,
    known_sla: Optional[List[str]] = None,
    known_replaces: Optional[List[str]] = None,
    known_industries: Optional[List[str]] = None,
    known_competitors: Optional[List[str]] = None,
    icp_evidence: Optional[Dict[str, Any]] = None,
    matched_existing: bool = False
) -> Tuple[str, str]:
    """
    Saves the discovered vertical configuration into verticals/<vertical_id>.json
    so that OntologyPipeline can immediately instantiate it.

    Returns (path, write_mode). See `merge_profile` for what the modes mean; a curated
    profile is never overwritten by a discovery run.
    """
    target_dir = verticals_dir()
    os.makedirs(target_dir, exist_ok=True)
    clean_id = _sanitize_slug(vertical_id)
    config_path = os.path.join(target_dir, f"{clean_id}.json")

    # /api/discover-industry writes one profile per call and is reachable by anyone who
    # can reach the API, so without a ceiling repeated calls fill the disk. Overwriting
    # an existing profile is always allowed; only creating a brand new one is capped.
    if not os.path.exists(config_path):
        existing = [f for f in os.listdir(target_dir) if f.endswith(".json")]
        if len(existing) >= MAX_VERTICAL_PROFILES:
            raise RuntimeError(
                f"Vertical profile limit reached ({MAX_VERTICAL_PROFILES}). "
                f"Remove unused profiles from verticals/ before discovering new ones."
            )

    config_data = {
        "vertical_id": clean_id,
        "display_name": display_name,
        "gliner_labels": gliner_labels,
        "mandatory_schema_types": [
            "SoftwareApplication",
            "Organization",
            "Offer"
        ],
        "core_seed_concepts": core_seed_concepts,
        "known_integrations": known_integrations,
        "known_compliance": known_compliance,
        "known_pricing": known_pricing,
        "known_features": known_features or [],
        "known_segments": known_segments or [],
        "known_deployment": known_deployment or [],
        "known_sla": known_sla or [],
        "known_replaces": known_replaces or [],
        # Discovery generated these two and then dropped them on the floor: they were
        # never written to the profile, so the buyer half of every auto-discovered
        # vertical was empty by construction rather than for want of evidence.
        "known_industries": known_industries or [],
        "known_competitors": known_competitors or [],
        # Which buyer values carried a quote we found on the page that was cited. Kept
        # beside the values so a later reader can tell a proven claim from a proposed one.
        "icp_evidence": icp_evidence or {},
        # Drives what triple extraction looks for on every site in this vertical.
        "known_automation": known_automation or [],
        # Lets coverage roll up: marketing a narrower concept counts as covering the
        # broader one it sits under.
        "concept_hierarchy": concept_hierarchy or {}
    }

    existing: Optional[Dict[str, Any]] = None
    if os.path.exists(config_path):
        try:
            with open(config_path, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                existing = loaded
            else:
                logger.warning("Existing profile %s is not an object; replacing it.", config_path)
        except Exception as err:
            # Unreadable is treated as absent, but never silently: a profile that cannot be
            # parsed is also a profile whose curated content cannot be protected.
            logger.warning("Could not read existing profile %s (%s); replacing it.", config_path, err)

    config_data, write_mode = merge_profile(existing, config_data, matched_existing=matched_existing)

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2)

    logger.info("Saved dynamic vertical config to %s (%s)", config_path, write_mode)
    if write_mode == "merged-into-curated":
        logger.info(
            "[Discovery] %s is curated: its existing vocabulary was kept and only empty "
            "keys were filled from this run.", config_path
        )
    return config_path, write_mode


async def discover_industry_profile_async(
    url: str,
    brand_hint: Optional[str] = None,
    save_config: bool = True
) -> IndustryDiscoveryResponse:
    """
    Full async orchestration pipeline for zero-shot industry discovery:
    1. Read the homepage and the buyer-evidence pages it links to
    2. LLM synthesis, buyer fields cited to those pages
    3. Verify each buyer claim against the page it cites
    4. Wikidata grounding
    5. Save configuration
    """
    # 1. Evidence
    evidence = gather_discovery_evidence(url)
    pages = evidence.get("pages") or []

    # 2. LLM synthesis
    discovered = call_gemini_industry_discovery(evidence, brand_hint=brand_hint)

    # 3. Verify the buyer half against the pages that were actually read
    buyer_values, icp_evidence, buyer_stats = resolve_buyer_fields(discovered, pages)
    if buyer_stats["bad_url"] or buyer_stats["quote_not_found"] or buyer_stats["unsupported"]:
        logger.warning(
            "[Discovery] %s: dropped evidence for %d buyer claims (%d cited an unread page, "
            "%d quoted text not on the cited page, %d quoted text that did not support the claim)",
            evidence.get("domain", ""),
            buyer_stats["bad_url"] + buyer_stats["quote_not_found"] + buyer_stats["unsupported"],
            buyer_stats["bad_url"],
            buyer_stats["quote_not_found"],
            buyer_stats["unsupported"],
        )

    brand_name = discovered.get("brand_name") or brand_hint or evidence["domain"]
    minted_id = _sanitize_slug(discovered.get("vertical_id") or f"custom_{brand_name}")
    display_name = discovered.get("display_name") or f"{brand_name} Vertical"

    # Match before mint. The model names a category from scratch every time, so the same
    # site read six times produced six vertical ids and four separate AI-security profiles
    # exist that barely share a word. A category that already has a home should be
    # deepened, not forked.
    match = match_existing_vertical(evidence_text(evidence))
    matched_existing = bool(match.get("vertical_id"))
    if matched_existing:
        vertical_id = match["vertical_id"]
        logger.info("[Discovery] %s joins existing vertical %r (%s); not minting %r.",
                    evidence.get("domain", ""), vertical_id, match.get("decision"), minted_id)
    else:
        vertical_id = minted_id
        logger.info("[Discovery] %s minted new vertical %r: %s",
                    evidence.get("domain", ""), vertical_id, match.get("reason", ""))
    category = discovered.get("category") or "B2B SaaS"
    gliner_labels = discovered.get("gliner_labels") or ["Software Platform", "Feature", "Compliance Standard", "Integration Partner"]
    core_seed_concepts = discovered.get("core_seed_concepts") or []
    known_compliance = discovered.get("known_compliance") or []
    known_integrations = discovered.get("known_integrations") or []
    known_pricing = discovered.get("known_pricing") or []
    known_features = discovered.get("known_features") or []
    known_deployment = discovered.get("known_deployment") or []
    known_sla = discovered.get("known_sla") or []
    known_automation = discovered.get("known_automation") or []

    known_segments = buyer_values["known_segments"]
    known_industries = buyer_values["known_industries"]
    known_competitors = buyer_values["known_competitors"]
    known_replaces = buyer_values["known_replaces"]

    concept_hierarchy = discovered.get("concept_hierarchy") or {}
    if not isinstance(concept_hierarchy, dict):
        logger.warning("Discarding non-dict concept_hierarchy from discovery output.")
        concept_hierarchy = {}
    suggested_competitors = discovered.get("suggested_competitors") or []
    discovery_summary = discovered.get("summary") or f"Discovered {display_name} ontology for {brand_name}."

    # 4. Ground entities
    grounded = await ground_discovered_entities(known_compliance, known_integrations)

    # 5. Save config
    config_file = None
    profile_write_mode = None
    if save_config:
        config_file, profile_write_mode = save_vertical_configuration(
            vertical_id=vertical_id,
            display_name=display_name,
            gliner_labels=gliner_labels,
            core_seed_concepts=core_seed_concepts,
            known_integrations=known_integrations,
            known_compliance=known_compliance,
            known_pricing=known_pricing,
            known_automation=known_automation,
            concept_hierarchy=concept_hierarchy,
            known_features=known_features,
            known_segments=known_segments,
            known_deployment=known_deployment,
            known_sla=known_sla,
            known_replaces=known_replaces,
            known_industries=known_industries,
            known_competitors=known_competitors,
            icp_evidence=icp_evidence,
            matched_existing=matched_existing
        )

    return IndustryDiscoveryResponse(
        url=evidence["url"],
        brand_name=brand_name,
        vertical_id=vertical_id,
        display_name=display_name,
        category=category,
        category_name=category,
        core_seed_concepts=core_seed_concepts,
        known_compliance=known_compliance,
        compliance_frameworks=known_compliance,
        known_integrations=known_integrations,
        ecosystem_integrations=known_integrations,
        known_pricing=known_pricing,
        known_features=known_features,
        known_segments=known_segments,
        known_deployment=known_deployment,
        known_sla=known_sla,
        known_replaces=known_replaces,
        known_industries=known_industries,
        known_competitors=known_competitors,
        icp_evidence=icp_evidence,
        evidence_urls=[p.get("url", "") for p in pages],
        pages_read=evidence.get("pages_read", 0),
        pages_failed=evidence.get("pages_failed", 0),
        buyer_claims_proposed=buyer_stats["proposed"],
        buyer_claims_evidenced=buyer_stats["evidenced"],
        gliner_labels=gliner_labels,
        suggested_competitors=suggested_competitors,
        direct_competitors=suggested_competitors,
        grounded_entities=grounded,
        config_file=config_file,
        profile_write_mode=profile_write_mode,
        vertical_match_mode="matched-existing" if matched_existing else "minted-new",
        vertical_match_reason=match.get("reason"),
        # What would have been created, reported only when it was not: after a mint,
        # vertical_id already is that id.
        minted_vertical_id=minted_id if matched_existing else None,
        confidence_score=discovery_confidence(
            buyer_stats,
            evidence.get("pages_read", 0),
            evidence.get("icp_pages_read", 0)
        ),
        discovery_degraded=discovered.get("discovery_degraded"),
        discovery_summary=discovery_summary,
        domain_scope_description=discovery_summary
    )


def discover_industry_profile(
    url: str,
    brand_hint: Optional[str] = None,
    save_config: bool = True
) -> IndustryDiscoveryResponse:
    """Sync wrapper for discover_industry_profile_async."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(discover_industry_profile_async(url, brand_hint, save_config))
        return loop.run_until_complete(discover_industry_profile_async(url, brand_hint, save_config))
    except RuntimeError:
        return asyncio.run(discover_industry_profile_async(url, brand_hint, save_config))
