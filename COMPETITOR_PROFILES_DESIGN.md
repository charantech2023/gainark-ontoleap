# Competitor Profiles — Design

Status: proposal, 19 Sep 2026. Nothing built. The decisions in [§11](#11-decisions-needed)
are open.

For each competitor, a record of what it claims about each concept in the vertical: the
quote and the page if it claims it, or an honest "not found" if it does not. It is set
beside the customer's own record in the same shape. The comparison answers the question
the evidence-engine position rests on: *why would an AI name them and not us for this,
and which page would change that?*

---

## 1. Why this is not a competitor ontology

A competitor is not a new vocabulary. It is a company (an entity) that makes claims in
the vertical's existing vocabulary (concepts). A separate competitor ontology would be a
second set of concepts that drifts away from the reviewed one. The comparison only works
while "usage-based billing" on chargebee.com and on ordwaylabs.com are **the same concept
URI**.

The design already agreed for the registry says the same
([ENTITY_REGISTRY_DESIGN.md §3.2](ENTITY_REGISTRY_DESIGN.md)): *competitor* is a role
held on an edge, not a kind of thing. So this module adds no ontology. It adds:

1. a **competitor set** for each customer site: which registry entities, at which domains;
2. **claims with evidence** in the run graph of every crawled site, customer or
   competitor alike;
3. a **matrix view** over the latest runs: concepts × companies, each cell a claim, a
   mention or an absence.

## 2. What already exists

| Piece | Where | Usable as is? |
|---|---|---|
| Competitor names, quoted when proven | `buyer_profiles.py`, `known_competitors` + `icp_evidence` | Yes. Names only, no domains. |
| Competitor role on a page | `page_graph._apply_role_evidence` (comparison wording) | Yes |
| Claims that speak for the site | `page_graph._extract_semantic_edges` + `_speaks_for_subject` | **Partly**: see P1 |
| Identity across sites | `entity_registry` (`domains`, `by_domain`, aliases, `distinct_from`) | Yes |
| Concept-level identity in graphs | shared concept namespace; `graph_store.vendors_covering(concept_uri)` | Yes: this is the cross-vendor query |
| Run history + diff | `graph_store.persist_graph`, `list_runs`, `diff_runs`; archive mirror | Yes |
| Background crawling | `crawl_jobs` (resumable, slice by slice) | Yes |
| Change tracking | ledger, `GET /api/competitor-changes` (Antigravity task 5) | Yes, left as it is |
| Page-level competitive gap | `pipeline.run_competitive_gap_analysis` | No: readiness scores for one page, no route calls it. Not built on. |

Most of the machinery is there. What is missing is three things the machinery does not
yet do (§5), plus the matrix itself.

## 3. Principles

- **A cell is evidence or it is nothing.** "Chargebee offers dunning" appears only with
  the sentence and the URL. Everything else is stated as the weaker thing it is.
- **Absent is not the same as "does not offer".** The 9 Sep finding was that the same
  alert appeared and vanished across four runs because page timeouts changed what was
  crawled. A concept not found in 25 pages is "not found in the pages read", and the cell
  names those pages. It is never "missing".
- **One code path for customer and competitor.** A competitor is crawled, resolved and
  stored exactly as the customer is. Any asymmetry would make the comparison measure the
  pipeline, not the companies.
- **No model in the loop.** Extraction, resolution and the matrix are deterministic and
  reproducible, and cost nothing per run beyond the crawl. A model may *propose*
  (competitor domains, new concepts) but never fills a cell.
- **Every review is data.** A reviewer's "this quote does not prove it" or "that is not
  their domain" is stored as an event and read by the next run
  (agreed 13 Sep 2026).

## 4. What a cell holds

Four states, strongest first:

| State | Meaning | Source |
|---|---|---|
| `claimed` | A sentence speaking for the company says it does this | claim edge + quote + URL |
| `mentioned` | The concept resolved on the company's pages, but no sentence claims it (a blog post, a glossary, a list) | resolved node, `source_urls` |
| `not_found` | Neither, in the pages read | the run's page list |
| `not_read` | No usable run for this company | — |

Shape of one row, with illustrative values only:

```jsonc
{
  "concept": "https://gainark.com/ontoleap/ontology/b2b_saas_fintech/concept/dunning-management",
  "label": "Dunning Management",
  "cells": {
    "ordwaylabs.com": {"state": "claimed", "predicate": "hasFeature",
                       "quote": "<sentence from the page>", "url": "https://...",
                       "run": "run:ordwaylabs.com:2026-09-20T..."},
    "chargebee.com":  {"state": "mentioned", "urls": ["https://.../blog/..."], "run": "..."},
    "maxio.com":      {"state": "not_found", "pages_read": 25, "run": "..."}
  },
  "verdict": "customer_only"   // customer_only | competitor_only | both | neither
}
```

Each cell names its run, so a later run showing a different state can be explained by
`diff_runs` instead of guessed at.

## 5. Prerequisites found while reading the code

These are gaps in the foundation, not in the use case. Fixing them improves the
customer's own graph as well, which keeps to the build order agreed on 9 Sep 2026.

**P1: claims are extracted against the billing vocabulary on every vertical.**
`_extract_semantic_edges` takes integrations and compliance from the vertical, but
`hasFeature` and `automates` come from `constants.KNOWN_FEATURES` and
`KNOWN_AUTOMATION`, which are billing lists (`resolve_surface_forms(None, ...)`). On
hr_payroll_benefits a competitor could be seen integrating and complying, and nothing
else. *Fix:* derive the feature, process and capability claim targets from the vertical's
concepts and alt labels, the same forms the resolver uses at rung 2, and emit the concept
URI as the target. The billing constants stay only as the fallback for a vertical with no
concept layer.

**P2: the run graph stores the claim but not its proof.**
`_build_site_turtle` writes `site ex:hasFeature node`. The `provenance_sentence` and
`source_url` on the `KGEdge` are dropped, so they survive only in the crawl job's JSON
result. A matrix built on the graph would have claims without quotes. *Fix:* write one
claim node per edge, with an id hashed from (subject, predicate, object, url), carrying
`prov:value` (the quote) and `prov:wasDerivedFrom` (the page). `diff_runs(claims_only=True)`
already ignores the `prov:` namespace, so run diffs are unaffected.

**P3: a competitor is a name, and a crawl needs a domain.**
`known_competitors` holds "Maxio (SaaSOptics)", not maxio.com. Resolution, cheapest first:
1. registry: the name resolves to an entity that has `domains`;
2. link: the comparison page that proved the competitor links to a single external
   domain whose label matches the name;
3. proposal: a model or a search suggests a domain, which stays a *candidate* until a
   reviewer confirms it.

Rungs 2 and 3 end as registry events (`domain` on the entity), so the next customer with
the same competitor needs no lookup at all.

## 6. Pipeline

```
buyer profile ──► competitor set ──► crawl jobs ──► run graphs ──► matrix view ──► API / prompts
(known_competitors)  (P3: domains,    (same code as    (P1 claims,   (latest usable run
                      reviewer-held)   the customer)    P2 proof)     per domain)
```

1. **Competitor set.** Stored per customer site next to its buyer profile
   (`buyers/<domain>.competitors.json`, mirrored): entity id, domain, how the domain was
   resolved, and who confirmed it. A reviewer can add a competitor discovery missed, or
   remove one.
2. **Crawl.** One `crawl_jobs` job per competitor, with the same planner and a fixed page
   budget (§11). `crawl_planner` already sorts pages into groups (comparison,
   pricing, integrations, security, docs, editorial); a competitor crawl spends its
   budget on the product-facing groups and skips editorial, whose sentences cannot
   prove a claim anyway (`_speaks_for_subject`).
3. **Runs.** Persisted as run graphs under the competitor's domain. Nothing new here once
   P1 and P2 are in.
4. **Matrix.** A read-only view: for each concept in the vertical, the latest usable run
   of each domain gives one cell (§4). It is computed on request, not stored, because it
   is a pure function of stored runs.
5. **Consumers** (§8).

## 7. How it learns

| Signal | Stored as | Read by |
|---|---|---|
| Reviewer: "not their domain" | registry `distinct_from` / domain removal event | P3 rung 1 |
| Reviewer: "this quote does not prove the claim" | claim rejection event (subject, predicate, object, url) | matrix: the cell drops to `mentioned`; the extractor's precision count |
| A competitor claims a term the vertical lacks | vocabulary proposal (existing `vocabulary_learning` flow) | reviewers; approved terms become concepts for every site |
| The same competitor in many customers' sets | entity evidence (`sites`) | ranking competitors to crawl first |

The third row is the one that compounds. Competitors are the best source of the
vertical's vocabulary a cold customer does not yet use, and the proposal flow already
exists to keep it reviewed.

## 8. Consumers

- **API:** `GET /api/competitor-matrix?domain=ordwaylabs.com[&concept=...]`, returning
  rows as in §4 plus a coverage block (pages read per company, run dates, cells
  `not_read`).
- **Prompt generator:** comparison prompts per concept, ranked by verdict.
  `customer_only` concepts are where "Ordway vs Chargebee for {concept}" can be won;
  `competitor_only` concepts are the content gap. The existing
  `"{left} vs {right}"` prompt stays.
- **Dashboard:** a later tab, not part of this build.

## 9. Test plan and the numbers reported

Curated first, then cold, and the gap between them is reported
(agreed 12 Sep 2026: Ordway is the test case, not the target).

| Run | Customer | Competitors | Vertical |
|---|---|---|---|
| Curated | ordwaylabs.com | Maxio, Chargebee, Zuora | b2b_saas_fintech (111 concepts) |
| Cold | bamboohr.com | Hibob | hr_payroll_benefits (27 reviewed concepts) |

Measured:

1. **Claim precision.** Sree marks about 40 `claimed` cells on the Ordway matrix true or
   false; this becomes the gold set in `eval/`. The target is set after the first
   reading, not before.
2. **Determinate share.** The share of cells that are `claimed` or `mentioned` rather than
   `not_found`, curated against cold. This is the onboarding-cost number.
3. **Stability.** The same crawl run twice a day apart: how many cells change state with
   no site change. This is the 9 Sep reproducibility lesson, measured rather than
   assumed. If it is high, the page budget or the planner is the fix, not the matrix.
4. **Resolution without lookup.** The share of competitor domains resolved by P3 rung 1.
   It should rise from the second customer on.

## 10. Phases

| Phase | Scope | Done when |
|---|---|---|
| 0 | P1 vertical-driven claims, P2 proof in the run graph | the Ordway and bamboohr runs hold concept-URI claims with quotes; existing tests pass |
| 1 | P3 competitor set + domain resolution | Maxio, Chargebee, Zuora and Hibob resolve to domains, confirmed |
| 2 | Crawl the competitors, persist runs | four competitor runs stored and mirrored |
| 3 | Matrix view + API | the §9 numbers reported for both runs |
| 4 | Prompt generator ranking by verdict | comparison prompts per concept, read by hand |

Phase 0 stands on its own. Even if the rest waits, it fixes the customer's own graph on
every non-billing vertical.

**Out of scope:** alerting (the ledger does it), share of voice in AI answers (roadmap
phase 2), pricing extraction, and any dashboard work.

## 11. Decisions needed

1. **Where the proof lives (P2).** Recommended: claim nodes in the run graph. The
   alternative is a JSON document next to each run, which is simpler but means two
   stores to keep in step.
2. **Page budget per competitor.** Recommended: 25 pages, planner-ranked. Four
   competitors are then about the size of one 100-page customer crawl. Budgets set too
   small show up as instability in §9.3.
3. **Refresh.** On demand, plus at most monthly. Change tracking already exists for
   anything more frequent.
4. **Who confirms competitor domains.** Recommended: rungs 1–2 apply automatically and are
   shown as such; a rung-3 proposal waits for a reviewer.
5. **Competitor terms as vocabulary proposals (§7).** Recommended: yes, through the
   existing proposal flow, never straight into the vertical.

