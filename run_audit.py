"""
GainARK OntoLeap — Generic Brand Audit Runner
Usage:
    python run_audit.py <marketing_url> [support_or_docs_url] [--fresh]

Examples:
    python run_audit.py https://www.chargebee.com
    python run_audit.py https://www.maxio.com https://support.maxio.com
    python run_audit.py https://www.zuora.com --fresh

Flags:
    --fresh   Force a live crawl even if a cached fixture exists

Results are saved to fixtures/<brand>_fixture.json and reused on future runs.
"""

import json
import os
import re
import sys
from urllib.parse import urlparse

from fastapi.testclient import TestClient
from api import app

client = TestClient(app)

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
os.makedirs(FIXTURE_DIR, exist_ok=True)


def slug(url: str) -> str:
    """Turn a URL into a safe filename stem."""
    host = urlparse(url).netloc or url
    host = host.lstrip("www.")
    return re.sub(r"[^a-z0-9]+", "_", host.lower()).strip("_")


def run_audit(marketing_url: str, docs_url: str = None, fresh: bool = False):
    brand_slug = slug(marketing_url)
    fixture_path = os.path.join(FIXTURE_DIR, f"{brand_slug}_fixture.json")

    if not fresh and os.path.exists(fixture_path):
        print(f"\n[FIXTURE] Loading cached result for {brand_slug}")
        print(f"          (run with --fresh to crawl again)\n")
        with open(fixture_path, encoding="utf-8") as f:
            data = json.load(f)
    else:
        print(f"\n[LIVE CRAWL] Auditing {marketing_url} ...")
        if docs_url:
            print(f"             Support/Docs: {docs_url}")
        print()

        req_body = {
            "marketing_url": marketing_url,
            "vertical_id": "b2b_saas_fintech",
        }
        if docs_url:
            req_body["tech_docs_url"] = docs_url

        resp = client.post("/api/product-truth", json=req_body)
        if resp.status_code != 200:
            print(f"ERROR {resp.status_code}: {resp.text[:500]}")
            sys.exit(1)

        data = resp.json()
        with open(fixture_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"[SAVED] {fixture_path}\n")

    # ── Print results ──────────────────────────────────────────────
    mgi = data.get("marketing_grounding_index", 0)
    alerts = data.get("drift_alerts", [])

    print("=" * 65)
    print(f"  Brand              : {data.get('brand_name', brand_slug)}")
    print(f"  MGI Score          : {mgi}%")
    print(f"  Marketing Claims   : {data.get('total_marketing_claims', '?')}")
    print(f"  Tech Capabilities  : {data.get('total_technical_capabilities', '?')}")
    print(f"  Verified Claims    : {data.get('verified_claims_count', '?')}")
    print(f"  Unbacked Claims    : {data.get('unbacked_claims_count', '?')}")
    print(f"  Hidden Capabilities: {data.get('hidden_capabilities_count', '?')}")
    print("=" * 65)
    print(f"\n  Summary: {data.get('executive_summary', '')[:200]}\n")

    if alerts:
        print(f"  Drift Alerts ({len(alerts)}):")
        for a in alerts:
            print("    * " + a[:140].encode("ascii", "replace").decode("ascii"))
    else:
        print("  No drift alerts — all marketing claims verified.")

    print()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]

    if not args:
        print(__doc__)
        sys.exit(0)

    marketing_url = args[0]
    docs_url = args[1] if len(args) > 1 else None
    fresh = "--fresh" in flags

    run_audit(marketing_url, docs_url=docs_url, fresh=fresh)
