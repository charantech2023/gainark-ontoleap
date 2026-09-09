"""
The knowledge graph survives the request that built it.

build_rdf_graph() assembled a graph, serialised it to a string and returned it. Nothing
was kept, so only one question could ever be answered: does this page's marketing match
this page's docs, right now. There was no subject for "what do we know about Ordway",
no earlier state for "what changed", and - because concepts were minted under each
audited client's own domain - no shared resource for "which vendors cover this".

These tests cover the store itself and the two questions that were previously
unanswerable. Uses a temporary database, touches no real store, makes no network calls
and does not load GLiNER.
"""

import os
import tempfile
from datetime import datetime, timezone

from rdflib import Graph, Literal, URIRef, XSD

import graph_store as gs
from ontology_schema import ONTOLOGY_BASE, concept_uri, scheme_uri

VERTICAL = "b2b_saas_fintech"
EX = "https://example.com"


def fresh_store():
    fd, path = tempfile.mkstemp(suffix=".sqlite")
    os.close(fd)
    os.remove(path)
    return path


def claim_graph(domain, concepts):
    """A minimal audit-shaped graph: instance triples plus shared concept triples."""
    g = Graph()
    product = URIRef("https://%s/#Product" % domain)
    for c in concepts:
        entity = URIRef("https://%s/entity/%s" % (domain, c))
        shared = URIRef(concept_uri(VERTICAL, c))
        g.add((product, URIRef("https://schema.org/featureList"), entity))
        g.add((entity, URIRef("http://www.w3.org/2000/01/rdf-schema#label"), Literal(c)))
        g.add((entity, URIRef("http://www.w3.org/2004/02/skos/core#related"), shared))
        g.add((shared, URIRef("http://www.w3.org/2004/02/skos/core#prefLabel"), Literal(c)))
    return g


def test_terms_survive_a_round_trip():
    """Datatypes and languages must not be flattened to plain strings."""
    print("\n[1] Term round-trip ...")
    path = fresh_store()
    g = Graph()
    s = URIRef(EX + "/s")
    g.add((s, URIRef(EX + "/int"), Literal(42)))
    g.add((s, URIRef(EX + "/when"), Literal("2026-09-09T00:00:00+00:00", datatype=XSD.dateTime)))
    g.add((s, URIRef(EX + "/text"), Literal("hello", lang="en")))
    g.add((s, URIRef(EX + "/ref"), URIRef(EX + "/o")))

    gs.persist_graph(g, EX + "/g", kind="run", domain="example.com", path=path)
    back = gs.load_graph(EX + "/g", path=path)

    print("    stored %d, loaded %d" % (len(g), len(back)))
    assert len(back) == len(g)
    for triple in g:
        assert triple in back, (
            "%r did not survive storage. A literal that comes back without its datatype "
            "or language is a different term, and SPARQL filters on it stop matching."
            % (triple,)
        )
    print("  PASS")


def test_repersisting_is_idempotent():
    """The ontology is written on every audit; the store must not grow for it."""
    print("\n[2] Re-persisting the same graph adds nothing ...")
    path = fresh_store()
    g = claim_graph("a.com", ["Renewal"])
    first = gs.persist_graph(g, EX + "/g", kind="run", domain="a.com", path=path)
    second = gs.persist_graph(g, EX + "/g", kind="run", domain="a.com", path=path)
    print("    first write %d quads, second write %d" % (first, second))
    assert first > 0 and second == 0, (
        "Re-persisting identical content wrote %d more quads. The shared ontology is "
        "stored on every run, so a non-idempotent write grows the store without adding "
        "information." % second
    )
    print("  PASS")


def test_ontology_and_instance_data_are_stored_apart():
    print("\n[3] Namespace split ...")
    path = fresh_store()
    g = claim_graph("ordwaylabs.com", ["Renewal", "Dunning"])
    result = gs.persist_audit(g, "ordwaylabs.com", VERTICAL,
                              when=datetime(2026, 9, 1, tzinfo=timezone.utc), path=path)
    print("    run %d quads | ontology %d quads"
          % (result["run_quads"], result["ontology_quads"]))

    run = gs.load_graph(result["run_graph"], path=path)
    onto = gs.load_graph(result["ontology_graph"], path=path)

    assert result["ontology_graph"] == scheme_uri(VERTICAL)
    assert all(not str(s).startswith(ONTOLOGY_BASE) for s, _, _ in run), (
        "A shared concept was stored inside a client's run graph. Stored per run, the "
        "whole ontology repeats once per audit and 'the concepts' becomes a question "
        "about which run to look in."
    )
    assert all(str(s).startswith(ONTOLOGY_BASE) for s, _, _ in onto), (
        "Client instance data leaked into the shared ontology graph."
    )
    assert len(run) and len(onto)
    print("  PASS")


def test_history_accumulates_and_is_not_rewritten():
    print("\n[4] Runs accumulate ...")
    path = fresh_store()
    for day, concepts in ((1, ["Renewal", "Customer Portal"]),
                          (9, ["Renewal", "Revenue Schedules"])):
        gs.persist_audit(claim_graph("ordwaylabs.com", concepts), "ordwaylabs.com", VERTICAL,
                         when=datetime(2026, 9, day, tzinfo=timezone.utc), path=path)

    runs = gs.list_runs("ordwaylabs.com", path=path)
    print("    %s" % [r["graph_id"].rsplit("/", 1)[-1] for r in runs])
    assert len(runs) == 2, "Expected two distinct runs, got %d." % len(runs)
    assert runs[0]["created_at"] >= runs[1]["created_at"], "Runs are not newest-first."

    earlier = gs.load_graph(runs[-1]["graph_id"], path=path)
    assert any("Customer Portal" in str(o) for _, _, o in earlier), (
        "The earlier run no longer contains what it found. History must not be "
        "rewritten by a later audit - a run is a record of one moment."
    )
    print("  PASS")


def test_diff_reports_claims_not_timestamps():
    print("\n[5] Diff between runs ...")
    path = fresh_store()
    ids = []
    for day, concepts in ((1, ["Renewal", "Customer Portal"]),
                          (9, ["Renewal", "Revenue Schedules"])):
        r = gs.persist_audit(claim_graph("ordwaylabs.com", concepts), "ordwaylabs.com",
                             VERTICAL, when=datetime(2026, 9, day, tzinfo=timezone.utc),
                             path=path)
        ids.append(r["run_graph"])

    d = gs.diff_runs(ids[0], ids[1], path=path)
    added = {str(o) for _, _, o in d["added"]}
    removed = {str(o) for _, _, o in d["removed"]}
    print("    added   %s" % sorted(x.rsplit('/', 1)[-1] for x in added))
    print("    removed %s" % sorted(x.rsplit('/', 1)[-1] for x in removed))

    assert any("Revenue Schedules" in x or "RevenueSchedules" in x for x in added)
    assert any("Customer Portal" in x or "CustomerPortal" in x for x in removed)
    assert not any("Renewal" in x for x in added | removed), (
        "An unchanged claim appeared in the diff."
    )
    print("  PASS")


def test_query_spans_vendors():
    """The payoff of the shared concept namespace."""
    print("\n[6] One concept, many vendors ...")
    path = fresh_store()
    gs.persist_audit(claim_graph("ordwaylabs.com", ["Renewal", "Customer Portal"]),
                     "ordwaylabs.com", VERTICAL,
                     when=datetime(2026, 9, 9, tzinfo=timezone.utc), path=path)
    gs.persist_audit(claim_graph("chargebee.com", ["Renewal", "Dunning"]),
                     "chargebee.com", VERTICAL,
                     when=datetime(2026, 9, 9, tzinfo=timezone.utc), path=path)

    both = {v["domain"] for v in gs.vendors_covering(concept_uri(VERTICAL, "Renewal"), path=path)}
    only = {v["domain"] for v in gs.vendors_covering(concept_uri(VERTICAL, "Dunning"), path=path)}
    print("    Renewal -> %s" % sorted(both))
    print("    Dunning -> %s" % sorted(only))

    assert both == {"ordwaylabs.com", "chargebee.com"}, (
        "A concept shared by two vendors did not join across them: %s. While concepts "
        "were minted under each client's domain this question had no subject to ask "
        "about." % sorted(both)
    )
    assert only == {"chargebee.com"}
    print("  PASS")


def test_dataset_keeps_runs_separate():
    print("\n[7] Dataset preserves named graphs ...")
    path = fresh_store()
    ids = []
    for day, concepts in ((1, ["Renewal"]), (9, ["Dunning"])):
        r = gs.persist_audit(claim_graph("ordwaylabs.com", concepts), "ordwaylabs.com",
                             VERTICAL, when=datetime(2026, 9, day, tzinfo=timezone.utc),
                             path=path)
        ids.append(r["run_graph"])

    ds = gs.load_dataset(ids, path=path)
    contexts = {str(c.identifier) for c in ds.contexts() if len(c)}
    print("    %d named graphs" % len(contexts))
    assert set(ids) <= contexts, (
        "Runs were merged into one graph, so a query can no longer tell which audit a "
        "triple came from - the reason runs are stored separately at all."
    )
    print("  PASS")


def test_replace_graph_drops_what_is_gone():
    print("\n[8] Replacing the ontology removes retired concepts ...")
    path = fresh_store()
    gid = scheme_uri(VERTICAL)
    gs.replace_graph(claim_graph("x.com", ["Renewal", "Retired Concept"]), gid,
                     kind="ontology", vertical_id=VERTICAL, path=path)
    gs.replace_graph(claim_graph("x.com", ["Renewal"]), gid,
                     kind="ontology", vertical_id=VERTICAL, path=path)
    after = gs.load_graph(gid, path=path)
    remaining = {str(o) for _, _, o in after}
    print("    concepts remaining: %d triples" % len(after))
    assert not any("Retired" in x for x in remaining), (
        "A concept removed from the vertical is still in the stored ontology. Appending "
        "instead of replacing leaves renamed and deleted concepts behind for ever."
    )
    assert any("Renewal" in x for x in remaining)
    print("  PASS")


if __name__ == "__main__":
    print("=" * 78)
    print("DURABLE KNOWLEDGE GRAPH STORE")
    print("=" * 78)
    test_terms_survive_a_round_trip()
    test_repersisting_is_idempotent()
    test_ontology_and_instance_data_are_stored_apart()
    test_history_accumulates_and_is_not_rewritten()
    test_diff_reports_claims_not_timestamps()
    test_query_spans_vendors()
    test_dataset_keeps_runs_separate()
    test_replace_graph_drops_what_is_gone()
    print("\n" + "=" * 78)
    print("ALL GRAPH STORE TESTS PASSED")
    print("=" * 78)
