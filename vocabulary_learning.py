"""
GainARK OntoLeap - Vocabulary Learning for Verticals
====================================================
A vertical is only as useful as its concepts. Two of eleven have any - both built by hand
- so for every other category a crawl has nothing to rank pages by and coverage has
nothing to measure. This lets a vertical learn its vocabulary from the sites crawled into
it, with a person deciding what it becomes.

Every persisted crawl already writes an observation (entity_registry.record_observation):
each surface form, and how the resolver identified it. The forms nothing identified are
where a vertical's missing vocabulary is. On 17 Sep 2026, rippling.com, gusto.com and
deel.com left 740 unresolved; 97 appeared on two or more of the three, and read like the
category's own words - payroll, benefits administration, HRIS, EOR, PEO, onboarding,
background checks, employment laws, 401(k), PTO, ACA - while each vendor's own product
names ("Rippling AI", "App Studio") appeared on one site only.

A term is proposed when:

  sites       it is written on at least MIN_SITES sites of the vertical;
  contrast    it is not about as common on the sites of other verticals - "customers",
              "businesses" and "cloud" are on every site of every kind;
  brand       it is not the name of a site in the vertical ("Gusto" on deel.com);
  decisions   nobody has decided it already, and nobody has called it generic anywhere.

Spelling variants ("compliance", "compliant", "compliantly") are proposed as one term,
and so are an acronym and its expansion ("LMS", "Learning Management System"). A site is
a registrable domain: support.ordwaylabs.com is not a second witness for ordwaylabs.com.
A term with a word written in lowercase, or with none but acronyms and numbers, is
proposed as a concept; one whose words are all capitalised ("SAP SuccessFactors") is
proposed as a name, for the registry. The reviewer can say otherwise either way.

Proposals are computed when asked for, from the observations and the decisions, so there
is nothing stored to go stale or to race over. Decisions are the durable part: one
immutable object each under vocabulary/decisions/ in the archive, folded latest-first.

  approve (concept)  adds the concept to the vertical and mirrors it (vertical_store)
  approve (name)     creates an active registry entity (a reviewer event)
  reject             never proposed again for this vertical
  generic            never proposed again for any vertical - reviews teach the contrast
                     what the observations cannot yet, while few verticals are crawled

The contrast grows with every crawl of any kind. With only billing sites to compare
against, the first HR proposals still carried "compliant", "legal" and "admins".
"""

import json
import logging
import os
import re
import threading
import time
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Tuple

from crawl_planner import brand_domain
from entity_registry import (ACTOR_REVIEWER, KINDS, OBSERVATIONS_PREFIX, default_store,
                             make_event)
from ontology_schema import slug_for_label

logger = logging.getLogger("gainark.vocabulary_learning")

DECISIONS_PREFIX = "vocabulary/decisions"
DECISIONS = ("approve", "reject", "generic")

MIN_SITES = int(os.environ.get("ONTOLEAP_VOCAB_MIN_SITES", "2") or 2)
# A term counts as generic when the share of other verticals' crawled pages that write it
# is at least this fraction of the share of this vertical's. Pages, not sites: with one
# billing site to compare against, a single billing page naming "payroll" would otherwise
# have made payroll generic for HR. ordwaylabs.com writes "customers" on 94 of 107 pages.
CONTRAST_RATIO = 0.5
# The resolver methods that mean "nothing identified this form". "suffix" is left out:
# it is a name with a product word on it, which is a vendor's, not a category's.
UNRESOLVED = {"observed", "mention", "spelling", "acronym"}
# Words shorter than this are kept whole when spelling variants are folded.
_VARIANT_STEM = 7
CACHE_SECONDS = int(os.environ.get("ONTOLEAP_VOCAB_CACHE_SECONDS", "120") or 120)

_ACRONYM = re.compile(r"^[A-Z][A-Z0-9&]{1,7}s?$")
_WORD = re.compile(r"[A-Za-z0-9()&'.-]+")
# A capital inside a word after a lowercase run. One such word is how products write
# themselves ("SuccessFactors", "QuickBooks"); two in a form is page text glued together
# ("PayrollBenefits AdministrationHead").
_INNER_CAPITAL = re.compile(r"[a-z]{2,}[A-Z]")
# A digit between letters, after a lowercase one: how a vendor spells a product
# ("Payroll4Free" on gusto.com), never how a category writes a term. B2B and W-2 pass.
_PRODUCT_DIGIT = re.compile(r"[a-z]\d+[A-Za-z]")
# What a word may carry and still be the same word. Any other short tail is the next word
# of the page glued on: "payrollin" is "payroll in".
_INFLECTIONS = {"s", "es", "ed", "d", "er", "ers", "ing", "ly"}
_TAIL = 3
# Words a seed term can carry that say nothing about which term it is.
_FILLER = {"and", "of", "the", "for", "a", "an", "to", "in", "with"}


def variant_key(key: str) -> str:
    """One key for a term's spelling variants: each word cut to its first seven letters.

    "compliance", "compliant" and "compliantly" meet; "payroll" and "payrolls" meet.
    """
    return " ".join(w[:_VARIANT_STEM] for w in key.split())


def _site(domain: str) -> str:
    """The site an observation speaks for: its registrable domain."""
    return brand_domain((domain or "").lower())


def _site_label(domain: str) -> str:
    return _site(domain).split(".")[0]


def _latest_per_site(observations: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Each site's most recent observation per host. A site crawled five times is one
    site; its help centre is the same site, read from another host."""
    latest: Dict[str, Dict[str, Any]] = {}
    for obs in observations:
        domain = (obs.get("domain") or "").lower()
        if domain and obs.get("forms"):
            latest[domain] = obs      # callers pass observations oldest first
    return list(latest.values())


def _words(form: str) -> List[str]:
    return _WORD.findall(form or "")


def _is_name(forms: List[str]) -> bool:
    """Every written form has all its words capitalised, and at least one word that is
    not an acronym or a number: "SAP SuccessFactors", not "HR compliance" or "401(k)"."""
    for form in forms:
        words = [w for w in _words(form) if not _ACRONYM.match(w) and not w[:1].isdigit()]
        if not words or any(w[:1].islower() for w in words):
            return False
    return True


def _clean(form: str) -> bool:
    """Not page text glued together ("PayrollBenefits AdministrationHead") and not a
    stutter ("Compliance Compliance")."""
    words = _words(form)
    glued = sum(1 for w in words if _INNER_CAPITAL.search(w)) >= 2
    product = any(_PRODUCT_DIGIT.search(w) for w in words)
    lower = [w.lower() for w in words]
    return bool(words) and not glued and not product and all(a != b for a, b in zip(lower, lower[1:]))


def _stray_tail(form: str, others: Iterable[str]) -> bool:
    """Another form of the same term with a fragment stuck on that is not an inflection:
    "payrollin" beside "payroll". Variant folding puts it in the term's group, since both
    begin with the same seven letters, and nothing else would take it out."""
    low = form.lower()
    for other in others:
        base = other.lower()
        if base != low and low.startswith(base) and len(low) - len(base) <= _TAIL                 and low[len(base):] not in _INFLECTIONS:
            return True
    return False


def _term_words(term: str) -> set:
    return {w[:_VARIANT_STEM] for w in re.findall(r"[a-z0-9]+", (term or "").lower())
            if w not in _FILLER}


def _seed_overlap(label: str, forms: List[str], seeds: Iterable[str]) -> Tuple[Optional[str], List[str]]:
    """(the seed term this proposal is, the seed terms it overlaps).

    A vertical's seed terms are strings, not concepts, so the resolver never identifies
    them and a crawl proposes them back: the held-out HR proposals carried Payroll
    processing, Benefits administration and Time tracking, all already seeds. The
    reviewer is told, rather than the proposal hidden - approving it is how a seed becomes
    a concept, and "Onboarding" beside "New hire onboarding" may be either one term or two.
    """
    same, near = None, []
    keys = [_term_words(f) for f in [label] + list(forms)]
    for seed in seeds or []:
        words = _term_words(seed)
        if not words:
            continue
        if any(k == words for k in keys):
            same = same or seed
        elif any(k and (k <= words or words <= k) for k in keys):
            near.append(seed)
    return same, near


def _pick_label(forms: Counter, prefer_spelled: bool) -> str:
    """The most written spelling, case aside, shown with a capital if a site wrote one."""
    by_lower: Counter = Counter()
    for form, count in forms.items():
        if not prefer_spelled or len(_words(form)) >= 2:
            by_lower[form.lower()] += count
    best = by_lower.most_common(1)[0][0]
    variants = [f for f, _ in forms.most_common() if f.lower() == best]
    return next((f for f in variants if f[:1].isupper()), variants[0])


def _initials(form: str) -> Optional[str]:
    """The acronym a written-out phrase abbreviates: "Employer of Record" -> "EOR". None
    for a phrase that is not written out ("ACA/COBRA admin") or is a single word."""
    words = _words(form)
    if len(words) < 2 or any(_ACRONYM.match(w) or not w[:1].isalpha() for w in words):
        return None
    return "".join(w[0] for w in words).upper()


def fold_decisions(events: Iterable[Dict[str, Any]]) -> Tuple[Dict[Tuple[str, str], Dict[str, Any]], set]:
    """(vertical_id, key) -> the latest decision, and the keys called generic anywhere."""
    latest: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for ev in sorted(events, key=lambda e: e.get("event_id", "")):
        latest[(ev.get("vertical_id", ""), ev.get("key", ""))] = ev
    generic = {key for (_, key), ev in latest.items() if ev.get("decision") == "generic"}
    return latest, generic


def build_proposals(observations: List[Dict[str, Any]], vertical_id: str,
                    decisions: Iterable[Dict[str, Any]] = (),
                    seed_terms: Iterable[str] = ()) -> Dict[str, Any]:
    """What a vertical's crawls suggest it is missing, best evidence first. Pure.

    `seed_terms` are the vertical's own seed strings; a proposal that is or overlaps one
    says so (see _seed_overlap)."""
    seed_terms = list(seed_terms or [])
    hosts = _latest_per_site(observations)
    ours = [o for o in hosts if o.get("vertical_id") == vertical_id]
    theirs = [o for o in hosts if o.get("vertical_id") != vertical_id]
    our_sites = {_site(o["domain"]) for o in ours}
    their_sites = {_site(o["domain"]) for o in theirs} - our_sites
    latest, generic = fold_decisions(decisions)
    brands = {_site_label(o["domain"]) for o in ours}

    groups: Dict[str, Dict[str, Any]] = {}
    for obs in ours:
        own = _site_label(obs["domain"])
        for rec in obs["forms"]:
            key = rec.get("key") or ""
            if rec.get("method") not in UNRESOLVED or not key:
                continue
            words = set(key.split())
            # The site's own brand in its own words, and any other site's brand at all.
            if own in key.replace(" ", "") or words & brands:
                continue
            g = groups.setdefault(variant_key(key), {"forms": Counter(), "sites": {},
                                                     "types": Counter(), "keys": set()})
            g["forms"].update(rec.get("forms") or {})
            g["types"].update(rec.get("labels") or {})
            g["keys"].add(key)
            site = _site(obs["domain"])
            g["sites"][site] = g["sites"].get(site, 0) + int(rec.get("pages") or 0)

    # An acronym joins the term it abbreviates, when one term has those initials.
    # Three letters at least: "HR" is human resources and hiring regulations alike.
    by_initials: Dict[str, List[str]] = {}
    for gkey, g in groups.items():
        for form in g["forms"]:
            initials = _initials(form)
            if initials and len(initials) >= 3:
                by_initials.setdefault(initials, []).append(gkey)
    for gkey in list(groups):
        forms = list(groups[gkey]["forms"])
        acronym = next((f[:-1] if f.endswith("s") else f for f in forms
                        if _ACRONYM.match(f) and len(_words(f)) == 1), None)
        targets = [t for t in set(by_initials.get(acronym, [])) - {gkey} if t in groups] if acronym else []
        if targets:
            # "LMS" is both "Learning Management System" and "learning management software":
            # it joins the one more sites write.
            best = max(targets, key=lambda t: (len(groups[t]["sites"]), sum(groups[t]["sites"].values()), t))
            into, g = groups[best], groups.pop(gkey)
            into["forms"].update(g["forms"])
            into["types"].update(g["types"])
            into["keys"] |= g["keys"]
            for site, pages in g["sites"].items():
                into["sites"][site] = into["sites"].get(site, 0) + pages

    elsewhere: Dict[str, set] = {}
    elsewhere_pages: Counter = Counter()
    for obs in theirs:
        site = _site(obs["domain"])
        if site in their_sites:
            for rec in obs["forms"]:
                if rec.get("key"):
                    elsewhere.setdefault(variant_key(rec["key"]), set()).add(site)
                    elsewhere_pages[variant_key(rec["key"])] += int(rec.get("pages") or 0)
    pages_here = sum(int(o.get("pages_crawled") or 0) for o in ours)
    pages_there = sum(int(o.get("pages_crawled") or 0) for o in theirs if _site(o["domain"]) in their_sites)

    proposals, skipped = [], Counter()
    for gkey, g in groups.items():
        if len(g["sites"]) < MIN_SITES:
            skipped["one site"] += 1
            continue
        if gkey in generic:
            skipped["called generic"] += 1
            continue
        if (vertical_id, gkey) in latest:
            skipped["decided"] += 1
            continue
        if pages_here and pages_there:
            share_here = sum(g["sites"].values()) / pages_here
            share_there = elsewhere_pages[gkey] / pages_there
        else:   # observations from before pages were counted
            share_here = len(g["sites"]) / len(our_sites)
            share_there = len(elsewhere.get(gkey, ())) / len(their_sites) if their_sites else 0.0
        if share_there and share_there >= CONTRAST_RATIO * share_here:
            skipped["on other verticals too"] += 1
            continue
        # Glued and stuttered forms never become labels or alternates; a term with no
        # other form is page noise.
        clean = Counter({f: c for f, c in g["forms"].items() if _clean(f)})
        clean = Counter({f: c for f, c in clean.items() if not _stray_tail(f, clean)})
        forms = [f for f, _ in clean.most_common()]
        if not forms:
            skipped["page noise"] += 1
            continue
        # The expansion names the term; the acronym is one of its forms.
        spelled = any(len(_words(f)) >= 2 for f in forms) and any(_ACRONYM.match(f) for f in forms)
        label = _pick_label(clean, prefer_spelled=spelled)
        seed, near_seeds = _seed_overlap(label, forms, seed_terms)
        proposals.append({
            "key": gkey,
            "label": label,
            "kind": "name" if _is_name(forms) else "concept",
            "forms": forms[:8],
            "sites": dict(sorted(g["sites"].items())),
            "site_count": len(g["sites"]),
            "pages": sum(g["sites"].values()),
            "types": [t for t, _ in g["types"].most_common(3)],
            "elsewhere": sorted(elsewhere.get(gkey, ())),
            "seed": seed,
            "near_seeds": near_seeds,
        })
    proposals.sort(key=lambda p: (-p["site_count"], -p["pages"], p["key"]))
    return {
        "vertical_id": vertical_id,
        "sites": sorted(our_sites),
        "compared_with": sorted(their_sites),
        "proposals": proposals,
        "skipped": dict(skipped),
    }


# ---------------------------------------------------------------------------
# Applying decisions to a vertical
# ---------------------------------------------------------------------------

def add_concept(data: Dict[str, Any], label: str, forms: List[str],
                definition: Optional[str] = None, broader: Optional[str] = None) -> bool:
    """Add a concept, or the forms of an existing one, to a vertical profile. Returns True
    when the profile changed. A form another concept already owns is not taken from it.

    `broader` names the parent concept by any of its labels. A parent not in the profile
    yet is skipped, not invented: the caller applies again once it has been approved.
    A parent that would make the tree a cycle is refused."""
    concepts = data.setdefault("concepts", [])
    label = label.strip()
    owned: Dict[str, Dict[str, Any]] = {}
    for c in concepts:
        for form in [c.get("prefLabel", "")] + list(c.get("altLabels") or []):
            owned.setdefault(form.strip().lower(), c)

    concept = owned.get(label.lower())
    changed = False
    if concept is None:
        taken = {c.get("id") for c in concepts}
        cid, n = slug_for_label(label), 2
        while cid in taken:
            cid, n = "%s-%d" % (slug_for_label(label), n), n + 1
        concept = {"id": cid, "prefLabel": label, "kind": "concept",
                   "definition": (definition or "").strip() or None,
                   "altLabels": [], "broader": None, "governs": [], "source": "learned"}
        concepts.append(concept)
        owned[label.lower()] = concept
        changed = True
    elif definition and not concept.get("definition"):
        concept["definition"] = definition.strip()
        changed = True

    parent = owned.get((broader or "").strip().lower())
    if parent is not None and concept.get("broader") != parent.get("id")             and not _descends_from(parent, concept, concepts):
        concept["broader"] = parent.get("id")
        changed = True

    alts = concept.setdefault("altLabels", [])
    for form in forms:
        low = form.strip().lower()
        if not low or owned.get(low) not in (None, concept):
            continue
        if low != concept["prefLabel"].lower() and all(a.lower() != low for a in alts):
            alts.append(form.strip())
            owned[low] = concept
            changed = True
    if changed:
        data.setdefault("alt_labels", {})[concept["prefLabel"]] = list(alts)
    return changed


def _descends_from(node: Dict[str, Any], ancestor: Dict[str, Any],
                   concepts: List[Dict[str, Any]]) -> bool:
    """Whether `ancestor` is `node` or sits above it in the tree."""
    by_id = {c.get("id"): c for c in concepts}
    seen = set()
    while node is not None and node.get("id") not in seen:
        if node is ancestor:
            return True
        seen.add(node.get("id"))
        node = by_id.get(node.get("broader"))
    return False


class VocabularyStore:
    """Observations and decisions in the archive; the vertical profile on disk and mirror."""

    def __init__(self, archive=None, registry_store=None):
        self._registry_store = registry_store or default_store()
        self._archive = archive
        self._cache: Dict[str, Tuple[float, List[Dict[str, Any]]]] = {}
        self._lock = threading.Lock()

    def archive(self):
        return self._archive or self._registry_store.archive()

    def _load(self, prefix: str) -> List[Dict[str, Any]]:
        with self._lock:
            hit = self._cache.get(prefix)
            if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
                return hit[1]
        store, items = self.archive(), []
        for key in sorted(store.list(prefix + "/")):
            try:
                payload = store.get(key)
                if payload:
                    items.append(json.loads(payload.decode("utf-8")))
            except Exception as err:
                logger.warning("[Vocabulary] Skipping unreadable %s: %s", key, err)
        with self._lock:
            self._cache[prefix] = (time.monotonic(), items)
        return items

    def observations(self) -> List[Dict[str, Any]]:
        return self._load(OBSERVATIONS_PREFIX)

    def decisions(self) -> List[Dict[str, Any]]:
        return self._load(DECISIONS_PREFIX)

    def proposals(self, vertical_id: str) -> Dict[str, Any]:
        return build_proposals(self.observations(), vertical_id, self.decisions(),
                               seed_terms=_seed_terms(vertical_id))

    def decide(self, vertical_id: str, key: str, decision: str, label: Optional[str] = None,
               kind: Optional[str] = None, definition: Optional[str] = None,
               entity_kind: str = "organization", broader: Optional[str] = None) -> Dict[str, Any]:
        """Record a reviewer's decision on a proposal and apply it. Raises ValueError for a
        decision that cannot be made: an unknown key, verdict or entity kind."""
        from security import is_valid_vertical_id
        if not is_valid_vertical_id(vertical_id):
            raise ValueError("Invalid vertical id %r." % vertical_id)
        if decision not in DECISIONS:
            raise ValueError("decision must be one of %s" % ", ".join(DECISIONS))
        current = {p["key"]: p for p in self.proposals(vertical_id)["proposals"]}
        proposal = current.get(key)
        if proposal is None:
            raise ValueError("No open proposal %r for %s." % (key, vertical_id))
        kind = kind or proposal["kind"]
        if kind not in ("concept", "name"):
            raise ValueError("kind must be concept or name")
        if kind == "name" and decision == "approve" and entity_kind not in KINDS:
            raise ValueError("entity_kind must be one of %s" % ", ".join(KINDS))

        event = make_event("vocabulary_decision", ACTOR_REVIEWER, vertical_id=vertical_id,
                           key=key, decision=decision, kind=kind,
                           label=(label or proposal["label"]).strip(),
                           forms=proposal["forms"], definition=(definition or "").strip() or None,
                           broader=(broader or "").strip() or None, sites=proposal["sites"])
        self.archive().put("%s/%s.json" % (DECISIONS_PREFIX, event["event_id"]),
                           json.dumps(event, ensure_ascii=False).encode("utf-8"))
        with self._lock:
            self._cache.pop(DECISIONS_PREFIX, None)

        applied = None
        if decision == "approve" and kind == "concept":
            applied = self._apply_concepts(vertical_id)
        elif decision == "approve":
            applied = self._create_entity(event, entity_kind)
        return {"decision": event, "applied": applied}

    def _apply_concepts(self, vertical_id: str) -> Dict[str, Any]:
        """Write every approved concept of the vertical into its profile and mirror it.

        All of them, not only the newest: the mirror is last-writer-wins, and a concept a
        concurrent write dropped comes back on the next approval.
        """
        import vertical_store
        from security import verticals_dir

        vertical_store.sync_down(force=True)
        path = os.path.join(verticals_dir(), "%s.json" % vertical_id)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        latest, _ = fold_decisions(self.decisions())
        approved = [ev for (vid, _key), ev in latest.items()
                    if vid == vertical_id and ev.get("decision") == "approve" and ev.get("kind") == "concept"]
        changed: List[str] = []
        # Twice: a child approved before its parent names a concept the first pass has not
        # written yet. The second pass finds it; nothing else changes on it.
        for _ in range(2):
            for ev in approved:
                if add_concept(data, ev["label"], ev.get("forms") or [], ev.get("definition"),
                               ev.get("broader")) and ev["label"] not in changed:
                    changed.append(ev["label"])
        if changed:
            tmp = path + ".partial"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, path)
        mirrored = vertical_store.publish(vertical_id, data) if changed else None
        return {"concepts_written": changed, "mirrored_to": mirrored}

    def _create_entity(self, event: Dict[str, Any], entity_kind: str) -> Dict[str, Any]:
        registry = self._registry_store.registry()
        label = event["label"]
        eid, n = slug_for_label(label), 2
        while eid in registry.entities:
            eid, n = "%s-%d" % (slug_for_label(label), n), n + 1
        aliases = [{"form": f, "source": ACTOR_REVIEWER} for f in event["forms"]
                   if f.strip().lower() != label.lower()]
        self._registry_store.append(make_event(
            "entity_created", ACTOR_REVIEWER,
            entity={"id": eid, "kind": entity_kind, "prefLabel": label,
                    "definition": event.get("definition") or "", "aliases": aliases}))
        return {"entity_created": eid}


def _seed_terms(vertical_id: str) -> List[str]:
    """The vertical's seed strings, or none when its profile cannot be read - the
    proposals are still right without them, only less annotated."""
    try:
        from industry_ontology import load_industry_ontology
        return list(load_industry_ontology(vertical_id).core_seed_concepts or [])
    except Exception as err:
        logger.warning("[Vocabulary] No seed terms for %s: %s", vertical_id, err)
        return []


_default: Optional[VocabularyStore] = None
_default_lock = threading.Lock()


def default_vocabulary() -> VocabularyStore:
    global _default
    with _default_lock:
        if _default is None:
            _default = VocabularyStore()
        return _default
