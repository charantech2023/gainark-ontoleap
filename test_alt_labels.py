"""
Marketing register and documentation register must resolve to one concept.

A product's marketing page and its own documentation describe the same capability
with different words. Validated against 818 Ordway support articles, 28 of 29
drafted feature labels were absent from the docs while their base concept appeared
in volume - "Renewal Management" nowhere, "renewal" 335 times. The audit compares
marketing claims against documentation evidence, so that mismatch made the tool
report drift on capabilities the product demonstrably has.

alt_labels maps every surface form back to one canonical label, and extraction emits
the canonical whichever form it matched. These tests assert that convergence, plus
the structural invariants that keep the mapping unambiguous.

Loads GLiNER. No network calls.
"""

import json
import re

from constants import KNOWN_FEATURES, resolve_surface_forms, resolve_vocabulary
from models import VerticalConfig
from pipeline import OntologyPipeline, DEFAULT_VERTICAL_PROFILE

PROFILE = json.load(open(DEFAULT_VERTICAL_PROFILE, encoding="utf-8"))
CONFIG = VerticalConfig(**PROFILE)

_pipeline = None


def pipe():
    global _pipeline
    if _pipeline is None:
        _pipeline = OntologyPipeline()
    return _pipeline


def claims(text):
    return {(t.predicate, t.object) for t in pipe().process(text=text).triples}


def test_alt_labels_survive_loading():
    """Pydantic drops undeclared fields, which is how the vocabulary went inert before."""
    print("\n[1] alt_labels reaches VerticalConfig ...")
    raw = PROFILE.get("alt_labels") or {}
    print("    %d canonicals, %d alternates" % (raw and len(raw), sum(len(v) for v in raw.values())))
    assert raw, "The default vertical declares no alt_labels - fixture no longer covers this."
    assert CONFIG.alt_labels == raw, (
        "alt_labels is stored in the vertical file but does not reach the config, so "
        "every concept falls back to a single spelling."
    )
    print("  PASS")


def test_every_alt_maps_to_exactly_one_canonical():
    """An ambiguous alternate would make the emitted concept depend on iteration order."""
    print("\n[2] Alternates are unambiguous ...")
    owner = {}
    dupes = []
    for canon, alts in CONFIG.alt_labels.items():
        for alt in alts:
            key = alt.lower()
            if key in owner and owner[key] != canon:
                dupes.append("%r -> %r and %r" % (alt, owner[key], canon))
            owner[key] = canon
    assert not dupes, "Alternates mapping to two canonicals: %s" % dupes

    # An alternate must not also be a canonical somewhere, or one mention would be
    # both a concept and a synonym for a different concept.
    canonicals = set()
    for field in ("known_features", "known_automation", "known_pricing",
                  "known_compliance", "known_integrations"):
        canonicals |= {v.lower() for v in (getattr(CONFIG, field, None) or [])}
    collisions = [a for a in owner if a in canonicals]
    assert not collisions, (
        "These are both a canonical label and an alternate for something else: %s"
        % collisions
    )
    print("    %d alternates, all unambiguous" % len(owner))
    print("  PASS")


def test_surface_forms_resolver_returns_longest_first():
    """Callers stop at the first match, so specificity must come first."""
    print("\n[3] Surface forms ordered longest-first ...")
    forms = dict(resolve_surface_forms(CONFIG, "known_features", KNOWN_FEATURES))
    probe = "Revenue Schedules"
    assert probe in forms, "%s missing from the feature vocabulary." % probe
    lengths = [len(f) for f in forms[probe]]
    print("    %s -> %s" % (probe, forms[probe]))
    assert lengths == sorted(lengths, reverse=True), (
        "Surface forms are not longest-first, so a short form can match before a more "
        "specific one and the wrong canonical wins."
    )
    print("  PASS")


def test_resolver_falls_back_without_alt_labels():
    """A vertical with no alt_labels must behave exactly as before."""
    print("\n[4] No alt_labels means one form per concept ...")
    bare = VerticalConfig(
        vertical_id="bare", display_name="Bare", gliner_labels=["X"],
        mandatory_schema_types=["Organization"], core_seed_concepts=["Thing"],
        known_features=["Audit Trail", "Data Export"],
    )
    forms = resolve_surface_forms(bare, "known_features", KNOWN_FEATURES)
    print("    %s" % forms)
    assert forms == [("Audit Trail", ["Audit Trail"]), ("Data Export", ["Data Export"])], (
        "A vertical without alt_labels must yield each term as its own only surface form."
    )
    assert resolve_surface_forms(None, "known_features", ["A"]) == [("A", ["A"])], (
        "A null config must fall back to the global default vocabulary."
    )
    print("  PASS")


def test_marketing_and_docs_converge_on_one_concept():
    """The reason this layer exists."""
    print("\n[5] Marketing copy and documentation converge ...")
    CASES = [
        ("Our platform offers Renewal Management and a Customer Payment Portal.",
         "To process a renewal, open the customer portal and select the subscription.",
         [("hasFeature", "Renewal"), ("hasFeature", "Customer Portal")]),
        ("Sales Tax Calculation and Revenue Recognition Automation are built in.",
         "Tax calculation applies at invoice time. Revenue recognition uses schedules.",
         [("automates", "Revenue Recognition")]),
    ]
    for marketing, docs, expected in CASES:
        m, d = claims(marketing), claims(docs)
        print("    marketing -> %s" % sorted(m))
        print("    docs      -> %s" % sorted(d))
        for claim in expected:
            assert claim in m, (
                "%s not raised from marketing copy %r - the alternate did not resolve "
                "to its canonical." % (claim, marketing)
            )
            assert claim in d, (
                "%s not found in documentation %r. Marketing raises the claim and the "
                "docs cannot verify it, so the audit reports drift on a capability the "
                "product has." % (claim, docs)
            )
    print("  PASS")


def test_raw_alternate_never_reaches_the_graph():
    """Entity extraction returns the page's own words; they must be normalised.

    NER hands back the matched span verbatim, so 'Automated Invoicing' and
    'Role-Based Access Control' were entering the graph as concepts of their own
    alongside the canonical 'Invoicing' and 'Permissions' - the same split this layer
    exists to close, reintroduced one code path later.
    """
    print("\n[6] Alternates never emitted as concepts ...")
    got = claims("Features include Role-Based Access Control and Automated Invoicing.")
    objects = {o for _, o in got}
    print("    %s" % sorted(got))

    every_alt = {a for alts in CONFIG.alt_labels.values() for a in alts}
    leaked = objects & every_alt
    assert not leaked, (
        "These alternates were emitted as concepts instead of their canonical: %s"
        % sorted(leaked)
    )
    assert "Permissions" in objects, "Role-Based Access Control did not normalise."
    assert "Invoicing" in objects, "Automated Invoicing did not normalise."
    print("  PASS")


if __name__ == "__main__":
    print("=" * 78)
    print("IDENTITY LAYER - CANONICAL LABELS AND ALTERNATES")
    print("=" * 78)
    test_alt_labels_survive_loading()
    test_every_alt_maps_to_exactly_one_canonical()
    test_surface_forms_resolver_returns_longest_first()
    test_resolver_falls_back_without_alt_labels()
    test_marketing_and_docs_converge_on_one_concept()
    test_raw_alternate_never_reaches_the_graph()
    print("\n" + "=" * 78)
    print("ALL IDENTITY LAYER TESTS PASSED")
    print("=" * 78)
