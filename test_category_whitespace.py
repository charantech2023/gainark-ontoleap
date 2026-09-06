"""
Category whitespace in the tri-ontology alignment.

Every other output of the alignment is relative to a competitor, so it can only say
"you are ahead here" or "you are behind there". Whitespace is derived from the
industry ontology instead: concepts the category expects that neither the company nor
any analysed competitor markets.

That is the one output which needs a third ontology to exist at all, and it is only
computable now that the vertical config carries its own vocabulary - before that the
industry layer contributed a display name and a few seed strings.

Offline: fixtures are built directly, so no crawling, no GLiNER and no Gemini.
"""

from typing import List

from models import (
    SemanticTriple, ProductTruthMatrixResponse, VerticalConfig,
)
import competitive_alignment


VERTICAL = VerticalConfig(
    vertical_id="healthtech_test",
    display_name="Healthcare & Clinical HealthTech",
    gliner_labels=["Clinical Platform"],
    mandatory_schema_types=["SoftwareApplication"],
    core_seed_concepts=[
        "Electronic Health Record",   # company markets it
        "Telehealth",                 # competitor markets it
        "Clinical Decision Support",  # nobody  -> whitespace
        "DICOM",                      # nobody  -> whitespace
    ],
    known_automation=[
        "Clinical Documentation",     # company markets it
        "Prior Authorization",        # nobody  -> whitespace
    ],
    known_compliance=["HIPAA"],
    known_integrations=["Epic Systems"],
)


def triple(obj: str, predicate: str = "automates") -> SemanticTriple:
    return SemanticTriple(subject="X", predicate=predicate, object=obj, confidence=0.9)


def matrix(brand: str, verified: List[str], unbacked: List[str] = (), hidden: List[str] = ()):
    return ProductTruthMatrixResponse(
        brand_name=brand,
        marketing_url="https://%s.example" % brand.lower(),
        marketing_grounding_index=50.0,
        total_marketing_claims=len(verified) + len(unbacked),
        total_technical_capabilities=len(verified) + len(hidden),
        verified_claims_count=len(verified),
        unbacked_claims_count=len(unbacked),
        hidden_capabilities_count=len(hidden),
        verified_triples=[triple(o) for o in verified],
        unbacked_claims=[triple(o) for o in unbacked],
        hidden_capabilities=[triple(o) for o in hidden],
        executive_summary="fixture",
    )


def _silence_ledger():
    """Keep fixture audits out of the real append-only ledger."""
    try:
        import truth_ledger.recorder as recorder
        recorder.record_alignment_to_ledger = lambda **kw: True
        recorder.record_audit_to_ledger = lambda **kw: True
    except Exception:
        pass


def align(monkeypatched_briefs=True):
    # The brief synthesiser calls Gemini; stub it so this suite stays offline.
    # Also stub the ledger writer: align_tri_ontologies appends to the real
    # truth_ledger/log.md, and fixture brands ("Acme vs Rival") landing there become
    # phantom competitors in the change-tracking history that reads that file.
    if monkeypatched_briefs:
        competitive_alignment.synthesize_counter_positioning_briefs = lambda **kw: []
    _silence_ledger()
    return competitive_alignment.align_tri_ontologies(
        company_matrix=matrix("Acme", verified=["Electronic Health Record", "Clinical Documentation"]),
        competitor_matrices=[matrix("Rival", verified=["Telehealth"], unbacked=["HIPAA"])],
        vertical_config=VERTICAL,
        vertical_id="healthtech_test",
    )


def test_whitespace_is_what_nobody_markets():
    print("\n[1] Concepts nobody in the set markets ...")
    result = align()
    found = {w.concept for w in result.category_whitespace}
    for w in result.category_whitespace:
        print("  %-28s (%s)" % (w.concept, w.source))

    assert "Clinical Decision Support" in found, (
        "A seed concept no party markets was not reported as whitespace."
    )
    assert "DICOM" in found, "A seed concept no party markets was not reported as whitespace."
    assert "Prior Authorization" in found, (
        "known_automation is not being consulted, so whitespace only sees seed concepts "
        "and misses the capability vocabulary the profiler now generates."
    )
    print("  PASS")


def test_marketed_concepts_are_not_whitespace():
    """The claim has to be false for anything either side already talks about."""
    print("\n[2] Concepts somebody markets must be excluded ...")
    result = align()
    found = {w.concept for w in result.category_whitespace}
    print("  reported whitespace: %s" % sorted(found))

    assert "Electronic Health Record" not in found, (
        "Reported whitespace for something the company itself markets."
    )
    assert "Telehealth" not in found, (
        "Reported whitespace for something the competitor markets - a marketer acting "
        "on this would 'claim' territory a rival already owns."
    )
    assert "Clinical Documentation" not in found, (
        "Reported whitespace for a company capability drawn from known_automation."
    )
    print("  PASS")


def test_whitespace_carries_its_source_and_advice():
    print("\n[3] Each item is auditable ...")
    result = align()
    assert result.category_whitespace, "No whitespace produced by the fixture."
    item = result.category_whitespace[0]
    print("  concept: %s | source: %s" % (item.concept, item.source))
    print("  insight: %s" % item.insight[:110])

    assert item.source in ("seed_concept", "automation"), item.source
    assert not item.company_markets_it
    assert item.competitors_marketing_it == []
    assert VERTICAL.display_name in item.insight, (
        "Insight should name the category, since the claim is about category expectation."
    )
    print("  PASS")


def test_empty_vertical_yields_no_whitespace():
    """No industry ontology means no basis for the claim - say nothing, not everything."""
    print("\n[4] Vertical with no vocabulary ...")
    bare = VerticalConfig(
        vertical_id="bare", display_name="Bare", gliner_labels=["X"],
        mandatory_schema_types=["Organization"], core_seed_concepts=[],
    )
    competitive_alignment.synthesize_counter_positioning_briefs = lambda **kw: []
    result = competitive_alignment.align_tri_ontologies(
        company_matrix=matrix("Acme", verified=["Something"]),
        competitor_matrices=[matrix("Rival", verified=["Other"])],
        vertical_config=bare,
        vertical_id="bare",
    )
    print("  whitespace: %d" % len(result.category_whitespace))
    assert result.category_whitespace == [], (
        "Produced whitespace with no industry ontology to derive it from."
    )
    print("  PASS")


HIERARCHICAL = VerticalConfig(
    vertical_id="healthtech_hier",
    display_name="Healthcare & Clinical HealthTech",
    gliner_labels=["Clinical Platform"],
    mandatory_schema_types=["SoftwareApplication"],
    core_seed_concepts=[
        "Revenue Cycle Management",   # nobody names it directly...
        "Interoperability",           # ...nor this
        "Telehealth",                 # genuinely unclaimed
    ],
    concept_hierarchy={
        "Prior Authorization": "Revenue Cycle Management",
        "Claims Processing": "Revenue Cycle Management",
        "HL7 FHIR": "Interoperability",
    },
)


def test_coverage_rolls_up_the_hierarchy():
    """The false positive the hierarchy field exists to prevent.

    A rival writing only about "Prior Authorization" does cover Revenue Cycle
    Management. Reported as whitespace, a marketer would spend a campaign claiming
    territory the competitor already owns under a narrower name - the most costly
    mistake this output can make.
    """
    print("\n[5] Coverage rolls up to broader concepts ...")
    competitive_alignment.synthesize_counter_positioning_briefs = lambda **kw: []
    result = competitive_alignment.align_tri_ontologies(
        company_matrix=matrix("Acme", verified=["HL7 FHIR"]),
        competitor_matrices=[matrix("Rival", verified=["Prior Authorization", "Claims Processing"])],
        vertical_config=HIERARCHICAL,
        vertical_id="healthtech_hier",
    )
    found = {w.concept for w in result.category_whitespace}
    print("  nobody names 'Revenue Cycle Management' or 'Interoperability' literally")
    print("  reported whitespace: %s" % sorted(found))

    assert "Revenue Cycle Management" not in found, (
        "Reported Revenue Cycle Management as unclaimed while the competitor markets "
        "Prior Authorization and Claims Processing, both of which sit under it."
    )
    assert "Interoperability" not in found, (
        "Reported Interoperability as unclaimed while the company markets HL7 FHIR, "
        "which sits under it."
    )
    assert "Telehealth" in found, (
        "Telehealth is genuinely unclaimed and must still be reported - the rollup "
        "should suppress false positives, not real findings."
    )
    print("  PASS - rolled-up concepts suppressed, genuine whitespace kept.")


def test_flat_vertical_behaves_as_before():
    print("\n[6] A vertical with no hierarchy is unaffected ...")
    result = align()
    found = {w.concept for w in result.category_whitespace}
    assert "Clinical Decision Support" in found and "DICOM" in found, (
        "Adding hierarchy support changed behaviour for verticals that define none."
    )
    print("  PASS - flat verticals unchanged.")


if __name__ == "__main__":
    print("=" * 78)
    print("CATEGORY WHITESPACE")
    print("=" * 78)
    test_whitespace_is_what_nobody_markets()
    test_marketed_concepts_are_not_whitespace()
    test_whitespace_carries_its_source_and_advice()
    test_empty_vertical_yields_no_whitespace()
    test_coverage_rolls_up_the_hierarchy()
    test_flat_vertical_behaves_as_before()
    print("\n" + "=" * 78)
    print("ALL CATEGORY WHITESPACE TESTS PASSED")
    print("The industry ontology can now surface positioning no rival has named.")
    print("=" * 78)
