"""
Test suite for Autonomous Industry Profiler (Step 1 of Tri-Ontology Architecture)
Verifies zero-shot discovery across different B2B domains.
"""

import os
from fastapi.testclient import TestClient
from api import app, get_pipeline

client = TestClient(app)

def test_industry_discovery_snyk():
    print("\n[1] Testing POST /api/discover-industry for Snyk (Cybersecurity / DevSecOps) ...")
    payload = {
        "url": "https://snyk.io",
        "brand_hint": "Snyk"
    }
    resp = client.post("/api/discover-industry", json=payload)
    assert resp.status_code == 200, f"Failed: {resp.status_code} - {resp.text}"
    data = resp.json()
    
    print(f"  Brand Name     : {data['brand_name']}")
    print(f"  Vertical ID    : {data['vertical_id']}")
    print(f"  Display Name   : {data['display_name']}")
    print(f"  Category       : {data['category']}")
    print(f"  Seed Concepts  : {data['core_seed_concepts'][:4]} ... ({len(data['core_seed_concepts'])} total)")
    print(f"  Compliance     : {data['known_compliance']}")
    print(f"  Integrations   : {data['known_integrations'][:4]} ... ({len(data['known_integrations'])} total)")
    print(f"  Competitors    : {data['suggested_competitors']}")
    print(f"  Grounded Q-IDs : {[g['name'] + ' -> ' + str(g['wikidata_id']) for g in data['grounded_entities'] if g.get('wikidata_id')]}")
    print(f"  Config File    : {data['config_file']}")

    assert data["brand_name"].lower() == "snyk"
    assert len(data["core_seed_concepts"]) >= 3
    assert len(data["known_compliance"]) >= 2
    assert len(data["known_integrations"]) >= 2
    assert data["config_file"] and os.path.exists(data["config_file"])
    
    vertical_id = data["vertical_id"]

    print("\n[2] Verifying discovered vertical is now listed in GET /api/verticals ...")
    r_vert = client.get("/api/verticals")
    assert r_vert.status_code == 200
    v_ids = [v["vertical_id"] for v in r_vert.json()["verticals"]]
    print(f"  Total Verticals in Registry: {len(v_ids)}")
    assert vertical_id in v_ids

    print("\n[3] Verifying OntologyPipeline can load the discovered vertical config ...")
    pipe = get_pipeline(vertical_id)
    assert pipe is not None
    assert pipe.config.vertical_id == vertical_id
    print(f"  Successfully loaded OntologyPipeline for vertical '{vertical_id}'!")
    print(f"  Pipeline mandatory schemas: {pipe.config.mandatory_schema_types}")
    print(f"  Pipeline core seed count  : {len(pipe.config.core_seed_concepts)}")

def test_industry_discovery_gusto():
    print("\n[4] Testing POST /api/discover-industry for Gusto (HR & Payroll) ...")
    payload = {
        "url": "https://gusto.com",
        "brand_hint": "Gusto"
    }
    resp = client.post("/api/discover-industry", json=payload)
    assert resp.status_code == 200, f"Failed: {resp.status_code} - {resp.text}"
    data = resp.json()
    
    print(f"  Brand Name     : {data['brand_name']}")
    print(f"  Vertical ID    : {data['vertical_id']}")
    print(f"  Display Name   : {data['display_name']}")
    print(f"  Category       : {data['category']}")
    print(f"  Seed Concepts  : {data['core_seed_concepts'][:4]} ...")
    print(f"  Competitors    : {data['suggested_competitors']}")
    assert "gusto" in data["brand_name"].lower()
    assert len(data["core_seed_concepts"]) >= 3

if __name__ == "__main__":
    test_industry_discovery_snyk()
    test_industry_discovery_gusto()
    print("\nAll Industry Profiler tests PASSED successfully!")
