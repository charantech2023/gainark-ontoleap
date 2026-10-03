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

# The buyer's role, as a small shared vocabulary. Order matters: the first pattern that
# matches wins, so "fractional CFO" is read before "CFO" and "VP Finance" before "Head".
ROLES: List[Tuple[str, str]] = [
    ("fractional-cfo", r"\bfractional\b.*\bcfo\b"),
    ("cfo", r"\bcfo\b|chief financial officer"),
    ("vp-finance", r"\bvp\b.*\bfinance\b|vice president.*financ"),
    ("controller", r"\bcontroller\b|\bcomptroller\b"),
    ("director-of-accounting", r"director of accounting|accounting director|head of accounting"),
    ("head-of-finance", r"head of finance|finance (?:lead|director|manager)|director of finance"),
    ("revops", r"\brevops\b|revenue operations"),
    ("founder-ceo", r"\bfounder\b|\bceo\b|chief executive"),
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
    ("growth-in-volume", r"\bgrow|growth|scal|more customers|volume|adding customers|revenue grew"),
]

# Ways of working a buyer leaves behind, as LegacyWorkflow values.
LEGACY: List[Tuple[str, str]] = [
    ("spreadsheets", r"spreadsheet|excel|google sheets"),
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
    return _first(ROLES, text)


def classify_trigger(text: str) -> Optional[str]:
    return _first(TRIGGERS, text)


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
        if not re.fullmatch(r"(?:B2B2?C?|SaaS|ERP|CRM|AR|AP)", name):
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
            role = classify_role(text)
            if role:
                facet.names.append(Named("role", role, text, str(SIT["role/" + role])))
        elif part == "trigger":
            trig = classify_trigger(quote)
            if trig:
                facet.names.append(Named("trigger", trig, text, str(SIT["trigger/" + trig])))
            facet.names += [n for n in vocab.names_in(text) if n.id in in_quote]
        else:
            facet.names += [n for n in vocab.names_in(text) if n.id in in_quote]
            # A pain about spreadsheets is about the workflow left behind, the same value
            # "before" holds: "tracking everything in Excel became unwieldy".
            if part in ("before", "pain"):
                legacy = _first(LEGACY, text) if _first(LEGACY, quote) else None
                if legacy:
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
                    f.names.append(Named(_kind_of(str(target)), str(target).rsplit("/", 1)[-1],
                                         str(g.value(target, SKOS.prefLabel) or ""), str(target)))
                for label in g.objects(fid, SIT.unresolved):
                    f.names.append(Named("unresolved", normalise_key(str(label)), str(label)))
                s.facets.append(f)
        out.append(s)
    return sorted(out, key=lambda s: s.source_url)


def _kind_of(uri: str) -> str:
    if "/concept/" in uri:
        return "concept"
    for kind in ("role", "trigger", "legacy"):
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
        "names": {k: count(k) for k in ("concept", "entity", "role", "trigger", "legacy", "unresolved")},
        "pains_without_concept": [f.text for f in facets if f.predicate == "sufferedFrom"
                                  and not any(n.kind == "concept" for n in f.names)],
        "needs_without_concept": [f.text for f in facets if f.predicate == "needed"
                                  and not any(n.kind == "concept" for n in f.names)],
        "roles_unclassified": [f.text for f in facets if f.predicate == "buyerRole" and not f.names],
        "triggers_unclassified": [f.text for f in facets if f.predicate == "triggeredBy"
                                  and not any(n.kind == "trigger" for n in f.names)],
        "registry_candidates": sorted({n.label for f in facets for n in f.names if n.kind == "unresolved"}),
    }


def read_site(site: str, vertical_id: str, stories: Optional[List[Dict[str, Any]]] = None,
              registry: Any = None, store: bool = True, path: Optional[str] = None) -> Dict[str, Any]:
    """Read a site's case studies into situations, store them, and report.

    `stories` lets a caller pass buyer_stories output already in hand; otherwise the pages
    are read now.
    """
    from industry_ontology import load_industry_ontology
    if stories is None:
        import buyer_stories
        stories = buyer_stories.prompts_for_site(site)["stories"]
    if registry is None:
        from entity_registry import default_store
        registry = default_store().registry()
    vocab = Vocabulary(vertical_id, load_industry_ontology(vertical_id).concepts, registry)
    situations = [from_story(s, site, vocab) for s in stories if s.get("parts")]
    gid = persist(situations, site, vertical_id, path=path) if store else None
    return {"graph_id": gid, "report": report(situations),
            "situations": [s.to_dict() for s in situations]}


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)
    stories = json.load(open(sys.argv[3], encoding="utf-8"))["stories"] if len(sys.argv) > 3 else None
    print(json.dumps(read_site(sys.argv[1], sys.argv[2], stories=stories), indent=1, ensure_ascii=False))
