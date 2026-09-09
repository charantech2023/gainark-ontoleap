# OntoLeap — Backlog

Open work from the security audit and the data-source evaluation.
Ordered by priority. Separate from `ANTIGRAVITY_TASKS.md`, which tracks the
accuracy/integrity fixes.

Security fixes from the audit are **already applied** — see `git diff` and
`test_security_controls.py`. What remains is listed here.

---

## Blocking decisions (need a human call)

### [RESOLVED] D1 — How should the dashboard authenticate?

Resolved: Key field & manager in the dashboard UI (`templates/dashboard.html`).
* Added API Key configuration modal and navbar indicator button (`#api-key-btn`).
* Key persists in `localStorage` (`ontoleap_api_key`).
* Global `window.fetch` interceptor automatically injects `x-api-key` header on all outgoing API calls.
* HTTP `401 Unauthorized` responses are automatically intercepted to display the modal with an actionable prompt.
* Verified with `test_security_controls.py` and dashboard template integration tests.

### D2 — Should auth fail closed?

Currently opt-in: no key means the API is open (with a startup warning). The
alternative is refusing to start without a key. Two-line change, but it breaks the
current deployment until the key is set.

---

## High priority — Completed

### [DONE] T1 — Fix `_concepts_match` false positives
* Completed: Added `_extract_semantic_head` and `_match_concept_details` in `product_truth.py`. Single-token matches require common token to match the semantic head of both concepts.
* Added `match_strength` to `SemanticTriple` and `verified_claims_breakdown` to `ProductTruthMatrixResponse`.
* Verified with `test_concept_matching.py`.

### [DONE] T2 — Gate or remove the Ordway demo fallback
* Completed: Gated `ordway_extraction.json` behind `ONTOLEAP_DEMO_MODE` env var and exact host verification (`ordwaylabs.com`).
* Verified with `test_concept_matching.py` and `test_product_truth.py`.

### [DONE] T3 — Add GitHub as a proof-discovery source
* Completed: Added `discover_github_evidence` to `proof_discovery.py` extracting official client SDKs (`providesSdk`), OpenAPI specs (`providesApi`), and connectors (`integratesWith`) from public GitHub org/user repositories. Integrated into `orchestrate_autonomous_proof_discovery`.
* Verified with `test_proof_discovery.py`.

---

## Medium priority

### T4 — Live Wikidata SPARQL for entity grounding

Currently 40 hardcoded Q-IDs in `constants.py:WIKIDATA_KB`. Live SPARQL against
`query.wikidata.org` widens coverage. Cheap; coverage is poor for small private B2B
companies, so keep the static KB as a fast path and fall back to live queries.

### T5 — Sentence Transformers for semantic matching

The principled fix behind T1: replaces token-overlap heuristics with embedding
similarity. `torch` is already a dependency so the install is cheap, but it adds
another model to memory — weigh against the shared-GLiNER work in
`pipeline.py:_load_shared_gliner`.

### T6 — PostgreSQL for the truth ledger

`truth_ledger/history.jsonl` is **ephemeral on Cloud Run** — it reseeds from `log.md`
on every revision. Competitor change-tracking over time cannot work reliably on that.
Rotation and write-locking were added in the audit as a stopgap. Largest lift here,
but the most architecturally real.

---

## Evaluated and rejected

Recorded so they are not re-litigated.

| Suggestion | Verdict | Reason |
| :--- | :--- | :--- |
| SEC EDGAR | No | Financial/legal identity, never a technical capability. Targets are private companies, so coverage is near-zero |
| Companies House | No | UK legal registry; feeds nothing in the claims-vs-reality diff. Also needs credentials |
| Common Crawl | No | Re-implements crawling that already exists; compute cost |
| Greenhouse / Lever | Not yet | Job postings are inferential, not proof. Would inflate MGI without improving truth |
| RapidFuzz | No | The substring rule in `_concepts_match` already handles company-name variants (5/6 test cases). Only fixes typos, which do not occur in names scraped from a company's own site |
| Scrapy | No | Would mean redoing the redirect-SSRF and response-size hardening for no capability gain |
| spaCy | No | Duplicates GLiNER |
| Neo4j | No | RDFLib is adequate at current graph sizes |
| RSS / sitemaps / changelogs / robots.txt / JSON-LD | Already built | `discover_public_changelog()`, `fetch_sitemap_urls()`, `probe_common_specs()`, Extruct |

---

## Known residual risks

Accepted during the audit; revisit if the threat model changes.

* **DNS rebinding** — validation and connection resolve separately. Full fix requires
  pinning the validated IP into the connection, which `curl_cffi` does not cleanly
  support. Egress firewall rules are the practical mitigation.
* **Rate limiting is per-process** — behind N replicas the effective limit is N×. Needs
  a shared store (Redis / Memorystore) for an exact global limit.
* **Prompt injection** — crawled third-party content flows into Gemini prompts. Output
  is HTML-escaped at every sink so it cannot execute, but a malicious page can still
  influence generated text.
* **Redirect preflight** costs one extra request per uncached fetch. Accepted over
  restructuring the three-level fallback cascade.
* **`unpkg.com` in `graph_export.py`** loads vis-network unpinned and without SRI in
  *exported* HTML. Outside the audited runtime; pin if those exports are distributed.

---

## Handoff — push & deploy (Completed)

* **GitHub branches**: Both `ontology-foundation` and `main` are up-to-date and synchronized with `origin` (`fc2aa27`).
* **Deployment**: Live on Google Cloud Run (`gainark-ontoleap-00040-n7d` in `asia-south1`).
* **Service URL**: https://gainark-ontoleap-35509275124.asia-south1.run.app
* **Status**: 100% traffic serving, health endpoints verified (/api/health, /api/info, /dashboard).

