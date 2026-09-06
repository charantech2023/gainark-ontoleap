"""
Unified Check Runner
Executes all compliance, integration, and pricing assertions against marketing claims.
"""
from typing import List, Dict, Any, Optional
from .check_compliance import check_compliance_claims, CheckResult
from .check_integrations import check_integration_claims
from .check_pricing_models import check_pricing_model_claims

def run_executable_checks(
    marketing_claims: List[Dict[str, Any]],
    openapi_spec: Optional[Dict[str, Any]] = None,
    docs_text: Optional[str] = None
) -> List[CheckResult]:
    """
    Runs full suite of executable assertions across all marketing claims.
    """
    results: List[CheckResult] = []
    
    # 1. Compliance assertions
    results.extend(check_compliance_claims(marketing_claims, openapi_spec, docs_text))
    
    # 2. Integration assertions
    results.extend(check_integration_claims(marketing_claims, openapi_spec, docs_text))
    
    # 3. Pricing model assertions
    results.extend(check_pricing_model_claims(marketing_claims, openapi_spec, docs_text))
    
    return results
