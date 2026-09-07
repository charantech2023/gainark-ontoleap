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

## High priority

### T1 — Fix `_concepts_match` false positives

**Problem.** [`product_truth.py:141`](product_truth.py:141) treats a single shared
token as a match when the token is not in `generic_terms` and is longer than 3 chars.
Confirmed failures:

```
'payment processing'  matches  'payment fraud detection'   -> True
'invoice automation'  matches  'invoice fraud'             -> True
```

`_concepts_match` decides **verified vs. unbacked** in `build_product_truth_matrix`,
so this inflates the Marketing Grounding Index and suppresses legitimate drift alerts.
It is silent — nothing in the output distinguishes a strong match from a one-token
coincidence.

Worse in the billing vertical, where `payment`, `invoice`, `billing`, `revenue`,
`subscription`, `tax` and `usage` are the highest-frequency tokens.

**Change.** Preferred approach (2 + 3 together):

1. *(Weakest)* Extend `generic_terms` with the domain head nouns. One line, but a
   blocklist needing per-vertical maintenance.
2. Require the shared token to be the **semantic head** (compare final tokens) rather
   than any set-intersection member. Kills both cases above;
   `dunning automation` / `automated dunning` still matches.
3. Carry **match strength** into the output — record whether a claim was verified by
   exact, substring, or single-token match, and treat single-token verifications as
   provisional in the MGI, the way `evidence_status` already handles thin evidence.

**Acceptance.** Regression tests asserting the two pairs above return `False` while
every pair in the `test_alert_precision` / `test_evidence_gating` suites is unchanged.

**Note.** This shifts a scoring rule and will move reported MGI values on
already-published audits.

### T2 — Gate or remove the Ordway demo fallback

**Problem.** [`product_truth.py:451`](product_truth.py:451):

```python
if "ordway" in url.lower() and os.path.exists("ordway_extraction.json"):
```

Any URL containing the substring `ordway` is served canned extraction data instead of
a live crawl. So an audit of Ordway is replaying a fixture, not measuring Ordway. It
also fires on unrelated domains (`ordway-consulting.com`).

This is why **every 100% grounding score in the ledger is the degenerate
`Marketing Claims: 3` case**.

For a product whose value proposition is verified ground truth, a shortcut that
fabricates a clean result for one named customer should not be in the shipped path.

**Change.** Gate behind an explicit `ONTOLEAP_DEMO_MODE` env var, or delete it. If
kept, match on exact host, not substring.

**Acceptance.** With `ONTOLEAP_DEMO_MODE` unset, an audit of `ordwaylabs.com`
performs a live crawl.

### T3 — Add GitHub as a proof-discovery source

**Problem.** Technical evidence starves. Across 29 logged audits: 17–75 marketing
claims against 0–16 technical capabilities, roughly a 10:1 imbalance. Every low MGI in
the ledger is a measurement artifact, not a finding.

**Change.** New source in `proof_discovery.py`, following the shape of
`discover_public_sdks()` (which already queries PyPI and npm):

* Company org repos, languages, release frequency
* OpenAPI / AsyncAPI specs committed to repos
* Public SDK repos corroborating registry findings

Unauthenticated at 60 req/hr; honour `GITHUB_TOKEN` for 5,000 req/hr when present.
Route through `smart_fetch` so it inherits SSRF and response-size protection.

Do **not** treat repo activity as proof of production use — emit it at a confidence
level `evidence_status` can gate on.

**Acceptance.** A brand with public repos yields technical triples that raise the
capability count above the `MIN_CONFIDENT_TECHNICAL_EVIDENCE` threshold.

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
