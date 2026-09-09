"""
One authority over what a phrase means.

Two systems were mapping surface forms to concepts by opposite methods. The vertical's
curated alt_labels normalise - every spelling resolves to one canonical. The generated
synonym map multiplied instead: expand_tech_triples() cloned each technical capability
into marketing-worded copies so the matcher would find them.

Running both was worse than either. Of 114 generated pairs for Ordway, 1 agreed with the
curated mapping. "automated tax calculation" was generated as a synonym of "avalara",
which would let evidence about an integration partner verify a claim about a tax
capability, and the clones inflated the technical capability count because one real
capability became several.

Generation is now a proposer. These tests hold that boundary: nothing generated reaches
the graph unreviewed, the curated answer wins any disagreement, and an approval lands in
the ontology rather than in a side cache.

No network calls, no GLiNER.
"""

import json
import os
import shutil
import tempfile

import sector_ontology as so
from models import VerticalConfig
from pipeline import DEFAULT_VERTICAL_PROFILE


def sandbox():
    """A throwaway copy of the real vertical, plus an empty candidate queue."""
    d = tempfile.mkdtemp()
    vertical = os.path.join(d, "vertical.json")
    shutil.copy(DEFAULT_VERTICAL_PROFILE, vertical)
    return vertical, os.path.join(d, "candidates.json")


def load(path):
    return VerticalConfig(**json.load(open(path, encoding="utf-8")))


def test_generated_synonyms_never_reach_the_graph():
    """The injection path is gone, not merely unused."""
    print("\n[1] No injection path remains ...")
    assert not hasattr(so, "expand_tech_triples"), (
        "expand_tech_triples still exists. While it does, generated synonyms can be "
        "put into the triple list without review, which is the arrangement this "
        "module was changed to end."
    )
    assert hasattr(so, "propose_alt_labels"), "The proposal path is missing."
    print("    expand_tech_triples removed; propose_alt_labels present")
    print("  PASS")


def test_proposals_are_queued_not_applied():
    print("\n[2] Proposals are queued, the vertical is untouched ...")
    vertical, queue = sandbox()
    before = open(vertical, encoding="utf-8").read()

    stats = so.propose_alt_labels(
        {"invoicing": ["Touchless Billing", "Effortless Invoicing"]},
        load(vertical), "TestBrand", queue_path=queue)
    after = open(vertical, encoding="utf-8").read()

    print("    %s" % stats)
    assert stats["proposed"] == 2
    assert before == after, (
        "Proposing changed the vertical. A generated pair must not enter the ontology "
        "until a person approves it."
    )
    q = json.load(open(queue, encoding="utf-8"))
    assert all(e["status"] == "pending" for e in q.values())
    print("    queue holds %d pending, vertical unchanged" % len(q))
    print("  PASS")


def test_curated_mapping_wins_a_disagreement():
    """The failure that motivated this: a generated pair contradicting the ontology."""
    print("\n[3] Curated alt_labels beat a conflicting proposal ...")
    vertical, queue = sandbox()
    cfg = load(vertical)

    # "Renewal Management" is a curated alternate of Renewal. A generator claiming it
    # means something else must not be able to move it.
    stats = so.propose_alt_labels(
        {"subscription lifecycle": ["Renewal Management"]}, cfg, "TestBrand",
        queue_path=queue)
    print("    %s" % stats)
    assert stats["conflicting"] == 1 and stats["proposed"] == 0, (
        "A proposal contradicting the curated mapping was queued as if it were new."
    )
    assert load(vertical).concept_by_label("Renewal Management").prefLabel == "Renewal", (
        "The curated canonical moved."
    )
    print("    'Renewal Management' still resolves to Renewal")
    print("  PASS")


def test_approval_writes_into_the_ontology():
    """A reviewer's judgement must land where extraction reads."""
    print("\n[4] Approval reaches the vertical ...")
    vertical, queue = sandbox()
    so.propose_alt_labels({"payment collection": ["Smart Collections"]},
                          load(vertical), "TestBrand", queue_path=queue)
    assert load(vertical).concept_by_label("Smart Collections") is None

    wrote = so.record_reviewer_synonym("Smart Collections", "Payment Collection",
                                       vertical, queue_path=queue)
    resolved = load(vertical).concept_by_label("Smart Collections")
    print("    written=%s  resolves to %s" % (wrote, resolved.prefLabel))

    assert wrote and resolved.prefLabel == "Payment Collection", (
        "An approved synonym did not reach the concept. Recorded anywhere else it is "
        "invisible to extraction, which reads alt_labels."
    )
    statuses = {e["status"] for e in json.load(open(queue, encoding="utf-8")).values()}
    assert "approved" in statuses, "The queue entry was not marked approved."
    print("  PASS")


def test_approval_refuses_to_create_ambiguity():
    print("\n[5] An approval cannot make one phrase mean two concepts ...")
    vertical, queue = sandbox()
    so.record_reviewer_synonym("Smart Collections", "Payment Collection", vertical,
                               queue_path=queue)
    clash = so.record_reviewer_synonym("Smart Collections", "Renewal", vertical,
                                       queue_path=queue)
    unknown = so.record_reviewer_synonym("Anything", "No Such Concept", vertical,
                                         queue_path=queue)
    print("    second concept refused=%s | unknown concept refused=%s"
          % (not clash, not unknown))
    assert not clash, (
        "The same surface form was attached to a second concept. Which canonical gets "
        "emitted would then depend on iteration order."
    )
    assert not unknown, "A synonym was recorded against a concept that does not exist."
    assert load(vertical).concept_by_label("Smart Collections").prefLabel == "Payment Collection"
    print("  PASS")


if __name__ == "__main__":
    print("=" * 78)
    print("SYNONYM GOVERNANCE - ONE AUTHORITY")
    print("=" * 78)
    test_generated_synonyms_never_reach_the_graph()
    test_proposals_are_queued_not_applied()
    test_curated_mapping_wins_a_disagreement()
    test_approval_writes_into_the_ontology()
    test_approval_refuses_to_create_ambiguity()
    print("\n" + "=" * 78)
    print("ALL SYNONYM GOVERNANCE TESTS PASSED")
    print("=" * 78)
