"""
Stored history survives the container that stored it.

graph_store keeps quads in SQLite, which is durable on a workstation and not durable at
all on Cloud Run: every instance gets its own ephemeral filesystem, so history lasts
until that container is recycled and no instance can see another's runs. A store that
looks like it is accumulating knowledge and silently is not is worse than one that never
claimed to.

The archive is the record and SQLite is an index over it. These tests cover the write-
through, the cold start that rebuilds an empty index, and the two-instance case - all
against a directory archive, which is both the offline test double and the real backend
for a mounted volume on Cloud Run.

No network calls, no GCS, no GLiNER.
"""

import json
import os
import tempfile
from datetime import datetime, timezone

from rdflib import Graph, Literal, URIRef

import graph_archive as ga
import graph_store as gs
from ontology_schema import concept_uri

VERTICAL = "b2b_saas_fintech"


def fresh_index():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    os.remove(path)
    return path


def fresh_archive():
    return ga.DirectoryArchive(tempfile.mkdtemp())


def claim_graph(domain, concepts):
    g = Graph()
    product = URIRef("https://%s/#Product" % domain)
    for c in concepts:
        entity = URIRef("https://%s/entity/%s" % (domain, c.replace(" ", "")))
        g.add((product, URIRef("https://schema.org/featureList"), entity))
        g.add((entity, URIRef("http://www.w3.org/2000/01/rdf-schema#label"), Literal(c)))
        g.add((entity, URIRef("http://www.w3.org/2004/02/skos/core#related"),
               URIRef(concept_uri(VERTICAL, c.lower().replace(" ", "-")))))
    return g


def test_graph_survives_encode_decode():
    print("\n[1] Archive payload round-trip ...")
    g = claim_graph("a.com", ["Renewal", "Dunning"])
    payload, meta = ga.encode(g, {"graph_id": "g1", "kind": "run"})
    back = ga.decode(payload)
    print("    %d triples out, %d back | meta keys %s"
          % (len(g), len(back), sorted(json.loads(meta))))
    assert len(back) == len(g)
    assert set(back) == set(g), "The archived graph is not what was stored."
    print("  PASS")


def test_persist_writes_through_to_the_archive():
    print("\n[2] Write-through ...")
    index, archive = fresh_index(), fresh_archive()
    gs.persist_graph(claim_graph("ordwaylabs.com", ["Renewal"]),
                     "https://ordwaylabs.com/audit/20260909T000000Z",
                     kind="run", domain="ordwaylabs.com", vertical_id=VERTICAL,
                     path=index, archive_write=False)
    assert archive.list() == [], "sanity: archive should be empty with archive_write off"

    gs._mirror_to_archive(claim_graph("ordwaylabs.com", ["Renewal"]),
                          "https://ordwaylabs.com/audit/20260909T000000Z",
                          "run", "ordwaylabs.com", VERTICAL, None, archive=archive)
    keys = archive.list()
    print("    archive holds: %s" % keys)
    assert any(k.endswith(".meta.json") for k in keys), "Metadata was not archived."
    assert any(not k.endswith(".meta.json") for k in keys), "The graph was not archived."
    print("  PASS")


def test_cold_instance_rebuilds_its_index():
    """The Cloud Run case: new container, empty SQLite, archive holds everything."""
    print("\n[3] Cold start restores history ...")
    archive = fresh_archive()
    first = fresh_index()
    for day, concepts in ((1, ["Renewal"]), (9, ["Dunning", "Revenue Schedules"])):
        gid = gs.run_graph_id("ordwaylabs.com", datetime(2026, 9, day, tzinfo=timezone.utc))
        gs.persist_graph(claim_graph("ordwaylabs.com", concepts), gid, kind="run",
                         domain="ordwaylabs.com", vertical_id=VERTICAL,
                         path=first, archive_write=False)
        gs._mirror_to_archive(claim_graph("ordwaylabs.com", concepts), gid, "run",
                              "ordwaylabs.com", VERTICAL, None, archive=archive)

    replacement = fresh_index()
    assert gs.list_runs(path=replacement) == [], "A fresh index should start empty."

    result = gs.sync_from_archive(archive, path=replacement)
    runs = gs.list_runs("ordwaylabs.com", path=replacement)
    print("    pulled %d graph(s); index now holds %d run(s)" % (result["pulled"], len(runs)))

    assert len(runs) == 2, (
        "A recycled instance did not recover its history. Without this the service "
        "answers 'no history' with complete confidence."
    )
    restored = gs.load_graph(runs[0]["graph_id"], path=replacement)
    assert any("Revenue Schedules" in str(o) for _, _, o in restored), (
        "The recovered run does not contain what it recorded."
    )
    print("  PASS")


def test_two_instances_see_each_others_runs():
    print("\n[4] Instances converge through the archive ...")
    archive = fresh_archive()
    a, b = fresh_index(), fresh_index()

    def write(index, domain, day, concepts):
        gid = gs.run_graph_id(domain, datetime(2026, 9, day, tzinfo=timezone.utc))
        gs.persist_graph(claim_graph(domain, concepts), gid, kind="run", domain=domain,
                         vertical_id=VERTICAL, path=index, archive_write=False)
        gs._mirror_to_archive(claim_graph(domain, concepts), gid, "run", domain,
                              VERTICAL, None, archive=archive)

    write(a, "ordwaylabs.com", 9, ["Renewal"])
    write(b, "chargebee.com", 9, ["Renewal", "Dunning"])

    for index in (a, b):
        gs.sync_from_archive(archive, path=index)

    seen_a = {v["domain"] for v in gs.vendors_covering(concept_uri(VERTICAL, "renewal"), path=a)}
    seen_b = {v["domain"] for v in gs.vendors_covering(concept_uri(VERTICAL, "renewal"), path=b)}
    print("    instance A sees %s" % sorted(seen_a))
    print("    instance B sees %s" % sorted(seen_b))
    assert seen_a == seen_b == {"ordwaylabs.com", "chargebee.com"}, (
        "Instances did not converge. Each replica holding only its own runs makes a "
        "cross-vendor question depend on which container answered it."
    )
    print("  PASS")


def test_sync_is_idempotent():
    print("\n[5] Re-syncing pulls nothing twice ...")
    archive, index = fresh_archive(), fresh_index()
    gid = gs.run_graph_id("ordwaylabs.com", datetime(2026, 9, 9, tzinfo=timezone.utc))
    gs._mirror_to_archive(claim_graph("ordwaylabs.com", ["Renewal"]), gid, "run",
                          "ordwaylabs.com", VERTICAL, None, archive=archive)
    first = gs.sync_from_archive(archive, path=index)
    second = gs.sync_from_archive(archive, path=index)
    print("    first %s | second %s" % (first, second))
    assert first["pulled"] == 1 and second["pulled"] == 0, (
        "A second sync re-pulled an already indexed graph. Run graphs are immutable, so "
        "re-reading one can only cost time."
    )
    print("  PASS")


def test_archive_key_cannot_escape_the_root():
    print("\n[6] Keys stay inside the archive ...")
    archive = fresh_archive()
    try:
        archive.put("../escaped.nt", b"nope")
    except ValueError:
        print("    traversal rejected")
        print("  PASS")
        return
    raise AssertionError(
        "An archive key containing .. was accepted, so a crafted graph id could write "
        "outside the archive root."
    )


def test_ephemeral_deployment_is_reported():
    print("\n[7] Unarchived container deployment warns ...")
    saved_uri, saved_k = ga.ARCHIVE_URI, os.environ.get("K_SERVICE")
    try:
        ga.ARCHIVE_URI = ""
        os.environ["K_SERVICE"] = "ontoleap"
        assert ga.warn_if_ephemeral(), (
            "Running in a container with no archive produced no warning. That is the "
            "case where history is silently discarded."
        )
        ga.ARCHIVE_URI = "/mnt/graph"
        assert ga.warn_if_ephemeral() is None, "Warned despite an archive being set."
        print("    warns without an archive, silent with one")
    finally:
        ga.ARCHIVE_URI = saved_uri
        if saved_k is None:
            os.environ.pop("K_SERVICE", None)
        else:
            os.environ["K_SERVICE"] = saved_k
    print("  PASS")


if __name__ == "__main__":
    print("=" * 78)
    print("DURABLE ARCHIVE")
    print("=" * 78)
    test_graph_survives_encode_decode()
    test_persist_writes_through_to_the_archive()
    test_cold_instance_rebuilds_its_index()
    test_two_instances_see_each_others_runs()
    test_sync_is_idempotent()
    test_archive_key_cannot_escape_the_root()
    test_ephemeral_deployment_is_reported()
    print("\n" + "=" * 78)
    print("ALL ARCHIVE TESTS PASSED")
    print("=" * 78)
