"""
buyer_situations.py - buyer stories as situations in the knowledge graph.

Phase 0 of BUYER_SITUATIONS_DESIGN.md. buyer_stories.read_story turns a case study into
parts (company, role, before, trigger, pain, need), each with a quote checked against the
page. This module turns those parts into a situation the graph can hold and walk:

    company -> buyerIs      the buyer's segment, as text plus any concept it names
    role    -> buyerRole    one of ROLES, keeping the page's wording
    before  -> usedBefore   registry entities (QuickBooks), LegacyWorkflow (spreadsheets)
    trigger -> triggeredBy  one of TRIGGERS, keeping the page's wording
    pain    -> sufferedFrom painAbout -> the concepts it names
    need    -> needed       the concepts it names
    and constrainedBy: a registry product or a standard named in the trigger, pain or need
    that the buyer did not use before (Paytient was moving to NetSuite)

Names are found the way entity_resolver finds them - normalise_key on whole word runs,
looked up in the vertical's concepts and the registry - but inside a phrase rather than
on a whole form, because a part is a phrase: "lost revenue from delayed proration
calculations" names Proration. Longest run first, no overlaps, no similarity. A run that
names two concepts is ambiguous and names neither.

A tool the registry does not know (Recurly, on 3 Oct 2026) stays as the page's text and is
reported as a registry candidate. It is never added to the registry from here: the
registry grows by review.

Situations are stored as their own graph kind, "situations", one graph per site per read.
Not inside a crawl's run graph: the reader fetches the case-study pages itself, and
writing what it found into an earlier crawl's record would change what that crawl saw.
"""

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from rdflib import RDF, RDFS, Graph, Literal, Namespace, URIRef
from rdflib.namespace import PROV, SKOS

from entity_resolver import normalise_key
from ontology_schema import ONTOLOGY_BASE, concept_uri

logger = logging.getLogger("gainark.buyer_situations")

SIT = Namespace(ONTOLOGY_BASE + "/situation#")
KIND = "situations"

# The buyer's role, as function x seniority, so a new vertical needs no new list. The
# first list was finance titles only (CFO, Controller, Director of Accounting) and read
# none of bamboohr.com's buyers on 4 Oct 2026 ("VP of People", "People and Culture
# Manager"). A role is "<function>/<level>"; either half may be "unspecified".
ROLE_FUNCTIONS: List[Tuple[str, str]] = [
    ("revops", r"\brevops\b|revenue operations|sales operations|\bsales ops\b"),
    ("accounting", r"accounting|accountant|\bcontroller\b|\bcomptroller\b|bookkeep"),
    ("finance", r"\bfinanc|\bcfo\b|fp&a|treasur"),
    ("people", r"\bpeople\b|\bhr\b|human resources|talent|\bculture\b|\bchro\b|\bpayroll\b"),
    ("it", r"\bit\b|information technology|\bcio\b|\bcto\b|security|engineering"),
    ("operations", r"operations|\bcoo\b"),
    ("executive", r"\bceo\b|founder|\bowner\b|\bpresident\b|chief executive"),
]
ROLE_LEVELS: List[Tuple[str, str]] = [
    ("c-level", r"\bchief\b|\bc[a-z]?[a-z]o\b|\bfounder\b|\bowner\b"),
    ("vp", r"\bvp\b|vice president|\bsvp\b|\bevp\b"),
    ("head", r"\bhead of\b"),
    # A controller runs the accounting function: director level in the companies these
    # case studies describe.
    ("director", r"\bdirector\b|\bcontroller\b|\bcomptroller\b"),
    ("manager", r"\bmanager\b|\blead\b"),
    ("staff", r"specialist|analyst|coordinator|administrator|generalist|accountant"),
]

# What made the old way stop working. Checked in order; the first that matches wins.
TRIGGERS: List[Tuple[str, str]] = [
    ("new-business-model", r"\bb2b2?c?\b|usage[- ]based|new (?:pricing|business) model|"
                           r"evolving pricing|pricing models?|new market"),
    ("erp-change", r"\berp\b|netsuite|sage intacct|migrat\w* to"),
    ("new-entity-or-country", r"\binternational|multi[- ]entity|new (?:entity|country|region)|global"),
    ("new-deal-types", r"deal types?|contract (?:types|changes|structures)|mid[- ]contract|"
                       r"changes to (?:customer )?contracts|upgrades?|amendments?|addend"),
    ("audit-or-funding", r"\baudit|\bipo\b|funding|investor|board"),
    # Growth in whatever the business counts: customers and revenue for a billing buyer,
    # people and locations for an HR buyer ("more than quadrupled its workforce").
    ("growth-in-volume", r"\bgrow|growth|scal|more customers|volume|adding customers|revenue grew|"
                         r"workforce|headcount|\bhires?\b|hiring|branches|locations|offices|"
                         r"\b(?:doubled|tripled|quadrupled)\b"),
]

# What a pain cost the buyer. Most pains on a case study are not about a product concept
# at all - "tracking everything in Excel became unwieldy", "two to three weeks to close
# the books" - and on 3 Oct 2026 forcing them onto concepts produced proposals like
# "billing foundation" -> Multi-Entity. So a pain is classified by what it cost, from its
# quote, and may be several things at once. The concept it is about, when there is one,
# is kept beside it.
PAIN_TYPES: List[Tuple[str, str]] = [
    ("revenue-leakage", r"lost revenue|losing revenue|revenue (?:loss|leak)|leakage|under-?bill|"
                        r"missed (?:billing|charges|revenue)"),
    ("vendor-support-fit", r"support from|hard to get support|get support|fit into (?:their|the)|"
                           r"didn'?t want to deal|not (?:flexible|responsive)|rigid"),
    ("errors-accuracy", r"\berrors?\b|error-prone|mistakes?|inaccura|\bwrong\b"),
    ("slow-cycle", r"\b\d+\s*(?:days?|weeks?)\b|\b(?:two|three|four|several) (?:days|weeks)\b|"
                   r"weeks to|days to|took (?:too )?long|delay|\bslow\b|close the books|"
                   r"month-end close|\blate\b|"
                   # Time lost on a routine, the way an HR team says it: "an entire whole
                   # day a month sorting HR documents" (crbr, bamboohr.com, 4 Oct 2026).
                   r"(?:entire|whole|full) (?:whole )?(?:day|week)|"
                   r"\bhours? (?:a|each|every|per) (?:day|week|month)"),
    ("cannot-scale", r"\bscal|unwieldy|outgr|keep up|not powerful enough|"
                     r"(?:became|become|becoming|got|gotten|getting|grew) (?:all )?"
                     r"(?:more |increasingly )?(?:complicated|complex)|"
                     r"(?:could not|couldn'?t|not able to) handle|as (?:the company |we )?gr[eo]w"),
    ("manual-effort", r"\bmanual|by hand|time-consuming|re-?key|spreadsheet|\bexcel\b|"
                      r"\bpaper|sorting|data entry|re-?enter"),
    ("missing-capability", r"not able to|was not able|could not|couldn'?t|did not support|"
                           r"lacked|no way to|not the best platform|not suitable"),
]

# Ways of working a buyer leaves behind, as LegacyWorkflow values.
LEGACY: List[Tuple[str, str]] = [
    ("spreadsheets", r"spreadsheet|excel|google sheets"),
    ("paper", r"\bpaper\b|paper-based|filing cabinet"),
    ("manual-process", r"\bmanual|by hand|semi-automated"),
]

PART_PREDICATES = {
    "company": "buyerIs", "role": "buyerRole", "before": "usedBefore",
    "trigger": "triggeredBy", "pain": "sufferedFrom", "need": "needed",
}

_MAX_RUN = 6     # longest word run looked up as one name


@dataclass
class Named:
    """A thing a phrase names: a concept, a registry entity, or a controlled value."""
    kind: str            # concept | entity | role | trigger | legacy | unresolved
    id: str
    label: str
    uri: str = ""


@dataclass
class Facet:
    predicate: str
    text: str
    quote: str
    url: str
    names: List[Named] = field(default_factory=list)


@dataclass
class Situation:
    id: str
    site: str
    source_url: str
    voice: str
    facets: List[Facet] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Finding names inside a phrase
# ---------------------------------------------------------------------------

def _first(patterns: List[Tuple[str, str]], text: str) -> Optional[str]:
    low = (text or "").lower()
    return next((value for value, pat in patterns if re.search(pat, low)), None)


def classify_role(text: str) -> Optional[str]:
    """ "<function>/<level>", or None when the phrase names neither."""
    function, level = _first(ROLE_FUNCTIONS, text), _first(ROLE_LEVELS, text)
    if not function and not level:
        return None
    return "%s/%s" % (function or "unspecified", level or "unspecified")


def classify_trigger(text: str) -> Optional[str]:
    return _first(TRIGGERS, text)


def classify_pain(text: str) -> List[str]:
    """Every pain type a phrase states, in PAIN_TYPES order."""
    low = (text or "").lower()
    return [value for value, pat in PAIN_TYPES if re.search(pat, low)]


class Vocabulary:
    """The keys a phrase can name: a vertical's concepts and the registry's entities.

    Built from the same normalise_key as entity_resolver, so a name found here is the
    name the resolver would find on a page. A key two concepts share names neither.
    """

    def __init__(self, vertical_id: str, concepts: Iterable[Any], registry: Any = None):
        self.vertical_id = vertical_id
        self.concepts: Dict[str, Any] = {}
        clash = set()
        for c in concepts or []:
            for form in [c.pref_label] + list(c.alt_labels or []):
                key = normalise_key(form)
                if not key:
                    continue
                if key in self.concepts and self.concepts[key].id != c.id:
                    clash.add(key)
                self.concepts.setdefault(key, c)
        for key in clash:
            self.concepts.pop(key, None)
        self.registry = registry

    def lookup(self, key: str) -> Optional[Named]:
        c = self.concepts.get(key)
        if c is not None:
            return Named("concept", c.id, c.pref_label, concept_uri(self.vertical_id, c.id))
        if self.registry is not None:
            hits = self.registry.lookup(key)
            if len(hits) == 1:
                e = hits[0]
                return Named("entity", e.id, e.prefLabel, e.uri)
        return None

    def names_in(self, text: str) -> List[Named]:
        """Concepts and entities a phrase names, longest word run first, no overlaps."""
        words = re.findall(r"[A-Za-z0-9][\w.&'-]*", text or "")
        taken = [False] * len(words)
        found: List[Named] = []
        for size in range(min(_MAX_RUN, len(words)), 0, -1):
            for i in range(len(words) - size + 1):
                if any(taken[i:i + size]):
                    continue
                named = self.lookup(normalise_key(" ".join(words[i:i + size])))
                if named is None:
                    continue
                for j in range(i, i + size):
                    taken[j] = True
                if all(named.id != f.id for f in found):
                    found.append(named)
        return found


_PROPER = re.compile(r"\b[A-Z][A-Za-z0-9]+(?:[ .][A-Z][A-Za-z0-9]+)*\b")


def unresolved_names(text: str, named: List[Named]) -> List[str]:
    """Capitalised names in a 'before' phrase that nothing resolved - registry candidates.
    "Recurly for subscription management" gives ["Recurly"]."""
    labels = [n.label.lower() for n in named if n.kind in ("concept", "entity")]
    out = []
    for m in _PROPER.finditer(text or ""):
        name = m.group(0)
        # "QuickBooks Online" is QuickBooks: a name containing one that resolved is that thing.
        if any(label in name.lower() for label in labels):
            continue
        # Category and function acronyms are not companies: "HR" was a registry candidate
        # on bamboohr.com. A vendor written in capitals (ADP, SAP) is not in this list.
        if not re.fullmatch(r"(?:B2B2?C?|SaaS|ERP|CRM|AR|AP|HR|HRIS|HCM|IT|ATS|LMS|PTO|PEO|EOR|"
                            r"CEO|CFO|COO|CTO|CIO|CHRO|API|AI)", name):
            out.append(name)
    return out


# ---------------------------------------------------------------------------
# A story into a situation
# ---------------------------------------------------------------------------

def situation_id(site: str, source_url: str) -> str:
    return "https://%s/situation/%s" % (
        site, hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:16])


def from_story(story: Dict[str, Any], site: str, vocab: Vocabulary) -> Situation:
    """One situation from one story as buyer_stories returns it (parts with quotes)."""
    url = story["source_url"]
    sit = Situation(id=situation_id(site, url), site=site, source_url=url,
                    voice=story.get("voice", "customer"))
    used_before: set = set()
    for part, predicate in PART_PREDICATES.items():
        p = (story.get("parts") or {}).get(part)
        if not p:
            continue
        text, quote = p["text"], p["quote"]
        facet = Facet(predicate=predicate, text=text, quote=quote, url=url)
        # A name counts only when the quote says it too. The text is the model's summary
        # of the quote; on 3 Oct 2026 a summary added "usage-based billing" that the
        # quote never said, and resolved it to Metered Billing.
        in_quote = {n.id for n in vocab.names_in(quote)}
        if part == "role":
            # The quote is usually the sign-off ("Josie Keucke, People and Culture Manager
            # at Civtec"), where the summary had shortened it to "people". Whichever says
            # more wins.
            candidates = [r for r in (classify_role(quote), classify_role(text)) if r]
            role = min(candidates, key=lambda r: r.count("unspecified")) if candidates else None
            if role:
                facet.names.append(Named("role", role, text, str(SIT["role/" + role])))
        elif part == "trigger":
            trig = classify_trigger(quote)
            if trig:
                facet.names.append(Named("trigger", trig, text, str(SIT["trigger/" + trig])))
            facet.names += [n for n in vocab.names_in(text) if n.id in in_quote]
        else:
            if part == "pain":
                facet.names += [Named("pain", p, p.replace("-", " "), str(SIT["pain/" + p]))
                                for p in classify_pain(quote)]
            facet.names += [n for n in vocab.names_in(text) if n.id in in_quote]
            # A pain about spreadsheets is about the workflow left behind, the same value
            # "before" holds: "tracking everything in Excel became unwieldy".
            if part in ("before", "pain"):
                # Every way of working the part and its quote both name: "paper files and
                # spreadsheets" left both behind.
                for legacy, pat in LEGACY:
                    if re.search(pat, text.lower()) and re.search(pat, quote.lower()):
                        facet.names.append(Named("legacy", legacy, legacy.replace("-", " "),
                                                 str(SIT["legacy/" + legacy])))
            if part == "before":
                for name in unresolved_names(text, facet.names):
                    facet.names.append(Named("unresolved", normalise_key(name), name))
                used_before |= {n.id for n in facet.names if n.kind == "entity"}
        sit.facets.append(facet)

    # A product or standard named in what changed or what was needed, that the buyer was
    # not already using, is a constraint on the choice: Paytient moving to NetSuite.
    for facet in [f for f in sit.facets if f.predicate in ("triggeredBy", "sufferedFrom", "needed")]:
        for n in facet.names:
            if n.kind == "entity" and n.id not in used_before:
                if all(n.id not in [x.id for x in f.names] for f in sit.facets
                       if f.predicate == "constrainedBy"):
                    sit.facets.append(Facet("constrainedBy", n.label, facet.quote, facet.url, [n]))
    return sit


# ---------------------------------------------------------------------------
# The graph
# ---------------------------------------------------------------------------

def to_graph(situations: List[Situation]) -> Graph:
    """Situations as RDF. Each facet is reified like a claim (site_graph, PR #7): the
    quote as prov:value and the page as prov:wasDerivedFrom."""
    g = Graph()
    g.bind("sit", SIT)
    g.bind("prov", PROV)
    g.bind("skos", SKOS)
    for s in situations:
        node = URIRef(s.id)
        g.add((node, RDF.type, SIT.Situation))
        g.add((node, SIT.site, Literal(s.site)))
        g.add((node, SIT.voice, Literal(s.voice)))
        g.add((node, PROV.wasDerivedFrom, URIRef(s.source_url)))
        for f in s.facets:
            fid = URIRef("%s/facet/%s" % (s.id, hashlib.sha1(
                "|".join((f.predicate, f.text, f.quote)).encode("utf-8")).hexdigest()[:16]))
            g.add((node, SIT[f.predicate], fid))
            g.add((fid, RDF.type, SIT.Facet))
            g.add((fid, RDFS.label, Literal(f.text)))
            g.add((fid, PROV.value, Literal(f.quote)))
            g.add((fid, PROV.wasDerivedFrom, URIRef(f.url)))
            for n in f.names:
                if n.uri:
                    target = URIRef(n.uri)
                    g.add((fid, SIT.names, target))
                    g.add((target, SKOS.prefLabel, Literal(n.label)))
                else:
                    g.add((fid, SIT.unresolved, Literal(n.label)))
    return g


def from_graph(g: Graph) -> List[Situation]:
    """Situations back out of a stored graph."""
    out = []
    for node in g.subjects(RDF.type, SIT.Situation):
        s = Situation(id=str(node), site=str(g.value(node, SIT.site) or ""),
                      source_url=str(g.value(node, PROV.wasDerivedFrom) or ""),
                      voice=str(g.value(node, SIT.voice) or ""))
        for predicate in list(PART_PREDICATES.values()) + ["constrainedBy"]:
            for fid in g.objects(node, SIT[predicate]):
                f = Facet(predicate, str(g.value(fid, RDFS.label) or ""),
                          str(g.value(fid, PROV.value) or ""),
                          str(g.value(fid, PROV.wasDerivedFrom) or ""))
                for target in g.objects(fid, SIT.names):
                    uri, kind = str(target), _kind_of(str(target))
                    # A controlled value's id may hold a slash ("finance/c-level"), so it is
                    # everything after "#role/", not after the last slash.
                    marker = "/situation#%s/" % kind
                    nid = uri.split(marker, 1)[1] if marker in uri else uri.rsplit("/", 1)[-1]
                    f.names.append(Named(kind, nid, str(g.value(target, SKOS.prefLabel) or ""), uri))
                for label in g.objects(fid, SIT.unresolved):
                    f.names.append(Named("unresolved", normalise_key(str(label)), str(label)))
                s.facets.append(f)
        out.append(s)
    return sorted(out, key=lambda s: s.source_url)


def _kind_of(uri: str) -> str:
    if "/concept/" in uri:
        return "concept"
    for kind in ("role", "trigger", "pain", "legacy"):
        if "/situation#%s/" % kind in uri:
            return kind
    return "entity"


def graph_id(site: str, when: Optional[datetime] = None) -> str:
    when = when or datetime.now(timezone.utc)
    return "https://%s/situations/%s" % (site, when.strftime("%Y%m%dT%H%M%SZ"))


def persist(situations: List[Situation], site: str, vertical_id: str,
            path: Optional[str] = None) -> str:
    """Store one read of a site's situations, mirrored to the archive. Returns its id."""
    import graph_store
    gid = graph_id(site)
    graph_store.persist_graph(to_graph(situations), gid, kind=KIND, domain=site,
                              vertical_id=vertical_id, path=path,
                              metadata=json.dumps({"situations": len(situations)}))
    return gid


def latest(site: str, path: Optional[str] = None) -> List[Situation]:
    """The most recent stored read of a site's situations, or none."""
    import graph_store
    with graph_store.connect(path) as conn:
        row = conn.execute("SELECT graph_id FROM graphs WHERE kind = ? AND domain = ?"
                           " ORDER BY created_at DESC LIMIT 1", (KIND, site)).fetchone()
    return from_graph(graph_store.load_graph(row[0], path=path)) if row else []


# ---------------------------------------------------------------------------
# A site, end to end
# ---------------------------------------------------------------------------

def report(situations: List[Situation]) -> Dict[str, Any]:
    """What resolved and what did not, for reading by hand (design §10.1)."""
    facets = [f for s in situations for f in s.facets]
    def count(kind):
        return sum(1 for f in facets for n in f.names if n.kind == kind)
    return {
        "situations": len(situations),
        "facets": len(facets),
        "names": {k: count(k) for k in ("concept", "entity", "role", "trigger", "pain", "legacy", "unresolved")},
        "pains_unclassified": [f.text for f in facets if f.predicate == "sufferedFrom"
                               and not any(n.kind == "pain" for n in f.names)],
        "pains_without_concept": [f.text for f in facets if f.predicate == "sufferedFrom"
                                  and not any(n.kind == "concept" for n in f.names)],
        "needs_without_concept": [f.text for f in facets if f.predicate == "needed"
                                  and not any(n.kind == "concept" for n in f.names)],
        "roles_unclassified": [f.text for f in facets if f.predicate == "buyerRole" and not f.names],
        "triggers_unclassified": [f.text for f in facets if f.predicate == "triggeredBy"
                                  and not any(n.kind == "trigger" for n in f.names)],
        "registry_candidates": sorted({n.label for f in facets for n in f.names if n.kind == "unresolved"}),
    }


# ---------------------------------------------------------------------------
# Concept proposals: what exact keys missed, for a reviewer
# ---------------------------------------------------------------------------

# Words a term never starts or ends on. A span ending on "can" or "that" is a fragment
# ("billing foundation that can" was proposed as Multi-Entity on 3 Oct 2026).
_EDGE_STOP = {
    "a", "an", "the", "and", "or", "of", "for", "to", "in", "on", "with", "our", "their", "its",
    "was", "were", "is", "be", "that", "can", "could", "would", "not", "more", "both", "as",
    "by", "at", "from", "into", "which", "became", "made", "had", "have", "we", "they", "it",
    "this", "need", "needed", "needs", "used", "use", "using", "support", "able", "lot",
    "handle", "better", "new", "complex", "unique", "flexible", "powerful", "scalable",
}
_MAX_SPAN = 4


def term_spans(text: str, quote: str) -> List[str]:
    """Candidate terms in a phrase: runs of two to four words that neither start nor end
    on a function word, every content word also in the quote. Single words are left out:
    an alt label of "contract" or "platform" would match everything."""
    from buyer_stories import supported
    words = re.findall(r"[A-Za-z0-9][\w'-]*", text or "")
    out = []
    for size in range(2, _MAX_SPAN + 1):
        for i in range(len(words) - size + 1):
            run = words[i:i + size]
            if run[0].lower() in _EDGE_STOP or run[-1].lower() in _EDGE_STOP:
                continue
            span = " ".join(run)
            if supported(span, quote) == 1.0 and span not in out:
                out.append(span)
    return out


def propose_concepts(situations: List[Situation], vertical_id: str, concepts: List[Any],
                     queue: Any = None, find: Any = None) -> List[Dict[str, Any]]:
    """Queue, for review, the strongest concept for each need and pain no concept named.

    semantic_match proposes and a person decides: an approved term becomes an alt label
    through /api/ontology/approve-synonym, and from the next read exact keys find it with
    no model involved. Each proposal carries the quote and page it came from. Returns the
    proposals; `queue` None proposes nothing and only returns them.
    """
    if find is None:
        import semantic_match
        find = semantic_match.find_candidates
    proposals = []
    for s in situations:
        for f in s.facets:
            if f.predicate not in ("needed", "sufferedFrom"):
                continue
            if any(n.kind == "concept" for n in f.names):
                continue
            # A pain is about a concept only when it is a capability the old system lacked.
            # The rest are typed by what they cost, and proposing concepts for them gave
            # "grew and added customers" -> Cohort Analysis (4 Oct 2026).
            if f.predicate == "sufferedFrom" and not any(
                    n.kind == "pain" and n.id == "missing-capability" for n in f.names):
                continue
            # From the quote as well as the summary: the summary of Civtec's need said
            # "time tracking", which the quote does not, while the quote's own "clock in
            # and clock out" was never tried (bamboohr.com, 4 Oct 2026).
            spans = list(dict.fromkeys(term_spans(f.text, f.quote) + term_spans(f.quote, f.quote)))
            found = find(spans, concepts) if spans else []
            if not found:
                continue
            best = max(found, key=lambda c: c.score)
            proposals.append({
                "surface_form": best.surface_form, "generated_canonical": best.concept,
                "brand": s.site, "vertical_id": vertical_id, "method": "situation",
                "score": best.score, "margin": best.margin, "runner_up": best.runner_up,
                "closes_gap": False, "facet": f.predicate, "source_url": f.url, "quote": f.quote,
            })
    if queue is not None and proposals:
        from synonym_queue import entry_key
        written = queue.propose({entry_key(p["generated_canonical"], p["surface_form"], vertical_id): p
                                 for p in proposals})
        logger.info("[Situations] %d concept proposal(s) queued, %d already there",
                    len(written), len(proposals) - len(written))
    return proposals


def read_site(site: str, vertical_id: str, stories: Optional[List[Dict[str, Any]]] = None,
              registry: Any = None, store: bool = True, path: Optional[str] = None,
              propose: bool = False, queue: Any = None) -> Dict[str, Any]:
    """Read a site's case studies into situations, store them, and report.

    `stories` lets a caller pass buyer_stories output already in hand; otherwise the pages
    are read now. `propose` files concept proposals for what exact keys missed, in `queue`
    or the configured review queue.
    """
    from industry_ontology import load_industry_ontology
    if stories is None:
        import buyer_stories
        stories = buyer_stories.prompts_for_site(site)["stories"]
    if registry is None:
        from entity_registry import default_store
        registry = default_store().registry()
    concepts = load_industry_ontology(vertical_id).concepts
    vocab = Vocabulary(vertical_id, concepts, registry)
    situations = [from_story(s, site, vocab) for s in stories if s.get("parts")]
    gid = persist(situations, site, vertical_id, path=path) if store else None
    proposals = []
    if propose:
        if queue is None:
            from synonym_queue import default_queue
            queue = default_queue()
        proposals = propose_concepts(situations, vertical_id, concepts, queue=queue)
    return {"graph_id": gid, "report": report(situations), "proposals": proposals,
            "situations": [s.to_dict() for s in situations]}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    stories = json.load(open(sys.argv[3], encoding="utf-8"))["stories"] if len(sys.argv) > 3 else None
    print(json.dumps(read_site(sys.argv[1], sys.argv[2], stories=stories), indent=1, ensure_ascii=False))
