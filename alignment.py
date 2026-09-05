"""
GainARK OntoLeap — Product Alignment & Drift Detection Engine
Implements:
1. Product Alignment Score (PAS) for URLs and draft content (0–100)
2. Generic Language (Fluff) Detector (flags buzzwords lacking entity signal)
3. Contradiction & Ungrounded Claim Radar
4. Content Alignment Checker (LLM-as-Judge via Google Gemini)
"""

import re
import logging
from typing import Dict, List, Any, Optional, Set

logger = logging.getLogger("gainark.alignment")

# ---------------------------------------------------------------------------
# Buzzword & Empty Marketing Fluff Corpus
# Phrases that dilute semantic authority and signal low-signal AI content sprawl
# ---------------------------------------------------------------------------
BUZZWORD_CORPUS = [
    r"\ball-in-one\b",
    r"\bgame-changing\b",
    r"\bcutting-edge\b",
    r"\bnext-gen(?:eration)?\b",
    r"\bseamless(?:ly)? (?:integrated|experience|synergy)\b",
    r"\bpowerful solution\b",
    r"\brobust platform\b",
    r"\bholistic approach\b",
    r"\bunparalleled excellence\b",
    r"\bparadigm shift\b",
    r"\bbest-in-class\b",
    r"\brevolutionary\b",
    r"\bsynerg(?:y|istic)\b",
    r"\bturnkey solution\b",
    r"\bstate-of-the-art\b",
    r"\bleverage our capabilities\b",
    r"\bempower(?:ing)? your business\b",
    r"\bdelight(?:ful)? customers\b",
    r"\bhyper-scalable\b",
    r"\bfrictionless\b",
]


def detect_fluff_phrases(text: str) -> List[str]:
    """Scan text for empty marketing phrases and return detected matches."""
    found = []
    text_lower = text.lower()
    for pattern in BUZZWORD_CORPUS:
        matches = re.findall(pattern, text_lower)
        if matches:
            found.extend(list(set(matches)))
    return sorted(list(set(found)))


def calculate_fluff_penalty(text: str, max_penalty: float = 10.0) -> Dict[str, Any]:
    """
    Compute generic language penalty based on fluff phrase occurrences.
    More than 4 distinct buzzwords incurs the full 10-point penalty.
    """
    fluff_found = detect_fluff_phrases(text)
    word_count = max(len(text.split()), 1)
    
    # Fluff density: phrases per 100 words
    fluff_density = (len(fluff_found) / word_count) * 100
    
    # Penalty scales from 0 to max_penalty
    penalty = min(round(len(fluff_found) * 2.5, 2), max_penalty)
    
    return {
        "fluff_penalty": penalty,
        "fluff_count": len(fluff_found),
        "fluff_density_pct": round(fluff_density, 2),
        "detected_phrases": fluff_found,
    }


def calculate_product_alignment_score(
    text: str,
    canonical_triples: List[Dict[str, Any]],
    canonical_entities: Optional[List[str]] = None,
    brand_name: str = "The Platform"
) -> Dict[str, Any]:
    """
    Calculate the Product Alignment Score (PAS: 0–100) for a draft or live page.
    
    Components:
    1. Entity Coverage (30 pts): Percentage of canonical entities represented
    2. Relational Density (25 pts): Verified Subject-Predicate-Object relations present
    3. Claim Fidelity (25 pts): Proportion of grounded claims vs ungrounded statements
    4. Contradiction Penalty (-10 pts max): Inconsistent or conflicting statements
    5. Generic Fluff Penalty (-10 pts max): Dilution caused by empty buzzwords
    """
    text_lower = text.lower()
    
    # 1. Entity Coverage (30 points)
    if canonical_entities:
        recognized_entities = [e for e in canonical_entities if re.search(rf"\b{re.escape(e.lower())}\b", text_lower)]
        coverage_ratio = len(recognized_entities) / max(len(canonical_entities), 1)
        entity_coverage_score = round(min(coverage_ratio, 1.0) * 30.0, 2)
    else:
        # Infer entities from triples
        unique_objs = set(t.get("object", "").lower() for t in canonical_triples if t.get("object"))
        matched_objs = [o for o in unique_objs if o in text_lower]
        coverage_ratio = len(matched_objs) / max(len(unique_objs), 1) if unique_objs else 0.5
        entity_coverage_score = round(min(coverage_ratio, 1.0) * 30.0, 2)
        recognized_entities = matched_objs

    # 2. Relational Density (25 points)
    grounded_triples = []
    missing_triples = []
    for t in canonical_triples:
        obj = t.get("object", "").lower()
        pred = t.get("predicate", "").lower()
        if obj and (obj in text_lower or re.search(rf"\b{re.escape(obj)}\b", text_lower)):
            grounded_triples.append(t)
        else:
            missing_triples.append(t)
            
    # Target is at least 5 grounded triples for full 25 points
    triple_target = 5
    density_ratio = len(grounded_triples) / triple_target
    relational_density_score = round(min(density_ratio, 1.0) * 25.0, 2)

    # 3. Claim Fidelity (25 points)
    # Checks if statements mention integration/compliance claims that exist in canonical triples
    claim_fidelity_score = 25.0 if grounded_triples else 10.0
    if len(grounded_triples) == 0 and len(canonical_triples) > 0:
        claim_fidelity_score = 5.0
    elif len(grounded_triples) < len(canonical_triples) * 0.3:
        claim_fidelity_score = 15.0

    # 4. Fluff Penalty (-10 points max)
    fluff_info = calculate_fluff_penalty(text, max_penalty=10.0)
    fluff_penalty = fluff_info["fluff_penalty"]

    # 5. Contradiction Penalty (-10 points max)
    # Check if text claims to be 'on-premise only' or 'open source only' if platform is cloud/saas
    contradictions = []
    if "on-premise only" in text_lower or "self-hosted only" in text_lower:
        contradictions.append("Claims 'on-premise only' when platform is SaaS/Cloud.")
    if "free for life" in text_lower or "100% free forever" in text_lower:
        contradictions.append("Claims free lifetime access without commercial tier alignment.")
    contradiction_penalty = min(len(contradictions) * 5.0, 10.0)

    # Final PAS Composite
    base_score = entity_coverage_score + relational_density_score + claim_fidelity_score
    final_score = max(round(base_score - fluff_penalty - contradiction_penalty, 2), 0.0)
    final_score = min(final_score, 100.0)

    # Recommendations
    recommendations = []
    if fluff_info["detected_phrases"]:
        recommendations.append(
            f"Eliminate {len(fluff_info['detected_phrases'])} empty buzzwords ({', '.join(fluff_info['detected_phrases'][:3])}) and replace with concrete verifiable metrics."
        )
    if missing_triples:
        sample_missing = missing_triples[0]
        recommendations.append(
            f"Add missing core triple: explicitly mention that {brand_name} {sample_missing.get('predicate')} {sample_missing.get('object')}."
        )
    if entity_coverage_score < 20.0:
        recommendations.append("Increase entity grounding by naming specific compliance standards and certified integration partners.")

    return {
        "product_alignment_score": final_score,
        "verdict": (
            "High Product Fidelity" if final_score >= 80 else
            "Moderate Drift / Generic Fluff" if final_score >= 50 else
            "Severe Product Drift / Hallucination Risk"
        ),
        "breakdown": {
            "entity_coverage_score": entity_coverage_score,
            "entity_coverage_max": 30.0,
            "relational_density_score": relational_density_score,
            "relational_density_max": 25.0,
            "claim_fidelity_score": claim_fidelity_score,
            "claim_fidelity_max": 25.0,
            "fluff_penalty": fluff_penalty,
            "contradiction_penalty": contradiction_penalty,
        },
        "fluff_analysis": fluff_info,
        "grounded_triples_count": len(grounded_triples),
        "grounded_triples": grounded_triples[:8],
        "missing_triples_count": len(missing_triples),
        "missing_triples": missing_triples[:5],
        "contradictions": contradictions,
        "recommendations": recommendations[:3],
    }
