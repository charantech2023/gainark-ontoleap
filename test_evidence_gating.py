"""
Evidence gating for the Product Truth audit.

The grounding score divides verified claims by total claims. When the docs crawl
returns nothing, that is 0/N = 0%, and every claim also becomes an "unbacked"
drift alert. The audit then reports maximum severity precisely when it knows the
least. Observed live against chargebee.com: 24 marketing claims, 0 technical
capabilities, 0.0% grounding, 21 "critical drift alerts" - every one an artifact
of a failed read rather than a finding about the claims.

Absence of evidence is not evidence of absence. These tests hold that line:

  no evidence      -> no score, no alerts, an explanation instead
  thin evidence    -> score marked provisional, alerts marked provisional
  enough evidence  -> unchanged behaviour

Offline; builds triples directly, no network and no GLiNER.
"""

import product_truth
from product_truth import build_product_truth_matrix, MIN_CONFIDENT_TECHNICAL_EVIDENCE
from models import SemanticTriple


# Distinct, non-overlapping names. Numbered labels are unsuitable on two counts:
# deduplication collapses by containment, so "Vendor 1" would swallow "Vendor 12", and
# alert precision only raises compliance alerts for real standards, so "Standard 0"
# produces no alert at all and the assertions here would pass vacuously.
_NAMES = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot", "Golf", "Hotel",
          "India", "Juliett", "Kilo", "Lima", "Mike", "November", "Oscar", "Papa",
          "Quebec", "Romeo", "Sierra", "Tango", "Uniform", "Victor", "Whiskey", "Xray",
          "Yankee", "Zulu"]


def marketing(n):
    assert n <= len(_NAMES), "fixture supports at most %d distinct claims" % len(_NAMES)
    return [
        SemanticTriple(
            subject="Acme", predicate="integratesWith", object="Vendor" + _NAMES[i],
            confidence=0.9, source_type="marketing_claim",
        )
        for i in range(n)
    ]


def technical(n):
    assert n <= len(_NAMES), "fixture supports at most %d distinct capabilities" % len(_NAMES)
    return [
        SemanticTriple(
            subject="Acme", predicate="automates", object="Capability" + _NAMES[i],
            confidence=0.9, source_type="technical_truth",
        )
        for i in range(n)
    ]


def build(n_marketing, n_technical):
    return build_product_truth_matrix(
        brand_name="Acme",
        marketing_url="https://acme.example",
        tech_docs_url="https://docs.acme.example",
        marketing_triples=marketing(n_marketing),
        technical_triples=technical(n_technical),
    )


def test_no_evidence_yields_no_verdict():
    """The Chargebee case: many claims, nothing readable to check them against."""
    print("\n[1] 24 marketing claims, 0 technical capabilities ...")
    r = build(24, 0)
    print("  grounding index : %r" % (r.marketing_grounding_index,))
    print("  evidence status : %s" % r.evidence_status)
    print("  drift alerts    : %d" % len(r.drift_alerts))

    assert r.marketing_grounding_index is None, (
        "Reported a %r%% grounding score with zero technical evidence. A number here "
        "reads as 'this share of your claims are true' when nothing was checked."
        % r.marketing_grounding_index
    )
    assert r.evidence_status == "inconclusive", r.evidence_status
    assert r.drift_alerts == [], (
        "Raised %d drift alerts with no technical baseline. Each one accuses the "
        "customer of an unsupported claim on the strength of a crawl that read "
        "nothing." % len(r.drift_alerts)
    )
    assert r.evidence_note and "could not" in r.evidence_note.lower()
    summary = r.executive_summary.lower()
    assert "not assessed" in summary, r.executive_summary
    for forbidden in ("severe marketing drift", "hallucination risk"):
        assert forbidden not in summary, (
            "Executive summary still alleges %r without evidence: %s"
            % (forbidden, r.executive_summary)
        )
    print("  PASS - no score, no alerts, explanation given instead.")


def test_thin_evidence_is_marked_provisional():
    print("\n[2] Below the confidence floor of %d ..." % MIN_CONFIDENT_TECHNICAL_EVIDENCE)
    r = build(10, MIN_CONFIDENT_TECHNICAL_EVIDENCE - 1)
    print("  grounding index : %s" % r.marketing_grounding_index)
    print("  evidence status : %s" % r.evidence_status)
    print("  alerts prefixed : %s" % (r.drift_alerts[0][:40] if r.drift_alerts else "(none)"))

    assert r.evidence_status == "low_confidence", r.evidence_status
    assert r.marketing_grounding_index is not None, "Thin evidence should still score."
    assert r.evidence_note, "low_confidence must explain itself."
    assert all(a.startswith("Provisional - ") for a in r.drift_alerts), (
        "Alerts from thin evidence are not marked provisional: %r" % (r.drift_alerts[:1],)
    )
    assert "provisional" in r.executive_summary.lower()
    print("  PASS - score and alerts both flagged provisional.")


def test_sufficient_evidence_is_unchanged():
    print("\n[3] At or above the floor ...")
    r = build(10, MIN_CONFIDENT_TECHNICAL_EVIDENCE)
    print("  grounding index : %s" % r.marketing_grounding_index)
    print("  evidence status : %s" % r.evidence_status)
    print("  drift alerts    : %d" % len(r.drift_alerts))

    assert r.evidence_status == "conclusive", r.evidence_status
    assert r.evidence_note is None
    assert isinstance(r.marketing_grounding_index, float)
    assert r.drift_alerts, "Genuine unbacked claims must still be reported."
    assert not any(a.startswith("Provisional") for a in r.drift_alerts)
    print("  PASS - full-confidence behaviour preserved.")


def test_no_claims_and_no_evidence_is_still_inconclusive():
    """Empty on both sides must not read as a clean bill of health."""
    print("\n[4] 0 claims, 0 capabilities ...")
    r = build(0, 0)
    print("  grounding index : %r | status: %s" % (r.marketing_grounding_index, r.evidence_status))
    assert r.marketing_grounding_index is None, (
        "Scored %r with nothing on either side." % (r.marketing_grounding_index,)
    )
    assert r.evidence_status == "inconclusive"
    print("  PASS - empty audit does not report a score.")


def test_executable_checks_fail_open_without_evidence():
    """The same hazard one layer up.

    run_executable_checks asserts claims against (openapi_spec + docs_text). Given
    neither, that combined text is empty, nothing matches, and every claim comes
    back CRITICAL_DRIFT. This test pins that behaviour so it stays visible - the
    caller in execute_product_truth_audit must keep skipping these checks when the
    matrix is inconclusive. A live audit of chargebee.com returned 10 such alerts
    after the matrix gate alone was added.
    """
    print("\n[5] Executable checks with no evidence at all ...")
    from truth_ledger.checks import run_executable_checks

    claims = [
        {"predicate": "compliesWith", "object": "SOC 2", "evidence": "We are SOC 2 compliant."},
        {"predicate": "compliesWith", "object": "ASC 606", "evidence": "Full ASC 606 support."},
        {"predicate": "integratesWith", "object": "NetSuite", "evidence": "Syncs with NetSuite."},
    ]
    results = run_executable_checks(marketing_claims=claims, openapi_spec=None, docs_text=None)
    critical = [r for r in results if not r.passed and r.severity == "CRITICAL_DRIFT"]
    print("  %d checks run, %d CRITICAL_DRIFT with zero evidence supplied"
          % (len(results), len(critical)))

    assert critical, (
        "Executable checks no longer fail-open on empty evidence. If that is a real "
        "improvement the gate in execute_product_truth_audit can be revisited; verify "
        "before removing it."
    )

    # And the caller must gate on it.
    import inspect
    src = inspect.getsource(product_truth.execute_product_truth_audit)
    assert 'evidence_status == "inconclusive"' in src, (
        "execute_product_truth_audit no longer skips executable checks when the matrix "
        "is inconclusive. Without that gate these checks re-introduce drift alerts "
        "derived from documentation that could not be read."
    )
    print("  PASS - hazard confirmed, and the audit gates on it.")


if __name__ == "__main__":
    print("=" * 78)
    print("PRODUCT TRUTH - EVIDENCE GATING")
    print("=" * 78)
    test_no_evidence_yields_no_verdict()
    test_thin_evidence_is_marked_provisional()
    test_sufficient_evidence_is_unchanged()
    test_no_claims_and_no_evidence_is_still_inconclusive()
    test_executable_checks_fail_open_without_evidence()
    print("\n" + "=" * 78)
    print("ALL EVIDENCE GATING TESTS PASSED")
    print("A failed docs crawl now reports that it failed, not that the")
    print("customer's marketing is 0% grounded.")
    print("=" * 78)
