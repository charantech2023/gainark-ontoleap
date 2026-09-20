"""
Per-vertical extraction vocabulary.

The industry ontology used to contribute almost nothing to extraction. The
profiler generated known_compliance / known_integrations / known_pricing, the
vertical JSON files stored them - healthtech.json carries Epic Systems, Cerner,
HIPAA and HITECH - and then VerticalConfig declared only five fields, so Pydantic
silently discarded the rest on load. Triple extraction fell back to the global,
billing-flavoured lists in constants.py, and a hospital software site was scanned
for "Dunning Automation" and NetSuite.

These tests hold the fields open and prove the vocabulary actually reaches the
extractor: the same text must yield different capabilities under different
verticals, and a vertical without its own lists must behave exactly as before.

Loads GLiNER (the pipeline needs it) but makes no network calls.
"""

import json

from constants import (
    KNOWN_AUTOMATION, KNOWN_COMPLIANCE, KNOWN_INTEGRATIONS, KNOWN_PRICING,
    KNOWN_SEGMENTS, KNOWN_COMPETITORS,
    resolve_vocabulary, resolve_matching_vocabulary,
)
from models import VerticalConfig

FINTECH = "verticals/b2b_saas_fintech.json"
HEALTHTECH = "verticals/healthtech.json"

# Deliberately mixed copy: a healthcare billing vendor would plausibly publish all
# of this. Which terms are recognised should depend on the vertical, not the text.
MIXED_COPY = """
Acme Health automates clinical documentation and streamlines revenue cycle management
for hospital networks. The platform integrates with Epic Systems and Cerner, and also
syncs with NetSuite for finance teams. Acme is HIPAA compliant and HITECH compliant,
and maintains SOC 2 Type II attestation. We support ASC 606 revenue recognition.
Our pricing uses Per-Provider Pricing for clinics and Usage-Based Pricing for API access.
Acme automates billing automation and invoicing workflows across the care network.
"""


def load(path):
    return VerticalConfig(**json.load(open(path, encoding="utf-8")))


def extract(config):
    """Run rule-based triple extraction under one vertical, grouped by predicate."""
    from pipeline import OntologyPipeline
    result = OntologyPipeline(config=config).process(text=MIXED_COPY)
    grouped = {}
    for t in result.triples:
        grouped.setdefault(t.predicate, set()).add(t.object)
    return grouped


def test_vertical_fields_survive_loading():
    """The regression that made the industry ontology inert."""
    print("\n[1] Vertical vocabulary survives VerticalConfig ...")
    raw = json.load(open(HEALTHTECH, encoding="utf-8"))
    cfg = load(HEALTHTECH)
    for field in ("known_compliance", "known_integrations", "known_pricing"):
        in_file = raw.get(field) or []
        on_config = getattr(cfg, field, None)
        print("  %-20s file=%-2d config=%s" % (field, len(in_file), len(on_config or [])))
        assert in_file, "%s missing from %s - fixture no longer covers this." % (field, HEALTHTECH)
        assert on_config == in_file, (
            "%s is stored in the vertical file but does not reach the config. Pydantic "
            "drops undeclared fields, so the industry ontology stops affecting extraction."
            % field
        )
    print("  PASS")


def test_resolver_prefers_vertical_then_falls_back():
    print("\n[2] Resolution order ...")
    health, fin = load(HEALTHTECH), load(FINTECH)
    bare = VerticalConfig(
        vertical_id="bare", display_name="Bare", gliner_labels=["X"],
        mandatory_schema_types=["Organization"], core_seed_concepts=["Thing"],
    )
    h = resolve_vocabulary(health, "known_compliance", KNOWN_COMPLIANCE)
    f = resolve_vocabulary(fin, "known_compliance", KNOWN_COMPLIANCE)
    b = resolve_vocabulary(bare, "known_compliance", KNOWN_COMPLIANCE)
    n = resolve_vocabulary(None, "known_compliance", KNOWN_COMPLIANCE)
    print("  healthtech -> %s" % h[:3])
    print("  fintech    -> %s" % f[:3])
    print("  bare/None  -> falls back to global defaults")

    assert any("HIPAA" in x for x in h), "Healthcare vertical lost its own compliance list."
    assert h != f, "Two different verticals resolved to the same vocabulary."
    assert b == KNOWN_COMPLIANCE and n == KNOWN_COMPLIANCE, (
        "A vertical with no vocabulary of its own must fall back to the global "
        "defaults, so configs written before these fields existed keep working."
    )
    print("  PASS")


def test_same_text_extracts_differently_per_vertical():
    """The point of the change, stated as a behaviour."""
    print("\n[3] Same copy, two verticals ...")
    fin = extract(load(FINTECH))
    health = extract(load(HEALTHTECH))
    for label, g in (("fintech", fin), ("healthtech", health)):
        print("  %-11s integratesWith=%s" % (label, sorted(g.get("integratesWith", []))))
        print("  %-11s compliesWith  =%s" % ("", sorted(g.get("compliesWith", []))))

    fin_int = {i.lower() for i in fin.get("integratesWith", [])}
    health_int = {i.lower() for i in health.get("integratesWith", [])}

    # Epic Systems is absent from the global list and present in healthtech's, so it
    # is only reachable when the vertical vocabulary is actually consulted.
    assert not any("epic" in i for i in KNOWN_INTEGRATIONS), (
        "Epic Systems is now in the global integration list, so it no longer proves "
        "the vertical vocabulary is being used. Pick another vertical-only term."
    )
    assert any("epic" in i for i in health_int), (
        "Epic Systems was not extracted under the healthcare vertical, so the "
        "vertical's own integration list is still not reaching the extractor."
    )
    assert not any("epic" in i for i in fin_int), (
        "Epic Systems was extracted under the fintech vertical, which does not list "
        "it - vocabulary is leaking across verticals."
    )
    assert any("netsuite" in i for i in fin_int), "NetSuite should be found under fintech."
    assert not any("netsuite" in i for i in health_int), (
        "NetSuite was extracted under healthcare, which does not list it."
    )

    fin_comp = {c.lower() for c in fin.get("compliesWith", [])}
    health_comp = {c.lower() for c in health.get("compliesWith", [])}
    assert any("asc 606" in c for c in fin_comp), "ASC 606 should be found under fintech."
    assert not any("asc 606" in c for c in health_comp), (
        "ASC 606 was extracted under healthcare, whose compliance list does not "
        "include it. The billing vocabulary is still bleeding through."
    )
    print("  PASS - Epic only in healthcare, NetSuite and ASC 606 only in fintech.")


def test_icp_findings_do_not_become_matching_vocabulary():
    """A discovered segment is evidence about one site, not vocabulary for every site.

    ICP discovery writes known_segments, and its honest answer generalises over one
    customer: b2b_saas_fintech holds six descriptions like "Equipment lifecycle software
    company in the construction industry", each quoted from a single case study. A filled
    field overrides the global taxonomy, so those six replaced KNOWN_SEGMENTS - and none
    of them appears verbatim on any other page. targetsSegment silently stopped firing
    for the whole vertical.
    """
    print("\n[4] ICP findings are not matching vocabulary ...")
    fin = load(FINTECH)
    discovered = set((fin.icp_evidence.get("known_segments") or {}).keys())
    assert discovered, (
        "This test needs a vertical whose known_segments came from ICP discovery; "
        "b2b_saas_fintech no longer has icp_evidence for that field."
    )

    raw = resolve_vocabulary(fin, "known_segments", KNOWN_SEGMENTS)
    matching = resolve_matching_vocabulary(fin, "known_segments", KNOWN_SEGMENTS)
    print("  stored    -> %d entries, %d of them ICP findings" % (len(raw), len(discovered)))
    print("  matching  -> %d entries" % len(matching))

    assert not (discovered & set(matching)), (
        "An ICP finding is being used as matching vocabulary. It cannot match: the value "
        "is not expected to appear even in its own proving quote."
    )
    assert set(KNOWN_SEGMENTS) <= set(matching), (
        "Dropping the findings left the vertical with less than the global taxonomy, so "
        "targetsSegment still cannot fire."
    )

    # The findings themselves are untouched - prompt_generator still reads them.
    assert discovered <= set(fin.known_segments), (
        "The findings were removed from the profile rather than merely excluded from "
        "matching."
    )
    print("  PASS - findings stay in the profile, out of the matcher.")


def test_curated_vertical_keeps_its_override():
    """The fix must not leak into verticals a human wrote.

    cybersecurity.json carries a hand-written segment taxonomy and no icp_evidence, so it
    must resolve exactly as before - narrowing it to the global list would undo the whole
    point of per-vertical vocabulary.
    """
    print("\n[5] Curated verticals keep their override ...")
    cyber = VerticalConfig(**json.load(open("verticals/cybersecurity.json", encoding="utf-8")))
    assert not cyber.icp_evidence, "cybersecurity.json unexpectedly carries icp_evidence."

    for field, default in (("known_segments", KNOWN_SEGMENTS),
                           ("known_competitors", KNOWN_COMPETITORS)):
        before = resolve_vocabulary(cyber, field, default)
        after = resolve_matching_vocabulary(cyber, field, default)
        assert before == after, (
            "%s changed for a curated vertical; the override contract only bends for "
            "fields ICP discovery has written into." % field
        )
    print("  PASS - MSSP and Public Sector still override the global list.")


def test_targets_segment_fires_under_a_discovered_vertical():
    """The behaviour, not the plumbing: the predicate comes back."""
    print("\n[6] targetsSegment fires under b2b_saas_fintech ...")
    from pipeline import OntologyPipeline
    copy = (
        "Acme Platform offers Automated Invoicing and Subscription Management. "
        "Built for Enterprise and High-Growth SaaS finance teams. "
        "Leading solution for SaaS and FinTech companies."
    )
    result = OntologyPipeline(config=load(FINTECH)).process(text=copy)
    segments = {t.object for t in result.triples if t.predicate == "targetsSegment"}
    print("  targetsSegment -> %s" % sorted(segments))
    assert "Enterprise" in segments, (
        "targetsSegment found nothing under the vertical whose known_segments ICP "
        "discovery overwrote."
    )
    print("  PASS")



if __name__ == "__main__":
    print("=" * 78)
    print("PER-VERTICAL EXTRACTION VOCABULARY")
    print("=" * 78)
    test_vertical_fields_survive_loading()
    test_resolver_prefers_vertical_then_falls_back()
    test_same_text_extracts_differently_per_vertical()
    test_icp_findings_do_not_become_matching_vocabulary()
    test_curated_vertical_keeps_its_override()
    test_targets_segment_fires_under_a_discovered_vertical()
    print("\n" + "=" * 78)
    print("ALL VERTICAL VOCABULARY TESTS PASSED")
    print("The industry ontology now drives what every site is read for.")
    print("=" * 78)
