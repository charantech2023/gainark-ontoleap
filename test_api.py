from fastapi.testclient import TestClient
from api import app

client = TestClient(app)

def test_api():
    print("[1] Testing GET /dashboard and GET /api/info ...")
    r_dash = client.get("/dashboard")
    assert r_dash.status_code == 200, f"Expected 200, got {r_dash.status_code}"
    assert "GainARK OntoLeap" in r_dash.text
    print("  Dashboard Status: 200 OK (HTML served)")

    r_info = client.get("/api/info")
    assert r_info.status_code == 200, f"Expected 200, got {r_info.status_code}"
    print("  API Info Status : 200", r_info.json())

    print("\n[2] Testing POST /api/audit ...")
    r_audit = client.post("/api/audit", json={"url": "https://www.ordwaylabs.com"})
    assert r_audit.status_code == 200, f"Expected 200, got {r_audit.status_code}: {r_audit.text}"
    data = r_audit.json()
    print("  Audited URL     :", data["url"])
    print("  Readiness Score :", data["readiness_score"])
    print("  Mandatory Schema:", data["mandatory_schema_status"])
    print("  Entity Labels   :", list(data["entity_breakdown"].keys()))
    print("  Patch Available :", data["recommended_patch"] is not None)
    if data["recommended_patch"]:
        print("  Patch Schemas   :", data["recommended_patch"].get("schemas_added"))

    print("\n[3] Testing POST /api/benchmark ...")
    bench_payload = {
        "urls": [
            "https://www.chargebee.com",
            "https://www.maxio.com"
        ]
    }
    r_bench = client.post("/api/benchmark", json=bench_payload)
    assert r_bench.status_code == 200, f"Expected 200, got {r_bench.status_code}: {r_bench.text}"
    bdata = r_bench.json()
    print("  Vertical       :", bdata["vertical_id"])
    print("  Total Analyzed :", bdata["total_analyzed"])
    for row in bdata["comparative_table"]:
        print(f"  - {row['url']}: {row['readiness_score']:.2f} | Schemas: {row['compliance_str']} | Entities: {row['entity_count']}")

    print("\n[4] Testing GET /api/verticals ...")
    r_vert = client.get("/api/verticals")
    assert r_vert.status_code == 200
    vids = [v["vertical_id"] for v in r_vert.json()["verticals"]]
    print("  Available Verticals:", vids)
    assert "b2b_saas_fintech" in vids
    assert "cybersecurity" in vids
    assert "healthtech" in vids
    assert "developer_tools" in vids

    print("\n[5] Testing POST /api/check-draft (PAS Scoring & Fluff Detection) ...")
    draft_req = {
        "draft_text": "Ordway is an automated billing platform that automates Revenue Recognition under ASC 606 and integrates with NetSuite.",
        "brand_name": "Ordway",
        "vertical_id": "b2b_saas_fintech",
        "triples": [
            {"subject": "Ordway", "predicate": "automates", "object": "Revenue Recognition"},
            {"subject": "Ordway", "predicate": "compliesWith", "object": "ASC 606"},
            {"subject": "Ordway", "predicate": "integratesWith", "object": "NetSuite"}
        ]
    }
    r_draft = client.post("/api/check-draft", json=draft_req)
    assert r_draft.status_code == 200, f"Error: {r_draft.text}"
    d_res = r_draft.json()
    print("  Draft PAS Score :", d_res["product_alignment_score"])
    print("  Draft Verdict   :", d_res["verdict"])
    print("  Grounded Triples:", d_res["grounded_triples_count"])

    print("\n[6] Testing POST /api/content-brief (Product Truth Brief) ...")
    brief_req = {
        "topic": "Usage-Based Invoicing & RevRec",
        "brand_name": "Ordway",
        "vertical_id": "b2b_saas_fintech",
        "triples": [
            {"subject": "Ordway", "predicate": "automates", "object": "Revenue Recognition"}
        ]
    }
    r_brief = client.post("/api/content-brief", json=brief_req)
    assert r_brief.status_code == 200, f"Error: {r_brief.text}"
    b_res = r_brief.json()
    print("  Brief Target PAS:", b_res["target_alignment_score"])
    print("  Must Include Ent:", b_res["must_include_entities"])

    print("\n[7] Testing POST /api/export-pdf (1-Click Executive PDF) ...")
    pdf_req = {
        "url": "https://www.ordwaylabs.com",
        "vertical_id": "b2b_saas_fintech",
        "readiness_score": 78.5,
        "mandatory_schema_status": {"SoftwareApplication": True, "Organization": True, "Offer": False},
        "triples": [
            {"subject": "Ordway", "predicate": "automates", "object": "Revenue Recognition"}
        ]
    }
    r_pdf = client.post("/api/export-pdf", json=pdf_req)
    assert r_pdf.status_code == 200, f"Error: {r_pdf.text}"
    assert r_pdf.headers["content-type"] == "application/pdf"
    assert r_pdf.content.startswith(b"%PDF-")
    print("  PDF Generated   : 200 OK (Bytes:", len(r_pdf.content), ")")

    print("\n[8] Testing POST /api/simulate-search (Google Gemini 2.5 Flash Grounded) ...")
    search_req = {
        "query": "Which platform automates ASC 606 revenue recognition?",
        "root_domain": "ordwaylabs.com",
        "triples": [
            {"subject": "Ordway", "predicate": "automates", "object": "Revenue Recognition"},
            {"subject": "Ordway", "predicate": "compliesWith", "object": "ASC 606"}
        ]
    }
    r_sim = client.post("/api/simulate-search", json=search_req)
    assert r_sim.status_code == 200, f"Error: {r_sim.text}"
    s_res = r_sim.json()
    print("  Grounding Risk  :", s_res.get("hallucination_risk"))
    print("  Synthesized Ans :", s_res.get("synthesized_answer")[:90], "...")

    print("\nAll API tests PASSED successfully!")

if __name__ == "__main__":
    test_api()
