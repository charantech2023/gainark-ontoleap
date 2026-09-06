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
    resolve_vocabulary,
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


if __name__ == "__main__":
    print("=" * 78)
    print("PER-VERTICAL EXTRACTION VOCABULARY")
    print("=" * 78)
    test_vertical_fields_survive_loading()
    test_resolver_prefers_vertical_then_falls_back()
    test_same_text_extracts_differently_per_vertical()
    print("\n" + "=" * 78)
    print("ALL VERTICAL VOCABULARY TESTS PASSED")
    print("The industry ontology now drives what every site is read for.")
    print("=" * 78)
