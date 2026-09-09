#!/usr/bin/env python3
"""
Quick test: runs the Product Truth Audit on Ordway Labs.
Run with:  venv\Scripts\python.exe test_audit_ordway.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pipeline import OntologyPipeline
from product_truth import execute_product_truth_audit
from models import ProductTruthRequest

print("=" * 62)
print("  OntoLeap — Product Truth Audit Test")
print("  Target: Ordway Labs")
print("=" * 62)

pipeline = OntologyPipeline()

req = ProductTruthRequest(
    marketing_url="https://www.ordwaylabs.com",
    tech_docs_url="https://support.ordwaylabs.com"
)

print(f"\nMarketing URL : {req.marketing_url}")
print(f"Docs URL      : {req.tech_docs_url}")
print("\nRunning audit... (cached — should be fast)\n")

result = execute_product_truth_audit(req, pipeline)

print("\n" + "=" * 62)
print(f"  MGI Score     : {result.marketing_grounding_index}")
print(f"  Verified      : {result.verified_claims_count} claims confirmed")
print(f"  Drift alerts  : {len(result.drift_alerts)}")
print("=" * 62)

if result.drift_alerts:
    print("\nDRIFT ALERTS:")
    for i, alert in enumerate(result.drift_alerts, 1):
        if isinstance(alert, str):
            print(f"\n  [{i}] {alert}")
        else:
            claim = getattr(alert, 'claim', str(alert))
            status = getattr(alert, 'evidence_status', 'unknown')
            severity = getattr(alert, 'severity', '?')
            print(f"\n  [{i}] [{severity.upper()}] {claim}")
            print(f"       Evidence: {status}")
else:
    print("\nNo drift alerts — clean audit!")

print(f"\nDone.\n")
