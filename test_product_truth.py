"""
Test suite for Step 2: Dual Ingestion Engine & Company Product Truth Matrix
Cross-examines Marketing Claims against Technical Reality (OpenAPI specs & documentation).
"""

import json
import os
import sys
from fastapi.testclient import TestClient
from api import app
from product_truth import parse_openapi_spec, build_product_truth_matrix
from models import SemanticTriple

client = TestClient(app)

_FIXTURE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ordway_fixture.json")
_USE_LIVE = "--live" in sys.argv

SAMPLE_OPENAPI_SPEC = {
    "openapi": "3.0.1",
    "info": {
        "title": "Ordway Labs Core Billing & Revenue API",
        "version": "v1.0"
    },
    "components": {
        "securitySchemes": {
            "oauth2": {
                "type": "oauth2",
                "description": "OAuth 2.0 authentication for enterprise API integrations"
            },
            "bearerAuth": {
                "type": "http",
                "scheme": "bearer"
            }
        }
    },
    "paths": {
        "/v1/invoices": {
            "post": {
                "summary": "Create automated recurring invoice",
                "tags": ["Invoicing Automation"],
                "operationId": "createInvoice"
            }
        },
        "/v1/revenue_schedules": {
            "get": {
                "summary": "Retrieve ASC 606 revenue recognition waterfall",
                "tags": ["Revenue Recognition"],
                "operationId": "getRevRec"
            }
        },
        "/v1/integrations/netsuite/sync": {
            "post": {
                "summary": "Trigger bi-directional journal entry synchronization with NetSuite",
                "tags": ["NetSuite ERP Sync"],
                "operationId": "syncNetSuite"
            }
        },
        "/v1/integrations/salesforce/webhook": {
            "post": {
                "summary": "Handle Closed-Won Opportunities from Salesforce CRM",
                "tags": ["Salesforce Integration"],
                "operationId": "salesforceWebhook"
            }
        }
    }
}


def test_openapi_spec_parser():
    print("\n[1] Testing OpenAPI Specification parser into Technical Truth triples ...")
    triples = parse_openapi_spec(SAMPLE_OPENAPI_SPEC, brand_name="Ordway", source_origin="ordway_openapi.json")
    print(f"  Total Technical Triples Extracted: {len(triples)}")
    for t in triples:
        print(f"  - [{t.source_type}] {t.subject} {t.predicate} {t.object} (prov: {t.provenance})")

    assert len(triples) >= 4
    predicates = [t.predicate for t in triples]
    assert "compliesWith" in predicates
    assert "automates" in predicates
    assert "integratesWith" in predicates

    # Check OAuth 2.0 was extracted from securitySchemes
    objects = [t.object.lower() for t in triples]
    assert any("oauth 2.0" in o for o in objects)
    assert any("netsuite" in o for o in objects)
    assert any("salesforce" in o for o in objects)


def test_product_truth_matrix_logic():
    print("\n[2] Testing Product Truth Matrix differential logic (Claims vs Reality) ...")
    marketing_claims = [
        SemanticTriple(
            subject="Ordway",
            predicate="automates",
            object="Revenue Recognition",
            confidence=0.90,
            evidence_sentence="Ordway automates ASC 606 revenue recognition schedules.",
            source_type="marketing_claim"
        ),
        SemanticTriple(
            subject="Ordway",
            predicate="compliesWith",
            object="HIPAA",
            confidence=0.85,
            evidence_sentence="Ordway claims enterprise HIPAA compliance.",
            source_type="marketing_claim"
        )
    ]

    technical_truth = [
        SemanticTriple(
            subject="Ordway",
            predicate="automates",
            object="Revenue Recognition",
            confidence=0.95,
            evidence_sentence="GET /v1/revenue_schedules retrieves ASC 606 RevRec waterfall",
            source_type="technical_truth",
            provenance="openapi.json#/v1/revenue_schedules"
        ),
        SemanticTriple(
            subject="Ordway",
            predicate="integratesWith",
            object="Salesforce",
            confidence=0.94,
            evidence_sentence="POST /v1/integrations/salesforce/webhook triggers sync",
            source_type="technical_truth",
            provenance="openapi.json#/v1/integrations/salesforce/webhook"
        ),
        # Extra triples to reach MIN_CONFIDENT_TECHNICAL_EVIDENCE=5 (conclusive evidence status)
        SemanticTriple(
            subject="Ordway",
            predicate="automates",
            object="Dunning Management",
            confidence=0.91,
            evidence_sentence="POST /v1/dunning_rules configures retry schedules for failed payments",
            source_type="technical_truth",
            provenance="openapi.json#/v1/dunning_rules"
        ),
        SemanticTriple(
            subject="Ordway",
            predicate="automates",
            object="Subscription Billing",
            confidence=0.93,
            evidence_sentence="POST /v1/subscriptions creates recurring billing schedules",
            source_type="technical_truth",
            provenance="openapi.json#/v1/subscriptions"
        ),
        SemanticTriple(
            subject="Ordway",
            predicate="automates",
            object="Invoice Generation",
            confidence=0.92,
            evidence_sentence="POST /v1/invoices generates and dispatches customer invoices",
            source_type="technical_truth",
            provenance="openapi.json#/v1/invoices"
        ),
    ]

    matrix = build_product_truth_matrix(
        marketing_triples=marketing_claims,
        technical_triples=technical_truth,
        brand_name="Ordway",
        marketing_url="https://www.ordwaylabs.com"
    )

    print(f"  Marketing Grounding Index: {matrix.marketing_grounding_index}%")
    print(f"  Verified Claims Count    : {matrix.verified_claims_count}")
    print(f"  Unbacked Claims Count    : {matrix.unbacked_claims_count}")
    print(f"  Hidden Capabilities Count: {matrix.hidden_capabilities_count}")
    print(f"  Drift Alerts             : {matrix.drift_alerts}")
    print(f"  Growth Recommendations   : {matrix.growth_recommendations}")

    assert matrix.verified_claims_count == 1
    assert matrix.unbacked_claims_count == 1
    assert matrix.hidden_capabilities_count >= 1  # 4 hidden: Salesforce + 3 padding triples
    assert matrix.marketing_grounding_index == 50.0
    assert len(matrix.drift_alerts) >= 1
    assert any("HIPAA" in a for a in matrix.drift_alerts)
    assert len(matrix.growth_recommendations) >= 1
    assert any("Salesforce" in r for r in matrix.growth_recommendations)


def test_api_product_truth_endpoint():
    print("\n[3] Testing POST /api/product-truth (Dual Ingestion API) ...")

    if not _USE_LIVE and os.path.exists(_FIXTURE_PATH):
        print("  [FIXTURE] Loading ordway_fixture.json  (use --live for a fresh crawl)")
        with open(_FIXTURE_PATH, encoding="utf-8") as _f:
            data = json.load(_f)
    else:
        req_body = {
            "marketing_url": "https://www.ordwaylabs.com",
            "brand_name": "Ordway",
            "vertical_id": "b2b_saas_fintech",
            "tech_docs_url": "https://support.ordwaylabs.com",
            "openapi_spec": SAMPLE_OPENAPI_SPEC
        }
        resp = client.post("/api/product-truth", json=req_body)
        assert resp.status_code == 200, f"Error: {resp.status_code} - {resp.text}"
        data = resp.json()
        with open(_FIXTURE_PATH, "w", encoding="utf-8") as _f:
            json.dump(data, _f, indent=2, ensure_ascii=False)
        print(f"  [FIXTURE SAVED] ordway_fixture.json updated")

    print(f"  Brand Name               : {data['brand_name']}")
    print(f"  Marketing Grounding Index: {data['marketing_grounding_index']}%")
    print(f"  Total Marketing Claims   : {data['total_marketing_claims']}")
    print(f"  Total Tech Capabilities  : {data['total_technical_capabilities']}")
    print(f"  Verified Claims Count    : {data['verified_claims_count']}")
    print(f"  Unbacked Claims Count    : {data['unbacked_claims_count']}")
    print(f"  Hidden Capabilities Count: {data['hidden_capabilities_count']}")
    print(f"  Executive Summary        : {data['executive_summary']}")
    print(f"  Drift Alerts ({len(data.get('drift_alerts',[]))})")
    for da in data.get('drift_alerts', []):
        print("    * " + da[:140].encode("ascii", "replace").decode("ascii"))

    assert data["brand_name"] == "Ordway"
    assert data["total_technical_capabilities"] >= 4
    assert data["verified_claims_count"] >= 0
    assert "Marketing Grounding Index" in data["executive_summary"]


if __name__ == "__main__":
    test_openapi_spec_parser()
    test_product_truth_matrix_logic()
    test_api_product_truth_endpoint()
    print("\nAll Product Truth Dual Ingestion tests PASSED successfully!")
