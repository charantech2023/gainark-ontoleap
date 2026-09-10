"""
GainARK OntoLeap - Sector Ontology / SKOS Synonym Layer
========================================================
Generates an LLM-powered synonym map that bridges marketing language to technical
documentation language. This is the core fix for "capability drift" alerts that fire
when a marketing claim uses buyer vocabulary ("Order-to-Revenue Cycle") but the tech
docs use API vocabulary ("order management", "revenue lifecycle").

This module PROPOSES; it no longer decides. The vertical's curated alt_labels are
authoritative, and generated pairs reach the ontology only once a person approves them.

Why it was demoted
------------------
Generation and curation were both mapping surface forms to concepts, by opposite
methods. alt_labels normalises: every spelling, marketing or technical, resolves to one
canonical. expand_tech_triples() multiplied instead, cloning each technical capability
into a marketing-worded copy so the matcher would find it.

Running both was worse than either. Measured against the two Ordway caches, 1 of 114
generated pairs agreed with the curated mapping. The disagreements were substantive:
"automated tax calculation" was generated as a synonym of "avalara", which would let
evidence about an integration partner verify a claim about a tax capability, and
"failed payment recovery" was mapped to "manual dunning". The clones also inflated the
technical capability count, because one real capability became several.

Architecture
------------
1. build_synonym_map()      — Gemini call at analysis time; JSON-cached per brand.
2. propose_alt_labels()     — files generated pairs as candidates for review, skipping
                              any the curated ontology already places and recording
                              which ones it contradicts.
3. record_reviewer_synonym()— approval path: writes the surface form onto the concept's
                              altLabels in the vertical itself, where extraction reads
                              it and always resolves back to the canonical.

Self-Building Ontology
----------------------
Unchanged in intent, corrected in destination. A reviewer dismissing an alert as a
false positive still teaches the system a synonym - but the judgement now lands in the
ontology rather than in a per-brand cache that only the generated path ever read.
"""

import os
import re
import json
import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger("gainark.sector_ontology")

# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _cache_path(brand: str, cache_dir: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "_", brand.lower())[:32]
    return os.path.join(cache_dir, f"synonym_cache_{slug}.json")


def _load_cache(cache_file: str) -> Optional[Dict[str, List[str]]]:
    try:
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.debug("Synonym cache read error: %s", e)
    return None


def _save_cache(cache_file: str, synonym_map: Dict[str, List[str]]) -> None:
    try:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(synonym_map, f, indent=2)
    except Exception as e:
        logger.warning("Synonym cache write error: %s", e)


# ---------------------------------------------------------------------------
# Gemini synonym generation
# ---------------------------------------------------------------------------

_SYSTEM_INSTRUCTION = (
    "You are a B2B SaaS ontology engineer. Output ONLY valid JSON — no markdown fences, "
    "no explanation, no trailing text."
)

_PROMPT_TEMPLATE = """You are building a SKOS synonym map for a B2B software product called "{brand}".

The product's extracted technical capabilities (short feature/object labels from the API spec and docs) are:
{tech_objects}

Additional documentation context (first 3000 chars):
---
{tech_excerpt}
---

Generate a JSON synonym map where:
- Keys are SHORT CANONICAL TERMS — single concepts, 1–4 words max, that appear in the
  capability list above (e.g. "revenue recognition", "dunning management", "netsuite", "invoicing").
  Each key must be a substring of one of the capability labels above.
- Values are lists of MARKETING / BUYER synonyms that a buyer might use for the same concept
  (e.g. "Order-to-Revenue Cycle", "revenue lifecycle management", "failed payment recovery",
  "smart dunning", "automated collections").

Rules:
1. Keys MUST be short enough to appear as a substring inside the capability labels — test this.
2. Each synonym list should have 2–5 buyer-language phrasings.
3. Focus on billing, revenue, compliance, integration, and automation terms.
4. Do NOT include generic filler like "software", "platform", "solution".
5. Output 15–30 pairs.

Output format (JSON only):
{{
  "short_canonical_term": ["marketing synonym 1", "marketing synonym 2"],
  ...
}}"""


def build_synonym_map(
    tech_text: str,
    brand: str,
    cache_dir: Optional[str] = None,
    force_refresh: bool = False,
    tech_triple_objects: Optional[List[str]] = None
) -> Dict[str, List[str]]:
    """
    Returns a synonym map {canonical_tech_term: [marketing_synonym, ...]} for the given
    product. Checks a JSON cache first; calls Gemini on a cache miss.

    Parameters
    ----------
    tech_text   : Raw text scraped from technical documentation (will be truncated).
    brand       : Brand/product name (used for cache key and prompt).
    cache_dir   : Directory for the JSON cache. Defaults to the directory of this file.
    force_refresh: Bypass the cache and regenerate.
    """
    if cache_dir is None:
        cache_dir = os.path.dirname(os.path.abspath(__file__))

    cache_file = _cache_path(brand, cache_dir)

    if not force_refresh:
        cached = _load_cache(cache_file)
        if cached:
            logger.info("Synonym cache hit for '%s' (%d pairs).", brand, len(cached))
            return cached

    # No cache — call Gemini
    try:
        import vertex_ai_client  # local module in same package
    except ImportError:
        logger.warning("vertex_ai_client not available; synonym map will be empty.")
        return {}

    if not vertex_ai_client.is_available():
        logger.warning("Gemini API key not configured; synonym map will be empty.")
        return {}

    tech_excerpt = (tech_text or "")[:6000].strip()
    if len(tech_excerpt) < 200:
        logger.info("Tech text too short for meaningful synonym generation; skipping.")
        return {}

    objects_list = "\n".join(f"  - {o}" for o in (tech_triple_objects or [])[:40])
    if not objects_list:
        objects_list = "(extracted from documentation context below)"
    prompt = _PROMPT_TEMPLATE.format(brand=brand, tech_excerpt=tech_excerpt[:3000], tech_objects=objects_list)

    logger.info("Calling Gemini to generate synonym map for '%s'...", brand)
    raw = vertex_ai_client._call_gemini(
        prompt=prompt,
        system_instruction=_SYSTEM_INSTRUCTION,
        temperature=0.1,
        max_output_tokens=2048,
        thinking_budget=0,
        timeout=25
    )

    if not raw:
        logger.warning("Gemini returned empty response for synonym map; using empty map.")
        return {}

    # Strip markdown fences if model disobeyed
    cleaned = re.sub(r"```[a-z]*\n?", "", raw).strip().rstrip("`").strip()

    # Extract first JSON object
    m = re.search(r"\{[\s\S]+\}", cleaned)
    if not m:
        logger.warning("No JSON object found in Gemini synonym response.")
        return {}

    try:
        synonym_map: Dict[str, List[str]] = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        logger.warning("JSON parse error for synonym map: %s", e)
        return {}

    # Normalise: ensure all values are lists of non-empty strings
    clean_map: Dict[str, List[str]] = {}
    for key, vals in synonym_map.items():
        if not isinstance(key, str) or not key.strip():
            continue
        if isinstance(vals, list):
            synonyms = [v.strip() for v in vals if isinstance(v, str) and v.strip()]
        elif isinstance(vals, str):
            synonyms = [vals.strip()] if vals.strip() else []
        else:
            continue
        if synonyms:
            clean_map[key.strip().lower()] = synonyms  # key normalised to lowercase

    _save_cache(cache_file, clean_map)
    logger.info("Synonym map generated for '%s': %d canonical terms.", brand, len(clean_map))
    return clean_map


# ---------------------------------------------------------------------------
# Self-building ontology: reviewer feedback loop
# ---------------------------------------------------------------------------

def record_reviewer_synonym(
    marketing_term: str,
    canonical_label: str,
    vertical_path: str,
    queue_path: Optional[str] = None
) -> bool:
    """Approve a surface form into the vertical's curated alt_labels.

    Called when a reviewer decides a marketing phrase does mean an existing concept -
    typically dismissing a drift alert as a false positive.

    It used to append to a per-brand JSON cache that only the generated-synonym path
    read, so a human decision landed in the weaker of the two systems and never reached
    the ontology. The judgement now goes where the ontology actually lives: onto the
    concept's altLabels, which extraction already matches and always resolves back to
    the canonical.

    Returns True when the vertical was changed.
    """
    surface = (marketing_term or "").strip()
    target = (canonical_label or "").strip()
    if not surface or not target:
        return False

    with open(vertical_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    concepts = data.get("concepts") or []
    concept = next((c for c in concepts
                    if c.get("prefLabel", "").strip().lower() == target.lower()), None)
    if concept is None:
        logger.warning("Cannot record synonym: %r is not a concept in %s",
                       target, os.path.basename(vertical_path))
        return False

    # Never let one surface form mean two concepts - the emitted canonical would then
    # depend on iteration order.
    for other in concepts:
        if other is concept:
            continue
        if any(a.strip().lower() == surface.lower() for a in other.get("altLabels") or []):
            logger.warning("Cannot record synonym: %r is already an alternate of %r",
                           surface, other.get("prefLabel"))
            return False

    alts = concept.setdefault("altLabels", [])
    if any(a.strip().lower() == surface.lower() for a in alts):
        return False
    alts.append(surface)
    data.setdefault("alt_labels", {})[concept["prefLabel"]] = list(alts)

    with open(vertical_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    queue_path = queue_path or _candidates_path()
    queue = _load_candidates(queue_path)
    changed = False
    for key, entry in queue.items():
        if entry.get("surface_form", "").strip().lower() == surface.lower():
            entry["status"] = "approved"
            entry["approved_as"] = concept["prefLabel"]
            changed = True
    if changed:
        _save_candidates(queue_path, queue)

    logger.info("Recorded alternate %r -> %r in %s",
                surface, concept["prefLabel"], os.path.basename(vertical_path))
    return True


# ---------------------------------------------------------------------------
# Triple expansion
# ---------------------------------------------------------------------------

def propose_alt_labels(
    synonym_map: Dict[str, List[str]],
    config: Any,
    brand: str,
    queue_path: Optional[str] = None
) -> Dict[str, int]:
    """Turn a generated synonym map into candidate alt_labels awaiting human approval.

    This replaces expand_tech_triples(), which injected the generated synonyms directly
    into the technical triple list as clone-triples. Two systems were then claiming
    authority over the same question, by opposite methods: the curated alt_labels in
    the vertical converge on ONE canonical by normalising every surface form, while the
    clones converged by multiplying each technical capability into a marketing-worded
    copy of itself.

    They also disagreed. Measured against the Ordway caches, exactly 1 of 114 generated
    pairs agreed with the curated mapping, and the disagreements were not cosmetic:
    "automated tax calculation" was generated as a synonym of "avalara", so evidence
    about an integration partner would have verified a marketing claim about a tax
    capability. "failed payment recovery" was mapped to "manual dunning".

    So generation is demoted to proposing. Curated alt_labels stay authoritative,
    nothing reaches the graph unreviewed, and the remaining generated pairs - most of
    which the ontology has never seen - are written somewhere a person can approve or
    reject them. Approving one calls record_reviewer_synonym(), which writes it into
    the vertical itself.

    Returns counts of what happened to each proposed pair.
    """
    if not synonym_map:
        return {"proposed": 0, "already_known": 0, "conflicting": 0}

    known: Dict[str, str] = {}
    for canonical, alts in (getattr(config, "alt_labels", None) or {}).items():
        known[canonical.strip().lower()] = canonical
        for alt in alts:
            known[alt.strip().lower()] = canonical

    queue_path = queue_path or _candidates_path()
    queue = _load_candidates(queue_path)
    stats = {"proposed": 0, "already_known": 0, "conflicting": 0}

    for generated_canonical, synonyms in synonym_map.items():
        for synonym in synonyms:
            key = synonym.strip().lower()
            if not key:
                continue
            owner = known.get(key)
            if owner is not None:
                # The curated ontology already places this surface form. If it places it
                # somewhere else, that is exactly the disagreement worth recording - but
                # the curated answer stands.
                stats["conflicting" if owner.strip().lower()
                      != generated_canonical.strip().lower() else "already_known"] += 1
                continue
            entry_key = "%s|%s" % (generated_canonical.strip().lower(), key)
            if entry_key in queue:
                continue
            queue[entry_key] = {
                "surface_form": synonym.strip(),
                "generated_canonical": generated_canonical.strip(),
                "brand": brand,
                "status": "pending",
            }
            stats["proposed"] += 1

    if stats["proposed"]:
        _save_candidates(queue_path, queue)
    logger.info(
        "Synonym proposals for %s: %d new candidates, %d already known, %d conflict "
        "with curated alt_labels (curated wins).",
        brand, stats["proposed"], stats["already_known"], stats["conflicting"])
    return stats


def _candidates_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "truth_ledger", "alt_label_candidates.json")


def _load_candidates(path: str) -> Dict[str, Any]:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        logger.debug("Candidate queue unreadable: %s", e)
    return {}


def _save_candidates(path: str, queue: Dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(queue, f, indent=2, ensure_ascii=False, sort_keys=True)
    except Exception as e:
        logger.warning("Could not write candidate queue: %s", e)
