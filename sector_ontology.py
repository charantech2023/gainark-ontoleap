"""
GainARK OntoLeap - Sector Ontology / SKOS Synonym Layer
========================================================
Generates an LLM-powered synonym map that bridges marketing language to technical
documentation language. This is the core fix for "capability drift" alerts that fire
when a marketing claim uses buyer vocabulary ("Order-to-Revenue Cycle") but the tech
docs use API vocabulary ("order management", "revenue lifecycle").

Architecture
------------
1. build_synonym_map()   — Gemini call at analysis time; result is JSON-cached per domain.
2. expand_tech_triples() — Injects synonym clone-triples into technical_triples so the
                          existing _match_concept_details() loop finds them naturally.

Self-Building Ontology
----------------------
When a human reviewer dismisses an alert as "false positive", the dismissed
(marketing_term, tech_term) pair is written to the synonym cache via
record_reviewer_synonym(). Future runs skip regenerating for those pairs and
load them directly — the ontology grows without additional LLM cost.
"""

import os
import re
import json
import hashlib
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
    tech_triple_objects: Optional[List[str]] = None,
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
        timeout=25,
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
    tech_term: str,
    brand: str,
    cache_dir: Optional[str] = None,
) -> None:
    """
    Called when a human reviewer dismisses an alert as a false positive.
    Writes the (tech_term → marketing_term) synonym pair to the cache so future
    runs suppress this alert without an LLM call.
    """
    if cache_dir is None:
        cache_dir = os.path.dirname(os.path.abspath(__file__))
    cache_file = _cache_path(brand, cache_dir)
    current = _load_cache(cache_file) or {}
    key = tech_term.strip().lower()
    existing = current.get(key, [])
    if marketing_term.strip() not in existing:
        existing.append(marketing_term.strip())
        current[key] = existing
        _save_cache(cache_file, current)
        logger.info("Reviewer synonym recorded: '%s' → '%s' for %s", tech_term, marketing_term, brand)


# ---------------------------------------------------------------------------
# Triple expansion
# ---------------------------------------------------------------------------

def expand_tech_triples(
    technical_triples: list,
    synonym_map: Dict[str, List[str]],
) -> list:
    """
    For each technical triple whose object matches a canonical key in synonym_map,
    inject additional clone-triples with the marketing synonym as the object.

    This lets the existing _match_concept_details() loop verify marketing claims
    that use buyer vocabulary even when the docs use API vocabulary.

    Returns a new list (original triples first, then clones).
    """
    if not synonym_map:
        return technical_triples

    try:
        from models import SemanticTriple  # local import to avoid circular imports
    except ImportError:
        return technical_triples

    clones: list = []
    seen_clone_keys: set = set()

    for tt in technical_triples:
        obj_lower = (tt.object or "").lower()

        for canonical, marketing_synonyms in synonym_map.items():
            # Match if the canonical term appears anywhere in the tech triple's object
            if canonical not in obj_lower:
                continue

            for mkt_syn in marketing_synonyms:
                clone_key = (tt.predicate.lower(), mkt_syn.lower())
                if clone_key in seen_clone_keys:
                    continue
                seen_clone_keys.add(clone_key)

                clones.append(SemanticTriple(
                    subject=tt.subject,
                    predicate=tt.predicate,
                    object=mkt_syn,                        # marketing language
                    confidence=tt.confidence,
                    evidence_sentence=tt.evidence_sentence,
                    source_type=tt.source_type,
                    provenance=f"synonym:{tt.provenance or 'tech_docs'}",
                ))

    if clones:
        logger.info(
            "Synonym expansion: injected %d synonym clone-triples (from %d tech triples, %d canonical terms).",
            len(clones), len(technical_triples), len(synonym_map),
        )

    return technical_triples + clones
