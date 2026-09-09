"""
Overlapping vocabulary terms must produce one claim, not two.

The vertical vocabularies overlap on purpose: "Invoicing" is a process the product
runs and "Automated Invoicing" is a feature it ships, and both belong in the
ontology. But both matched the same phrase, so one marketing sentence became two
claims - and the truth matrix then demanded evidence for both, turning a single
unsupported phrase into two drift alerts.

A cross-bucket guard that compared terms for EXACT equality passed this vocabulary
clean and gave false assurance: the 21 real collisions are all containment, not
equality. These tests assert the extractor's behaviour instead of the vocabulary's
shape, because containment in the vocabulary is legitimate - it is double-emission
that is the defect.

Loads GLiNER. No network calls.
"""

import json
import re

from pipeline import OntologyPipeline, DEFAULT_VERTICAL_PROFILE

_pipeline = None


def pipe():
    global _pipeline
    if _pipeline is None:
        _pipeline = OntologyPipeline()
    return _pipeline


def claims(text):
    """Every (predicate, object) the extractor produces for one sentence."""
    return {(t.predicate, t.object) for t in pipe().process(text=text).triples}


def test_containment_pairs_are_reported():
    """Inventory, not a gate. Containment is allowed; silent double-emission is not."""
    print("\n[1] Containment pairs in the default vertical ...")
    data = json.load(open(DEFAULT_VERTICAL_PROFILE, encoding="utf-8"))
    terms = {}
    for field, values in data.items():
        if field.startswith("known_") and isinstance(values, list):
            for v in values:
                terms[v] = field.replace("known_", "")

    pairs = []
    for short, short_field in terms.items():
        for long, long_field in terms.items():
            if short == long or len(short) >= len(long):
                continue
            if re.search(rf'\b{re.escape(short.lower())}\b', long.lower()):
                pairs.append((short, short_field, long, long_field))

    for short, sf, long, lf in sorted(pairs)[:10]:
        print("    %-26s [%s]  inside  %-32s [%s]" % (short, sf, long, lf))
    print("    %d containment pair(s) total" % len(pairs))
    assert pairs, (
        "No containment pairs found. Either the vocabulary changed shape or this "
        "fixture no longer covers the case these tests exist for."
    )
    print("  PASS")


def test_one_phrase_yields_one_claim():
    """The defect: a phrase matching two overlapping terms emitted both."""
    print("\n[2] Overlapping matches collapse to the specific reading ...")
    # The feature/automation register collisions this test first covered - "Automated
    # Invoicing" against "Invoicing" - are now resolved upstream by alt_labels, which
    # records that they are one concept rather than two overlapping ones. What is left
    # here is genuine specificity: a longer standard or product name that contains a
    # shorter one, where the longer reading is the right one.
    cases = [
        # sentence, the claim that must survive, the subsumed one that must not
        ("Ordway is SOC 2 Type II certified for the current audit period.",
         ("certifiedBy", "SOC 2 Type II"), ("certifiedBy", "SOC 2")),
        ("The platform integrates with Sage Intacct for general ledger sync.",
         ("integratesWith", "Sage Intacct"), ("integratesWith", "Sage")),
    ]
    for text, keep, drop in cases:
        got = claims(text)
        print("    %-52s -> %s" % (text[:50], sorted(got)))
        assert keep in got, (
            "The specific reading %s was lost for %r." % (keep, text)
        )
        assert drop not in got, (
            "%s survived alongside %s for %r - one phrase produced two claims, so "
            "the truth matrix will look for two pieces of evidence and can raise two "
            "drift alerts for a single unsupported phrase." % (drop, keep, text)
        )
    print("  PASS")


def test_short_term_still_fires_when_used_alone():
    """Resolution is per sentence, so a subsumed term keeps its own independent life."""
    print("\n[3] Subsumed terms still extract on their own ...")
    got = claims("Ordway automates invoicing for finance teams.")
    print("    %s" % sorted(got))
    assert ("automates", "Invoicing") in got, (
        "'Invoicing' no longer extracts from a sentence where nothing subsumes it. "
        "Overlap resolution must be scoped to the sentence, not applied globally to "
        "the vocabulary."
    )
    print("  PASS")


def test_genuine_multi_predicate_terms_survive():
    """SOC 2 Type II is correctly compliesWith AND certifiedBy.

    Resolution keys on containment between DIFFERENT objects, so an identical object
    under two predicates is never touched. This is the case that makes a naive
    'one object, one predicate' dedup wrong.
    """
    print("\n[4] Identical object under two predicates is preserved ...")
    got = claims("Ordway is SOC 2 Type II certified and maintains SOC 2 Type II compliance.")
    preds = {p for p, o in got if o == "SOC 2 Type II"}
    print("    SOC 2 Type II -> %s" % sorted(preds))
    assert {"compliesWith", "certifiedBy"} <= preds, (
        "SOC 2 Type II lost a predicate. Compliance and certification are different "
        "claims needing different evidence; collapsing them hides one."
    )
    print("  PASS")


if __name__ == "__main__":
    print("=" * 78)
    print("VOCABULARY OVERLAP RESOLUTION")
    print("=" * 78)
    test_containment_pairs_are_reported()
    test_one_phrase_yields_one_claim()
    test_short_term_still_fires_when_used_alone()
    test_genuine_multi_predicate_terms_survive()
    print("\n" + "=" * 78)
    print("ALL OVERLAP TESTS PASSED")
    print("=" * 78)
