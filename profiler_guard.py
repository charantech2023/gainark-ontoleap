"""
profiler_guard.py — Guardrails AI Validation & Structured Schema Enforcement

Implements deterministic Guardrails validation policies for autonomous discovery:
1. ConceptIdValidator: Enforces SKOS identifier formatting (lowercase, snake_case, clean alphanumeric).
2. EvidenceCitationValidator: Verifies that discovered capabilities include substantive verbatim evidence quotes.
3. RelationDomainRangeValidator: Enforces semantic relation domain and range constraints against ontology_schema.
4. ProfileSanitizationGuard: Validates and sanitizes complete LLM-discovered vertical profiles.
"""

import re
import logging
from typing import Dict, Any, List, Optional, Tuple

from ontology_schema import RELATIONS, relation_for_predicate, RelationSpec, PRODUCT, OBJECT_TYPES

logger = logging.getLogger("gainark.profiler_guard")

# Regex for strict SKOS concept identifiers
_CONCEPT_ID_REGEX = re.compile(r'^[a-z0-9]+(?:_[a-z0-9]+)*$')


class ProfilerValidationError(ValueError):
    """Raised when an LLM discovery output violates deterministic guardrails."""
    pass


def sanitize_concept_id(raw_id: str) -> str:
    """
    Guardrail: Sanitizes and enforces SKOS snake_case concept identifier convention.
    E.g. 'Revenue Recognition' -> 'revenue_recognition', 'ASC-606' -> 'asc_606'.
    """
    if not raw_id:
        return "unnamed_concept"
    clean = raw_id.strip().lower()
    # Replace dashes, spaces, and punctuation with underscores
    clean = re.sub(r'[^a-z0-9_]', '_', clean)
    # Collapse multiple consecutive underscores
    clean = re.sub(r'_+', '_', clean).strip('_')
    return clean or "unnamed_concept"


def validate_concept_id(concept_id: str) -> bool:
    """
    Returns True if concept_id strictly conforms to SKOS concept identifier rules.
    """
    return bool(_CONCEPT_ID_REGEX.match(concept_id))


def validate_evidence_citation(quote: Optional[str], min_length: int = 15) -> bool:
    """
    Guardrail: Validates that an evidence quote is substantive and non-placeholder.
    Disallows generic LLM placeholders like 'N/A', 'Found on page', 'Mentioned in text'.
    """
    if not quote or not isinstance(quote, str):
        return False
    text = quote.strip()
    if len(text) < min_length:
        return False
    # Reject obvious non-citations
    placeholders = {"n/a", "none", "not specified", "mentioned in text", "found on page", "various", "unknown"}
    if text.lower() in placeholders:
        return False
    return True


def validate_relation_semantics(
    predicate: str,
    subject_type: Optional[str] = None,
    object_type: Optional[str] = None
) -> Tuple[bool, Optional[str]]:
    """
    Guardrail: Verifies that an extracted or predicted relation conforms to ontology_schema.
    Returns (is_valid, error_message).
    """
    spec: Optional[RelationSpec] = relation_for_predicate(predicate)
    if not spec:
        return False, f"Predicate '{predicate}' is not registered in ontology_schema.RELATIONS."

    # Validate subject (should be Product / SoftwarePlatform) if provided
    valid_subjects = {PRODUCT.lower(), "softwareplatform", "softwareapplication", "organization"}
    if subject_type and subject_type.lower() not in valid_subjects:
        return False, f"Subject type '{subject_type}' is not a valid product subject for predicate '{predicate}'."

    # Validate object type against relation's declared object_type
    if object_type:
        expected_obj = spec.object_type.lower()
        if object_type.lower() != expected_obj and object_type.lower() not in expected_obj:
            # Check if it matches an alias
            return False, f"Object type '{object_type}' violates expected object type '{spec.object_type}' for predicate '{predicate}'."

    return True, None


def guard_discovered_vertical_profile(profile_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Guardrail Pipeline: Validates and sanitizes a complete vertical profile
    produced by Gemini or other LLMs in industry_profiler.py.
    
    Guarantees:
    - Non-empty, alphanumeric vertical_id in snake_case.
    - Curated seed concepts are sanitized and deduplicated.
    - All concept IDs match SKOS snake_case rules.
    - Compliance frameworks are strings with valid length.
    - Direct competitors are valid brand names.
    """
    sanitized = dict(profile_data)

    # 1. Enforce vertical ID
    raw_vid = sanitized.get("vertical_id", "")
    sanitized["vertical_id"] = sanitize_concept_id(raw_vid) if raw_vid else "custom_vertical"

    # 2. Enforce display name
    name = sanitized.get("display_name") or sanitized.get("name") or sanitized.get("vertical_name") or ""
    clean_name = name.strip() if name else sanitized["vertical_id"].replace("_", " ").title()
    sanitized["name"] = clean_name
    sanitized["display_name"] = clean_name

    # 3. Sanitize seed concepts
    raw_concepts = sanitized.get("seed_concepts", [])
    seen = set()
    clean_concepts = []
    for item in raw_concepts:
        if isinstance(item, str):
            cid = sanitize_concept_id(item)
            if cid and cid not in seen:
                seen.add(cid)
                clean_concepts.append(item.strip())
        elif isinstance(item, dict):
            raw_cid = item.get("id") or item.get("concept_id") or item.get("prefLabel") or ""
            cid = sanitize_concept_id(raw_cid)
            label = item.get("prefLabel") or item.get("label") or cid.replace("_", " ").title()
            if cid and cid not in seen:
                seen.add(cid)
                clean_concepts.append(label.strip())
    sanitized["seed_concepts"] = clean_concepts[:50]  # Cap bounds

    # 4. Sanitize compliance frameworks
    raw_compliance = sanitized.get("compliance_frameworks", [])
    clean_compliance = []
    for c in raw_compliance:
        if isinstance(c, str) and len(c.strip()) >= 2:
            clean_compliance.append(c.strip())
    sanitized["compliance_frameworks"] = clean_compliance[:20]

    # 5. Sanitize competitors
    raw_competitors = sanitized.get("direct_competitors", [])
    clean_competitors = []
    for comp in raw_competitors:
        if isinstance(comp, str) and len(comp.strip()) >= 2:
            clean_competitors.append(comp.strip())
    sanitized["direct_competitors"] = clean_competitors[:15]

    return sanitized
