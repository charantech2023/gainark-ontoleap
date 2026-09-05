# OntoLeap — Accuracy & Integrity Fixes

Four changes to close gaps between what the product claims and what the code computes.
Ordered by priority. Task 1 is already applied in the local tree — verify before redoing.

Repo: `Ontology/` (FastAPI + GLiNER, entry point `api.py`)

---

## Task 1 — Remove PyKEEN / TransE misrepresentation ✅ likely already applied

**Problem.** `link_prediction.py` contains no knowledge-graph embeddings. It is a table of
five keyword-triggered templates (`PREDICTION_TEMPLATES`) with hand-written confidence
values, scaled by `0.75 + 0.05 × matched_trigger_count`. PyKEEN is not in `requirements.txt`
and no model is trained or loaded. The API response, the OpenAPI summary and the dashboard
badge nevertheless advertised "TransE / ComplEx KG Link Predictor (PyKEEN Paradigm)".

A technical evaluator who opens `/docs` and reads the response will catch this, and will then
distrust the parts of the platform that are real.

**Change.** Replace every PyKEEN/TransE/ComplEx reference with accurate naming:

| File | Was | Now |
| :--- | :--- | :--- |
| `link_prediction.py` module docstring | "Inspired by PyKEEN and Knowledge Graph Embedding (TransE / ComplEx) paradigms" | "Rule-based relation inference over hand-curated ontological priors" |
| `link_prediction.py` `predict_kg_links()` return | `model_name="TransE / ComplEx KG Link Predictor (PyKEEN Paradigm)"` | `model_name="Rule-Based Relation Inference (Ontological Priors)"` |
| `models.py` `PredictedLink` docstring | "(PyKEEN / TransE embedding paradigm)" | "derived from rule-based ontological priors" |
| `models.py` `LinkPredictionRequest` docstring | "(PyKEEN / TransE paradigm)" | "from ontological priors" |
| `models.py` `LinkPredictionResponse.model_name` default | `"TransE / ComplEx KG Link Predictor (PyKEEN Paradigm)"` | `"Rule-Based Relation Inference (Ontological Priors)"` |
| `api.py` `/api/predict-links` summary | "(PyKEEN Paradigm)" | "(Ontological Priors)" |
| `linking.py` inline comment | "via AI Link Prediction (PyKEEN Paradigm)" | "from rule-based ontological priors" |
| `templates/dashboard.html` card badge | `PyKEEN Paradigm · TransE / ComplEx` | `Ontological Priors · Rule-Based` |
| `templates/dashboard.html` two HTML/JS comments | "(PyKEEN Paradigm)" | "(Ontological Priors)" |

**Acceptance.** This returns nothing:

```bash
grep -rn "PyKEEN\|pykeen\|TransE\|ComplEx" --include="*.py" --include="*.html" . | grep -v venv | grep -v __pycache__
```

**Do not** implement real KG embeddings to make the old label true. The rule table produces
useful, explainable output; only the label was wrong.

---

## Task 2 — Reconcile the AI Citation Readiness Index with its documentation ⚠️ decision needed

**Problem.** All marketing (README, exec summary, dashboard copy) describes **four pillars
weighted 25% each**: Entity Grounding, Relational Density, Topic Silo Integrity, Schema
Coverage.

`OntologyPipeline.calculate_readiness_score()` in `pipeline.py:519` actually computes **three**
components:

| Component | Max points | How |
| :--- | :---: | :--- |
| Mandatory schema | 40 | `present / len(config.mandatory_schema_types) × 40` |
| Seed concepts | 30 | `matched / len(config.core_seed_concepts) × 30` |
| Entity richness | 30 | `(label_diversity × 15) + (avg_confidence × volume_factor × 15)` |

There is no relational-density pillar and no silo-integrity pillar anywhere in the calculation.
`ReadinessBreakdown` in `models.py:48` carries exactly three score fields.

**Constraint that decides the approach.** `calculate_readiness_score()` runs **per page**, on
`mandatory_status`, `seed_matches` and `entities` only.

- **Relational density is implementable now** — triples already exist at the call site
  (`pipeline.py:492`, inside `process()`) and just need passing into the function.
- **Silo integrity is not** — it is a site-level property that only exists after
  `crawl_and_build_unified_graph()`. A single-page audit has no data to compute it from.

**Pick one:**

- **(A) Document reality.** Update README, dashboard copy and any client-facing decks to say
  40 / 30 / 30 with the real pillar names. No code change, no score movement, existing reports
  and `benchmark_report.md` stay valid. Lowest risk.
- **(B) Implement four pillars at 25%.** Add relational density and silo integrity, rebalance.
  Every historical score changes, `benchmark_report.md` must be regenerated, and single-page
  audits need silo integrity either stubbed or its 25 points redistributed.
- **(C) Hybrid.** Four pillars for sitemap crawls where silo data exists; three pillars for
  single-page audits, clearly labelled as such. Most accurate, two scales to explain.

**Acceptance.** Whichever is chosen, the pillar names and weights shown in the dashboard,
the README and `ReadinessBreakdown` all match what the code computes.

---

## Task 3 — `graph_completeness_score` is circular

**Problem.** `link_prediction.py:289-297`:

```python
total_potential = existing_count + predicted_count
completeness = round((existing_count / total_potential) * 100.0, 1)
```

The denominator is driven by how many of our own hardcoded templates happened to fire. A site
that matches fewer trigger keywords gets *fewer* predictions and therefore scores as *more*
complete. The metric rewards being outside the covered vertical, which inverts its meaning.
It is displayed prominently in the dashboard as "KG Completeness".

**Change.** Either:
- score completeness against a fixed denominator — the count of predicates the ontology
  *expects* for the detected vertical, independent of trigger matches; or
- drop the percentage and display the raw counts ("14 relations found, 6 gaps identified"),
  which is honest and just as useful in a client conversation.

**Acceptance.** Adding a page that matches no prediction templates must not raise the
completeness score.

---

## Task 4 — Calibrate the readiness score against known-good sites

**Problem.** `benchmark_report.md` reports **Stripe at 31.42/100** and Chargebee at 60.74/100.
Stripe is among the most-cited entities in payments across every AI answer engine. Any prospect
who notices this stops trusting the number.

The score currently measures "does this page's HTML carry three schema types and ten specific
fintech keywords" — a legitimate technical checklist, but not a citability index, and the seed-
concept component is literal substring matching against a fixed list, which contradicts the
product's own thesis that keyword matching is obsolete.

**Change.** Build a small calibration set of sites known to be cited heavily by
ChatGPT / Perplexity / AI Overviews for their category. Tune weights so those sites land high.
If the current signals can't produce that ordering, the honest fix is to rename the output to
what it measures — e.g. "Structured Data Readiness" — rather than keep an index whose top
result contradicts observable reality.

**Acceptance.** No site in the calibration set scores below a site that AI engines demonstrably
cite less often.

---

## Notes for whoever picks this up

- `vertical_config.json` is hardwired to `b2b_saas_fintech`. `KNOWN_INTEGRATIONS`,
  `KNOWN_COMPLIANCE`, `KNOWN_PRICING` and `WIKIDATA_KB` are hand-curated for billing software.
  Onboarding a client in another vertical is a curation project, not a config change — worth
  knowing before promising self-serve.
- The strongest code in the repo is `clustering.py` (correct smoothed-IDF TF-IDF, L2 norm,
  cosine) and the internal linking engine in `linking.py`. Neither needs defending; both are
  doing real work. Nothing in these tasks should change their behaviour.
- Untracked files `constants.py` and `models_backup.py` are present in the working tree and
  are not part of any task above.
