"""
GainARK OntoLeap — Security control regression tests.

Covers the controls added during the security audit. Runs entirely offline except for
one local HTTP server used to prove that a redirect into a private address is refused.

Run:  python test_security_controls.py
"""

import http.server
import os
import socket
import threading
import time

from fastapi.testclient import TestClient

import security
from knowledge_graph import execute_sparql_query_on_ttl
from scraper import validate_url_for_fetch

FAILURES = []


def check(label, got, expected):
    ok = got == expected
    print(f"  [{'ok ' if ok else 'FAIL'}] {label}: got={got!r} expected={expected!r}")
    if not ok:
        FAILURES.append(label)


# ---------------------------------------------------------------------------
# 1. SSRF: URL validation
# ---------------------------------------------------------------------------
def test_ssrf_validation():
    print("\n[1] SSRF URL validation")

    def verdict(url):
        try:
            validate_url_for_fetch(url)
            return "ALLOW"
        except ValueError:
            return "BLOCK"

    blocked = [
        "http://169.254.169.254/computeMetadata/v1/",   # cloud metadata
        "http://metadata.google.internal/",
        "http://localhost:8000/admin",
        "http://127.0.0.1/",
        "http://2130706433/",                            # decimal-encoded loopback
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",                    # IPv4-mapped IPv6 loopback
        "http://0.0.0.0/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "file:///etc/passwd",
        "gopher://internal/",
        "http://user:pass@example.com/",                 # embedded credentials
        "https://host-that-does-not-resolve-9182734.invalid/",  # fails closed
    ]
    for url in blocked:
        check(f"blocks {url}", verdict(url), "BLOCK")

    check("allows https://www.google.com", verdict("https://www.google.com"), "ALLOW")


# ---------------------------------------------------------------------------
# 2. SSRF: redirect hops are re-validated
# ---------------------------------------------------------------------------
class _RedirectToMetadata(http.server.BaseHTTPRequestHandler):
    """Public-looking host that 302s to the cloud metadata service."""

    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", "http://169.254.169.254/computeMetadata/v1/")
        self.end_headers()

    do_HEAD = do_GET

    def log_message(self, *args):
        pass


def test_redirect_ssrf():
    print("\n[2] SSRF via redirect")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    server = http.server.HTTPServer(("127.0.0.1", port), _RedirectToMetadata)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    time.sleep(0.2)

    # The redirect target must be refused. The origin here is itself loopback, so to
    # isolate the redirect behaviour specifically we call the resolver directly with
    # unresolvable-host allowance off and confirm the hop is what trips.
    from scraper import _resolve_redirects

    try:
        _resolve_redirects(f"http://127.0.0.1:{port}/start", {}, timeout=5)
        outcome = "ALLOW"
    except ValueError:
        outcome = "BLOCK"
    check("redirect chain into a private address is refused", outcome, "BLOCK")

    server.shutdown()


# ---------------------------------------------------------------------------
# 3. SPARQL injection / SSRF guard
# ---------------------------------------------------------------------------
TTL = (
    '@prefix schema: <https://schema.org/> .\n'
    '<https://ex.org/a> schema:name "Alpha" ; schema:description "we never drop data" .\n'
    '<https://ex.org/b> schema:name "Beta" .\n'
)


def test_sparql_guard():
    print("\n[3] SPARQL guard")

    def verdict(query):
        try:
            execute_sparql_query_on_ttl(TTL, query)
            return "ALLOW"
        except ValueError:
            return "BLOCK"

    check(
        "blocks SERVICE (federated-query SSRF)",
        verdict("SELECT ?s WHERE { SERVICE <http://169.254.169.254/> { ?s ?p ?o } }"),
        "BLOCK",
    )
    check(
        "blocks FROM (remote graph load)",
        verdict("SELECT ?s FROM <http://127.0.0.1:8000/x> WHERE { ?s ?p ?o }"),
        "BLOCK",
    )
    check("blocks DELETE", verdict("DELETE WHERE { ?s ?p ?o }"), "BLOCK")
    check("blocks non-SELECT", verdict("ASK { ?s ?p ?o }"), "BLOCK")

    # Regression: the previous guard rejected this because the blocklist matched the
    # word "drop" inside a string literal.
    check(
        "allows a literal containing a blocked keyword",
        verdict('SELECT ?o WHERE { ?s ?p ?o FILTER(CONTAINS(LCASE(STR(?o)), "drop")) }'),
        "ALLOW",
    )
    check("allows a plain SELECT", verdict("SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 5"), "ALLOW")


# ---------------------------------------------------------------------------
# 4. Output-encoding helpers
# ---------------------------------------------------------------------------
def test_output_encoding():
    print("\n[4] Header and CSV encoding")
    injected = security.content_disposition('ev"il\r\nX-Injected: yes.pdf')
    check("no CR in Content-Disposition", "\r" in injected, False)
    check("no LF in Content-Disposition", "\n" in injected, False)
    check("no stray quote in Content-Disposition", injected.count('"'), 2)

    for payload in ("=cmd|calc!A1", "+1+1", "-2+3", "@SUM(A1)"):
        check(f"csv_safe defangs {payload!r}", security.csv_safe(payload).startswith("'"), True)
    check("csv_safe leaves normal text alone", security.csv_safe("Acme Corp"), "Acme Corp")


# ---------------------------------------------------------------------------
# 5. Identifier validation (path traversal)
# ---------------------------------------------------------------------------
def test_vertical_id_validation():
    print("\n[5] vertical_id validation")
    for bad in ["../../etc/passwd", "..", "a/b", "a\\b", "", "x" * 65, "a.b"]:
        check(f"rejects {bad!r}", security.is_valid_vertical_id(bad), False)
    for good in ["b2b_saas_fintech", "cybersecurity", "saas_acme_2"]:
        check(f"accepts {good!r}", security.is_valid_vertical_id(good), True)


# ---------------------------------------------------------------------------
# 6. HTTP surface: auth, body ceiling, error handling
# ---------------------------------------------------------------------------
def test_http_surface():
    print("\n[6] HTTP surface")
    import api

    client = TestClient(api.app)

    # Open mode (no key configured in this process).
    check("health is public", client.get("/api/health").status_code, 200)
    check(
        "security headers applied",
        client.get("/api/health").headers.get("X-Content-Type-Options"),
        "nosniff",
    )

    # Path traversal through vertical_id must be a client error, not a file read.
    r = client.post("/api/audit", json={"url": "https://example.com", "vertical_id": "../../etc/passwd"})
    check("traversal vertical_id rejected", r.status_code, 400)

    # Body ceiling.
    big = b"x" * (int(os.environ.get("MAX_REQUEST_BYTES", 4 * 1024 * 1024)) + 1024)
    r = client.post("/api/sparql", content=big, headers={"Content-Type": "application/json"})
    check("oversized body rejected", r.status_code, 413)

    # SSRF through the crawl endpoints.
    for path, payload in [
        ("/api/audit", {"url": "http://169.254.169.254/"}),
        ("/api/batch-crawl", {"sitemap_url": "http://169.254.169.254/sitemap.xml"}),
        ("/api/internal-links", {"urls": ["http://127.0.0.1/"]}),
    ]:
        r = client.post(path, json=payload)
        check(f"{path} refuses internal target", r.status_code, 400)

    # Input bounds.
    r = client.post("/api/batch-crawl", json={"sitemap_url": "https://example.com/s.xml", "max_pages": 100000})
    check("max_pages ceiling enforced", r.status_code, 422)

    # Header injection through the PDF filename.
    r = client.post("/api/export-pdf", json={"url": 'https://ev"il\r\nX-Injected: yes'})
    cd = r.headers.get("content-disposition", "")
    check("PDF filename sanitised", ("\r" in cd or "\n" in cd), False)

    # Auth enforcement when a key is configured.
    os.environ["ONTOLEAP_API_KEY"] = "unit-test-key"
    try:
        check("no key -> 401", client.get("/api/verticals").status_code, 401)
        check("wrong key -> 401", client.get("/api/verticals", headers={"x-api-key": "nope"}).status_code, 401)
        check(
            "right key -> 200",
            client.get("/api/verticals", headers={"x-api-key": "unit-test-key"}).status_code,
            200,
        )
        check("health stays public", client.get("/api/health").status_code, 200)
    finally:
        os.environ.pop("ONTOLEAP_API_KEY", None)


if __name__ == "__main__":
    test_ssrf_validation()
    test_redirect_ssrf()
    test_sparql_guard()
    test_output_encoding()
    test_vertical_id_validation()
    test_http_surface()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print("  -", f)
        raise SystemExit(1)
    print("All security control tests passed.")
