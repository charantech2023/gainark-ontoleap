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

from synonym_queue import entry_key

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
    queue: Any = None
) -> bool:
    """Approve a surface form into the vertical's curated alt_labels.

    Called when a reviewer decides a marketing phrase does mean an existing concept -
    typically dismissing a drift alert as a false positive.

    It used to append to a per-brand JSON cache that only the generated-synonym path
    read, so a human decision landed in the weaker of the two systems and never reached
    the ontology. The judgement now goes where the ontology actually lives: onto the
    concept's altLabels, which extraction already matches and always resolves back to
    the canonical.

    On Cloud Run the verticals directory is the container filesystem and sync_down()
    overwrites it from the mirror, so the approval is applied through
    vertical_store.update(): to the mirror's latest copy, and re-applied if another writer
    got there first. If the mirror cannot take it, MirrorWriteError propagates and the
    approval has not happened - reporting success for a change the next sync would undo
    is how an approval used to vanish while the queue said "approved".

    Returns True when the vertical was changed.
    """
    import vertical_store

    surface = (marketing_term or "").strip()
    target = (canonical_label or "").strip()
    if not surface or not target:
        return False
    vertical_id = os.path.splitext(os.path.basename(vertical_path))[0]
    applied: Dict[str, str] = {}

    def add_alternate(data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if data is None:
            logger.warning("Cannot record synonym: vertical %s has no profile", vertical_id)
            return None
        concepts = data.get("concepts") or []
        concept = next((c for c in concepts
                        if c.get("prefLabel", "").strip().lower() == target.lower()), None)
        if concept is None:
            logger.warning("Cannot record synonym: %r is not a concept in %s",
                           target, vertical_id)
            return None

        # Never let one surface form mean two concepts - the emitted canonical would then
        # depend on iteration order.
        for other in concepts:
            if other is concept:
                continue
            if any(a.strip().lower() == surface.lower() for a in other.get("altLabels") or []):
                logger.warning("Cannot record synonym: %r is already an alternate of %r",
                               surface, other.get("prefLabel"))
                return None

        alts = concept.setdefault("altLabels", [])
        if any(a.strip().lower() == surface.lower() for a in alts):
            return None
        alts.append(surface)
        data.setdefault("alt_labels", {})[concept["prefLabel"]] = list(alts)
        applied["prefLabel"] = concept["prefLabel"]
        return data

    if vertical_store.update(vertical_id, add_alternate, path=vertical_path) is None:
        return False

    # Settles every queued candidate with this surface form in this vertical, on every
    # instance.
    try:
        (queue or _default_queue()).approve(surface, applied["prefLabel"], vertical_id)
    except Exception as err:
        # The vertical is already changed and mirrored; only the queue's mark is missing,
        # and the candidate would merely show as pending again.
        logger.error("Could not record approval of %r in the review queue: %s", surface, err)

    logger.info("Recorded alternate %r -> %r in %s", surface, applied["prefLabel"], vertical_id)
    return True


def withdraw_reviewer_synonym(marketing_term: str, canonical_label: str,
                              vertical_path: str) -> bool:
    """Take an approved surface form back off the concept's altLabels.

    The reverse of record_reviewer_synonym, through the same vertical_store.update(), so
    MirrorWriteError propagates the same way. Returns True when the vertical changed.
    """
    import vertical_store

    surface = (marketing_term or "").strip().lower()
    target = (canonical_label or "").strip().lower()
    vertical_id = os.path.splitext(os.path.basename(vertical_path))[0]

    def remove_alternate(data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        concept = next((c for c in (data or {}).get("concepts") or []
                        if c.get("prefLabel", "").strip().lower() == target), None)
        if concept is None:
            return None
        alts = concept.get("altLabels") or []
        kept = [a for a in alts if a.strip().lower() != surface]
        if len(kept) == len(alts):
            return None
        concept["altLabels"] = kept
        data.setdefault("alt_labels", {})[concept["prefLabel"]] = list(kept)
        return data

    changed = vertical_store.update(vertical_id, remove_alternate, path=vertical_path) is not None
    if changed:
        logger.info("Withdrew alternate %r from %r in %s", marketing_term, canonical_label,
                    vertical_id)
    return changed


def reject_synonym_candidate(key: str, vertical_path_for: Any, queue: Any = None) -> Dict[str, Any]:
    """Reject one queued pairing, and undo it in the vertical if it had been approved.

    A rejection after an approval is a reviewer changing their mind. Recording it only in
    the queue left the alternate in the vertical, where extraction kept applying it while
    the queue said "rejected". So the alternate comes out first - if that fails, nothing
    is recorded - and the rejection then withdraws the approval for the surface form's
    other pairings as well.

    An approval of the surface form as a different concept is left alone: rejecting
    "variable pricing" as Transaction Pricing says nothing about it meaning Dynamic Pricing.

    `vertical_path_for(vertical_id)` resolves a vertical to its profile, or None.
    Raises ValueError for an unknown key, MirrorWriteError when the vertical could not be
    changed.
    """
    queue = queue or _default_queue()
    entry = queue.get(key)
    if entry is None:
        raise ValueError("No candidate %r in the queue." % key)

    withdraws = False
    approved_as = (entry.get("approved_as") or "").strip()
    if (entry.get("status") == "approved" and entry.get("vertical_id")
            and approved_as.lower() == (entry.get("generated_canonical") or "").strip().lower()):
        path = vertical_path_for(entry["vertical_id"])
        if path:
            withdraw_reviewer_synonym(entry.get("surface_form", ""), approved_as, path)
            withdraws = True
    return queue.reject(key, withdraws_approval=withdraws)


# ---------------------------------------------------------------------------
# Triple expansion
# ---------------------------------------------------------------------------

def propose_alt_labels(
    synonym_map: Dict[str, List[str]],
    config: Any,
    brand: str,
    queue: Any = None
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

    queue = queue or _default_queue()
    vertical_id = getattr(config, "vertical_id", None)
    proposals: Dict[str, Dict[str, Any]] = {}
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
            proposals.setdefault(entry_key(generated_canonical, key, vertical_id), {
                "surface_form": synonym.strip(),
                "generated_canonical": generated_canonical.strip(),
                "brand": brand,
                "vertical_id": vertical_id,
            })

    if proposals:
        stats["proposed"] = len(queue.propose(proposals))
    logger.info(
        "Synonym proposals for %s: %d new candidates, %d already known, %d conflict "
        "with curated alt_labels (curated wins).",
        brand, stats["proposed"], stats["already_known"], stats["conflicting"])
    return stats


def _default_queue():
    from synonym_queue import default_queue
    return default_queue()
