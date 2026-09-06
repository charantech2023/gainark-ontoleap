"""
Calibration harness for the single-page Structured Data Readiness score.
Closes ANTIGRAVITY_TASKS.md Task 4.

The original acceptance criterion was:

    "No site in the calibration set scores below a site that AI engines
     demonstrably cite less often."

This suite establishes that the criterion is unreachable for the signals the
score is built from, which is the documented trigger for the alternative fix in
the same task ("rename the output to what it measures"). The tests below pin
that conclusion so it stays true, and fail loudly if anyone re-labels the score
as a citation or GEO index without first satisfying the ordering.

Runs offline against benchmark_results.json. Pass --live to re-crawl the
calibration set through the pipeline (slow: loads GLiNER + torch).
"""

import json
import os
import sys

CALIBRATION_SET = "calibration_set.json"
FIXTURE = "benchmark_results.json"

# Phrasings that assert the single-page score predicts AI citation behaviour.
# Task 4 forbids these on the single-page path; the site-wide 4-pillar index in
# linking.py is a different metric and is explicitly out of scope here.
CITATION_CLAIM_TERMS = ["citation readiness", "citation authority", "citation index", "geo index"]
SINGLE_PAGE_SURFACES = ["pipeline.py", "benchmark.py", "demo.py"]


def load_calibration():
    with open(CALIBRATION_SET, encoding="utf-8") as f:
        data = json.load(f)
    return {s["url"]: s for s in data["sites"]}


def component_ratios(breakdown):
    """Back the four raw component ratios out of a stored ReadinessBreakdown."""
    details = breakdown["details"]
    covered, total = details["gliner_labels_covered"].split("/")
    entity_count = details["entities_extracted"]
    avg_conf = details["average_entity_confidence"]
    return {
        "schema": breakdown["schema_score"] / 40.0,
        "concept": breakdown["concept_score"] / 30.0,
        "diversity": int(covered) / int(total),
        "strength": avg_conf * min(entity_count / 5.0, 1.0),
    }


def score_with_weights(ratios, w_schema, w_concept, w_entity):
    entity = 0.5 * ratios["diversity"] + 0.5 * ratios["strength"]
    return w_schema * ratios["schema"] + w_concept * ratios["concept"] + w_entity * entity


def load_observations(live=False):
    """Return [{url, brand, citation_tier, score, ratios}], highest score first."""
    calibration = load_calibration()

    if live:
        from pipeline import OntologyPipeline
        pipeline = OntologyPipeline(config_path="vertical_config.json")
        raw = []
        for url in calibration:
            result = pipeline.process(url=url)
            raw.append((url, result.readiness_score, result.readiness_breakdown.model_dump()))
    else:
        assert os.path.exists(FIXTURE), (
            f"{FIXTURE} missing. Run `python benchmark.py` first, or pass --live."
        )
        with open(FIXTURE, encoding="utf-8") as f:
            results = json.load(f)
        raw = [(r["url"], r["readiness_score"], r["readiness_breakdown"]) for r in results]

    observations = []
    for url, score, breakdown in raw:
        assert url in calibration, f"{url} is scored but absent from {CALIBRATION_SET}"
        observations.append({
            "url": url,
            "brand": calibration[url]["brand"],
            "citation_tier": calibration[url]["citation_tier"],
            "score": score,
            "ratios": component_ratios(breakdown),
        })
    assert len(observations) == len(calibration), (
        f"Calibration set has {len(calibration)} sites but only {len(observations)} were scored."
    )
    return sorted(observations, key=lambda o: o["score"], reverse=True)


def test_score_does_not_track_citation_tier(observations):
    """The original Task 4 acceptance criterion, recorded as failing."""
    print("\n[1] Ranking by score vs. external citation tier ...")
    print(f"  {'brand':<12} {'score':>8} {'citation tier':>15}")
    for o in observations:
        print(f"  {o['brand']:<12} {o['score']:>8.2f} {o['citation_tier']:>15}")

    top_cited = max(observations, key=lambda o: o["citation_tier"])
    inversions = [
        (a["brand"], b["brand"])
        for i, a in enumerate(observations)
        for b in observations[i + 1:]
        if a["citation_tier"] < b["citation_tier"]
    ]
    assert inversions, (
        "The score now matches citation ordering. If that is reproducible and not a "
        "fixture artifact, Task 4's first option is back on the table and this metric "
        "may be renamed to a citation index. Re-run with --live to confirm."
    )
    print(f"  Inversions vs. citation tier: {inversions}")
    print(f"  Most-cited site ({top_cited['brand']}) ranks "
          f"{observations.index(top_cited) + 1} of {len(observations)} by score.")
    print("  PASS - score does not predict citation behaviour, as documented.")


DEFAULT_SCHEMA_WEIGHT = 40.0
# Free parameters in the rubric: the two independent ratios among the three
# component weights. Ordering constraints available: one per adjacent pair of
# distinct citation tiers. Tuning is only defensible with real slack between them.
FREE_WEIGHT_PARAMETERS = 2
MIN_CONSTRAINTS_PER_PARAMETER = 3


def test_no_weighting_recovers_citation_order(observations):
    """Sweep the weight space. Citation order is recoverable only by cutting the
    mandatory-schema component to a fraction of its weight, and the surviving
    window is too narrow - and the calibration set too small - to be a real fit."""
    print("\n[2] Weight sensitivity sweep ...")
    grid = [(s, (100 - s) / 2, (100 - s) / 2) for s in range(0, 45, 5)]
    brands = [o["brand"] for o in observations]
    header = " ".join(f"{b:>10}" for b in brands)
    print(f"  {'schema/concept/entity':<24} {header}   order OK?")

    recovering_weights = []
    for w_schema, w_concept, w_entity in grid:
        scored = [
            (o, score_with_weights(o["ratios"], w_schema, w_concept, w_entity))
            for o in observations
        ]
        # Acceptance criterion: no site may score below a less-cited site.
        ok = all(
            not (a["citation_tier"] > b["citation_tier"] and sa < sb)
            and not (b["citation_tier"] > a["citation_tier"] and sb < sa)
            for i, (a, sa) in enumerate(scored)
            for (b, sb) in scored[i + 1:]
        )
        if ok:
            recovering_weights.append(w_schema)
        label = f"{w_schema:.0f}/{w_concept:.0f}/{w_entity:.0f}"
        row = " ".join(f"{s:>10.2f}" for _, s in scored)
        print(f"  {label:<24} {row}   {'YES' if ok else 'no'}")

    assert recovering_weights, (
        "No weighting in the sweep recovers citation order at all - the signals carry "
        "no citation information and the structured-data naming is the only option."
    )

    # Finding 1: the window only opens once mandatory schema is gutted.
    max_viable = max(recovering_weights)
    cut = (DEFAULT_SCHEMA_WEIGHT - max_viable) / DEFAULT_SCHEMA_WEIGHT
    assert cut >= 0.5, (
        f"Citation ordering survives at schema weight {max_viable} of 100, only a "
        f"{cut:.0%} cut from the current {DEFAULT_SCHEMA_WEIGHT:.0f}. The schema component "
        "would still carry the score, so weight-tuning is defensible - revisit the rename."
    )
    print(f"  Citation ordering requires cutting schema weight from "
          f"{DEFAULT_SCHEMA_WEIGHT:.0f} to <= {max_viable:.0f} ({cut:.0%} cut).")

    # Finding 2: the calibration set cannot support fitting those weights.
    tiers = {o["citation_tier"] for o in observations}
    constraints = len(tiers) - 1
    needed = FREE_WEIGHT_PARAMETERS * MIN_CONSTRAINTS_PER_PARAMETER
    assert constraints < needed, (
        f"The calibration set now yields {constraints} independent ordering constraints "
        f"for {FREE_WEIGHT_PARAMETERS} free weight parameters, which is enough to fit them "
        "honestly. Task 4's weight-tuning option is back on the table."
    )
    print(f"  Calibration set yields {constraints} independent ordering constraint(s) "
          f"across {len(observations)} sites for {FREE_WEIGHT_PARAMETERS} free weight "
          f"parameters; {needed} constraints needed to fit rather than overfit.")
    print("  PASS - the recovering window is an artifact of a set too small to tune on.")


def test_score_tracks_structured_data(observations):
    """What the score does measure, stated positively: mandatory schema coverage."""
    print("\n[3] Ranking by score vs. mandatory schema coverage ...")
    by_schema = sorted(observations, key=lambda o: o["ratios"]["schema"], reverse=True)
    print(f"  {'brand':<12} {'score':>8} {'schema ratio':>14}")
    for o in observations:
        print(f"  {o['brand']:<12} {o['score']:>8.2f} {o['ratios']['schema']:>14.2f}")

    assert [o["brand"] for o in observations] == [o["brand"] for o in by_schema], (
        "Score ordering diverged from mandatory-schema ordering. The score's headline "
        "claim is that it measures structured data implementation - if that no longer "
        "holds, the label needs revisiting again."
    )
    print("  PASS - score ordering matches structured data implementation.")


def test_no_citation_claims_on_single_page_surfaces():
    """Regression guard for the rename: the single-page score must not be
    described as a citation or GEO index anywhere it is surfaced."""
    print("\n[4] Scanning single-page surfaces for citation claims ...")
    violations = []
    for path in SINGLE_PAGE_SURFACES:
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                lowered = line.lower()
                for term in CITATION_CLAIM_TERMS:
                    # benchmark.py legitimately points readers at the separate
                    # site-wide index; that reference is not a claim about this score.
                    if term in lowered and "site-wide" not in lowered:
                        violations.append(f"{path}:{lineno}: {line.strip()[:100]}")
    assert not violations, (
        "The single-page score is described as a citation/GEO index here:\n  "
        + "\n  ".join(violations)
        + "\nThat claim fails test 1. Either satisfy the citation ordering or keep the "
          "structured-data naming."
    )
    print(f"  Scanned {len(SINGLE_PAGE_SURFACES)} surfaces, no citation claims found.")
    print("  PASS")


if __name__ == "__main__":
    live = "--live" in sys.argv
    print("=" * 78)
    print("STRUCTURED DATA READINESS - CALIBRATION SUITE (Task 4)")
    print(f"Source: {'live pipeline crawl' if live else FIXTURE}")
    print("=" * 78)

    observations = load_observations(live=live)
    test_score_does_not_track_citation_tier(observations)
    test_no_weighting_recovers_citation_order(observations)
    test_score_tracks_structured_data(observations)
    test_no_citation_claims_on_single_page_surfaces()

    print("\n" + "=" * 78)
    print("ALL CALIBRATION TESTS PASSED")
    print("Conclusion: the score measures single-page structured data implementation.")
    print("It is not a predictor of AI citation frequency and is not labelled as one.")
    print("=" * 78)
