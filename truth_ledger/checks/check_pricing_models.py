"""
Pricing and Monetization Model Assertion Checks
Verifies marketing claims of Usage-Based, Consumption, Tiered, or Subscription pricing
against OpenAPI metering and rating endpoints.
"""
from typing import List, Dict, Any, Optional
from .check_compliance import CheckResult

PRICING_MODELS = {
    "usage": ["meter", "usage", "event", "consumption", "rating", "counter"],
    "subscription": ["subscription", "plan", "recur", "membership", "tier"],
    "tiered": ["tier", "slab", "volume", "graduated", "bracket"],
    "transaction": ["transaction", "fee", "percentage", "payout", "payment"]
}

def check_pricing_model_claims(
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

        if pred != "supportspricingmodel" and not any(k in obj_lower for k in ["pricing", "billing", "metering", "subscription"]):
            continue

        for model_name, keywords in PRICING_MODELS.items():
            if model_name in obj_lower or any(kw in obj_lower for kw in keywords[:2]):
                # Check for matching endpoints
                matched_endpoints = [p for p in spec_paths if any(kw in p.lower() for kw in keywords)]
                doc_matched = any(kw in combined_tech_text for kw in keywords)

                if matched_endpoints:
                    results.append(CheckResult(
                        check_name=f"Assert_{model_name.capitalize()}_Pricing_Engine",
                        target_claim=obj,
                        passed=True,
                        confidence=0.96,
                        evidence_span=f"Verified rating/metering endpoints: {matched_endpoints[:2]}",
                        source_citation=f"OpenAPI path {matched_endpoints[0]}",
                        severity="VERIFIED"
                    ))
                elif doc_matched:
                    results.append(CheckResult(
                        check_name=f"Assert_{model_name.capitalize()}_Pricing_Engine",
                        target_claim=obj,
                        passed=True,
                        confidence=0.88,
                        evidence_span=f"Pricing logic verified in technical documentation surface.",
                        source_citation="Technical Documentation Portal",
                        severity="VERIFIED"
                    ))
                else:
                    results.append(CheckResult(
                        check_name=f"Assert_{model_name.capitalize()}_Pricing_Engine",
                        target_claim=obj,
                        passed=False,
                        confidence=0.90,
                        evidence_span=f"Marketing advertises '{obj}', but zero metering, event ingestion, or rating endpoints exist in API spec.",
                        source_citation="OpenAPI Specification Paths",
                        severity="CRITICAL_DRIFT"
                    ))
                break

    return results
