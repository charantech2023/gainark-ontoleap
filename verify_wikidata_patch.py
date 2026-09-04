import requests
import json

print("[1] Checking GET /dashboard ...")
r_dash = requests.get("http://localhost:8080/dashboard")
print("Dashboard status:", r_dash.status_code)
assert "Vertical: B2B SaaS &amp; FinTech (Auto-Detected)" in r_dash.text
assert "audit-vertical" not in r_dash.text
print("Dashboard UI checks PASSED! Auto-detected badge present and vertical dropdown removed.")

print("\n[2] Testing POST /api/audit on https://www.ordwaylabs.com ...")
r_audit = requests.post("http://localhost:8080/api/audit", json={"url": "https://www.ordwaylabs.com"})
print("Audit status:", r_audit.status_code)
assert r_audit.status_code == 200
data = r_audit.json()

patch = data.get("recommended_patch", {})
graph = patch.get("patch_dict", {}).get("@graph", [])
print("Graph nodes count:", len(graph))

software_node = next((n for n in graph if n.get("@type") == "SoftwareApplication"), None)
assert software_node is not None
print("SoftwareApplication name:", software_node.get("name"))
print("SoftwareApplication about count:", len(software_node.get("about", [])))
print("SoftwareApplication about entities:")
for a in software_node.get("about", []):
    print(f"  - [{a.get('@type')}] {a.get('name')} -> {a.get('sameAs')}")

print("\nFormatted JSON-LD snippet preview:")
print(patch.get("json_ld", "")[:700])
print("...")
print("\nAll verification checks PASSED!")
