"""
GainARK OntoLeap — Multi-Step Compliance Ontology & Evidence Inferrer
=====================================================================
Structured regulatory modeling for B2B SaaS standards.

Standard → Steps / Criteria → Required Capabilities & Keywords
---------------------------------------------------------------
1. FASB ASC 606 (5-Step Framework):
   - Step 1: Identify contract with customer (contract management, contract modification)
   - Step 2: Identify performance obligations in the contract (performance obligations)
   - Step 3: Determine transaction price (pricing models, usage rating, discounts)
   - Step 4: Allocate transaction price to obligations (standalone selling price, SSP allocation)
   - Step 5: Recognize revenue when/as obligation is satisfied (revenue recognition, revenue schedules, waterfalls)

2. AICPA SOC 2 (5 Trust Services Criteria):
   - Security (Common Criteria): RBAC, permissions, SSO, audit trail, MFA
   - Availability: Uptime SLA, disaster recovery, redundancy, failover
   - Confidentiality: Data encryption, tokenization, card vaulting
   - Processing Integrity: Financial reconciliation, input validation, audit logs
   - Privacy: Data privacy policies, consent management, data retention
"""

import logging
from typing import List, Dict, Tuple, Set, Any, Optional

logger = logging.getLogger("gainark.compliance_ontology")

# ---------------------------------------------------------------------------
# Formal Multi-Step Compliance Frameworks
# ---------------------------------------------------------------------------

COMPLIANCE_FRAMEWORKS: Dict[str, Dict[str, Any]] = {
    "ASC 606": {
        "standard": "ASC 606",
        "title": "FASB ASC 606 / IFRS 15 Revenue from Contracts with Customers",
        "authority": "Financial Accounting Standards Board (FASB)",
        "description": "5-step framework governing revenue recognition from customer contracts.",
        "min_steps_satisfied": 3,
        "steps": [
            {
                "step_number": 1,
                "name": "Step 1: Identify the contract with a customer",
                "concept_ids": ["contract-management", "contract-modification"],
                "keywords": ["contract", "contract modification", "master services agreement", "amendment", "contract lifecycle"],
                "required": True,
            },
            {
                "step_number": 2,
                "name": "Step 2: Identify the performance obligations in the contract",
                "concept_ids": ["performance-obligation"],
                "keywords": ["performance obligation", "distinct goods", "bundled services", "deliverable"],
                "required": True,
            },
            {
                "step_number": 3,
                "name": "Step 3: Determine the transaction price",
                "concept_ids": ["usage-rating", "tiered-pricing", "recurring-billing"],
                "keywords": ["transaction price", "variable consideration", "discounts", "rate schedule", "usage rating"],
                "required": False,
            },
            {
                "step_number": 4,
                "name": "Step 4: Allocate the transaction price to performance obligations",
                "concept_ids": ["standalone-selling-price", "revenue-allocation"],
                "keywords": ["standalone selling price", "ssp allocation", "relative standalone selling price", "revenue allocation", "residual approach"],
                "required": True,
            },
            {
                "step_number": 5,
                "name": "Step 5: Recognize revenue when (or as) the entity satisfies a performance obligation",
                "concept_ids": ["revenue-recognition", "revenue-schedules", "revenue-waterfall"],
                "keywords": ["revenue recognition", "revenue schedule", "revenue waterfall", "deferred revenue", "over time", "point in time", "milestone recognition"],
                "required": True,
            },
        ],
    },
    "IFRS 15": {
        "standard": "IFRS 15",
        "title": "IFRS 15 Revenue from Contracts with Customers",
        "authority": "International Accounting Standards Board (IASB)",
        "description": "International standard governing revenue recognition.",
        "min_steps_satisfied": 3,
        "steps": [
            {
                "step_number": 1,
                "name": "Step 1: Identify the contract",
                "concept_ids": ["contract-management"],
                "keywords": ["contract", "contract asset", "contract liability"],
                "required": True,
            },
            {
                "step_number": 2,
                "name": "Step 2: Identify performance obligations",
                "concept_ids": ["performance-obligation"],
                "keywords": ["performance obligation", "distinct goods", "services"],
                "required": True,
            },
            {
                "step_number": 3,
                "name": "Step 3: Determine transaction price",
                "concept_ids": ["usage-rating", "pricing"],
                "keywords": ["transaction price", "variable consideration"],
                "required": False,
            },
            {
                "step_number": 4,
                "name": "Step 4: Allocate transaction price",
                "concept_ids": ["standalone-selling-price", "revenue-allocation"],
                "keywords": ["standalone selling price", "revenue allocation", "ssp"],
                "required": True,
            },
            {
                "step_number": 5,
                "name": "Step 5: Recognize revenue",
                "concept_ids": ["revenue-recognition", "revenue-schedules"],
                "keywords": ["revenue recognition", "revenue schedule", "deferred revenue"],
                "required": True,
            },
        ],
    },
    "SOC 2": {
        "standard": "SOC 2",
        "title": "AICPA SOC 2 Trust Services Criteria",
        "authority": "American Institute of CPAs (AICPA)",
        "description": "Attestation of internal controls over security, availability, confidentiality, and integrity.",
        "min_steps_satisfied": 1,
        "steps": [
            {
                "step_number": 1,
                "name": "Security (Common Criteria)",
                "concept_ids": ["permissions", "single-sign-on", "audit-trail"],
                "keywords": ["access control", "rbac", "permissions", "single sign-on", "sso", "audit trail", "mfa", "multi-factor", "soc 2", "soc2"],
                "required": True,
            },
            {
                "step_number": 2,
                "name": "Availability",
                "concept_ids": ["sla"],
                "keywords": ["uptime", "availability", "disaster recovery", "sla", "redundancy", "failover"],
                "required": False,
            },
            {
                "step_number": 3,
                "name": "Confidentiality",
                "concept_ids": ["tokenization"],
                "keywords": ["encryption", "confidentiality", "aes-256", "tls", "key management", "tokenization"],
                "required": False,
            },
            {
                "step_number": 4,
                "name": "Processing Integrity",
                "concept_ids": ["reconciliation", "bulk-operations"],
                "keywords": ["reconciliation", "input validation", "data integrity", "processing controls", "audit log"],
                "required": False,
            },
            {
                "step_number": 5,
                "name": "Privacy",
                "concept_ids": ["data-privacy"],
                "keywords": ["privacy", "data retention", "consent management", "gdpr", "ccpa", "pii"],
                "required": False,
            },
        ],
    },
    "US GAAP": {
        "standard": "US GAAP",
        "title": "Generally Accepted Accounting Principles (US GAAP)",
        "authority": "Financial Accounting Standards Board (FASB)",
        "description": "Standard framework of guidelines for financial accounting in the United States.",
        "min_steps_satisfied": 1,
        "steps": [
            {
                "step_number": 1,
                "name": "Accrual Accounting & Revenue Recognition",
                "concept_ids": ["revenue-recognition", "gl-account"],
                "keywords": ["revenue recognition", "accrual", "deferred revenue", "revenue schedule", "financial reporting", "gaap", "asc 606"],
                "required": True,
            }
        ],
    },
    "SOX": {
        "standard": "SOX",
        "title": "Sarbanes-Oxley Act (SOX Section 404)",
        "authority": "Public Company Accounting Oversight Board (PCAOB)",
        "description": "Internal controls and audit trail verification for financial reporting.",
        "min_steps_satisfied": 1,
        "steps": [
            {
                "step_number": 1,
                "name": "Financial Internal Controls & Audit Trail",
                "concept_ids": ["audit-trail", "gl-account"],
                "keywords": ["audit trail", "audit log", "financial controls", "segregation of duties", "access control", "change log", "immutable log", "sox"],
                "required": True,
            }
        ],
    },
    "PCI DSS": {
        "standard": "PCI DSS",
        "title": "Payment Card Industry Data Security Standard",
        "authority": "PCI Security Standards Council",
        "description": "Technical controls for cardholder data vaulting and secure processing.",
        "min_steps_satisfied": 1,
        "steps": [
            {
                "step_number": 1,
                "name": "Cardholder Data Protection & Vaulting",
                "concept_ids": ["tokenization", "credit-card-processing"],
                "keywords": ["pci", "payment card", "card data", "tokenization", "card vault", "card storage", "cvv", "cardholder data"],
                "required": True,
            }
        ],
    },
    "HIPAA": {
        "standard": "HIPAA",
        "title": "Health Insurance Portability and Accountability Act",
        "authority": "U.S. Department of Health and Human Services (HHS)",
        "description": "Protection standards for sensitive patient health information (PHI).",
        "min_steps_satisfied": 1,
        "steps": [
            {
                "step_number": 1,
                "name": "PHI Protection & Healthcare Privacy",
                "concept_ids": ["data-privacy"],
                "keywords": ["phi", "protected health information", "hipaa", "healthcare data", "medical record", "patient data"],
                "required": True,
            }
        ],
    },
    "GDPR": {
        "standard": "GDPR",
        "title": "General Data Protection Regulation",
        "authority": "European Union Data Protection Authorities",
        "description": "Regulation on data protection and privacy in the European Union.",
        "min_steps_satisfied": 1,
        "steps": [
            {
                "step_number": 1,
                "name": "Data Privacy & Data Subject Rights",
                "concept_ids": ["data-privacy"],
                "keywords": ["gdpr", "data privacy", "consent management", "right to erasure", "data subject", "personal data", "data retention", "privacy policy", "dpa"],
                "required": True,
            }
        ],
    },
    "ISO 27001": {
        "standard": "ISO 27001",
        "title": "ISO/IEC 27001 Information Security Management",
        "authority": "International Organization for Standardization (ISO)",
        "description": "Information security management systems (ISMS) specification.",
        "min_steps_satisfied": 1,
        "steps": [
            {
                "step_number": 1,
                "name": "Information Security Management Controls",
                "concept_ids": ["permissions", "audit-trail"],
                "keywords": ["iso 27001", "information security management", "isms", "risk assessment", "security policy", "access management"],
                "required": True,
            }
        ],
    },
}

# Legacy fallback keyword patterns for standards not defined in COMPLIANCE_FRAMEWORKS
_LEGACY_EVIDENCE_MAP: Dict[str, List[Tuple[str, List[str]]]] = {
    "GAAP": [
        ("", ["revenue recognition", "accrual accounting", "deferred revenue",
              "financial reporting", "gaap"]),
    ],
    "SOC 1": [
        ("", ["soc 1", "soc1", "audit trail", "financial controls", "gl account", "reconciliation"]),
    ],
    "SOC 1 Type II": [
        ("", ["soc 1", "soc1", "audit trail", "financial controls", "type ii", "type 2"]),
    ],
    "SOC 2 Type II": [
        ("", ["soc 2", "soc2", "type ii", "type 2", "security controls", "audit log"]),
    ],
}

# Backward compatibility alias
_EVIDENCE_MAP = {
    standard: [("", [kw for s in fw["steps"] for kw in s["keywords"]])]
    for standard, fw in COMPLIANCE_FRAMEWORKS.items()
}
_EVIDENCE_MAP.update(_LEGACY_EVIDENCE_MAP)
_MIN_MATCHES = {k: fw.get("min_steps_satisfied", 1) for k, fw in COMPLIANCE_FRAMEWORKS.items()}
_MIN_MATCHES.update({"GAAP": 2, "SOC 1": 2, "SOC 1 Type II": 2, "SOC 2 Type II": 2})


def _collect_tech_text(technical_triples: list) -> str:
    """Build a searchable text blob from all tech triple objects + evidence sentences."""
    parts = []
    for t in technical_triples:
        if t.object:
            parts.append(t.object.lower())
        if t.evidence_sentence:
            parts.append(t.evidence_sentence.lower())
    return " ".join(parts)


def evaluate_compliance_framework(
    technical_triples: list,
    standard: str,
) -> Optional[Dict[str, Any]]:
    """
    Evaluates technical triples against a formal multi-step compliance framework.
    Returns detailed step satisfaction, evidence matches, and overall status.
    """
    std_key = standard.strip()
    framework = COMPLIANCE_FRAMEWORKS.get(std_key)
    if not framework:
        for k, v in COMPLIANCE_FRAMEWORKS.items():
            if k.lower() == std_key.lower():
                framework = v
                break
    if not framework:
        return None

    tech_blob = _collect_tech_text(technical_triples)
    triple_objects = {str(getattr(t, "object", "")).strip().lower() for t in technical_triples}

    evaluated_steps = []
    satisfied_count = 0

    for step in framework["steps"]:
        matched_evidence: List[str] = []

        # Check concept ID matches in triple objects
        for cid in step.get("concept_ids", []):
            cid_clean = cid.replace("-", " ").lower()
            for obj in triple_objects:
                if cid_clean in obj or obj in cid_clean:
                    matched_evidence.append(f"concept:{cid} ('{obj}')")
                    break

        # Check keyword matches in tech text
        for kw in step.get("keywords", []):
            if kw.lower() in tech_blob:
                clean_kw = kw.lower()
                already_in = False
                for m in matched_evidence:
                    if clean_kw in m.lower():
                        already_in = True
                        break
                if not already_in:
                    matched_evidence.append(kw)

        is_satisfied = len(matched_evidence) > 0
        if is_satisfied:
            satisfied_count += 1

        evaluated_steps.append({
            "step_number": step["step_number"],
            "name": step["name"],
            "required": step.get("required", False),
            "satisfied": is_satisfied,
            "concept_ids": step.get("concept_ids", []),
            "matched_evidence": matched_evidence[:5],
        })

    min_needed = framework.get("min_steps_satisfied", 1)
    if satisfied_count >= len(framework["steps"]):
        status = "compliant"
    elif satisfied_count >= min_needed:
        status = "satisfied"
    elif satisfied_count > 0:
        status = "partial"
    else:
        status = "non_compliant"

    return {
        "standard": framework["standard"],
        "title": framework["title"],
        "authority": framework.get("authority", ""),
        "description": framework.get("description", ""),
        "status": status,
        "is_compliant": satisfied_count >= min_needed,
        "steps_satisfied_count": satisfied_count,
        "steps_total": len(framework["steps"]),
        "min_steps_required": min_needed,
        "steps": evaluated_steps,
    }


def evaluate_all_compliance(technical_triples: list) -> List[Dict[str, Any]]:
    """Evaluates technical triples against all registered compliance frameworks."""
    results = []
    for standard in COMPLIANCE_FRAMEWORKS:
        ev = evaluate_compliance_framework(technical_triples, standard)
        if ev:
            results.append(ev)
    return results


def list_compliance_frameworks() -> List[Dict[str, Any]]:
    """Returns metadata and step definitions for all registered compliance frameworks."""
    out = []
    for key, fw in COMPLIANCE_FRAMEWORKS.items():
        out.append({
            "standard": fw["standard"],
            "title": fw["title"],
            "authority": fw.get("authority", ""),
            "description": fw.get("description", ""),
            "steps_count": len(fw["steps"]),
            "min_steps_required": fw["min_steps_satisfied"],
            "steps": [
                {
                    "step_number": s["step_number"],
                    "name": s["name"],
                    "required": s.get("required", False),
                    "concept_ids": s.get("concept_ids", []),
                }
                for s in fw["steps"]
            ],
        })
    return out


def inject_compliance_triples(
    technical_triples: list,
    brand: str,
) -> list:
    """
    Scans technical_triples for feature-level compliance evidence.
    Injects `compliesWith <Standard>` triples for any standard where
    sufficient evidence is found, citing the exact steps/criteria satisfied.

    Returns the expanded triple list (originals first, then injected).
    """
    try:
        from models import SemanticTriple
    except ImportError:
        logger.warning("models.SemanticTriple not importable; compliance injection skipped.")
        return technical_triples

    # Already-present compliesWith standards (don't double-inject)
    already_present: Set[str] = set()
    for t in technical_triples:
        if getattr(t, "predicate", "").lower() == "complieswith":
            already_present.add(str(getattr(t, "object", "")).strip())

    injected: List = []

    # 1. Evaluate structured multi-step frameworks
    for standard in COMPLIANCE_FRAMEWORKS:
        if standard in already_present:
            continue

        eval_res = evaluate_compliance_framework(technical_triples, standard)
        if eval_res and eval_res["is_compliant"]:
            satisfied_step_names = [
                f"Step {s['step_number']}" for s in eval_res["steps"] if s["satisfied"]
            ]
            evidence_note = (
                f"[supportsEvidenceFor] Tech docs satisfy {standard} requirements "
                f"({len(satisfied_step_names)}/{eval_res['steps_total']} steps: {', '.join(satisfied_step_names)})"
            )
            injected.append(SemanticTriple(
                subject=brand,
                predicate="compliesWith",
                object=standard,
                confidence=0.92,
                evidence_sentence=evidence_note,
                source_type="compliance_ontology_inference",
                provenance="compliance_ontology:supportsEvidenceFor",
            ))
            already_present.add(standard)
            logger.info(
                "Compliance ontology: injecting compliesWith '%s' "
                "(satisfied steps: %s)", standard, satisfied_step_names
            )

    # 2. Evaluate legacy fallback patterns
    tech_blob = _collect_tech_text(technical_triples)
    for standard, rules in _LEGACY_EVIDENCE_MAP.items():
        if standard in already_present:
            continue

        matched_kws: List[str] = []
        for _pred, kws in rules:
            for kw in kws:
                if kw in tech_blob and kw not in matched_kws:
                    matched_kws.append(kw)

        if len(matched_kws) >= 2:
            evidence_note = (
                f"[supportsEvidenceFor] Tech docs contain evidence for {standard}: "
                f"found keywords: {', '.join(matched_kws[:6])}"
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
            already_present.add(standard)
            logger.info(
                "Compliance ontology: injecting compliesWith '%s' (keywords: %s)",
                standard, matched_kws[:6]
            )

    if injected:
        logger.info(
            "Compliance ontology: injected %d standard triples for %s (%s).",
            len(injected), brand,
            ", ".join(t.object for t in injected)
        )

    return technical_triples + injected
