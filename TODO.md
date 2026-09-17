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

### [RESOLVED] D2 — Should auth fail closed?

Resolved: yes, it fails closed (`api.py`, commit `a4f7cd1`). The warning was not a
control — the deployed service was found running with no key set and `allUsers` holding
`roles/run.invoker`, so every endpoint was reachable by anyone with the URL and free to
spend metered Gemini / Google Knowledge Graph quota.
* Startup raises when `ONTOLEAP_API_KEY` is unset, so the container fails to come up.
* On Cloud Run the new revision never takes traffic and the last good one keeps serving:
  the deploy fails, the service does not.
* `ONTOLEAP_ALLOW_UNAUTHENTICATED=1` still allows open mode, asked for by name and logged
  on every boot. Anything that is not a yes is not consent — a stray `0` refuses.
* Documented in `.env.example`; covered by `test_fail_closed.py` (4 cases).

The objection that it "breaks the current deployment until the key is set" stands, and is
the point: **set `ONTOLEAP_API_KEY` on the service before deploying `a4f7cd1` or later.**
The dashboard already carries a key (D1), so it keeps working once one is set.

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

### [DONE] T4 — Live Wikidata grounding for entities

Filed as "live SPARQL". SPARQL was the wrong endpoint: name → Q-ID is what
`wbsearchentities` is for, and `remediation.resolve_wikidata` already called it
conservatively — dropping scholarly-article and place-name matches, requiring a
business/software/finance description, refusing ambiguous results. It was wired into
`industry_profiler` and nowhere else, so the graph never saw it.

New `entity_grounding.py` is the single authority over which external identity a phrase
has. The 40 curated Q-IDs in `constants.WIKIDATA_KB` stay the fast path and are never
overridden by a fetched answer; live resolution sits behind them.

* **Latency shaped the design.** The call sites are tight loops inside a synchronous
  graph build, and a marketing page yields dozens of phrases the KB has never seen, so
  a network call per miss would turn one build into minutes of sequential 4s timeouts.
  `ground_url()` / `ground_id()` / `is_grounded()` therefore never touch the network;
  `prefetch()` is the only path that does, in one bounded concurrent batch per run.
* Repointed `knowledge_graph.py` (brand, triple objects, hubs, OWL individuals),
  `link_prediction.py` and `competitive_alignment.py`. Both graph builders prefetch
  once up front.
* Negative results are cached, so a phrase Wikidata cannot ground is asked about once
  per process rather than once per graph.
* `ONTOLEAP_WIKIDATA_LIVE=0` restores the old dict-only behaviour exactly;
  `ONTOLEAP_WIKIDATA_BUDGET` (default 24) caps lookups per run.
* **Bug fixed in passing:** `industry_profiler.ground_discovered_entities` read
  `res["wikidata_id"]` / `res["wikidata_url"]`, but the resolver returns `id` / `sameAs`.
  Every entity on the one live-grounding path in the codebase came back with a
  description and no Q-ID — grounding that reported itself as ungrounded.
* Verified with `test_entity_grounding.py` — precedence, the pure-lookup promise,
  negative caching, the budget, an unreachable resolver, the disabled path, prefetch
  under a running event loop, and an end-to-end graph carrying a live-resolved `sameAs`.

### [DONE] T5 — Sentence Transformers for semantic matching

Filed as "replace token-overlap heuristics with embedding similarity", as the principled
fix behind T1. Both halves of that turned out wrong. T1's code (`product_truth.py`) was
deleted in `b7a9174`, and current matching is exact label/alt-label containment, not
token overlap. And measured, embedding similarity cannot *decide* a match here.

Scored across the 111 defined concepts of `b2b_saas_fintech` with all-MiniLM-L6-v2:

| pair | similarity |
|---|---|
| Dunning ↔ "failed payment retries" (true) | 0.603 |
| Cash Application ↔ "matching payments to invoices" (true) | 0.791 |
| **SOC 1 Type II ↔ SOC 2 Type II** (distinct) | **0.946** |
| MRR ↔ ARR (distinct) | 0.876 |
| Subscription Upgrades ↔ Downgrades (opposites) | 0.803 |

True matches and distinct-concept confusions overlap completely; 34 distinct pairs still
score above 0.70. Used as a coverage signal, a page mentioning SOC 1 would be reported as
covering SOC 2. **That variant is rejected** - see below.

Built instead as a proposer (`semantic_match.py`), on the path `propose_alt_labels`
already set when generated synonyms were demoted for agreeing with curation on 1 of 114:
* Unplaced terms (an alignment's `proprietary_concepts`) are matched to defined concepts
  and written to the review queue with score, runner-up and margin. A reviewer approves
  through `/api/ontology/approve-synonym`, the form becomes an alt label, and exact
  matching finds it from then on with no model involved. **Coverage is never touched.**
* Concepts are embedded **with their definitions**. As bare labels Dunning scored -0.014
  against "failed payment retries"; with its definition, 0.603. A concept with no
  definition is not searched, so a cold vertical proposes nothing and says so.
* A candidate must lead the runner-up by 0.05, computed against **every** defined concept.
  "SOC audit report" scores SOC 1 at 0.775 with SOC 2 0.035 behind, and is refused.
  Searching only whitespace concepts would hide the runner-up and defeat this.
* `closes_gap` marks a candidate whose concept alignment reported as whitespace - the
  difference between "the site lacks this" and "the ontology lacks this word". Those sort
  first.
* Runs on a thread after `/api/kg/align` returns, so an audit neither waits on a cold
  model load nor fails because a suggestion could not be made.
* No new dependency: `transformers` is already pinned, and sentence-transformers is a
  wrapper over the same mean pooling. The Dockerfile pre-downloads the 23M-parameter
  model beside GLiNER. `ONTOLEAP_SEMANTIC_MATCH=0` switches it off.
* Verified with `test_semantic_match.py` - fixed-vector tests for the logic, plus a test
  that runs the real model against the real vertical, since the thresholds are claims
  about that model. Each guard (score floor, margin, definitions) was removed in turn and
  each removal fails the suite.

### [DONE] T6 — Durable truth ledger

Filed as "PostgreSQL for the truth ledger". Postgres turned out to be the wrong tool:
`fc2aa27` built `graph_archive` for exactly this failure, and ledger snapshots are
already immutable append-only records keyed by `(brand, recorded_at)`, which is the
shape that archive serves. Reusing it needs no database, no VPC and no second piece
of configuration - only IAM on a bucket, or a mounted volume.

* `history.jsonl` is now a local **index**; the archive holds the record.
* `record_snapshot()` mirrors each snapshot to `ledger/<recorded_at>__<brand>`.
  Keying on the identity the module already dedupes by makes the write idempotent.
* `sync_from_archive()` pulls anything an instance does not hold, called from FastAPI
  startup, so a recycled or newly scaled replica recovers before serving.
* Reads never touch the archive, so `brand_timeline()` costs one local file scan as
  before rather than an object fetch per audit ever recorded.
* `warn_if_ephemeral()` logs at error level when unconfigured in a container. This
  ledger fails *quietly*: without it, a recycled instance reseeds from `log.md` (which
  truncates verified claims at five) and answers confidently from the summary.
* Verified with `test_ledger_archive.py` - write-through, cold start, two instances,
  idempotent sync, reseed precedence, unreachable archive, and read cost.

**Deploy requirement:** `ONTOLEAP_GRAPH_ARCHIVE` must be set on Cloud Run for any of
this to take effect. One variable now backs both the graph and the ledger, so a
deployment cannot end up half-durable.

### T7 — Move the alt-label candidate queue into the archive

`truth_ledger/alt_label_candidates.json` (`sector_ontology._candidates_path`, also built
by hand in `POST /api/ontology/approve-synonym`) is local-disk only. On Cloud Run pending
proposals, and the "approved" marks `record_reviewer_synonym` sets, vanish when the
instance is recycled and differ between instances. Approved synonyms themselves are safe:
since 17 Sep 2026 the approval syncs the vertical from `ONTOLEAP_VERTICALS_MIRROR` first
and publishes it after. The queue should move into the same archive, as an append-only
decision log like `vocabulary_learning.VocabularyStore`, rather than a rewritten JSON file.

---

## Evaluated and rejected

### Embedding similarity as a coverage signal (T5, 16 Sep 2026)

Distinct concepts score as high as true synonyms - SOC 1 and SOC 2 Type II at 0.946 - so
no threshold separates them, and a coverage figure built on it would claim audits a site
never mentioned. Embeddings propose alt labels for review instead (`semantic_match.py`).
Revisit only with a model that separates those pairs, measured the same way.

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

