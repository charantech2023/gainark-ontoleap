# OntoLeap — Backlog

Open work from the security audit and the data-source evaluation.
Ordered by priority. Separate from `ANTIGRAVITY_TASKS.md`, which tracks the
accuracy/integrity fixes.

Security fixes from the audit are **already applied** — see `git diff` and
`test_security_controls.py`. What remains is listed here.

---

## Blocking decisions (need a human call)

### D1 — How should the dashboard authenticate?

`ONTOLEAP_API_KEY` is implemented and enforced, but **the dashboard has no way to
send the key**. Setting it in production today means `/dashboard` renders and every
data call returns 401.

Pick one:

| Option | Effect |
| :--- | :--- |
| Network-layer auth (IAP / Cloud Armor) | No code change. Dashboard and API both protected at the perimeter. API key stays for programmatic clients |
| Key field in the dashboard UI | Dashboard stores the key in `localStorage`, sends `x-api-key` on every fetch. Simple, but the key lives in the browser |
| Leave auth off, restrict at the network | Status quo. Only safe if the service is genuinely not internet-reachable |

**Until this is decided, do not set `ONTOLEAP_API_KEY` in production.**

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

## Handoff — push & deploy (owner: Antigravity)

Claude did the security audit, fixes and testing. Push and deploy are Antigravity's.
State as of the handoff:

**Two commits sit on local branch `security/audit-hardening`, not yet on GitHub.**

| Commit | Contents |
| :--- | :--- |
| `a66ab07` | Security audit fixes — 36 files, +2804/-325 |
| `657ac27` | Test isolation fix (see below) |

`origin/main` is still at `bf04891`. A push attempt did not reach GitHub; the branch
does not exist on the remote. `credential.helper=manager` is configured, so a GitHub
PAT prompt is the likely cause.

### Step 1 — push

```bash
git push -u origin security/audit-hardening
```

### Step 2 — resolve two working-tree files before deploying

`gcloud run deploy --source .` ships the **working tree**, not the pushed branch.
These are still dirty and would go to production as-is:

| File | State |
| :--- | :--- |
| `verticals/ai_security_devsecops.json` | Overwritten by a Claude test run on 2026-09-07 — auto-generated content replaced curated entries (`"AI Agent Governance"` became `"Development Agent"`) |
| `verticals/hr_payroll_benefits.json` | Same |
| `truth_ledger/log.md` | +70 lines of test-run audit entries; noise, safe to discard |

The lost content was never committed, so git cannot restore it. **Antigravity should
confirm whether those entries were hand-curated and re-author them if so.** Restoring
to the committed version (`git checkout security/audit-hardening -- verticals/`) gives
a state that predates both Antigravity's edits and the damage — possibly stale.

Root cause is fixed in `657ac27`: the discovery suite now writes to a temp directory
via `ONTOLEAP_VERTICALS_DIR`. Verified — `verticals/` checksums are byte-identical
before and after a full run. This will not recur.

### Step 3 — deploy

`gcloud` is authed as `y.sreecharan@gmail.com`, project `robotic-catwalk-463901-h0`.

```bash
gcloud run deploy gainark-ontoleap --source . --region asia-south1
```

**Do not set `ONTOLEAP_API_KEY` in this deploy** — decision D1 above is still open,
and setting it makes the dashboard return 401 on every data call. Everything else in
the audit takes effect with no configuration: both SSRF criticals, all XSS fixes,
header and CSV injection, and every DoS ceiling.

### Verification state

21 of 21 existing suites pass, plus 50 new assertions in `test_security_controls.py`,
including a live redirect-SSRF test. No secrets in the staged diff; `.env` is not
tracked and is excluded from Docker and gcloud builds.
