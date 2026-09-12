"""
industry_ontology.py — Industry Reference Ontology & Semantic Alignment Engine

Manages standard vertical reference models and aligns Page/Site Knowledge Graphs
against industry taxonomies:
1. Loads SKOS / OWL vertical reference models from `verticals/*.json`.
2. Computes Covered Concepts: Domain capabilities grounded in industry standards.
3. Computes Category Whitespace: Industry expectations that are unclaimed in the graph.
4. Identifies Proprietary Concepts: Unique company innovations outside the standard taxonomy.
5. Evaluates standards compliance coverage and integration density.
"""

import os
import re
import json
import logging
from typing import Optional, List, Dict, Union, Any

from security import verticals_dir
from models import (
    IndustryOntologyModel, IndustryConcept, GraphAlignmentResult,
    PageKnowledgeGraph, SiteKnowledgeGraph
)

logger = logging.getLogger("gainark.industry_ontology")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def _verticals_dir() -> str:
    """Where vertical profiles live, resolved per call.

    This module used to hardcode BASE_DIR/verticals while the writer used
    `security.verticals_dir()`, which honours ONTOLEAP_VERTICALS_DIR. With the override
    set - which is how the test suites keep discovery away from the curated profiles -
    discovery wrote to one directory and every reader here looked in another, so a freshly
    discovered vertical was invisible to routing. Matching a site to an existing vertical
    reads this list, so the two have to agree.
    """
    return verticals_dir()


# A concept is written differently depending on who is writing: a vendor page says
# "EDR" where the ontology says "Endpoint Detection and Response". Matching only the
# prefLabel meant a page using the industry's own abbreviations scored as if it had
# never mentioned the capability, so altLabels are matched here too.
#
# Forms shorter than this are matched on word boundaries rather than as raw
# substrings. "IR" is inside "firewall" and "CTI" is inside "detection", so a plain
# substring test would turn every short abbreviation into a false positive.
_MIN_SUBSTRING_FORM = 6


def _alias_index(industry: IndustryOntologyModel) -> Dict[str, List[str]]:
    """Map each concept's canonical label to every lowercase surface form it may appear as.

    Longest form first, so a caller that stops at the first hit reads the most specific.
    """
    index: Dict[str, List[str]] = {}
    for c in industry.concepts:
        if not c.pref_label:
            continue
        forms = {c.pref_label}
        forms.update(a for a in (c.alt_labels or []) if a)
        index[c.pref_label] = sorted(
            {f.strip().lower() for f in forms if f and f.strip()},
            key=lambda f: (-len(f), f),
        )
    return index


def _surface_forms(label: str, index: Dict[str, List[str]]) -> List[str]:
    """Every form `label` may be written as. Falls back to the label itself."""
    return index.get(label) or [label.strip().lower()]


def _canonical_matches(label_lower: str, graph_terms: set, allow_reverse: bool) -> bool:
    """Match a canonical label exactly as this module always has.

    `allow_reverse` preserves the difference between the call sites: concept coverage
    tested containment both ways, while compliance and integrations tested only whether
    the known name appears inside a graph term. Widening the latter lets a graph term
    claim any longer name it is a substring of - "Sentinel" matching "SentinelOne".
    """
    if label_lower in graph_terms:
        return True
    if any(label_lower in term for term in graph_terms):
        return True
    if not allow_reverse:
        return False
    # The reverse reading - a graph term sitting inside the concept label - is a raw
    # substring test, which for a short term is almost always an accident: "EDR" is
    # inside "FedRAMP", "CTI" inside "Data Protection", "BAS" inside "Usage-Based
    # Pricing". Requiring a word boundary keeps the readings that are real, such as
    # "API" inside "REST API".
    for term in graph_terms:
        if not term:
            continue
        if len(term) >= _MIN_SUBSTRING_FORM:
            if term in label_lower:
                return True
        elif re.search(r"\b" + re.escape(term) + r"\b", label_lower):
            return True
    return False


def _alias_matches(form: str, graph_terms: set) -> bool:
    """Match one alternate label. Forward-only, so an alias can never widen a call site.

    Short forms are matched on word boundaries: "IR" is inside "firewall" and "CTI" is
    inside "detection", so a raw substring test would make every abbreviation a hit.
    """
    if not form:
        return False
    if form in graph_terms:
        return True
    if len(form) >= _MIN_SUBSTRING_FORM:
        return any(form in term for term in graph_terms)
    pattern = re.compile(r"\b" + re.escape(form) + r"\b")
    return any(pattern.search(term) for term in graph_terms)


def _label_matches(
    label: str,
    index: Dict[str, List[str]],
    graph_terms: set,
    allow_reverse: bool = True,
) -> bool:
    """Whether the graph claims this concept under its own name or any alternate label."""
    canonical = label.strip().lower()
    if _canonical_matches(canonical, graph_terms, allow_reverse):
        return True
    return any(
        _alias_matches(form, graph_terms)
        for form in _surface_forms(label, index)
        if form != canonical
    )


def list_available_industries(include_unusable: bool = False) -> List[Dict[str, Any]]:
    """Available industry ontologies, with the size of each concept layer.

    A vertical with no concepts cannot measure coverage - align scores against its
    concepts, so a site routed there is told every concept is a gap. Those are hidden
    unless asked for, because offering one in a picker is offering a wrong answer.
    """
    industries = []
    if not os.path.isdir(_verticals_dir()):
        return industries

    for f in sorted(os.listdir(_verticals_dir())):
        if f.endswith(".json"):
            vid = f[:-5]
            fpath = os.path.join(_verticals_dir(), f)
            try:
                with open(fpath, "r", encoding="utf-8") as jf:
                    data = json.load(jf)
                    name = data.get("display_name", vid.replace("_", " ").title())
                    concepts = len(data.get("concepts") or [])
                    industries.append({"vertical_id": vid, "display_name": name,
                                       "concepts": concepts, "usable": concepts > 0})
            except Exception:
                industries.append({"vertical_id": vid, "display_name": vid.replace("_", " ").title(),
                                   "concepts": 0, "usable": False})

    if not include_unusable:
        industries = [i for i in industries if i["usable"]]
    return industries


def load_industry_ontology(vertical_id: str = "b2b_saas_fintech") -> IndustryOntologyModel:
    """
    Load an Industry Reference Ontology from the verticals definition files.
    """
    fpath = os.path.join(_verticals_dir(), f"{vertical_id}.json")
    if not os.path.isfile(fpath):
        # Substituting billing here defeated every check above it: a typo in a vertical
        # id, or a vertical that was removed, quietly returned the billing ontology and
        # the caller was told nothing. Coverage and whitespace were then computed against
        # a vocabulary nobody asked for.
        available = sorted(
            f[:-5] for f in os.listdir(_verticals_dir()) if f.endswith(".json")
        ) if os.path.isdir(_verticals_dir()) else []
        raise ValueError(
            "No industry ontology named %r. Available: %s"
            % (vertical_id, ", ".join(available) or "none"))

    with open(fpath, "r", encoding="utf-8") as f:
        raw = json.load(f)

    # Parse SKOS concepts
    concepts_list: List[IndustryConcept] = []
    for c in raw.get("concepts", []):
        concepts_list.append(IndustryConcept(
            id=c.get("id", ""),
            pref_label=c.get("prefLabel", ""),
            kind=c.get("kind", "concept"),
            definition=c.get("definition"),
            alt_labels=c.get("altLabels", []),
            broader=c.get("broader")
        ))

    return IndustryOntologyModel(
        vertical_id=raw.get("vertical_id", vertical_id),
        display_name=raw.get("display_name", vertical_id.replace("_", " ").title()),
        classes=raw.get("gliner_labels", []),
        core_seed_concepts=raw.get("core_seed_concepts", []),
        concepts=concepts_list,
        known_integrations=raw.get("known_integrations", []),
        known_compliance=raw.get("known_compliance", []),
        known_segments=raw.get("known_segments", []),
        known_industries=raw.get("known_industries", []),
        known_competitors=raw.get("known_competitors", []),
        known_replaces=raw.get("known_replaces", []),
        icp_evidence=raw.get("icp_evidence", {}),
        standard_predicates=raw.get("standard_predicates", [
            "automates", "integratesWith", "compliesWith", "supportsPricingModel", "subClassOf", "partOf"
        ])
    )


# ---------------------------------------------------------------------------
# Routing a site to a vertical
# ---------------------------------------------------------------------------
# A vertical with no concept layer cannot measure anything: coverage is computed against
# its concepts, so routing a site to one scores it near zero and reports every concept as
# whitespace. Those profiles exist because discovery mints a vertical per site instead of
# matching an existing one, so they are excluded here rather than trusted.

# A site must match at least this many distinct vocabulary terms to be routed at all.
_MIN_ROUTING_EVIDENCE = 5
# ...and the winner must beat the runner-up by this factor, or the answer is ambiguous.
_MIN_ROUTING_MARGIN = 1.5


def usable_verticals() -> List[str]:
    """Vertical ids that carry a concept layer, and so can actually measure coverage."""
    usable = []
    for meta in list_available_industries(include_unusable=True):
        vid = meta.get("vertical_id")
        if not vid:
            continue
        try:
            if load_industry_ontology(vid).concepts:
                usable.append(vid)
        except Exception as err:
            logger.warning("Skipping unreadable vertical %r: %s", vid, err)
    return usable


def _vocabulary_terms(industry: IndustryOntologyModel) -> List[str]:
    """Every phrase that is evidence for this vertical, longest first."""
    terms = set(industry.core_seed_concepts) | set(industry.known_compliance)
    terms |= set(industry.known_integrations)
    for c in industry.concepts:
        if c.pref_label:
            terms.add(c.pref_label)
        terms.update(a for a in (c.alt_labels or []) if a)
    cleaned = {t.strip().lower() for t in terms if t and len(t.strip()) >= 3}
    return sorted(cleaned, key=lambda t: (-len(t), t))


def _count_evidence(text_lower: str, terms: List[str]) -> List[str]:
    """Distinct vocabulary terms the text mentions.

    Word boundaries, not raw substrings: the same lesson as the alignment matcher, where
    "IR" sat inside "firewall" and "CTI" inside "detection". A term counts once however
    often it appears, so a page repeating one word cannot outvote a page covering many.
    """
    hits = []
    for term in terms:
        if re.search(r"\b" + re.escape(term) + r"\b", text_lower):
            hits.append(term)
    return hits


def classify_vertical(text: str, candidates: Optional[List[str]] = None) -> Dict[str, Any]:
    """Pick the existing vertical whose vocabulary the text best matches.

    Returns the choice, the evidence behind it, and every candidate's score, so a caller
    can show why a site was routed the way it was. `vertical_id` is None when nothing
    matched well enough - an honest refusal, because the alternative is measuring a site
    against a vocabulary that does not describe it.
    """
    text_lower = (text or "").lower()
    scores = []
    for vid in (candidates if candidates is not None else usable_verticals()):
        try:
            industry = load_industry_ontology(vid)
        except Exception as err:
            logger.warning("Skipping vertical %r during routing: %s", vid, err)
            continue
        hits = _count_evidence(text_lower, _vocabulary_terms(industry))
        scores.append({
            "vertical_id": vid,
            "display_name": industry.display_name,
            "matched": len(hits),
            "evidence": hits[:12],
        })

    scores.sort(key=lambda r: r["matched"], reverse=True)
    if not scores:
        return {"vertical_id": None, "reason": "No vertical carries a concept layer.",
                "candidates": []}

    best = scores[0]
    runner_up = scores[1]["matched"] if len(scores) > 1 else 0

    if best["matched"] < _MIN_ROUTING_EVIDENCE:
        reason = ("Best match %s found only %d vocabulary terms; %d are needed to route."
                  % (best["vertical_id"], best["matched"], _MIN_ROUTING_EVIDENCE))
        return {"vertical_id": None, "reason": reason, "candidates": scores}

    if runner_up and best["matched"] < runner_up * _MIN_ROUTING_MARGIN:
        reason = ("Ambiguous: %s matched %d and %s matched %d, too close to choose."
                  % (best["vertical_id"], best["matched"],
                     scores[1]["vertical_id"], scores[1]["matched"]))
        return {"vertical_id": None, "reason": reason, "candidates": scores}

    return {
        "vertical_id": best["vertical_id"],
        "display_name": best["display_name"],
        "matched": best["matched"],
        "evidence": best["evidence"],
        "reason": "Matched %d vocabulary terms, %.1fx the runner-up."
                  % (best["matched"], (best["matched"] / runner_up) if runner_up else float(best["matched"])),
        "candidates": scores,
    }


def match_existing_vertical(text: str) -> Dict[str, Any]:
    """Find the vertical a newly discovered site belongs to, if one already exists.

    Deliberately a different question from `classify_vertical`, and scored against a
    different candidate set. Routing asks "which vocabulary may I measure this site
    against", so it considers only verticals carrying a concept layer and refuses when the
    answer is ambiguous - measuring against the wrong vocabulary is worse than not
    measuring. This asks "does this category already have a home", and the cost of
    refusing is a duplicate: six discovery runs over chargebee.com minted six verticals,
    and four separate profiles exist for AI security whose seed concepts agree on as
    little as nothing out of twelve.

    So every vertical is a candidate here, concept layer or not, and ambiguity resolves
    rather than refuses: between two verticals that both describe the category, the one
    carrying concepts is the one worth deepening.
    """
    candidates = [meta.get("vertical_id") for meta in list_available_industries(include_unusable=True)]
    candidates = [vid for vid in candidates if vid]
    result = classify_vertical(text, candidates=candidates)

    scores = result.get("candidates") or []
    if result.get("vertical_id"):
        return {
            "vertical_id": result["vertical_id"],
            "reason": result.get("reason", ""),
            "decision": "matched",
            "candidates": scores,
        }

    # classify_vertical refused. Refusing here mints a duplicate, so try to resolve.
    viable = [row for row in scores if row["matched"] >= _MIN_ROUTING_EVIDENCE]
    if not viable:
        return {
            "vertical_id": None,
            "reason": result.get("reason", "Nothing matched well enough."),
            "decision": "no-match",
            "candidates": scores,
        }

    def _preference(row: Dict[str, Any]) -> Any:
        try:
            has_concepts = 1 if load_industry_ontology(row["vertical_id"]).concepts else 0
        except Exception:
            has_concepts = 0
        # Concepts first, then evidence, then the id, so the choice is reproducible.
        return (has_concepts, row["matched"], row["vertical_id"])

    best = max(viable, key=_preference)
    return {
        "vertical_id": best["vertical_id"],
        "reason": ("Ambiguous by routing rules, resolved to %s (%d vocabulary terms); "
                   "a new vertical here would duplicate an existing category."
                   % (best["vertical_id"], best["matched"])),
        "decision": "resolved-ambiguous",
        "candidates": scores,
    }


def align_graph_with_industry(
    kg: Union[PageKnowledgeGraph, SiteKnowledgeGraph],
    industry: Optional[IndustryOntologyModel] = None,
    vertical_id: Optional[str] = None
) -> GraphAlignmentResult:
    """
    Aligns a PageKnowledgeGraph or SiteKnowledgeGraph against an Industry Reference Ontology.
    """
    if industry is None:
        # A graph records the vocabulary it was built with, and that beats a constant.
        # Scoring a security site's graph against billing is not a smaller error than
        # extracting it wrong - it is the same error, one step later.
        ind_id = vertical_id or getattr(kg, "vertical_id", None)
        if not ind_id:
            ind_id = "b2b_saas_fintech"
            logger.warning(
                "Aligning a graph that names no vertical; defaulting to %s. Coverage and "
                "whitespace below are measured against that vocabulary.", ind_id)
        industry = load_industry_ontology(ind_id)

    # Identify subject
    subject_id = getattr(kg, "domain", None) or getattr(kg, "url", "Subject")

    # Build lookup of concepts and aliases present in the Knowledge Graph
    graph_terms = set()

    for node in kg.nodes:
        graph_terms.add(node.canonical_name.strip().lower())
        for alias in node.aliases:
            graph_terms.add(alias.strip().lower())

    for edge in kg.edges:
        graph_terms.add(edge.target.strip().lower())
        graph_terms.add(edge.source.strip().lower())

    alias_index = _alias_index(industry)

    # 1. Covered Concepts
    covered_concepts = []
    category_whitespace = []

    # Check industry concepts hierarchy
    all_industry_concepts = list(industry.core_seed_concepts)
    for c in industry.concepts:
        if c.pref_label not in all_industry_concepts:
            all_industry_concepts.append(c.pref_label)

    for item in all_industry_concepts:
        matched = _label_matches(item, alias_index, graph_terms)

        if matched:
            covered_concepts.append(item)
        else:
            category_whitespace.append(item)

    # 2. Compliance standards covered
    compliance_covered = []
    for std in industry.known_compliance:
        if _label_matches(std, alias_index, graph_terms, allow_reverse=False):
            compliance_covered.append(std)

    # 3. Integrations covered
    integrations_covered = []
    for int_p in industry.known_integrations:
        if _label_matches(int_p, alias_index, graph_terms, allow_reverse=False):
            integrations_covered.append(int_p)

    # 4. Proprietary Concepts (nodes in graph not found in industry standard list)
    industry_lower_set = set()
    for known in all_industry_concepts + industry.known_compliance + industry.known_integrations:
        industry_lower_set.update(_surface_forms(known, alias_index))
    proprietary = []
    for node in kg.nodes:
        name_lower = node.canonical_name.lower()
        if not any(name_lower in std or std in name_lower for std in industry_lower_set):
            if node.canonical_name not in proprietary and len(node.canonical_name) > 3:
                proprietary.append(node.canonical_name)

    # Coverage is the share of the reference ontology the graph actually claims, so it
    # agrees with the covered/whitespace lists returned beside it. Scoring the ten seed
    # concepts alone ignored the concept layer: a graph covering eight concepts scored
    # 0% when none of them were seeds, and every score was a multiple of ten.
    total_concepts = max(len(all_industry_concepts), 1)
    coverage_score = round(min((len(covered_concepts) / total_concepts) * 100.0, 100.0), 1)

    # The seed list is a deliberate statement of the few concepts that define a category,
    # which a whole-ontology percentage cannot express. Kept as its own reading.
    total_seeds = max(len(industry.core_seed_concepts), 1)
    seed_hits = sum(1 for s in industry.core_seed_concepts
                    if _label_matches(s, alias_index, graph_terms, allow_reverse=False))
    seed_coverage_score = round(min((seed_hits / total_seeds) * 100.0, 100.0), 1)

    # What this was measured on. A page graph is one page by definition; a site graph
    # carries its own crawl counts.
    pages_sampled = getattr(kg, "pages_crawled", None)
    pages_found = getattr(kg, "pages_discovered", None) or 0
    if pages_sampled is None:
        pages_sampled = 1
        caveat = ("Measured on a single page. A concept covered elsewhere on the site is "
                  "listed as unclaimed here.")
    elif pages_found > pages_sampled:
        caveat = ("Measured on %d of %d pages found. A concept on a page that was not read "
                  "is listed as unclaimed." % (pages_sampled, pages_found))
    else:
        caveat = None

    return GraphAlignmentResult(
        subject_identifier=subject_id,
        vertical_id=industry.vertical_id,
        industry_name=industry.display_name,
        total_industry_concepts=len(all_industry_concepts),
        covered_concepts=covered_concepts,
        category_whitespace=category_whitespace,
        proprietary_concepts=proprietary[:20],
        coverage_score=coverage_score,
        seed_coverage_score=seed_coverage_score,
        compliance_standards_covered=compliance_covered,
        integrations_covered=integrations_covered,
        pages_sampled=pages_sampled,
        pages_found=pages_found,
        whitespace_caveat=caveat
    )
