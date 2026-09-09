"""
Concepts are identified independently of what they are called.

Before this, a concept WAS its display string. The string keyed every vocabulary list
and concept_hierarchy, and a slugify of it produced the graph URI - so renaming a
concept silently minted a different resource and orphaned every triple about the old
one. Worse, that URI was built under the audited client's own domain, which made
"Revenue Schedules" in an Ordway graph a different resource from the same concept in a
Chargebee graph: the shared vertical ontology existed only as a private per-client copy,
and no question could span companies.

These tests hold both properties: identity survives renaming, and concepts live in one
shared namespace while instance data stays with the client.

No network calls and no GLiNER - this is model and graph level only.
"""

import json

from models import Concept, VerticalConfig, SemanticTriple
from knowledge_graph import export_to_rdf_turtle
import ontology_schema as schema
from pipeline import DEFAULT_VERTICAL_PROFILE

PROFILE = json.load(open(DEFAULT_VERTICAL_PROFILE, encoding="utf-8"))
CONFIG = VerticalConfig(**PROFILE)


def test_concepts_load_and_derive_the_flat_views():
    """`concepts` is the source of truth; the old fields are projections of it."""
    print("\n[1] Concepts derive the legacy vocabulary views ...")
    assert CONFIG.concepts, "The default vertical declares no concepts."
    by_kind = {}
    for c in CONFIG.concepts:
        by_kind.setdefault(c.kind, []).append(c)
    print("    %d concepts: %s" % (
        len(CONFIG.concepts),
        ", ".join("%s %d" % (k, len(v)) for k, v in sorted(by_kind.items()))))

    expected = {
        "known_features": "feature",
        "known_automation": "process",
        "known_pricing": "pricing",
        "known_compliance": "standard",
    }
    for field, kind in expected.items():
        derived = getattr(CONFIG, field)
        want = [c.prefLabel for c in CONFIG.concepts if c.kind == kind]
        assert sorted(derived) == sorted(want), (
            "%s does not match the %r concepts it should be derived from. The flat "
            "lists are projections - if they can disagree there are two sources of "
            "truth again." % (field, kind)
        )
    print("    all four vocabulary buckets match their concept kinds")
    print("  PASS")


def test_ids_are_stable_shaped_and_unique():
    print("\n[2] Ids are slug-shaped and unique ...")
    ids = [c.id for c in CONFIG.concepts]
    assert len(ids) == len(set(ids)), "Duplicate concept ids: identity is not unique."
    # The pattern is enforced by the model; this asserts the data actually went
    # through it rather than being hand-edited into the file.
    for c in CONFIG.concepts:
        assert c.id == c.id.lower() and " " not in c.id, "Malformed id %r" % c.id
    print("    %d unique ids" % len(ids))
    print("  PASS")


def test_every_concept_has_a_definition():
    """Without definitions no reviewer or model can check whether a match was right."""
    print("\n[3] Every concept is defined ...")
    undefined = [c.prefLabel for c in CONFIG.concepts if not c.definition.strip()]
    assert not undefined, (
        "%d concepts carry no definition: %s. A concept with no written meaning cannot "
        "be reviewed, and two similar ones cannot be told apart."
        % (len(undefined), undefined[:8])
    )
    print("    %d definitions, none empty" % len(CONFIG.concepts))
    print("  PASS")


def test_hierarchy_and_governs_resolve():
    print("\n[4] broader and governs point at real concepts ...")
    ids = {c.id for c in CONFIG.concepts}
    roots = [c for c in CONFIG.concepts if c.broader is None]
    assert len(roots) == 1, (
        "Expected exactly one root; found %s. An orphan concept is invisible to "
        "ancestor inference." % [c.prefLabel for c in roots]
    )
    dangling = [(c.id, g) for c in CONFIG.concepts for g in c.governs if g not in ids]
    assert not dangling, "governs points at unknown ids: %s" % dangling
    links = sum(len(c.governs) for c in CONFIG.concepts)
    print("    root: %s | %d governing links" % (roots[0].prefLabel, links))
    print("  PASS")


def test_standards_are_not_narrower_kinds_of_capabilities():
    """The relation split. ASC 606 governs revenue recognition; it is not a kind of it."""
    print("\n[5] Standards sit in their own branch ...")
    by_id = {c.id: c for c in CONFIG.concepts}
    asc = CONFIG.concept_by_label("ASC 606")
    assert asc is not None, "ASC 606 is missing from the vertical."
    parent = by_id[asc.broader]
    print("    ASC 606 broader -> %s" % parent.prefLabel)
    print("    ASC 606 governs -> %s" % [by_id[g].prefLabel for g in asc.governs])
    assert parent.kind == "domain" and "Standard" in parent.prefLabel, (
        "ASC 606's parent is %r. A standard parented onto the capability it regulates "
        "makes coverage inference treat complying with a rule and shipping a feature as "
        "the same class of evidence." % parent.prefLabel
    )
    assert any(by_id[g].prefLabel == "Revenue Recognition" for g in asc.governs), (
        "The ASC 606 -> Revenue Recognition link was lost when standards moved branch. "
        "It should have become a governs relation, not disappeared."
    )
    print("  PASS")


def test_dangling_parent_is_rejected():
    print("\n[6] A dangling parent fails loudly ...")
    try:
        VerticalConfig(
            vertical_id="x", display_name="X", gliner_labels=["A"],
            mandatory_schema_types=["Organization"], core_seed_concepts=["T"],
            concepts=[Concept(id="a", prefLabel="A", kind="feature", broader="nope")],
        )
    except ValueError as e:
        assert "not a concept id" in str(e), e
        print("    rejected as expected")
        print("  PASS")
        return
    raise AssertionError(
        "A concept whose broader names no existing concept was accepted. It would drop "
        "silently out of the hierarchy and out of ancestor inference."
    )


def test_identity_survives_renaming():
    """The property the whole change exists for."""
    print("\n[7] Renaming a concept does not change its URI ...")
    c = CONFIG.concept_by_label("Renewal")
    before = schema.concept_uri(CONFIG.vertical_id, c.id)
    renamed = c.model_copy(update={"prefLabel": "Renewals & Extensions"})
    after = schema.concept_uri(CONFIG.vertical_id, renamed.id)
    print("    %r -> %r" % (c.prefLabel, renamed.prefLabel))
    print("    uri unchanged: %s" % after.rsplit("/", 1)[-1])
    assert before == after, (
        "The URI moved when the label changed, so every triple previously emitted "
        "about this concept now refers to a different resource."
    )
    # And the slug helper must not be used as a lookup path back to identity.
    assert schema.slug_for_label("Renewals & Extensions") != c.id, (
        "This fixture no longer demonstrates anything: the renamed label happens to "
        "slug back to the original id. Pick a different rename."
    )
    print("  PASS")


def test_graph_separates_shared_concepts_from_client_instances():
    print("\n[8] Emitted graph: concepts shared, instances client-scoped ...")
    ttl = export_to_rdf_turtle(
        "ordwaylabs.com",
        [SemanticTriple(subject="Ordway", predicate="hasFeature", object="Renewal",
                        confidence=0.9, evidence_sentence="Ordway handles renewal.")],
        hubs={}, entities=[], vertical_id=CONFIG.vertical_id,
    )
    shared = schema.concept_uri(CONFIG.vertical_id, "renewal")
    print("    shared concept present : %s" % (shared in ttl))
    print("    client concept URIs    : %s" % ("ordwaylabs.com/concept" in ttl))

    assert shared in ttl, "The shared concept URI is not in the graph."
    assert "ordwaylabs.com/concept" not in ttl, (
        "Concepts are still minted under the client domain, so the same concept in two "
        "clients' graphs remains two unjoinable resources."
    )
    assert "ordwaylabs.com/entity/" in ttl, (
        "Instance data should stay under the client's domain - that part genuinely is "
        "client-specific."
    )
    for needle in ("skos:definition", "skos:altLabel", "onto:governs"):
        assert needle in ttl, "%s never reaches the graph." % needle
    print("    definitions, altLabels and governs all emitted")
    print("  PASS")


if __name__ == "__main__":
    print("=" * 78)
    print("CONCEPT IDENTITY")
    print("=" * 78)
    test_concepts_load_and_derive_the_flat_views()
    test_ids_are_stable_shaped_and_unique()
    test_every_concept_has_a_definition()
    test_hierarchy_and_governs_resolve()
    test_standards_are_not_narrower_kinds_of_capabilities()
    test_dangling_parent_is_rejected()
    test_identity_survives_renaming()
    test_graph_separates_shared_concepts_from_client_instances()
    print("\n" + "=" * 78)
    print("ALL CONCEPT IDENTITY TESTS PASSED")
    print("=" * 78)
