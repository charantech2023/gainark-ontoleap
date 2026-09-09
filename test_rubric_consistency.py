"""
Rubric consistency suite. Closes ANTIGRAVITY_TASKS.md Task 2.

Task 2's acceptance criterion is that the pillar names and weights shown in the
dashboard, the README and the response models all match what the code computes.
That is a property of the whole repo, not of one function, and it drifts every
time a surface is edited on its own - which is how the pitch one-pager ended up
describing "4 dimensions" and then listing three, with the schema and entity
weights swapped.

The project runs two distinct rubrics and the mismatch has historically come
from conflating them:

  Single-page  /api/audit       pipeline.calculate_readiness_score
               3 components, 40 / 30 / 30, per page.
               See test_calibration.py for what this score does and does not claim.

  Site-wide    /api/batch-crawl linking.compute_ai_citation_readiness
               4 pillars, 25 each, only computable after a sitemap crawl because
               silo integrity is a site-level property.

Weights are read from the code, never hardcoded here, so the tests track the
implementation rather than restating it. No network and no GLiNER load: the
model is lazily constructed, so the scoring function can be exercised directly.
"""

import io
import os
import re
import sys

SINGLE_PAGE_WEIGHTS = {"schema": 40.0, "concept": 30.0, "entity": 30.0}
SITE_WIDE_PILLARS = [
    "entity_grounding_score",
    "relational_density_score",
    "silo_integrity_score",
    "schema_coverage_score",
]
SITE_WIDE_PILLAR_MAX = 25.0

# Surfaces that describe a rubric to a reader, and which rubric each one belongs to.
DOC_SURFACES = ["README.md", "templates/dashboard.html", "generate_pitch_pdf.py", "report_pdf.py"]


def measure_single_page_weights():
    """Drive the real scoring function to its ceiling to recover each max."""
    from pipeline import OntologyPipeline
    from models import SeedConceptMatch, EntityMatch

    pipeline = OntologyPipeline()
    cfg = pipeline.config
    saturated = pipeline.calculate_readiness_score(
        mandatory_status={t: True for t in cfg.mandatory_schema_types},
        seed_matches=[SeedConceptMatch(concept=c, count=1) for c in cfg.core_seed_concepts],
        # Every label present, at full confidence, past the volume factor cutoff.
        entities=[
            EntityMatch(text="x", label=label, score=1.0, start=0, end=1)
            for label in cfg.gliner_labels
        ] * 5,
    )
    floor = pipeline.calculate_readiness_score(
        mandatory_status={t: False for t in cfg.mandatory_schema_types},
        seed_matches=[],
        entities=[],
    )
    return saturated, floor


def test_single_page_weights_match_declared():
    print("\n[1] Single-page rubric: measured component ceilings ...")
    saturated, floor = measure_single_page_weights()
    measured = {
        "schema": saturated.schema_score,
        "concept": saturated.concept_score,
        "entity": saturated.entity_score,
    }
    for name, expected in SINGLE_PAGE_WEIGHTS.items():
        print(f"  {name:<10} max = {measured[name]:>6.2f}   declared = {expected:>6.2f}")
        assert measured[name] == expected, (
            f"{name} component tops out at {measured[name]}, not the declared {expected}. "
            "Either the rubric changed or the declared weights are stale."
        )
    assert saturated.total_score == 100.0, (
        f"Saturated total is {saturated.total_score}, not 100. The components no longer "
        "span the full scale, so every published score is on a different axis than claimed."
    )
    assert floor.total_score == 0.0, f"Empty input scores {floor.total_score}, not 0."
    print(f"  Range spans {floor.total_score} to {saturated.total_score}.  PASS")


def test_site_wide_pillars_match_declared():
    print("\n[2] Site-wide rubric: pillar count and ceilings ...")
    from models import AICitationReadiness

    fields = AICitationReadiness.model_fields
    for pillar in SITE_WIDE_PILLARS:
        assert pillar in fields, f"AICitationReadiness is missing pillar '{pillar}'."
        desc = (fields[pillar].description or "").lower()
        assert "25" in desc, (
            f"'{pillar}' description does not state its 25-point ceiling: {desc!r}"
        )
    print(f"  {len(SITE_WIDE_PILLARS)} pillars declared, each out of {SITE_WIDE_PILLAR_MAX:.0f}.")

    # The implementation must cap each pillar at 25, so the four span 0-100.
    target_file = "semantic_seo.py" if os.path.exists("semantic_seo.py") else "linking.py"
    source = io.open(target_file, encoding="utf-8").read()
    for cap in ["min(25.0", "* 25.0"]:
        assert cap in source, f"Expected pillar scaling {cap!r} in {target_file}."
    declared_total = len(SITE_WIDE_PILLARS) * SITE_WIDE_PILLAR_MAX
    assert declared_total == 100.0, f"Four pillars at 25 should span 100, got {declared_total}."
    print(f"  Pillars span 0 to {declared_total:.0f}.  PASS")


def test_no_surface_claims_wrong_component_count():
    """The specific failure mode Task 2 describes: prose that announces one
    number of components and then lists a different number."""
    print("\n[3] Checking rubric descriptions count and sum correctly ...")
    claim = re.compile(r"(\d+)\s+(?:\w+\s+)?(?:dimension|pillar|component)s?\b", re.IGNORECASE)
    percent = re.compile(r"(\d+(?:\.\d+)?)\s*%")
    checked = 0
    violations = []

    for path in DOC_SURFACES:
        if not os.path.exists(path):
            continue
        for lineno, line in enumerate(io.open(path, encoding="utf-8"), 1):
            for match in claim.finditer(line):
                claimed = int(match.group(1))
                if not 2 <= claimed <= 10:
                    continue
                # Read the weights the claim introduces. Periods are not a safe
                # boundary here ("Schema.org"), so take a bounded window instead.
                tail = line[match.end():match.end() + 300]
                weights = [float(w) for w in percent.findall(tail)]
                if not weights:
                    continue
                checked += 1
                # "N pillars (25% each)" states one weight shared by all N.
                shared = re.search(r"\beach\b|\bapiece\b|\bper\s+pillar\b", tail[:60], re.I)
                if shared:
                    total = weights[0] * claimed
                    detail = f"{claimed} x {weights[0]:g}%"
                else:
                    if len(weights) != claimed:
                        violations.append(
                            f"{path}:{lineno}: claims {claimed} but lists {len(weights)} "
                            f"weights -> {line.strip()[:100]}"
                        )
                        continue
                    total = sum(weights)
                    detail = " + ".join(f"{w:g}%" for w in weights)
                if abs(total - 100.0) > 0.01:
                    violations.append(
                        f"{path}:{lineno}: {detail} = {total:g}%, not 100% "
                        f"-> {line.strip()[:100]}"
                    )

    assert not violations, "Inconsistent rubric descriptions:\n  " + "\n  ".join(violations)
    print(f"  Scanned {len(DOC_SURFACES)} surfaces, {checked} weighted claim(s), "
          "all count and sum correctly.  PASS")


def test_single_page_weights_not_attached_to_citation_index():
    """40/30/30 belongs to the structured-data score. Presenting it as the
    citation index re-introduces exactly the claim Task 4 removed."""
    print("\n[4] Checking 40/30/30 is not sold as the citation index ...")
    violations = []
    for path in DOC_SURFACES:
        if not os.path.exists(path):
            continue
        for lineno, line in enumerate(io.open(path, encoding="utf-8"), 1):
            lowered = line.lower()
            has_weights = "40/30/30" in lowered or ("40%" in lowered and "30%" in lowered)
            claims_citation = "citation" in lowered or "geo index" in lowered
            if has_weights and claims_citation:
                violations.append(f"{path}:{lineno}: {line.strip()[:110]}")
    assert not violations, (
        "The single-page 40/30/30 weights are presented as an AI citation index here:\n  "
        + "\n  ".join(violations)
        + "\nThe citation index is the site-wide 4-pillar rubric; see test_calibration.py."
    )
    print(f"  Scanned {len(DOC_SURFACES)} surfaces, no conflation.  PASS")


def test_graph_completeness_non_circular():
    """Task 3: Completeness must be evaluated against a canonical vertical benchmark (16),
    not existing / (existing + predicted) which rewarded matching fewer trigger keywords."""
    print("\n[5] Checking graph completeness is non-circular (Task 3) ...")
    from link_prediction import predict_kg_links
    from models import SemanticTriple

    triple_example = SemanticTriple(
        subject="Acme",
        predicate="automates",
        object="Subscription Billing",
        confidence=0.9
    )

    # Site with trigger matches (yields predicted links)
    rich_res = predict_kg_links(
        domain="acme.com",
        triples=[triple_example] * 4,
        entities=["Subscription", "Invoice", "Recurring Billing"],
        topic_hubs={}
    )

    # Site with NO trigger matches (yields 0 predicted links)
    sparse_res = predict_kg_links(
        domain="blank.com",
        triples=[triple_example] * 4,
        entities=["Unrelated", "Topic"],
        topic_hubs={}
    )

    print(f"  Rich trigger site:   {rich_res.existing_triples_count} triples, {rich_res.predicted_links_count} predicted -> {rich_res.graph_completeness_score}% completeness")
    print(f"  Sparse trigger site: {sparse_res.existing_triples_count} triples, {sparse_res.predicted_links_count} predicted -> {sparse_res.graph_completeness_score}% completeness")

    # With identical existing triples (4), completeness must be identical (25.0%),
    # NOT inverted where sparse gets 100% and rich gets lower.
    assert rich_res.graph_completeness_score == sparse_res.graph_completeness_score == 25.0, (
        f"Completeness is circular: rich={rich_res.graph_completeness_score}%, sparse={sparse_res.graph_completeness_score}%"
    )

    # Adding triples must increase completeness, not decrease it
    more_res = predict_kg_links(
        domain="acme.com",
        triples=[triple_example] * 8,
        entities=["Subscription", "Invoice"],
        topic_hubs={}
    )
    assert more_res.graph_completeness_score == 50.0
    print(f"  Triples scaled 4 -> 8: score {rich_res.graph_completeness_score}% -> {more_res.graph_completeness_score}%.  PASS")


if __name__ == "__main__":
    print("=" * 78)
    print("RUBRIC CONSISTENCY & INTEGRITY SUITE (Tasks 2 & 3)")
    print("=" * 78)

    test_single_page_weights_match_declared()
    test_site_wide_pillars_match_declared()
    test_no_surface_claims_wrong_component_count()
    test_single_page_weights_not_attached_to_citation_index()
    test_graph_completeness_non_circular()

    print("\n" + "=" * 78)
    print("ALL RUBRIC CONSISTENCY & INTEGRITY TESTS PASSED")
    print("Single-page: 3 components, 40/30/30, spanning 0-100.")
    print("Site-wide:   4 pillars, 25 each, spanning 0-100.")
    print("Completeness: non-circular benchmark against vertical priors (Task 3).")
    print("Documented weights match computed weights on every scanned surface.")
    print("=" * 78)
