"""
Alert precision for the Product Truth audit.

Evidence gating (test_evidence_gating.py) stopped the tool accusing brands on zero
evidence. This suite covers the next failure: accusing them wrongly on real evidence.

A live audit of chargebee.com against its own API documentation produced 45 drift
alerts, among them "Regulatory Drift: Marketing claims compliance with 'SQL'" - and
the same for 'CPQ', 'MRR', 'Net D', 'global taxes' and 'enterprise-grade safeguards'.
Those are a query language, a sales process, a metric, payment terms and marketing
prose. The extractor files anything near compliance language under compliesWith, and
the alert generator trusted the label.

A reader who spots one obviously wrong alert discounts the correct ones beside it, so
precision here matters more than recall.

Offline; no network and no GLiNER.
"""

from product_truth import _is_recognized_standard, _dedupe_triples, build_product_truth_matrix
from models import SemanticTriple

# Objects seen in live compliesWith output, split by whether they are actually standards.
REAL_STANDARDS = [
    "ASC 606", "IFRS 15", "GAAP", "US GAAP", "GDPR", "HIPAA", "CCPA",
    "PCI", "PCI-DSS", "PCI-compliant", "SOC 1", "SOC 2", "SOC 2 Type II",
    "ISO 27001", "SAML", "SCIM", "OAuth 2.0", "SEPA", "PEPPOL",
    "EU VAT", "Australian GST",
]
NOT_STANDARDS = [
    "SQL", "CPQ", "MRR", "Net D", "global taxes", "enterprise-grade safeguards",
    "Omnichannel", "MCP", "accounting system", "tiered", "AI-powered retention engine",
]


def triple(predicate, obj, source="marketing_claim"):
    return SemanticTriple(
        subject="Acme", predicate=predicate, object=obj,
        confidence=0.9, source_type=source,
    )


def test_standard_recognition():
    print("\n[1] Recognising real standards vs extraction noise ...")
    missed = [o for o in REAL_STANDARDS if not _is_recognized_standard(o)]
    leaked = [o for o in NOT_STANDARDS if _is_recognized_standard(o)]
    print("  %d/%d real standards recognised" % (len(REAL_STANDARDS) - len(missed), len(REAL_STANDARDS)))
    print("  %d/%d non-standards correctly rejected" % (len(NOT_STANDARDS) - len(leaked), len(NOT_STANDARDS)))

    assert not missed, (
        "Real standards would no longer raise a regulatory alert, silently losing "
        "genuine findings: %s" % missed
    )
    assert not leaked, (
        "Extraction noise would be reported as a compliance claim: %s. Each of these "
        "renders as 'Regulatory Drift: Marketing claims compliance with ...' and is "
        "wrong on its face." % leaked
    )
    print("  PASS")


def test_no_regulatory_alert_for_non_standards():
    """End to end through the matrix, not just the predicate helper."""
    print("\n[2] Non-standards must raise no regulatory alert ...")
    marketing = [triple("compliesWith", o) for o in NOT_STANDARDS]
    marketing += [triple("compliesWith", "ASC 606")]
    technical = [triple("compliesWith", "SOC 2", "technical_truth")]
    technical += [triple("automates", "Capability %d" % i, "technical_truth") for i in range(5)]

    r = build_product_truth_matrix(
        marketing_triples=marketing, technical_triples=technical,
        brand_name="Acme", marketing_url="https://acme.example",
        tech_docs_url="https://docs.acme.example",
    )
    regulatory = [a for a in r.drift_alerts if "Regulatory Drift" in a]
    print("  %d regulatory alerts from %d non-standards + 1 real standard"
          % (len(regulatory), len(NOT_STANDARDS)))
    for a in regulatory:
        print("   -", a[:88])

    for junk in NOT_STANDARDS:
        assert not any(f"'{junk}'" in a for a in regulatory), (
            "Raised a regulatory alert for %r, which is not a compliance standard." % junk
        )
    assert any("ASC 606" in a for a in regulatory), (
        "Suppressed the alert for ASC 606, a real standard. The gate is too aggressive "
        "and is now hiding genuine findings."
    )
    print("  PASS - only the real standard alerts.")


def test_alerts_do_not_overstate_certainty():
    """Absence from API reference docs is not proof a claim is false."""
    print("\n[3] Regulatory alert wording ...")
    r = build_product_truth_matrix(
        marketing_triples=[triple("compliesWith", "ASC 606")],
        technical_triples=[triple("compliesWith", "SOC 2", "technical_truth")]
        + [triple("automates", "Cap %d" % i, "technical_truth") for i in range(5)],
        brand_name="Acme", marketing_url="https://acme.example",
        tech_docs_url="https://docs.acme.example",
    )
    alert = next(a for a in r.drift_alerts if "Regulatory Drift" in a)
    print("  %s" % alert[:150])
    assert "was not found in" in alert, "Alert should say where it looked."
    assert "trust or security page" in alert, (
        "Alert should tell the reader compliance attestations often live outside API "
        "docs, so a missing mention is not proof of a missing capability."
    )
    print("  PASS - states where it looked and what that does not prove.")


def test_dedupe_collapses_wording_not_meaning():
    print("\n[4] Deduplication ...")
    items = [
        triple("compliesWith", "PCI"), triple("compliesWith", "PCI-compliant"),
        triple("compliesWith", "OpenAPI"), triple("compliesWith", "OpenAPI Specification"),
        triple("automates", "Subscription Management"),
        triple("automates", "Subscription Billing"),
        triple("integratesWith", "NetSuite"),
    ]
    out = _dedupe_triples(items)
    objs = [t.object for t in out]
    print("  %d -> %d : %s" % (len(items), len(out), objs))

    assert "PCI-compliant" not in objs, "'PCI' and 'PCI-compliant' should collapse."
    assert sum(1 for o in objs if "OpenAPI" in o) == 1, "OpenAPI variants should collapse."
    assert "Subscription Management" in objs and "Subscription Billing" in objs, (
        "Distinct capabilities were merged. _concepts_match treats one shared token as a "
        "match; deduplication must be stricter than that or real claims disappear."
    )
    assert "NetSuite" in objs
    print("  PASS - wording collapsed, meaning preserved.")


if __name__ == "__main__":
    print("=" * 78)
    print("PRODUCT TRUTH - ALERT PRECISION")
    print("=" * 78)
    test_standard_recognition()
    test_no_regulatory_alert_for_non_standards()
    test_alerts_do_not_overstate_certainty()
    test_dedupe_collapses_wording_not_meaning()
    print("\n" + "=" * 78)
    print("ALL ALERT PRECISION TESTS PASSED")
    print("Compliance alerts fire only for real standards, are deduplicated,")
    print("and say where they looked rather than asserting the claim is false.")
    print("=" * 78)
