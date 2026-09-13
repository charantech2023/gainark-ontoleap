"""
GainARK OntoLeap - Crawl Planning
=================================
Which pages a site crawl reads, in what order, and when it has read enough.

The site crawl used to go breadth-first from the homepage, reading any link under a
handful of "deep" paths first. Replaying the stored crawls page by page showed what that
cost. On ordwaylabs.com the first ten pages covered 52 vertical concepts and the next
thirty added 12, most of the budget going to resource-hub index pages. On chargebee.com
fifteen customer stories in a row added one concept, and the five pages after them added
eight. The sitemap offered 414 pages on Ordway, including both comparison pages and
seventeen customer stories the crawl never reached.

So a crawl is planned rather than walked:

  candidates  the site's sitemap, the sitemap of each docs or help host it links to, and
              every link found on a page read so far - deduplicated, same brand only,
              translations and pages that make no product claim left out
  kinds       each candidate is classified by its path (product, pricing, integrations,
              security, comparison, docs, customers, resources, company, editorial) and
              read in that order, each kind within a share of the page budget
  yield       each page read is scored by what it added to the graph that nothing before
              it had: a registry entity, a vertical concept, a claim. A kind whose last
              pages added nothing is set aside, and the crawl ends when no kind is still
              yielding, even with budget left

Everything here is pure: the plan is data in the crawl state, so a job can stop after any
page and resume on another instance. Fetching and extraction stay in site_graph.
"""

import re
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# Kinds
# ---------------------------------------------------------------------------

# Matched per path segment, left to right (see classify). This order only breaks ties
# within one segment: "blog-*" is editorial before anything else can claim it. A blog post
# comparing rivals ("/blog/stripe-vs-chargebee") stays editorial because its first
# segment decides - the vendor's own comparison page and a post naming a rival in passing
# are different evidence, which discovery learned the hard way.
KIND_HINTS: List[Tuple[str, List[str]]] = [
    ("excluded", ["careers", "career", "jobs", "legal", "privacy", "privacy-policy", "terms*",
                  "cookie*", "login", "log-in", "signin", "sign-in", "signup", "sign-up",
                  "register", "app", "demo", "get-demo", "book-a-demo", "request-demo",
                  "schedule-demo", "watch-product-tour", "contact*", "lp", "thank-you*",
                  "search", "tag", "tags", "category", "categories", "author", "authors",
                  "page", "feed", "rss", "cdn-cgi", "wp-*", "*-tos", "*terms-and-conditions*",
                  "master-agreement", "anti-slavery", "unsubscribe", "status", "*sitemap*",
                  "blog-author", "blog-category", "blog-tag"]),
    ("editorial", ["blog", "blogs", "blog-*", "news", "press", "press-releases", "newsroom", "events",
                   "event", "webinar*", "podcast*", "video", "videos", "community",
                   "champions-of-change", "churnfm", "*-conference*"]),
    ("docs", ["docs", "doc", "documentation", "api", "api-reference", "developers",
              "developer", "reference", "hc", "help", "support", "knowledge-base", "kb",
              "changelog", "release-notes"]),
    ("customers", ["customers", "customer", "customer-stories", "case-studies", "case-study",
                   "success-stories", "testimonials"]),
    ("comparison", ["compare", "comparison", "compare-competitors", "vs", "alternatives",
                    "alternative", "*-vs-*", "*-alternative", "*-alternatives"]),
    ("pricing", ["pricing", "plans", "price"]),
    ("security", ["security", "trust", "trust-center", "compliance", "gdpr", "soc-2", "soc2"]),
    ("integrations", ["integrations", "integration", "connectors", "connector", "apps",
                      "marketplace", "app-marketplace"]),
    ("resources", ["resources", "resource", "guides", "guide", "glossary", "glossaries",
                   "white-papers", "whitepapers", "white-paper", "ebook", "ebooks", "research",
                   "reports", "report", "faq", "faqs", "buyers-guide", "infographics", "tools",
                   "templates", "saas-metrics-hub", "learn", "academy"]),
    ("company", ["about", "about-us", "company", "partners", "partner", "leadership", "team",
                 "investors"]),
    ("product", ["products", "product", "platform", "features", "feature", "capabilities",
                 "modules", "solutions", "solution", "use-cases", "usecases", "use-case",
                 "usecase", "industries", "industry", "roles", "role", "why-*", "how-it-works",
                 "product-tour", "overview"]),
]

# Read order, and each kind's ceiling as a share of the page budget. A kind that stops
# yielding is set aside before its ceiling; the ceiling stops a kind that never stops
# yielding small novelties - 981 help articles each naming one new setting - from spending
# the whole crawl. `other` is anything unclassified: on SaaS sites many root-level pages
# ("/cpq", "/billing") are product landing pages, so it reads ahead of customer stories.
KIND_ORDER: List[Tuple[str, float]] = [
    ("home", 1.0),
    ("product", 0.35),
    ("pricing", 0.05),
    ("integrations", 0.20),
    ("security", 0.05),
    ("comparison", 0.08),
    ("docs", 0.30),
    ("other", 0.20),
    ("customers", 0.08),
    ("resources", 0.10),
    ("company", 0.04),
    ("editorial", 0.06),
]
KIND_RANK = {kind: i for i, (kind, _) in enumerate(KIND_ORDER)}

# A kind is set aside once this many consecutive pages of it added nothing new.
YIELD_PATIENCE = 6

# Hosts other than the site itself that still speak for the brand: its documentation and
# help centre. support.ordwaylabs.com carries 981 articles, and it is where the product's
# own vocabulary - rather than its marketing's - is written.
DOC_HOST_LABELS = {"docs", "doc", "help", "support", "developer", "developers", "dev", "api",
                   "kb", "knowledge", "learn", "academy", "guide", "guides", "trust",
                   "security", "status-docs"}

MAX_DOC_HOSTS = 3
MAX_CANDIDATES = 5000

_ASSET = re.compile(r"\.(pdf|png|jpe?g|svg|css|js|webp|gif|zip|xml|ico|mp4|mp3|woff2?|json|txt)$", re.I)
_LOCALE = re.compile(r"^[a-z]{2}(?:[-_][a-z]{2})?$")
_ENGLISH = {"en", "en-us", "en-gb", "en_us", "en_gb"}
_LOCALE_WORDS = {"de", "fr", "es", "it", "pt", "nl", "ja", "ko", "zh", "sv", "da", "no", "fi",
                 "pl", "tr", "ru", "ar", "he", "id", "th", "vi", "cs", "hu", "ro", "br"}


def _segments(path: str) -> List[str]:
    return [s for s in path.lower().split("/") if s]


def _match(segment: str, hint: str) -> bool:
    if hint.startswith("*") and hint.endswith("*"):
        return hint[1:-1] in segment
    if hint.endswith("*"):
        return segment.startswith(hint[:-1])
    if hint.startswith("*"):
        return segment.endswith(hint[1:])
    return segment == hint


def brand_domain(host: str) -> str:
    """The registrable part of a host, for deciding what counts as the same brand.

    Two labels is right for .com, .io and .ai, which is nearly every SaaS vendor. A
    two-part public suffix (.co.uk) keeps three.
    """
    labels = host.lower().split(":")[0].split(".")
    if len(labels) >= 3 and labels[-2] in ("co", "com", "org", "net", "ac") and len(labels[-1]) == 2:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


def host_role(host: str, site_host: str) -> Optional[str]:
    """'site' for the site itself, 'docs' for a docs or help host of the same brand, None
    for anything else - another company, or the brand's app, blog or status page."""
    host = host.lower().split(":")[0]
    site_host = site_host.lower().split(":")[0]
    bare = lambda h: h[4:] if h.startswith("www.") else h
    if bare(host) == bare(site_host):
        return "site"
    if brand_domain(host) != brand_domain(site_host):
        return None
    first = bare(host).split(".")[0]
    # "apidocs", "developer-docs", "helpcenter": the label need only contain the word.
    if first in DOC_HOST_LABELS or "docs" in first or first.startswith(("help", "support")):
        return "docs"
    return None


def is_translation(path: str) -> bool:
    """A non-English variant: a locale segment anywhere in the first two positions.

    Help centres put the locale second ("/hc/de/articles"), marketing sites first
    ("/de/pricing"). English locales ("/hc/en-us/...") are the page itself.
    """
    for seg in _segments(path)[:2]:
        if _LOCALE.match(seg) and seg not in _ENGLISH and seg.split("-")[0].split("_")[0] in _LOCALE_WORDS:
            return True
    return False


# Sections that only contain other kinds of page. "/products/integrations" is an
# integrations page and "/resources/case-studies/x" a customer story, so a container
# decides the kind only when nothing after it does.
_CONTAINERS = {"resources", "resource", "products", "product", "platform", "learn"}


def _segment_kind(segment: str) -> Optional[str]:
    for kind, hints in KIND_HINTS:
        if any(_match(segment, hint) for hint in hints):
            return kind
    return None


def classify(url: str, site_host: str) -> Optional[str]:
    """The kind of page a URL is, or None when it should not be read at all.

    Segments are read left to right, and the first that names a kind decides. A path's
    leading section says what it is: "/solutions/api" is a solution page, "/docs/pricing"
    is documentation, "/blog/stripe-vs-chargebee" is a blog post. Within one segment,
    KIND_HINTS order breaks ties. An excluded segment anywhere excludes the page.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return None
    role = host_role(parsed.netloc, site_host)
    if role is None:
        return None
    path = parsed.path or "/"
    if _ASSET.search(path) or is_translation(path):
        return None
    segments = _segments(path)
    if role == "site" and not segments:
        return "home"
    kinds = [_segment_kind(seg) for seg in segments]
    if "excluded" in kinds:
        return None
    if role == "docs":
        # Everything on a docs host is documentation, whatever its path calls itself.
        return "docs"
    fallback = None
    for seg, kind in zip(segments, kinds):
        if kind is None:
            continue
        if seg in _CONTAINERS:
            fallback = fallback or kind
            continue
        return kind
    return fallback or "other"


def page_key(url: str) -> str:
    """Identity of a page: host without www, path without trailing slash. Query and
    fragment dropped - tracking parameters would otherwise make one page many."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host + (parsed.path.rstrip("/").lower() or "/")


def clean_url(url: str) -> str:
    return urlparse(url)._replace(fragment="", query="").geturl()


# ---------------------------------------------------------------------------
# The plan, as JSON-safe state
# ---------------------------------------------------------------------------

def new_plan(start_url: str) -> Dict[str, Any]:
    return {
        "site_host": urlparse(start_url).netloc.lower(),
        "candidates": {},          # page key -> {url, kind, source, inlinks, order}
        "kinds": {},               # kind -> {read, streak, yield, set_aside}
        "identities": [],          # what the graph already has, for scoring yield
        "sitemaps": [],            # hosts whose sitemap has been read
        "yield_log": [],           # [{url, kind, new}] in read order
        "order": 0,
    }


def add_candidates(plan: Dict[str, Any], urls: Iterable[str], source: str,
                   seen: Set[str]) -> List[str]:
    """Add URLs to the plan. Returns docs hosts seen for the first time, whose sitemaps
    the caller may want to read. A URL already a candidate gains an inlink instead."""
    new_doc_hosts: List[str] = []
    candidates = plan["candidates"]
    for url in urls:
        kind = classify(url, plan["site_host"])
        if kind is None:
            continue
        key = page_key(url)
        if key in seen:
            continue
        existing = candidates.get(key)
        if existing is not None:
            existing["inlinks"] += 1
            continue
        if len(candidates) >= MAX_CANDIDATES:
            break
        host = urlparse(url).netloc.lower()
        if (host_role(host, plan["site_host"]) == "docs" and host not in plan["sitemaps"]
                and host not in new_doc_hosts
                and len([h for h in plan["sitemaps"] if h != plan["site_host"]]) + len(new_doc_hosts) < MAX_DOC_HOSTS):
            new_doc_hosts.append(host)
        plan["order"] += 1
        candidates[key] = {"url": clean_url(url), "kind": kind, "source": source,
                           "inlinks": 1 if source == "link" else 0, "order": plan["order"]}
    return new_doc_hosts


def _kind_state(plan: Dict[str, Any], kind: str) -> Dict[str, Any]:
    return plan["kinds"].setdefault(kind, {"read": 0, "streak": 0, "yield": 0, "set_aside": False})


def kind_open(plan: Dict[str, Any], kind: str) -> bool:
    """A kind is open until its own pages stop yielding."""
    return not _kind_state(plan, kind)["set_aside"]


def next_batch(plan: Dict[str, Any], max_pages: int, attempts: int, size: int) -> List[Dict[str, Any]]:
    """The next pages to read, best first, removed from the candidates.

    Within a kind, pages more of the site links to come first, then the order they were
    found - the sitemap's order, or the order links appeared.

    Ceilings are shares, not walls: they stop one kind crowding out the others, so a kind
    at its ceiling waits while any other open kind still has pages. When none does, it
    reads on - a small site's last pricing page is worth more than stopping with budget
    left. A kind set aside for yield reads nothing more either way.
    """
    remaining = max_pages - attempts
    if remaining <= 0:
        return []
    size = min(size, remaining)
    room: Dict[str, int] = {}
    for kind, share in KIND_ORDER:
        ks = _kind_state(plan, kind)
        ceiling = max(1, int(round(max_pages * share)))
        room[kind] = max(ceiling - ks["read"], 0)

    ranked = [(key, cand) for key, cand in
              sorted(plan["candidates"].items(),
                     key=lambda kv: (KIND_RANK.get(kv[1]["kind"], 99), -kv[1]["inlinks"], kv[1]["order"]))
              if kind_open(plan, cand["kind"])]
    batch, taken = [], set()
    for key, cand in ranked:
        if len(batch) >= size:
            break
        if room.get(cand["kind"], 0) <= 0:
            continue
        room[cand["kind"]] -= 1
        batch.append(dict(cand, key=key))
        taken.add(key)
    if not batch:
        # Every open kind is at its ceiling: fill in rank order regardless.
        for key, cand in ranked[:size]:
            batch.append(dict(cand, key=key))
            taken.add(key)
    for key in taken:
        plan["candidates"].pop(key, None)
    return batch


def record_yield(plan: Dict[str, Any], url: str, kind: str, identities: Iterable[str]) -> int:
    """Score one page read. Returns how many identities it added that the graph lacked."""
    known = set(plan["identities"])
    new = [i for i in identities if i not in known]
    new = list(dict.fromkeys(new))
    plan["identities"].extend(new)
    ks = _kind_state(plan, kind)
    ks["read"] += 1
    ks["yield"] += len(new)
    ks["streak"] = 0 if new else ks["streak"] + 1
    # The homepage is one page; it is never "set aside", it is simply read.
    if kind != "home" and ks["streak"] >= YIELD_PATIENCE:
        ks["set_aside"] = True
    plan["yield_log"].append({"url": url, "kind": kind, "new": len(new)})
    return len(new)


def record_failure(plan: Dict[str, Any], kind: str) -> None:
    """A page that could not be read counts against its kind's ceiling, not its yield."""
    _kind_state(plan, kind)["read"] += 1


def exhausted(plan: Dict[str, Any]) -> bool:
    """No candidate left in any kind that is still yielding."""
    return not any(kind_open(plan, c["kind"]) for c in plan["candidates"].values())


def summary(plan: Dict[str, Any]) -> Dict[str, Any]:
    """Per kind: pages read, what they added, whether the kind was set aside, and what was
    left unread. This is what tells the next crawl of a similar site where yield lives."""
    left: Dict[str, int] = {}
    for c in plan["candidates"].values():
        left[c["kind"]] = left.get(c["kind"], 0) + 1
    return {kind: dict(plan["kinds"].get(kind, {"read": 0, "yield": 0, "set_aside": False}),
                       unread=left.get(kind, 0))
            for kind, _ in KIND_ORDER
            if kind in plan["kinds"] or kind in left}
