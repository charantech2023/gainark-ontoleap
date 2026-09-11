"""
End-to-end API tests for the Pure Knowledge Graph & Ontology Engine (api.py).
"""

from fastapi.testclient import TestClient
from api import app

client = TestClient(app)


def test_api():
    print("[1] Testing GET /dashboard and GET /api/info ...")
    r_dash = client.get("/dashboard")
    assert r_dash.status_code == 200, f"Expected 200, got {r_dash.status_code}"
    assert "GainARK OntoLeap" in r_dash.text
    assert "Knowledge Graph" in r_dash.text
    print("  Dashboard Status: 200 OK (Clean KG Workbench served)")

    r_info = client.get("/api/info")
    assert r_info.status_code == 200, f"Expected 200, got {r_info.status_code}"
    info_data = r_info.json()
    assert info_data["version"] == "3.0.0"
    assert info_data["features"]["page_knowledge_graph_extraction"] is True
    assert info_data["features"]["industry_ontology_alignment"] is True
    print("  API Info Status : 200 OK (v3.0.0 pure KG manifest)")

    print("\n[2] Testing GET /api/kg/industries ...")
    r_ind = client.get("/api/kg/industries")
    assert r_ind.status_code == 200
    industries = r_ind.json()
    print(f"  Available Industries ({len(industries)}): {[i['vertical_id'] for i in industries]}")
    # Only verticals with a concept layer are listed; see test_kg_engine for why a
    # count of four stopped being the right assertion.
    assert industries, "No vertical carries a concept layer."
    assert all(i["concepts"] > 0 for i in industries)

    print("\n[3] Testing GET /api/kg/industry/b2b_saas_fintech ...")
    r_onto = client.get("/api/kg/industry/b2b_saas_fintech")
    assert r_onto.status_code == 200
    onto_data = r_onto.json()
    print("  Vertical Name   :", onto_data["display_name"])
    print("  Hierarchy Count :", len(onto_data["concepts"]))
    print("  Known Standards :", len(onto_data["known_compliance"]))
    assert len(onto_data["concepts"]) > 10

    print("\n[4] Testing GET /api/ontology/schema ...")
    r_schema = client.get("/api/ontology/schema")
    assert r_schema.status_code == 200
    schema_data = r_schema.json()
    print("  Schema Relations:", schema_data["relations_count"])
    assert schema_data["relations_count"] > 0

    print("\n[5] Testing POST /api/kg/page ...")
    sample_html = """
    <html>
    <head><title>Ordway Labs — Cloud Billing</title></head>
    <body>
        <h1>Ordway Billing Platform</h1>
        <p>Ordway automates revenue recognition and complies with ASC 606 and SOC 2. Integrates with Salesforce and Stripe.</p>
    </body>
    </html>
    """
    r_page = client.post(
        "/api/kg/page",
        json={"url_or_html": sample_html, "url": "https://www.ordwaylabs.com", "vertical_id": "b2b_saas_fintech"}
    )
    assert r_page.status_code == 200, f"Error: {r_page.text}"
    page_data = r_page.json()
    print("  Extracted Nodes :", len(page_data["nodes"]))
    print("  Extracted Edges :", len(page_data["edges"]))
    print("  JSON-LD Length  :", len(page_data.get("json_ld", "")))
    print("  Turtle Length   :", len(page_data.get("turtle", "")))
    assert len(page_data["nodes"]) > 0
    assert len(page_data["edges"]) > 0

    print("\n[6] Testing POST /api/kg/align ...")
    r_align = client.post(
        "/api/kg/align",
        json={"page_kg": page_data, "vertical_id": "b2b_saas_fintech"}
    )
    assert r_align.status_code == 200, f"Error: {r_align.text}"
    align_data = r_align.json()
    print("  Coverage Score  :", f"{align_data['coverage_score']}%")
    print("  Covered Concepts:", len(align_data["covered_concepts"]))
    print("  Whitespace      :", len(align_data["category_whitespace"]))
    assert len(align_data["covered_concepts"]) > 0

    print("\n[7] Testing GET /api/health ...")
    r_health = client.get("/api/health")
    assert r_health.status_code == 200
    assert r_health.json()["status"] in ("healthy", "ok")
    print("  Health Status   : 200 OK")

    print("\n=======================================================")
    print("ALL API ENDPOINT INTEGRATION TESTS PASSED SUCCESSFULLY!")
    print("=======================================================")


if __name__ == "__main__":
    test_api()
