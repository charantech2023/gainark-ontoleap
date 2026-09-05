"""
Test suite for Step 3: Tri-Ontology Competitive Alignment Engine
Cross-examines Company Product Truth vs Competitor Footprints against Industry Ontology.
"""

from fastapi.testclient import TestClient
from api import app
from competitive_alignment import align_tri_ontologies
from models import (
    SemanticTriple,
    ProductTruthMatrixResponse
)

client = TestClient(app)

SAMPLE_OPENAPI_SPEC = {
    "openapi": "3.0.1",
    "info": {"title": "Ordway Labs Core API", "version": "v1.0"},
    "components": {
        "securitySchemes": {
            "oauth2": {"type": "oauth2", "description": "OAuth 2.0 Auth"}
        }
    },
    "paths": {
        "/v1/revenue_schedules": {
            "get": {
                "summary": "Real-time ASC 606 revenue recognition schedules",
                "tags": ["Revenue Recognition"]
            }
        },
        "/v1/integrations/netsuite/sync": {
            "post": {
                "summary": "Native bi-directional NetSuite ERP sync",
                "tags": ["NetSuite ERP Sync"]
            }
        }
    }
}


class MockVerticalConfig:
    display_name = "B2B SaaS Fintech & Revenue Automation"
    core_seed_concepts = ["ASC 606", "Revenue Recognition", "Subscription Management", "ERP Integration"]


def test_align_tri_ontologies_logic():
    print("\n[1] Testing Tri-Ontology comparative alignment differential logic ...")
    company_matrix = ProductTruthMatrixResponse(
        brand_name="Ordway",
        marketing_url="https://www.ordwaylabs.com",
        marketing_grounding_index=85.0,
        total_marketing_claims=2,
        total_technical_capabilities=2,
        verified_claims_count=2,
        unbacked_claims_count=0,
        hidden_capabilities_count=0,
        verified_triples=[
            SemanticTriple(
                subject="Ordway",
                predicate="automates",
                object="Revenue Recognition",
                confidence=0.95,
                evidence_sentence="GET /v1/revenue_schedules provides real-time rev rec",
                source_type="verified_truth",
                provenance="ordway_api.json#/v1/revenue_schedules"
            ),
            SemanticTriple(
                subject="Ordway",
                predicate="integratesWith",
                object="NetSuite",
                confidence=0.94,
                evidence_sentence="POST /v1/integrations/netsuite/sync bi-directional connector",
                source_type="verified_truth",
                provenance="ordway_api.json#/v1/integrations/netsuite"
            )
        ],
        executive_summary="Ordway has high grounding."
    )

    competitor_matrix = ProductTruthMatrixResponse(
        brand_name="CompetitorX",
        marketing_url="https://www.competitorx.com",
        marketing_grounding_index=33.3,
        total_marketing_claims=3,
        total_technical_capabilities=1,
        verified_claims_count=1,
        unbacked_claims_count=2,
        hidden_capabilities_count=0,
        verified_triples=[
            SemanticTriple(
                subject="CompetitorX",
                predicate="supportsPricingModel",
                object="Tiered Pricing",
                confidence=0.90,
                source_type="verified_truth"
            )
        ],
        unbacked_claims=[
            SemanticTriple(
                subject="CompetitorX",
                predicate="automates",
                object="Revenue Recognition",
                confidence=0.85,
                evidence_sentence="Marketing claims full ASC 606 automation",
                source_type="unbacked_marketing_claim"
            ),
            SemanticTriple(
                subject="CompetitorX",
                predicate="integratesWith",
                object="NetSuite",
                confidence=0.82,
                evidence_sentence="Marketing claims seamless NetSuite integration",
                source_type="unbacked_marketing_claim"
            )
        ],
        executive_summary="CompetitorX has significant marketing drift."
    )

    res = align_tri_ontologies(
        company_matrix=company_matrix,
        competitor_matrices=[competitor_matrix],
        vertical_config=MockVerticalConfig(),
        vertical_id="b2b_saas_fintech"
    )

    print(f"  Company Name          : {res.company_name}")
    print(f"  Competitors           : {res.competitor_names}")
    print(f"  Industry Category     : {res.industry_category}")
    print(f"  Company Advantages    : {len(res.company_advantages)}")
    for a in res.company_advantages:
        print(f"    * [{a.predicate}] {a.concept}: {a.insight}")
    print(f"  Comp Vulnerabilities  : {len(res.competitor_vulnerabilities)}")
    for v in res.competitor_vulnerabilities:
        print(f"    * [{v.predicate}] {v.concept} ({v.competitor_name}): {v.insight}")
    print(f"  Table Stakes          : {res.table_stakes}")
    print(f"  Counter-Position Briefs: {len(res.counter_positioning_briefs)}")
    for b in res.counter_positioning_briefs:
        print(f"    -> Title: '{b.angle_title}' against {b.target_competitor}")
        print(f"       Narrative: {b.core_narrative}")

    assert len(res.company_advantages) >= 2
    assert len(res.competitor_vulnerabilities) >= 2
    adv_concepts = [a.concept.lower() for a in res.company_advantages]
    assert any("revenue recognition" in c for c in adv_concepts)
    assert any("netsuite" in c for c in adv_concepts)
    assert len(res.table_stakes) >= 1


def test_api_tri_ontology_endpoint():
    print("\n[2] Testing POST /api/tri-ontology-align (Live Tri-Ontology API) ...")
    payload = {
        "company": {
            "marketing_url": "https://www.ordwaylabs.com",
            "brand_name": "Ordway",
            "openapi_spec": SAMPLE_OPENAPI_SPEC
        },
        "competitors": [
            {
                "marketing_url": "https://www.chargebee.com",
                "brand_name": "Chargebee"
            }
        ],
        "vertical_id": "b2b_saas_fintech"
    }
    resp = client.post("/api/tri-ontology-align", json=payload)
    assert resp.status_code == 200, f"Error: {resp.status_code} - {resp.text}"
    data = resp.json()

    print(f"  Company Name          : {data['company_name']}")
    print(f"  Competitor Names      : {data['competitor_names']}")
    print(f"  Industry Category     : {data['industry_category']}")
    print(f"  Company Advantages    : {len(data['company_advantages'])}")
    print(f"  Comp Vulnerabilities  : {len(data['competitor_vulnerabilities'])}")
    print(f"  Table Stakes Count    : {len(data['table_stakes'])}")
    print(f"  Executive Summary     : {data['executive_summary']}")

    assert data["company_name"] == "Ordway"
    assert "Chargebee" in data["competitor_names"]
    assert "Tri-Ontology Alignment" in data["executive_summary"]


if __name__ == "__main__":
    test_align_tri_ontologies_logic()
    test_api_tri_ontology_endpoint()
    print("\nAll Tri-Ontology Competitive Alignment tests PASSED successfully!")
