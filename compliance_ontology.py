"""
GainARK OntoLeap - Compliance Ontology / supportsEvidenceFor Layer
==================================================================
Key insight (from Grok): A billing/revenue product that handles RevenueSchedule +
PerformanceObligation IS providing ASC 606 evidence — even if its docs never say "ASC 606".
The same logic applies to IFRS 15, US GAAP, SOX, PCI DSS, etc.

This module scans the already-extracted technical triples for feature-level evidence,
then injects `compliesWith <Standard>` triples so the truth matrix can verify those
marketing claims instead of raising drift alerts.

supportsEvidenceFor mapping
---------------------------
  Standard   ← evidence from tech triples whose object contains any of the listed keywords
  ASC 606    ← revenue schedule, performance obligation, revenue recognition, deferred revenue, SSP
  IFRS 15    ← revenue recognition, performance obligation, contract asset, deferred revenue
  US GAAP    ← revenue recognition, accrual, financial reporting, revenue schedule
  SOX        ← audit trail, audit log, financial controls, segregation of duties
  PCI DSS    ← card data, PCI, payment card, tokenization, card vault
  HIPAA      ← PHI, protected health information, HIPAA, healthcare data
  SOC 2      ← security controls, availability, confidentiality, access control, audit log
  GDPR       ← data privacy, consent management, right to erasure, data subject, personal data
"""

import logging
from typing import List, Dict, Tuple, Set

logger = logging.getLogger("gainark.compliance_ontology")

# ---------------------------------------------------------------------------
# supportsEvidenceFor map
# Standard → list of (predicate_hint, [keyword_patterns])
# predicate_hint: if "" matches any predicate; otherwise must match
# ---------------------------------------------------------------------------

_EVIDENCE_MAP: Dict[str, List[Tuple[str, List[str]]]] = {
    "ASC 606": [
        ("", ["revenue schedule", "performance obligation", "revenue recognition",
              "deferred revenue", "contract modification", "standalone selling price",
              "ssp allocation", "transaction price", "variable consideration",
              "revenue waterfall", "revenue deferral"]),
    ],
    "IFRS 15": [
        ("", ["revenue recognition", "performance obligation", "contract asset",
              "deferred revenue", "revenue schedule", "revenue allocation",
              "variable consideration", "revenue deferral"]),
    ],
    "US GAAP": [
        ("", ["revenue recognition", "accrual", "deferred revenue", "revenue schedule",
              "financial reporting", "gaap", "asc 606"]),
    ],
    "GAAP": [
        ("", ["revenue recognition", "accrual accounting", "deferred revenue",
              "financial reporting", "gaap"]),
    ],
    "SOX": [
        ("", ["audit trail", "audit log", "financial controls", "segregation of duties",
              "access control", "change log", "immutable log", "sox"]),
    ],
    "PCI DSS": [
        ("", ["pci", "payment card", "card data", "tokenization", "card vault",
              "card storage", "cvv", "cardholder data"]),
    ],
    "HIPAA": [
        ("", ["phi", "protected health information", "hipaa", "healthcare data",
              "medical record", "patient data"]),
    ],
    "SOC 2": [
        ("", ["soc 2", "soc2", "security controls", "availability monitoring",
              "confidentiality controls", "access control", "audit log",
              "penetration testing", "vulnerability scanning", "trust service"]),
    ],
    "GDPR": [
        ("", ["gdpr", "data privacy", "consent management", "right to erasure",
              "data subject", "personal data", "data retention", "privacy policy",
              "data processing agreement", "dpa"]),
    ],
    "ISO 27001": [
        ("", ["iso 27001", "information security management", "isms",
              "risk assessment", "security policy", "access management"]),
    ],
}

# Minimum number of distinct keyword matches required before we inject the triple.
# Prevents single-word false positives (e.g. one mention of "accrual" → US GAAP).
_MIN_MATCHES: Dict[str, int] = {
    "ASC 606": 2,
    "IFRS 15": 2,
    "US GAAP": 2,
    "GAAP": 2,
    "SOX": 2,
    "PCI DSS": 1,
    "HIPAA": 1,
    "SOC 2": 2,
    "GDPR": 1,
    "ISO 27001": 1,
}


def _collect_tech_text(technical_triples: list) -> str:
    """Build a searchable text blob from all tech triple objects + evidence sentences."""
    parts = []
    for t in technical_triples:
        if t.object:
            parts.append(t.object.lower())
        if t.evidence_sentence:
            parts.append(t.evidence_sentence.lower())
    return " ".join(parts)


def inject_compliance_triples(
    technical_triples: list,
    brand: str,
) -> list:
    """
    Scans technical_triples for feature-level compliance evidence.
    Injects `compliesWith <Standard>` triples for any standard where
    sufficient evidence keywords are found.

    Returns the expanded triple list (originals first, then injected).
    """
    try:
        from models import SemanticTriple
    except ImportError:
        logger.warning("models.SemanticTriple not importable; compliance injection skipped.")
        return technical_triples

    tech_blob = _collect_tech_text(technical_triples)

    # Already-present compliesWith standards (don't double-inject)
    already_present: Set[str] = set()
    for t in technical_triples:
        if t.predicate.lower() == "complieswith":
            already_present.add(t.object.strip())

    injected: List = []
    for standard, evidence_rules in _EVIDENCE_MAP.items():
        if standard in already_present:
            continue

        matched_keywords: List[str] = []
        for _predicate_hint, keywords in evidence_rules:
            for kw in keywords:
                if kw in tech_blob and kw not in matched_keywords:
                    matched_keywords.append(kw)

        min_needed = _MIN_MATCHES.get(standard, 1)
        if len(matched_keywords) >= min_needed:
            evidence_note = (
                f"[supportsEvidenceFor] Tech docs contain evidence for {standard}: "
                f"found keywords: {', '.join(matched_keywords[:6])}"
            )
            injected.append(SemanticTriple(
                subject=brand,
                predicate="compliesWith",
                object=standard,
                confidence=0.88,
                evidence_sentence=evidence_note,
                source_type="compliance_ontology_inference",
                provenance="compliance_ontology:supportsEvidenceFor",
            ))
            logger.info(
                "Compliance ontology: injecting compliesWith '%s' "
                "(evidence keywords: %s)", standard, matched_keywords[:6]
            )

    if injected:
        logger.info(
            "Compliance ontology: injected %d standard triples for %s (%s).",
            len(injected), brand,
            ", ".join(t.object for t in injected)
        )

    return technical_triples + injected
