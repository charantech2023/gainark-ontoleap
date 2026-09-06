"""
Integration and Partner Ecosystem Assertion Checks
Verifies marketing claims of third-party integrations (NetSuite, Salesforce, QuickBooks, Stripe, etc.)
against OpenAPI connector endpoints and technical documentation.
"""
from typing import List, Dict, Any, Optional
from .check_compliance import CheckResult

KEY_INTEGRATIONS = [
    "netsuite", "salesforce", "quickbooks", "stripe", "hubspot",
    "xero", "workday", "sap", "avalara", "taxjar", "jira", "slack"
]

def check_integration_claims(
    marketing_claims: List[Dict[str, Any]],
    openapi_spec: Optional[Dict[str, Any]],
    docs_text: Optional[str]
) -> List[CheckResult]:
    results: List[CheckResult] = []

    spec_paths = []
    if openapi_spec and isinstance(openapi_spec, dict):
        spec_paths = list(openapi_spec.get("paths", {}).keys())

    combined_tech_text = " ".join(spec_paths).lower() + " " + (docs_text or "").lower()

    for claim in marketing_claims:
        pred = (claim.get("predicate") or "").lower()
        obj = claim.get("object") or ""
        obj_lower = obj.lower()

        if pred != "integrateswith" and not any(k in obj_lower for k in KEY_INTEGRATIONS):
            continue

        matched_partner = None
        for partner in KEY_INTEGRATIONS:
            if partner in obj_lower:
                matched_partner = partner
                break

        if not matched_partner:
            continue

        # Check in OpenAPI paths
        matching_paths = [p for p in spec_paths if matched_partner in p.lower()]
        
        # Check in documentation
        doc_mentions = [
            line.strip() for line in (docs_text or "").splitlines()
            if matched_partner in line.lower() and any(w in line.lower() for w in ["sync", "connect", "api", "integration", "mapping", "webhook", "export"])
        ]

        if matching_paths:
            results.append(CheckResult(
                check_name=f"Assert_{matched_partner.capitalize()}_Integration",
                target_claim=obj,
                passed=True,
                confidence=0.98,
                evidence_span=f"Direct OpenAPI endpoints verified: {matching_paths[:2]}",
                source_citation=f"OpenAPI path {matching_paths[0]}",
                severity="VERIFIED"
            ))
        elif doc_mentions:
            results.append(CheckResult(
                check_name=f"Assert_{matched_partner.capitalize()}_Integration",
                target_claim=obj,
                passed=True,
                confidence=0.90,
                evidence_span=f"Technical documentation guide verified: '{doc_mentions[0][:140]}'",
                source_citation="Technical Documentation Portal",
                severity="VERIFIED"
            ))
        else:
            results.append(CheckResult(
                check_name=f"Assert_{matched_partner.capitalize()}_Integration",
                target_claim=obj,
                passed=False,
                confidence=0.88,
                evidence_span=f"Marketing advertises integration with '{obj}', but zero API connector endpoints or documentation configuration guides exist.",
                source_citation="OpenAPI Specification & Tech Docs",
                severity="CRITICAL_DRIFT"
            ))

    return results
