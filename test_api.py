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

    print("\nAll API tests PASSED successfully!")

if __name__ == "__main__":
    test_api()
