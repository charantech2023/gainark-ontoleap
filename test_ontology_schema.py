"""
Generic ontology schema: the declaration must match the system it describes.

ontology_schema.RELATIONS claims that each relation is backed by a predicate the
pipeline emits, a VerticalConfig field, a constants.py global, and a set of GLiNER
labels. Nothing enforced that before. A declaration nobody checks is a comment, and
it rots the first time someone adds a predicate.

These tests read pipeline.py and constants.py as source and assert the four sites
agree. They make no network calls and do not load GLiNER, so they run in about a
second - the slow behavioural coverage lives in test_vertical_vocabulary.py.
"""

import json
import glob
import os
import re

import constants
import ontology_schema as schema
from models import VerticalConfig

PIPELINE_SRC = open("pipeline.py", encoding="utf-8").read()


def test_every_predicate_is_emitted_by_the_pipeline():
    """A declared relation that the extractor never produces is a lie."""
    print("\n[1] Declared predicates are emitted by pipeline.py ...")
    emitted = set(re.findall(r'add_triple\(\s*["\']([A-Za-z]+)["\']', PIPELINE_SRC))
    print("  pipeline emits %d distinct predicates" % len(emitted))

    missing = [p for p in schema.all_predicates() if p not in emitted]
    assert not missing, (
        "Declared in ontology_schema but never emitted by pipeline.py: %s. "
        "Either the pipeline rule was removed or the declaration is aspirational."
        % missing
    )

    undeclared = sorted(emitted - set(schema.all_predicates()))
    assert not undeclared, (
        "pipeline.py emits predicates the schema does not declare: %s. "
        "Add a RelationSpec so drift/scoring code can resolve them." % undeclared
    )
    print("  PASS - %d predicates, both directions" % len(schema.all_predicates()))


def test_every_vocab_field_exists_on_vertical_config():
    """The per-vertical override field must actually be a VerticalConfig field."""
    print("\n[2] Vocab fields exist on VerticalConfig ...")
    fields = set(VerticalConfig.model_fields)
    missing = [r.vocab_field for r in schema.RELATIONS if r.vocab_field not in fields]
    assert not missing, (
        "RelationSpec.vocab_field names that VerticalConfig does not declare: %s. "
        "Pydantic drops undeclared keys, so the vertical vocabulary would be silently "
        "discarded on load." % missing
    )
    print("  PASS - all %d vocab fields present" % len(schema.RELATIONS))


def test_every_global_constant_exists():
    """The documented fallback must resolve, or resolve_vocabulary has nothing to return."""
    print("\n[3] Global fallback constants exist in constants.py ...")
    missing = [
        r.global_constant for r in schema.RELATIONS
        if not isinstance(getattr(constants, r.global_constant, None), list)
    ]
    assert not missing, (
        "constants.py has no list named: %s" % missing
    )
    print("  PASS - all %d fallback lists present" % len(schema.RELATIONS))


def test_gliner_label_mapping_matches_the_pipeline():
    """The schema's label->predicate map must match pipeline.py's elif chain.

    These are the same decision written twice. When they disagree, entity extraction
    silently produces a different predicate than the schema says it does.
    """
    print("\n[4] GLiNER label mapping agrees with pipeline.py ...")
    # Matches: ent.label == "X"  /  ent.label in ["X", "Y"]   followed by the
    # add_triple("predicate", ...) inside that branch.
    # Entity branches go through add_entity_triple (the precision guard that drops a
    # GLiNER assignment the rule pass already claimed for another predicate); the
    # plain add_triple spelling is still accepted so this keeps working either way.
    chain = re.findall(
        r'ent\.label\s*(?:==\s*"([^"]+)"|in\s*\[([^\]]+)\])'
        r'[^\n]*:\s*\n\s*add_(?:entity_)?triple\(\s*"([A-Za-z]+)"',
        PIPELINE_SRC,
    )
    assert chain, "Could not parse the GLiNER elif chain out of pipeline.py."

    pipeline_map = {}
    for single, multi, predicate in chain:
        labels = [single] if single else re.findall(r'"([^"]+)"', multi)
        for label in labels:
            pipeline_map[label] = predicate

    print("  pipeline maps %d labels" % len(pipeline_map))
    for label, predicate in sorted(pipeline_map.items()):
        spec = schema.relation_for_gliner_label(label)
        declared = spec.predicate if spec else None
        flag = "ok" if declared == predicate else "MISMATCH"
        print("    %-22s pipeline=%-20s schema=%-20s %s"
              % (label, predicate, declared, flag))

    mismatches = {
        label: (predicate, getattr(schema.relation_for_gliner_label(label), "predicate", None))
        for label, predicate in pipeline_map.items()
        if getattr(schema.relation_for_gliner_label(label), "predicate", None) != predicate
    }
    assert not mismatches, (
        "GLiNER label -> predicate disagreement between pipeline.py and "
        "ontology_schema.py (label: pipeline vs schema): %s" % mismatches
    )
    print("  PASS")


def _labels_declared_by_verticals():
    labels = set()
    for path in glob.glob("verticals/*.json") + ["vertical_config.json"]:
        if not os.path.exists(path):
            continue
        labels.update(json.load(open(path, encoding="utf-8")).get("gliner_labels", []))
    return labels


def test_benchmarks_and_api_load_the_same_profile():
    """The default pipeline profile must be the one the API actually serves.

    These were two files - vertical_config.json and verticals/b2b_saas_fintech.json -
    both claiming vertical_id b2b_saas_fintech with different gliner_labels (6 vs 16).
    benchmark.py and demo.py read one, routers/deps.py read the other, so scores were
    measured against a configuration no request ever used. With only 6 labels GLiNER
    had no correct bucket for several entities and forced them into the nearest one:
    Chargebee, a competitor, was extracted as an integration partner.
    """
    print("\n[9] Benchmarks and API load the same profile ...")
    from pipeline import DEFAULT_VERTICAL_PROFILE
    from routers.deps import DEFAULT_CONFIG, DEFAULT_VERTICAL_ID

    print("    pipeline default : %s" % os.path.basename(DEFAULT_VERTICAL_PROFILE))
    print("    api default      : %s" % os.path.basename(DEFAULT_CONFIG))
    assert os.path.exists(DEFAULT_VERTICAL_PROFILE), DEFAULT_VERTICAL_PROFILE
    assert os.path.samefile(DEFAULT_VERTICAL_PROFILE, DEFAULT_CONFIG), (
        "The default pipeline profile and the API default config are different files. "
        "Benchmarks would measure a configuration that is never served."
    )

    served = os.path.join("verticals", "%s.json" % DEFAULT_VERTICAL_ID)
    assert os.path.samefile(DEFAULT_VERTICAL_PROFILE, served), (
        "The default profile is not the file routers/deps.py resolves for "
        "DEFAULT_VERTICAL_ID (%s)." % DEFAULT_VERTICAL_ID
    )

    # No pipeline caller may go back to loading the root config by path.
    offenders = []
    for path in glob.glob("*.py") + glob.glob("routers/*.py"):
        if os.path.basename(path) == os.path.basename(__file__):
            continue
        src = open(path, encoding="utf-8").read()
        if re.search(r'OntologyPipeline\(\s*config_path\s*=\s*["\']vertical_config\.json', src):
            offenders.append(path)
    assert not offenders, (
        "These load the root vertical_config.json as a pipeline config again: %s"
        % offenders
    )
    print("  PASS")


def test_every_shipped_label_is_accounted_for():
    """Each label a vertical declares is mapped, unmapped-on-purpose, or triaged.

    An unrecognised label is extraction work thrown away with nothing recording it,
    which is how 44 of these accumulated unnoticed. A new one must fail here.
    """
    print("\n[5] Every shipped GLiNER label is accounted for ...")
    declared = _labels_declared_by_verticals()

    unaccounted = sorted(
        label for label in declared
        if schema.relation_for_gliner_label(label) is None
        and label not in schema.UNMAPPED_GLINER_LABELS
        and label not in schema.TRIAGE_GLINER_LABELS
    )
    mapped = sorted(l for l in declared if schema.relation_for_gliner_label(l))
    triaged = sorted(declared & schema.TRIAGE_GLINER_LABELS)

    print("    mapped to a relation : %d" % len(mapped))
    print("    product-naming       : %d" % len(declared & schema.UNMAPPED_GLINER_LABELS))
    print("    awaiting triage      : %d  %s" % (len(triaged), triaged[:4]))
    for label in unaccounted:
        print("    UNACCOUNTED: %s" % label)

    assert not unaccounted, (
        "Vertical profiles declare GLiNER labels the schema has never seen: %s. "
        "Map each to a RelationSpec, add it to UNMAPPED_GLINER_LABELS if it names "
        "the product, or put it in TRIAGE_GLINER_LABELS to decide later." % unaccounted
    )
    print("  PASS")


def test_triage_backlog_does_not_grow():
    """TRIAGE is a backlog. Pin its size so it shrinks and never quietly grows."""
    print("\n[6] Triage backlog is bounded ...")
    print("    %d labels awaiting a domain decision" % len(schema.TRIAGE_GLINER_LABELS))
    assert len(schema.TRIAGE_GLINER_LABELS) <= 18, (
        "The triage backlog grew. Labels are meant to leave this set by being mapped, "
        "declared product-naming, or removed from the vertical that declares them - "
        "not to accumulate."
    )
    stale = sorted(schema.TRIAGE_GLINER_LABELS - _labels_declared_by_verticals())
    assert not stale, (
        "TRIAGE_GLINER_LABELS lists labels no vertical declares any more: %s. "
        "Drop them." % stale
    )
    print("  PASS")


def test_schema_mappings_the_pipeline_still_ignores():
    """The wiring backlog: labels the schema maps that pipeline.py drops on the floor.

    Informational, and deliberately not a failure - the schema is allowed to describe
    the intended mapping before the extractor is rewired to consult it. This prints
    the exact list that wiring work has to cover.
    """
    print("\n[7] Schema mappings not yet honoured by pipeline.py ...")
    in_pipeline = set(re.findall(r'ent\.label\s*==\s*"([^"]+)"', PIPELINE_SRC))
    for match in re.findall(r'ent\.label\s*in\s*\[([^\]]+)\]', PIPELINE_SRC):
        in_pipeline.update(re.findall(r'"([^"]+)"', match))

    declared = _labels_declared_by_verticals()
    unwired = sorted(
        label for label in declared
        if schema.relation_for_gliner_label(label) and label not in in_pipeline
    )
    for label in unwired:
        print("    %-24s -> %s" % (label, schema.relation_for_gliner_label(label).predicate))
    print("    %d label(s) mapped in schema, dropped by pipeline.py" % len(unwired))
    print("  PASS (informational)")


def test_coverage_report_for_shipped_verticals():
    """Not an assertion about quality - a printed inventory of what is half-wired.

    Fails only if the default vertical wires up nothing at all, which would mean the
    schema and the profiles have diverged completely.
    """
    print("\n[8] Per-vertical schema coverage ...")
    print("    %-42s %6s %6s %6s" % ("vertical", "wired", "novocab", "nolabel"))
    default_wired = None
    for path in sorted(glob.glob("verticals/*.json")):
        cfg = VerticalConfig(**json.load(open(path, encoding="utf-8")))
        cov = schema.check_vertical_coverage(cfg)
        print("    %-42s %6d %6d %6d" % (
            os.path.basename(path),
            len(cov["fully_wired"]), len(cov["no_vocabulary"]), len(cov["no_gliner_label"]),
        ))
        if cfg.vertical_id == "b2b_saas_fintech":
            default_wired = cov

    assert default_wired is not None, "The default vertical b2b_saas_fintech is missing."
    assert default_wired["fully_wired"], (
        "The default vertical wires up none of the %d relations end to end."
        % len(schema.RELATIONS)
    )
    print("    default vertical fully wired: %s" % default_wired["fully_wired"])
    print("  PASS")


if __name__ == "__main__":
    print("=" * 78)
    print("GENERIC ONTOLOGY SCHEMA")
    print("=" * 78)
    test_every_predicate_is_emitted_by_the_pipeline()
    test_every_vocab_field_exists_on_vertical_config()
    test_every_global_constant_exists()
    test_gliner_label_mapping_matches_the_pipeline()
    test_every_shipped_label_is_accounted_for()
    test_triage_backlog_does_not_grow()
    test_schema_mappings_the_pipeline_still_ignores()
    test_coverage_report_for_shipped_verticals()
    test_benchmarks_and_api_load_the_same_profile()
    print("\n" + "=" * 78)
    print("ALL ONTOLOGY SCHEMA TESTS PASSED")
    print("=" * 78)
