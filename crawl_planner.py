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
  scent       within a kind, what a page looks to be about before it is read - its URL,
              a help centre's title and labels, the text of links to it - matched to the
              vertical's vocabulary. Pages naming a concept the graph lacks come first,
              then the section read least, so a kind is judged on a spread of its pages
              rather than the first few its sitemap lists
  yield       each page read is scored by what it added to the graph that nothing before
              it had: a registry entity, a vertical concept, a claim. A kind whose last
              pages added nothing is set aside - later the more of its pages have added
              something - and then reads only pages that name something the graph lacks.
              The crawl ends when no kind has a page left worth reading, even with budget
              left

Everything here is pure: the plan is data in the crawl state, so a job can stop after any
page and resume on another instance. Fetching and extraction stay in site_graph.
"""

import math
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

# A kind is set aside once this many consecutive pages of it added nothing new, scaled up
# by how often its pages have added something: 6 at a hit rate of 0, 18 at a rate of 1.
# A flat 6 closed ordwaylabs.com's help centre (17 Sep 2026) after 18 of 830 articles,
# although those 18 had added 7 things the graph lacked.
YIELD_PATIENCE = 6
PATIENCE_HIT_RATE_WEIGHT = 2.0
# A set-aside kind still reads pages whose scent names something the graph lacks, until
# this many of those in a row add nothing - a title can name a concept a page only mentions.
SCENT_PATIENCE = 3


def patience(ks: Dict[str, Any]) -> int:
    """How many pages of a kind in a row may add nothing before it is set aside."""
    rate = ks.get("hits", 0) / ks["read"] if ks.get("read") else 0.0
    return int(round(YIELD_PATIENCE * (1 + PATIENCE_HIT_RATE_WEIGHT * rate)))


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


def _english_locale(segment: str) -> bool:
    return bool(_LOCALE.match(segment)) and re.split(r"[-_]", segment)[0] == "en"


def _locale_rank(url: str) -> int:
    """Which copy of a page to keep: 0 unprefixed, 1 plain or US English, 2 regional."""
    for seg in _segments(urlparse(url).path)[:2]:
        if _english_locale(seg):
            return 1 if seg in ("en", "en-us", "en_us") else 2
    return 0


def page_key(url: str) -> str:
    """Identity of a page: host without www, path without trailing slash or an English
    locale in its first two segments. Query and fragment dropped - tracking parameters
    would otherwise make one page many.

    "/en-AU/blog/x", "/en-CA/blog/x" and "/blog/x" are one page. On the 17 Sep 2026
    rippling.com crawl, 81 of 127 pages read were Australian, Canadian, British and Irish
    copies of pages it had, or would have, read once.
    """
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    parts = _segments(parsed.path)
    for i, seg in enumerate(parts[:2]):
        if _english_locale(seg):
            del parts[i]
            break
    return host + ("/" + "/".join(parts) if parts else "/")


def clean_url(url: str) -> str:
    return urlparse(url)._replace(fragment="", query="").geturl()


# ---------------------------------------------------------------------------
# Scent
# ---------------------------------------------------------------------------

# Before scent, a kind was read in sitemap order. On the 17 Sep 2026 ordwaylabs.com crawl
# the help centre was judged on 12 of 962 articles, and in its API's order the first
# picks were release notes and a maintenance notice.
SCENT_MAX_WORDS = 5
# A page naming more missing concepts than this is not worth more for it.
SCENT_MISSING_CAP = 3
SCENT_KNOWN_CAP = 5
ANCHOR_MAX_CHARS = 300

# One score within a kind, so no single signal is a wall. A missing concept is worth most.
# A hub the site links to from everywhere earns up to INLINK_CAP (log2 of its inlinks);
# each page already read from a section costs the next one from it a point. Ranking by
# section first read one page from each of a dozen one-page sections on ordwaylabs.com
# (17 Sep 2026) and never reached the subscription billing guide, the only page naming
# TaxJar.
MISSING_WEIGHT = 3.0
INLINK_CAP = 3.0
SECTION_PENALTY = 1.0

_WORD = re.compile(r"[a-z0-9]+")


def set_vocabulary(plan: Dict[str, Any], forms: Dict[str, str]) -> None:
    """Give the plan the vertical's vocabulary: surface form -> identity ("concept:<id>"),
    the identity page scoring records when a page covers it. Candidates already in the
    plan are matched against it."""
    vocab: Dict[str, str] = {}
    for form, identity in forms.items():
        words = _WORD.findall((form or "").lower())
        phrase = " ".join(words)
        if len(phrase) >= 3 and len(words) <= SCENT_MAX_WORDS:
            vocab[phrase] = identity
    plan["vocabulary"] = vocab
    for cand in plan["candidates"].values():
        _match_terms(plan, cand)


def _candidate_text(cand: Dict[str, Any]) -> str:
    path = urlparse(cand["url"]).path
    # In "53579365031572-Associate-Custom-Objects" the id says nothing; the 606 in
    # "asc-606" and the 27001 in "iso-27001" do.
    words = [w for w in re.split(r"[^A-Za-z0-9]+", path) if w and not (w.isdigit() and len(w) > 5)]
    return " ".join(words + [cand.get("title") or "", cand.get("labels") or "", cand.get("anchor") or ""])


def _match_terms(plan: Dict[str, Any], cand: Dict[str, Any]) -> None:
    vocab = plan.get("vocabulary")
    if not vocab:
        return
    words = _WORD.findall(_candidate_text(cand).lower())
    found = set()
    for n in range(1, SCENT_MAX_WORDS + 1):
        for i in range(len(words) - n + 1):
            identity = vocab.get(" ".join(words[i:i + n]))
            if identity:
                found.add(identity)
    if found:
        cand["terms"] = sorted(found)
    else:
        cand.pop("terms", None)


def section_of(cand: Dict[str, Any]) -> str:
    """The part of a site a page belongs to: its help centre section when the API gave
    one, otherwise its host and up to two leading path segments ("acme.com/blog",
    "acme.com/resources/guides")."""
    if cand.get("section"):
        return "section:" + cand["section"]
    parsed = urlparse(cand["url"])
    host = parsed.netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return "/".join([host] + _segments(parsed.path)[:-1][:2])


def _scent(cand: Dict[str, Any], known: Set[str]) -> Tuple[int, int]:
    """(concepts it names the graph lacks, concepts it names the graph has), capped."""
    terms = cand.get("terms") or []
    missing = sum(1 for t in terms if t not in known)
    return min(missing, SCENT_MISSING_CAP), min(len(terms) - missing, SCENT_KNOWN_CAP)


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
        "vocabulary": {},          # surface form -> identity, for scent
        "sections_read": {},       # section -> pages picked, for spreading reads
    }


def add_candidates(plan: Dict[str, Any], urls: Iterable[str], source: str,
                   seen: Set[str], meta: Optional[Dict[str, Dict[str, str]]] = None) -> List[str]:
    """Add URLs to the plan. Returns docs hosts seen for the first time, whose sitemaps
    the caller may want to read. A URL already a candidate gains an inlink instead.

    `meta` maps a URL to what a source already knows of the page without reading it - a
    help centre API gives each article's title, section and labels, a link its anchor
    text - and is kept on the candidate, merged into one that exists."""
    new_doc_hosts: List[str] = []
    candidates = plan["candidates"]
    for url in urls:
        kind = classify(url, plan["site_host"])
        if kind is None:
            continue
        key = page_key(url)
        if key in seen:
            continue
        known = (meta or {}).get(url)
        existing = candidates.get(key)
        if existing is not None:
            # Only a link is a vote. A sitemap or an API naming a page it already listed
            # counted as one too, until 17 Sep 2026.
            if source == "link":
                existing["inlinks"] += 1
            if _locale_rank(url) < _locale_rank(existing["url"]):
                existing["url"] = clean_url(url)
            if known:
                _merge_meta(existing, known)
                _match_terms(plan, existing)
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
        if known:
            _merge_meta(candidates[key], known)
        _match_terms(plan, candidates[key])
    return new_doc_hosts


def _merge_meta(cand: Dict[str, Any], known: Dict[str, str]) -> None:
    for field, value in known.items():
        if field == "url" or not value:
            continue
        if field == "anchor":
            # Every page linking here may word it differently; each wording is evidence.
            current = cand.get("anchor") or ""
            if value.lower() not in current.lower() and len(current) < ANCHOR_MAX_CHARS:
                cand["anchor"] = (current + " | " + value if current else value)[:ANCHOR_MAX_CHARS]
        else:
            cand[field] = value


def _kind_state(plan: Dict[str, Any], kind: str) -> Dict[str, Any]:
    return plan["kinds"].setdefault(kind, {"read": 0, "streak": 0, "yield": 0, "set_aside": False})


def kind_open(plan: Dict[str, Any], kind: str) -> bool:
    """A kind is open until its own pages stop yielding."""
    return not _kind_state(plan, kind)["set_aside"]


def _worth_reading(plan: Dict[str, Any], cand: Dict[str, Any], known: Set[str]) -> bool:
    """An open kind's pages all are; a set-aside kind's only while they name something
    the graph lacks and its scent reads have not stopped paying."""
    ks = _kind_state(plan, cand["kind"])
    if not ks["set_aside"]:
        return True
    return not ks.get("scent_closed") and _scent(cand, known)[0] > 0


def next_batch(plan: Dict[str, Any], max_pages: int, attempts: int, size: int) -> List[Dict[str, Any]]:
    """The next pages to read, best first, removed from the candidates.

    Within a kind, by one score: concepts the page names that the graph lacks, concepts it
    names that the graph has, how many of the site's pages link to it, less the pages
    already read from its section (counting this batch). Ties go to the order found.

    Ceilings are shares, not walls: they stop one kind crowding out the others, so a kind
    at its ceiling waits while any other open kind still has pages. When none does, it
    reads on - a small site's last pricing page is worth more than stopping with budget
    left. A kind set aside for yield reads only pages that name something the graph lacks.
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

    known = set(plan["identities"])
    sections = plan.setdefault("sections_read", {})
    pool = []
    for key, cand in plan["candidates"].items():
        if _worth_reading(plan, cand, known):
            missing, have = _scent(cand, known)
            merit = (MISSING_WEIGHT * missing + have
                     + min(math.log2(1 + cand["inlinks"]), INLINK_CAP))
            pool.append({"rank": KIND_RANK.get(cand["kind"], 99), "merit": merit,
                         "order": cand["order"], "key": key, "cand": cand, "section": section_of(cand)})

    def pick(respect_room: bool) -> List[Dict[str, Any]]:
        chosen = []
        while len(chosen) < size and pool:
            best, best_at = None, None
            for i, p in enumerate(pool):
                if respect_room and room.get(p["cand"]["kind"], 0) <= 0:
                    continue
                score = (p["rank"], SECTION_PENALTY * sections.get(p["section"], 0) - p["merit"],
                         p["order"])
                if best is None or score < best:
                    best, best_at = score, i
            if best_at is None:
                break
            p = pool.pop(best_at)
            room[p["cand"]["kind"]] = room.get(p["cand"]["kind"], 0) - 1
            sections[p["section"]] = sections.get(p["section"], 0) + 1
            chosen.append(dict(p["cand"], key=p["key"]))
        return chosen

    batch = pick(respect_room=True)
    if not batch:
        # Every open kind is at its ceiling: fill in rank order regardless.
        batch = pick(respect_room=False)
    for cand in batch:
        plan["candidates"].pop(cand["key"], None)
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
    ks["hits"] = ks.get("hits", 0) + (1 if new else 0)
    ks["streak"] = 0 if new else ks["streak"] + 1
    # The homepage is one page; it is never "set aside", it is simply read.
    if kind != "home":
        if ks["set_aside"]:
            # Only scent reads happen now; they get their own, shorter, patience.
            ks["scent_streak"] = 0 if new else ks.get("scent_streak", 0) + 1
            if ks["scent_streak"] >= SCENT_PATIENCE:
                ks["scent_closed"] = True
        elif ks["streak"] >= patience(ks):
            ks["set_aside"] = True
    plan["yield_log"].append({"url": url, "kind": kind, "new": len(new)})
    return len(new)


def record_failure(plan: Dict[str, Any], kind: str) -> None:
    """A page that could not be read counts against its kind's ceiling, not its yield."""
    _kind_state(plan, kind)["read"] += 1


def exhausted(plan: Dict[str, Any]) -> bool:
    """No candidate left worth reading."""
    known = set(plan["identities"])
    return not any(_worth_reading(plan, c, known) for c in plan["candidates"].values())


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
