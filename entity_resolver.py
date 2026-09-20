"""
GainARK OntoLeap - Entity Resolution
====================================
Turns the surface forms a crawl extracted into identities.

Every page of a crawl yields raw nodes: the text the extractor found, the label it gave,
the pages it came from. Before this module the site graph merged them on lowercase text
after stripping one of seven suffixes, so "Salesforce", "Salesforce.com" and
"Salesforce CRM" stayed three nodes, "churned customer" and "churned customers" two, and
the first spelling and first label seen won. This replaces that with one ladder, cheapest
and most certain first:

    0  group     forms that normalise to the same key are one group
    1  registry  the key is a curated name or alias of a registry entity
    2  concept   the key is a label or alternate label of a vertical concept - which
                 resolves to the registry entity instead when the registry names that
                 concept too, so a thing never has two identities
    3  brand     the key names the audited site itself (its domain, or its name
                 with a common domain affix: "Ordway" on ordwaylabs.com)
    4  suffix    the key is a known thing plus one product word ("Salesforce CRM",
                 "ERP systems"), and that known thing was found by rungs 1-3 or is
                 another named form on the same site
    5  mention   an unresolved common-noun phrase ("new customers", "billing portal"):
                 kept for coverage scoring, left out of the exported graph
    6  observed  an unresolved proper name: a node, identified within this site only

Two rules keep it honest:

  * A wrong merge is worse than a duplicate. Nothing here merges on similarity. Rung 4
    only attaches a form to something already identified, and the product words that
    turn "Google Cloud" into "Google" are allowed only onto registry entities, concepts
    and the brand - never onto a name this site merely happens to contain.
  * The key is for matching, never for display. A node keeps every spelling it was seen
    under, so coverage scoring still finds whatever the page actually wrote.

Resolution reads a Registry and never writes one. What each run saw is returned as an
observation, which the caller stores for the learning loop.
"""

import re
import unicodedata
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from entity_registry import Registry, entity_uri
from models import KGEdge, KGNode
from ontology_schema import canonical_class, concept_uri

# ---------------------------------------------------------------------------
# Rung 0 - normalisation
# ---------------------------------------------------------------------------

_LEGAL_SUFFIXES = {"inc", "incorporated", "llc", "ltd", "limited", "plc", "corp",
                   "corporation", "gmbh"}
_LEADING_ARTICLES = {"the", "a", "an"}
_ATTACHED_TLD = re.compile(r"(?<=[A-Za-z0-9])\.(com|io|ai|co|net|org|app)\b", re.I)


def _singular(word: str) -> str:
    if len(word) <= 4:
        return word  # "saas", "apis", "news": short words are rarely plurals worth folding
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("sses", "shes", "ches", "xes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def normalise_key(form: str) -> str:
    """The matching key for a surface form.

    Folds what no reader would keep apart: case, punctuation and hyphens, a possessive, a
    leading article, a legal suffix ("Stripe, Inc."), an attached TLD ("Salesforce.com"),
    and a trailing plural - but the plural only on a word written in lowercase or on a
    generic word, so a proper name ending in s is never cut.
    """
    s = unicodedata.normalize("NFKC", form or "").strip()
    s = s.replace("’", "'").replace("&", " and ")
    s = _ATTACHED_TLD.sub("", s)
    s = re.sub(r"'s\b", "", s)
    tokens = re.findall(r"[A-Za-z0-9]+", s)
    if len(tokens) > 1 and tokens[0].lower() in _LEADING_ARTICLES:
        tokens = tokens[1:]
    while len(tokens) > 1 and tokens[-1].lower() in _LEGAL_SUFFIXES:
        tokens = tokens[:-1]
    if not tokens:
        return ""
    words = [t.lower() for t in tokens]
    last = tokens[-1]
    if re.fullmatch(r"[A-Z]{2,}s", last):
        words[-1] = words[-1][:-1]  # "CRMs", "APIs": an acronym's plural is the acronym
    elif last == last.lower() or words[-1] in GENERIC_WORDS or _singular(words[-1]) in GENERIC_WORDS:
        words[-1] = _singular(words[-1])
    return " ".join(words)


def _fold(key: str) -> str:
    """A key with its spaces and its last word's plural gone: "card connect" and
    "cardconnect", "coupons" and "coupon" fold together."""
    words = key.split()
    if words:
        words[-1] = _singular(words[-1])
    return "".join(words)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:80] or "x"


# ---------------------------------------------------------------------------
# Rung 5 - what counts as a common-noun phrase
# ---------------------------------------------------------------------------

# Words a phrase can be built entirely from without naming anything in particular.
# "Enterprise Customers" is capitalised and still names no one.
GENERIC_WORDS = {
    "customer", "client", "user", "business", "company", "industry", "market", "segment",
    "region", "account", "team", "vendor", "provider", "partner", "investor",
    "organization", "organisation", "enterprise", "people", "consumer", "subscriber",
    "buyer", "member", "platform", "system", "software", "solution", "tool", "app",
    "application", "service", "product", "feature", "workflow", "model", "plan",
    "pricing", "price", "billing", "portal", "cloud", "data", "finance", "tax",
    "commitment", "count", "name", "base", "relationship", "strategy", "geography",
    "geographic", "global", "globally", "worldwide", "other", "large", "big", "small",
    "mid", "sized", "key", "end", "sales", "revenue", "payment", "process", "operation",
    "option", "method", "term", "contract", "channel", "cycle", "stack", "infrastructure",
    "integration", "connector", "functionality", "communication", "connectivity",
    "automation", "security", "technology", "financial", "institution", "success",
    "high", "growth", "growing", "fast", "early", "stage", "complex", "potential",
    "self", "serve", "active", "beta", "licensed", "competitor", "competitive",
}

# A phrase opening with one of these describes a group rather than naming a thing.
_DESCRIPTIVE_OPENERS = {
    "the", "a", "an", "our", "your", "their", "its", "this", "that", "these", "those",
    "new", "existing", "current", "other", "others", "many", "some", "all", "any", "each",
    "every", "most", "more", "multiple", "various", "several", "few", "specific", "top",
    "first", "different",
}


def _opens_descriptively(form: str) -> bool:
    """ "new customers", "Our clients", "500 customers" - but not "New York City market".

    The opener describes a group only when the word after it is ordinary. When both are
    capitalised the pair is part of a name.
    """
    tokens = re.findall(r"[A-Za-z0-9%.]+", form)
    if not tokens:
        return False
    if tokens[0][0].isdigit():
        return True
    if tokens[0].lower() not in _DESCRIPTIVE_OPENERS or len(tokens) < 2:
        return False
    return tokens[0].islower() or not tokens[1][0].isupper()


def is_common_noun_phrase(forms: List[str], key: str) -> bool:
    words = key.split()
    if not words:
        return True
    if any(_opens_descriptively(f) for f in forms):
        return True
    # Written without a capital anywhere it appeared: the site used it as an ordinary
    # phrase, not a name.
    if any(not re.search(r"[A-Z]", f) for f in forms):
        return True
    return all(w in GENERIC_WORDS or _singular(w) in GENERIC_WORDS for w in words)


# ---------------------------------------------------------------------------
# Rungs 3 and 4 - brand and suffix rules
# ---------------------------------------------------------------------------

# What sites add to a name to get a domain: ordway -> ordwaylabs.com, jobber -> getjobber.com.
_DOMAIN_AFFIXES = ("labs", "hq", "app", "inc", "io", "ai", "get", "try", "use", "go",
                   "join", "the", "software", "tech", "hub")

# Words that only turn "X word" into X when X is already identified by the registry, a
# concept or the brand. "Salesforce CRM" -> Salesforce, but never "Google Cloud" -> an
# unregistered "Google" just because both appeared.
_PRODUCT_WORDS = {"crm", "online", "cloud", "erp", "payments", "payment", "billing",
                  "model", "system", "app", "suite", "hcm", "financials", "platform",
                  "software", "api", "integration", "connector", "sync", "solution"}

# The subset the site graph always stripped between any two forms on a site. Kept for
# observed targets so no merge the old canonicaliser made is lost.
_CONNECTIVE_WORDS = {"api", "integration", "connector", "sync", "platform", "software", "solution"}


def domain_label(domain: str) -> str:
    host = (domain or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host.split(".")[0]


def names_brand(key: str, domain: str) -> bool:
    label = domain_label(domain)
    joined = key.replace(" ", "")
    if len(joined) < 4 or not label:
        return False
    if joined == label:
        return True
    for affix in _DOMAIN_AFFIXES:
        if label == joined + affix or label == affix + joined:
            return True
    return False


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

class _Group:
    __slots__ = ("key", "forms", "labels", "mentions", "sources", "order")

    def __init__(self, key: str, order: int):
        self.key = key
        self.forms: Counter = Counter()
        self.labels: Counter = Counter()
        self.mentions = 0
        self.sources: List[str] = []
        self.order = order

    def add(self, form: str, label: Optional[str], count: int, urls: List[str]):
        self.forms[form] += max(count, 1)
        if label:
            self.labels[canonical_class(label)] += 1
        self.mentions += max(count, 0)
        for u in urls:
            if u not in self.sources:
                self.sources.append(u)

    def display(self) -> str:
        # Most frequent spelling; ties go to the one with a capital, then first seen.
        ranked = sorted(self.forms.items(),
                        key=lambda kv: (-kv[1], 0 if re.search(r"[A-Z]", kv[0]) else 1))
        return ranked[0][0]

    def label(self) -> str:
        return self.labels.most_common(1)[0][0] if self.labels else "Entity"


_KIND_TYPES = {"organization": "Organization", "product": "Product", "standard": "Standard",
               "topic": "Topic", "place": "Place"}
_CONCEPT_TYPES = {"feature": "Feature", "process": "Process", "pricing": "PricingModel",
                  "standard": "Standard", "domain": "Domain"}


class SiteResolution:
    """The resolved graph of one site: nodes, mentions, edges and what happened."""

    def __init__(self, nodes: List[KGNode], mentions: List[KGNode], edges: List[KGEdge],
                 brand: KGNode, summary: Dict[str, Any], observation: Dict[str, Any]):
        self.nodes = nodes
        self.mentions = mentions
        self.edges = edges
        self.brand = brand
        self.summary = summary
        self.observation = observation


class _ConceptIndex:
    def __init__(self, industry):
        self.by_key: Dict[str, Any] = {}
        ambiguous = set()
        for c in (industry.concepts if industry else []):
            for form in [c.pref_label] + list(c.alt_labels or []):
                key = normalise_key(form)
                if not key:
                    continue
                if key in self.by_key and self.by_key[key].id != c.id:
                    ambiguous.add(key)
                self.by_key.setdefault(key, c)
        for key in ambiguous:
            self.by_key.pop(key, None)

    def get(self, key: str):
        return self.by_key.get(key)


def resolve_site(raw_nodes: List[KGNode], raw_edges: List[KGEdge], domain: str,
                 vertical_id: Optional[str], registry: Registry, industry=None) -> SiteResolution:
    """Resolve one crawl's raw page nodes and edges into identified nodes and edges."""
    concepts = _ConceptIndex(industry)
    # A vertical concept that a registry entity also names is one thing with two records.
    # Resolve it to the entity - which carries the external identity and is shared across
    # verticals - and keep the concept as the entity's exactMatch.
    concept_entity: Dict[str, Any] = {}
    for ent in registry.entities.values():
        if ent.status != "active":
            continue
        for form in ent.forms():
            c = concepts.get(normalise_key(form))
            if c is not None:
                concept_entity.setdefault(c.id, ent)
    brand_entity = registry.by_domain(domain)
    brand_id = brand_entity.uri if brand_entity else entity_uri("org-" + slug(domain))

    # Rung 0: group forms by key.
    groups: Dict[str, _Group] = {}
    for n in raw_nodes:
        key = normalise_key(n.canonical_name)
        if not key:
            continue
        g = groups.get(key)
        if g is None:
            g = groups[key] = _Group(key, len(groups))
        g.add(n.canonical_name, n.entity_type, n.mentions_count, n.source_urls)
        for alias in n.aliases:
            if alias != n.canonical_name and normalise_key(alias) == key:
                g.forms[alias] += 0

    def direct(key: str) -> Optional[Tuple[str, str, Any]]:
        """Rungs 1-3 for one key: (target kind, target id, record), or None."""
        if registry.is_blocked(key):
            return ("blocked", key, None)
        hits = registry.lookup(key)
        if len(hits) == 1:
            return ("entity", hits[0].id, hits[0])
        if len(hits) > 1:
            return ("ambiguous", key, hits)
        concept = concepts.get(key)
        if concept is not None:
            ent = concept_entity.get(concept.id)
            if ent is not None:
                return ("entity", ent.id, ent)
            return ("concept", concept.id, concept)
        if brand_entity is not None and key in {normalise_key(f) for f in brand_entity.forms()}:
            return ("brand", brand_id, brand_entity)
        if names_brand(key, domain):
            return ("brand", brand_id, brand_entity)
        return None

    targets: Dict[str, Tuple[str, str, str]] = {}   # key -> (kind, id, method)
    for key in groups:
        hit = direct(key)
        if hit and hit[0] in ("entity", "concept", "brand"):
            method = {"entity": "registry", "concept": "concept", "brand": "brand"}[hit[0]]
            targets[key] = (hit[0], hit[1], method)
        elif hit and hit[0] == "blocked":
            targets[key] = ("mention", key, "blocked")
        elif hit and hit[0] == "ambiguous":
            targets[key] = ("observed", key, "ambiguous")

    named_keys = {k for k, g in groups.items()
                  if k not in targets and not is_common_noun_phrase(list(g.forms), k)}

    def by_suffix(key: str) -> Optional[Tuple[str, str, str]]:
        words = key.split()
        if len(words) < 2 or words[-1] not in _PRODUCT_WORDS:
            return None
        rest = " ".join(words[:-1])
        if len(rest.replace(" ", "")) < 3:
            return None
        hit = direct(rest)
        if hit and hit[0] in ("entity", "concept", "brand"):
            return (hit[0], hit[1], "suffix")
        prior = targets.get(rest)
        if prior and prior[0] in ("entity", "concept", "brand"):
            return (prior[0], prior[1], "suffix")
        if words[-1] in _CONNECTIVE_WORDS and rest in named_keys:
            return ("observed", rest, "suffix")
        return None

    # Rung 4, then rungs 5 and 6 for whatever is left.
    for key, g in groups.items():
        if key in targets:
            continue
        hit = by_suffix(key)
        if hit:
            targets[key] = hit
        elif key in named_keys:
            targets[key] = ("observed", key, "observed")
        else:
            targets[key] = ("mention", key, "mention")

    # A suffix may point at an observed key that itself resolved further; follow once.
    for key, (kind, tid, method) in list(targets.items()):
        if kind == "observed" and tid != key and tid in targets and targets[tid][0] != "observed":
            targets[key] = (targets[tid][0], targets[tid][1], method)

    # Rung 7: one name written two ways on the same site. Normalisation keeps "Card
    # Connect" and "CardConnect", "Coupon" and "Coupons", "AWS" and "Amazon Web Services"
    # apart; the 14 Sep 2026 ordwaylabs.com run listed each pair as two entities. Only
    # keys nothing else resolved are moved - a registry or concept match already is an
    # identity, and a blocked key stays blocked.
    def movable(key: str) -> bool:
        kind, _, method = targets[key]
        return kind in ("observed", "mention") and method != "blocked"

    def rank(key: str) -> Tuple[int, int, int]:
        kind = targets[key][0]
        g = groups.get(key)
        return (0 if kind in ("entity", "concept", "brand") else 1 if kind == "observed" else 2,
                -(g.mentions if g else 0), -len(key.split()))

    # Keys join when any of their folds meet. A written form is folded as well as the key,
    # because the key has already lost a legal suffix: "Hashi Corp" keys as "hashi".
    parent: Dict[str, str] = {}

    def find(k: str) -> str:
        while parent.setdefault(k, k) != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    first_by_fold: Dict[str, str] = {}
    for key in targets:
        if targets[key][2] == "blocked":
            continue
        g = groups.get(key)
        key_folds = {_fold(key)} | {_fold(" ".join(re.findall(r"[a-z0-9]+", f.lower())))
                                    for f in (g.forms if g else [])}
        for fold in key_folds:
            if not fold:
                continue
            if fold in first_by_fold:
                parent[find(key)] = find(first_by_fold[fold])
            else:
                first_by_fold[fold] = key
    clusters: Dict[str, List[str]] = {}
    for key in parent:
        clusters.setdefault(find(key), []).append(key)
    fold_target: Dict[str, Tuple[str, str]] = {}
    for keys in clusters.values():
        best = min(keys, key=rank)
        for k in keys:
            fold_target[_fold(k)] = (targets[best][0], targets[best][1])
            if k != best and movable(k):
                targets[k] = (targets[best][0], targets[best][1], "spelling")

    # An acronym the site writes in capitals, when exactly one longer name it uses has
    # those initials: "AWS" is "Amazon Web Services", "US" is "United States".
    by_initials: Dict[str, set] = {}
    for key in targets:
        words = key.split()
        if len(words) >= 2 and all(len(w) > 1 and w.isalpha() for w in words) and targets[key][2] != "blocked":
            by_initials.setdefault("".join(w[0] for w in words), set()).add(
                (targets[key][0], targets[key][1]))
    for key, g in groups.items():
        if " " in key or not movable(key) or not any(
                re.fullmatch(r"[A-Z]{2,5}", f.replace(".", "")) for f in g.forms):
            continue
        found = by_initials.get(key.replace(" ", ""), set())
        if len(found) == 1:
            kind, tid = next(iter(found))
            targets[key] = (kind, tid, "acronym")

    # Anything still pointing at a key that just moved follows it.
    for key, (kind, tid, method) in list(targets.items()):
        moved = targets.get(tid)
        if kind in ("observed", "mention") and tid != key and moved and (moved[0], moved[1]) != (kind, tid):
            targets[key] = (moved[0], moved[1], method)

    # Edges name their endpoints by text too; resolve those forms the same way.
    def resolve_form(form: str) -> Tuple[str, str, str]:
        key = normalise_key(form)
        if key in targets:
            return targets[key]
        hit = direct(key)
        if hit and hit[0] in ("entity", "concept", "brand"):
            t = (hit[0], hit[1], {"entity": "registry", "concept": "concept", "brand": "brand"}[hit[0]])
        elif _fold(key) in fold_target:
            t = fold_target[_fold(key)] + ("spelling",)
        else:
            t = by_suffix(key) or ("observed", key, "edge")
        targets[key] = t
        return t

    edge_forms: Dict[str, Counter] = {}
    for e in raw_edges:
        for form in (e.source, e.target):
            t = resolve_form(form)
            ident = "%s|%s" % (t[0], t[1])
            edge_forms.setdefault(ident, Counter())[form] += 1

    # Build one node per identity.
    def node_id(kind: str, tid: str) -> str:
        if kind == "entity":
            return entity_uri(tid)
        if kind == "concept":
            return concept_uri(vertical_id or "unknown", tid)
        if kind == "brand":
            return brand_id
        if kind == "mention":
            return "https://%s/mention/%s" % (domain, slug(tid))
        return "https://%s/entity/%s" % (domain, slug(tid))

    buckets: Dict[str, Dict[str, Any]] = {}

    def bucket(kind: str, tid: str) -> Dict[str, Any]:
        ident = "%s|%s" % (kind, tid)
        b = buckets.get(ident)
        if b is None:
            b = buckets[ident] = {"kind": kind, "tid": tid, "forms": Counter(), "labels": Counter(),
                                  "mentions": 0, "sources": [], "methods": Counter(), "order": len(buckets)}
        return b

    for key, g in sorted(groups.items(), key=lambda kv: kv[1].order):
        kind, tid, method = targets[key]
        b = bucket(kind, tid)
        b["forms"].update(g.forms)
        b["labels"].update(g.labels)
        b["mentions"] += g.mentions
        b["methods"][method] += 1
        for u in g.sources:
            if u not in b["sources"]:
                b["sources"].append(u)

    for ident, forms in edge_forms.items():
        kind, tid = ident.split("|", 1)
        b = bucket(kind, tid)
        if not b["forms"]:
            b["methods"]["edge"] += 1
        for form, count in forms.items():
            b["forms"][form] += 0 if form in b["forms"] else count

    brand_label = domain_label(domain)
    nodes: List[KGNode] = []
    mentions: List[KGNode] = []
    brand_node: Optional[KGNode] = None
    by_ident: Dict[str, KGNode] = {}

    for ident, b in sorted(buckets.items(), key=lambda kv: kv[1]["order"]):
        kind, tid = b["kind"], b["tid"]
        forms_ranked = [f for f, _ in sorted(b["forms"].items(),
                                             key=lambda kv: (-kv[1], 0 if re.search(r"[A-Z]", kv[0]) else 1))]
        wikidata = description = concept_link = None
        if kind == "entity":
            ent = registry.entities[tid]
            name, etype = ent.prefLabel, _KIND_TYPES.get(ent.kind, "Entity")
            wikidata, description = ent.wikidata, ent.definition or None
            c = next((concepts.get(normalise_key(f)) for f in ent.forms()
                      if concepts.get(normalise_key(f)) is not None), None)
            if c is not None:
                concept_link = concept_uri(vertical_id or "unknown", c.id)
        elif kind == "concept":
            c = concepts.by_key.get(normalise_key(forms_ranked[0])) if forms_ranked else None
            c = next((x for x in concepts.by_key.values() if x.id == tid), c)
            name = c.pref_label if c else forms_ranked[0]
            etype = _CONCEPT_TYPES.get(getattr(c, "kind", ""), "Concept")
            description = getattr(c, "definition", None) or None
        elif kind == "brand":
            if brand_entity is not None:
                name, etype = brand_entity.prefLabel, _KIND_TYPES.get(brand_entity.kind, "Organization")
                wikidata, description = brand_entity.wikidata, brand_entity.definition or None
            else:
                # A name the site writes beats its bare domain label ("Ordway", not "Ordwaylabs").
                # Among forms that name the brand itself - not "Chargebee Billing", which
                # reached it through a product word - the most frequent.
                own = [f for f in forms_ranked if names_brand(normalise_key(f), domain)]
                written = [f for f in own if normalise_key(f).replace(" ", "") != brand_label]
                name = (written or own or [brand_label.capitalize()])[0]
                etype = "Organization"
        else:
            name = forms_ranked[0] if forms_ranked else tid
            etype = b["labels"].most_common(1)[0][0] if b["labels"] else "Entity"

        # A role is only labelled where a page stated it (page_graph._apply_role_evidence),
        # so one page naming Zuora as the alternative outweighs five that just list it. A
        # role is relative to this site, so it also shows over a registry entity's kind.
        if kind in ("entity", "observed", "mention"):
            for role in ("Competitor", "Customer"):
                if b["labels"].get(role):
                    etype = role
                    break

        node = KGNode(
            id=node_id(kind, tid),
            canonical_name=name,
            entity_type=etype,
            aliases=forms_ranked,
            wikidata_id=wikidata,
            description=description,
            mentions_count=b["mentions"] or sum(b["forms"].values()),
            source_urls=b["sources"],
            resolution=b["methods"].most_common(1)[0][0],
            concept_uri=concept_link,
        )
        by_ident[ident] = node
        if kind == "brand":
            brand_node = node
        elif kind == "mention":
            mentions.append(node)
        else:
            nodes.append(node)

    if brand_node is None:
        brand_node = KGNode(id=brand_id, canonical_name=(brand_entity.prefLabel if brand_entity
                                                          else brand_label.capitalize()),
                            entity_type="Organization", resolution="brand",
                            wikidata_id=brand_entity.wikidata if brand_entity else None)
    nodes.insert(0, brand_node)

    # Edges, deduplicated on identities rather than on spellings.
    unique: Dict[Tuple[str, str, str], KGEdge] = {}
    for e in raw_edges:
        s, t = resolve_form(e.source), resolve_form(e.target)
        sn = by_ident.get("%s|%s" % (s[0], s[1]))
        tn = by_ident.get("%s|%s" % (t[0], t[1]))
        if sn is None or tn is None or sn.id == tn.id:
            continue
        # A node nothing typed ("Overage Pricing" as ENTITY) takes the type the relation
        # names for its object, so "hasFeature -> Entity" is not induced beside
        # "hasFeature -> Feature".
        for node, implied in ((sn, e.source_type), (tn, e.target_type)):
            if node.entity_type in ("Entity", "Concept") and implied:
                node.entity_type = canonical_class(implied)
        k = (sn.id, e.predicate, tn.id)
        existing = unique.get(k)
        if existing is None:
            unique[k] = KGEdge(
                id="edge:%s-%s-%s" % (slug(sn.id.rsplit("/", 1)[-1]), e.predicate.lower(),
                                      slug(tn.id.rsplit("/", 1)[-1])),
                source=sn.canonical_name, target=tn.canonical_name,
                source_id=sn.id, target_id=tn.id,
                predicate=e.predicate, source_type=sn.entity_type, target_type=tn.entity_type,
                confidence=e.confidence, provenance_sentence=e.provenance_sentence,
                source_url=e.source_url,
            )
        else:
            existing.confidence = max(existing.confidence, e.confidence)
            # The sentence and its page travel together. Taking the longer sentence alone
            # paired it with the first edge's URL, a quote attributed to a page it is not on.
            if e.provenance_sentence and len(e.provenance_sentence) > len(existing.provenance_sentence or ""):
                existing.provenance_sentence = e.provenance_sentence
                existing.source_url = e.source_url

    # The resolution ladder: how much each rung carried. The learning metric is this
    # shifting toward "registry" and "concept" run over run.
    by_method_groups: Counter = Counter()
    by_method_mentions: Counter = Counter()
    for key, g in groups.items():
        method = targets[key][2]
        by_method_groups[method] += 1
        by_method_mentions[method] += max(g.mentions, 1)
    summary = {
        "raw_nodes": len(raw_nodes),
        "forms": len(groups),
        "nodes": len(nodes),
        "mentions": len(mentions),
        "groups_by_method": dict(by_method_groups),
        "mentions_by_method": dict(by_method_mentions),
    }

    observation = {
        "domain": domain,
        "vertical_id": vertical_id,
        "brand_id": brand_id,
        "forms": [
            {"key": key, "forms": dict(g.forms), "labels": dict(g.labels),
             "mentions": g.mentions, "pages": len(g.sources),
             "target": "%s:%s" % (targets[key][0], targets[key][1]), "method": targets[key][2]}
            for key, g in sorted(groups.items())
        ],
    }
    return SiteResolution(nodes, mentions, list(unique.values()), brand_node, summary, observation)
