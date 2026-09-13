"""
Entity registry and resolution: one identity per thing, and no identity a reviewer refused.

The site graph used to merge names on lowercase text after stripping one of seven
suffixes. "Salesforce", "Salesforce.com" and "Salesforce CRM" stayed three nodes, "CRMs"
and "CRM" two, and a concept reached through its alternate label duplicated the registry
topic it was the same thing as. These tests hold the replacement to the rules in
ENTITY_REGISTRY_DESIGN.md:

  * one identity per thing, across spellings, plurals, legal suffixes and product words;
  * no merge on mere similarity - "Google Cloud" does not become "Google";
  * a reviewer outranks automation, and distinct_from outranks merged, in any order;
  * common-noun phrases leave the export but stay visible to coverage scoring;
  * the curated seed is internally consistent.

The replay against real stored crawls is eval/registry_replay.py. This file is offline:
no network, no GLiNER.
"""

import json
import os
import tempfile

import entity_registry as er
from entity_registry import (ACTOR_AUTOMATION, ACTOR_REVIEWER, Registry, RegistryStore,
                             fold, load_seed, make_event)
from entity_resolver import is_common_noun_phrase, normalise_key, resolve_site
from graph_archive import DirectoryArchive
from models import IndustryConcept, IndustryOntologyModel, KGEdge, KGNode


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def seed_registry(events=None) -> Registry:
    return fold(load_seed(), events or [], normalise_key)


def industry(*concepts) -> IndustryOntologyModel:
    return IndustryOntologyModel(vertical_id="test_vertical", display_name="Test",
                                 concepts=list(concepts))


def raw(name, label="Software Platform", count=1, url="https://acme.com/p1"):
    return KGNode(id="entity:%s" % name.lower(), canonical_name=name, entity_type=label,
                  aliases=[name], mentions_count=count, source_urls=[url])


def ids_of(result, *names):
    """The node id each name ended up under, nodes and mentions alike."""
    found = {}
    for n in result.nodes + result.mentions:
        for form in [n.canonical_name] + n.aliases:
            found.setdefault(form, n.id)
    return [found.get(name) for name in names]


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------

def test_keys_fold_what_no_reader_keeps_apart():
    print("\n[1] Keys fold case, punctuation, legal suffixes, TLDs and plurals ...")
    same = [
        ("Salesforce", "Salesforce.com"), ("Stripe", "Stripe, Inc."),
        ("Usage-Based Pricing", "usage based pricing"), ("churned customer", "churned customers"),
        ("CRM", "CRMs"), ("API", "APIs"), ("The Sage Group plc", "Sage Group"),
        ("business customers", "business customer"),
    ]
    for a, b in same:
        assert normalise_key(a) == normalise_key(b), (a, b, normalise_key(a), normalise_key(b))
    # A capitalised name ending in s is not a plural: only lowercase and generic words fold.
    assert normalise_key("Atlassian Jobs") == "atlassian jobs"
    assert normalise_key("SaaS") == "saas"
    assert normalise_key("Atlassian") == "atlassian"
    assert normalise_key("Google Cloud") != normalise_key("Google")
    print("    %d pairs fold; SaaS, Atlassian and Google Cloud keep their shape" % len(same))
    print("  PASS")


def test_common_noun_phrases_are_told_from_names():
    print("\n[2] Common-noun phrases are mentions; names are not ...")
    mentions = [["new customers"], ["Customers"], ["billing portal"], ["Enterprise Customers"],
                ["Our clients"], ["500 customers"], ["Finance teams"]]
    names = [["New York City market"], ["Adyen"], ["Google Cloud"], ["HubSpot"], ["B2B"]]
    for forms in mentions:
        assert is_common_noun_phrase(forms, normalise_key(forms[0])), forms
    for forms in names:
        assert not is_common_noun_phrase(forms, normalise_key(forms[0])), forms
    print("    %d mentions, %d names classified" % (len(mentions), len(names)))
    print("  PASS")


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------

def test_seed_is_internally_consistent():
    print("\n[3] The curated seed holds together ...")
    seed = load_seed()
    reg = seed_registry()
    assert not reg.conflicts, reg.conflicts

    for ent in reg.entities.values():
        assert ent.kind in er.KINDS, (ent.id, ent.kind)
        assert ent.definition, "%s has no definition; nobody can review a match against it" % ent.id
        for ref in (ent.part_of, ent.parent_org):
            assert ref is None or ref in reg.entities, (ent.id, ref)

    # No form may name two entities: the resolver would refuse it as ambiguous, silently.
    owners = {}
    for ent in reg.entities.values():
        for form in ent.forms():
            key = normalise_key(form)
            assert owners.setdefault(key, ent.id) == ent.id, (
                "%r names both %s and %s" % (form, owners[key], ent.id))

    # Junk Wikidata aliases stay out. These are exactly the ones the Wikidata items carry.
    assert reg.lookup("crm") == [], "Wikidata's 'CRM' alias for Salesforce leaked into the seed"
    assert reg.lookup("big red") == []

    qids = [e.wikidata for e in reg.entities.values() if e.wikidata]
    assert len(qids) == len(set(qids)), "Two entities claim the same Wikidata item"
    print("    %d entities, %d forms, %d Q-IDs, no collisions" % (len(seed), len(owners), len(qids)))
    print("  PASS")


# ---------------------------------------------------------------------------
# Fold
# ---------------------------------------------------------------------------

def test_events_extend_and_merges_redirect():
    print("\n[4] Events extend the seed, and merged ids keep resolving ...")
    new = {"id": "acme", "kind": "organization", "prefLabel": "Acme", "definition": "A test company."}
    events = [
        make_event("entity_created", ACTOR_REVIEWER, entity=new),
        make_event("alias_added", ACTOR_REVIEWER, entity_id="acme", form="Acme Corp"),
        make_event("entity_created", ACTOR_REVIEWER,
                   entity={"id": "acme-dup", "kind": "organization", "prefLabel": "Acme Holdings",
                           "definition": "Duplicate."}),
        make_event("merged", ACTOR_REVIEWER, entity_id="acme-dup", into="acme"),
    ]
    # Written back to back, as one reviewer action would be: the fold must still apply
    # them in the order they were made.
    assert [e["event_id"] for e in events] == sorted(e["event_id"] for e in events)
    reg = seed_registry(events)
    assert not reg.conflicts, reg.conflicts
    assert [e.id for e in reg.lookup(normalise_key("Acme Corp"))] == ["acme"]
    assert reg.canonical("acme-dup").id == "acme", "A merged id stopped resolving."
    assert reg.lookup(normalise_key("Acme Holdings")) == [], "A merged entity still answers lookups."
    print("    alias added, merge redirects acme-dup -> %s" % reg.canonical("acme-dup").id)
    print("  PASS")


def test_a_reviewer_outranks_automation_in_any_order():
    print("\n[5] A reviewer's split beats an automated merge, whichever came first ...")
    base = [make_event("entity_created", ACTOR_REVIEWER,
                       entity={"id": i, "kind": "organization", "prefLabel": i, "definition": "x"})
            for i in ("google", "google-cloud")]
    merge = make_event("merged", ACTOR_AUTOMATION, entity_id="google-cloud", into="google")
    split = make_event("distinct_from_added", ACTOR_REVIEWER, entity_id="google", other="google-cloud")

    for order in ([merge, split], [split, merge]):
        # Event ids carry time; re-stamp so the list order is the fold order.
        evs = [dict(e, event_id="%013d-x" % (i + 1)) for i, e in enumerate(base + order)]
        reg = seed_registry(evs)
        assert reg.entities["google-cloud"].status == "active", [e["type"] for e in evs]
        assert reg.are_distinct("google", "google-cloud")
    print("    merge-then-split and split-then-merge both leave two entities")
    print("  PASS")


def test_distinct_from_outranks_even_a_reviewer_merge():
    print("\n[6] distinct_from undoes a merge across it ...")
    evs = [
        make_event("entity_created", ACTOR_REVIEWER,
                   entity={"id": i, "kind": "standard", "prefLabel": i, "definition": "x"})
        for i in ("std-a", "std-b")
    ] + [
        make_event("distinct_from_added", ACTOR_AUTOMATION, entity_id="std-a", other="std-b"),
        make_event("merged", ACTOR_REVIEWER, entity_id="std-b", into="std-a"),
    ]
    evs = [dict(e, event_id="%013d-x" % (i + 1)) for i, e in enumerate(evs)]
    reg = seed_registry(evs)
    assert reg.entities["std-b"].status == "active"
    assert any("declared distinct" in c for c in reg.conflicts), reg.conflicts
    print("    conflict recorded: %s" % [c for c in reg.conflicts if "distinct" in c][0])
    print("  PASS")


def test_the_store_is_append_only_and_survives_a_new_process():
    print("\n[7] Events land in the archive and a fresh store folds them ...")
    with tempfile.TemporaryDirectory() as tmp:
        archive = DirectoryArchive(tmp)
        writer = RegistryStore(archive=archive, key_fn=normalise_key)
        writer.append(make_event("entity_created", ACTOR_REVIEWER,
                                 entity={"id": "zuora", "kind": "organization", "prefLabel": "Zuora",
                                         "definition": "Subscription billing company."}))
        key = writer.record_observation({"domain": "acme.com", "forms": []})

        reader = RegistryStore(archive=archive, key_fn=normalise_key)   # another instance
        reg = reader.registry()
        assert [e.id for e in reg.lookup("zuora")] == ["zuora"]
        assert key and archive.get(key) is not None
        stored = archive.list(er.EVENTS_PREFIX + "/")
    print("    %d event object(s); a new store resolves 'Zuora'" % len(stored))
    print("  PASS")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def test_spellings_and_product_words_resolve_to_one_entity():
    print("\n[8] Spellings, legal suffixes and product words resolve to one entity ...")
    nodes = [raw("Salesforce", count=4), raw("Salesforce.com"), raw("Salesforce CRM"),
             raw("QuickBooks Online"), raw("Intuit Quickbooks"), raw("Oracle Netsuite"),
             raw("ERP systems")]
    res = resolve_site(nodes, [], "acme.com", "test_vertical", seed_registry())
    sf = ids_of(res, "Salesforce", "Salesforce.com", "Salesforce CRM")
    assert len(set(sf)) == 1 and sf[0] == er.entity_uri("salesforce"), sf
    assert len(set(ids_of(res, "QuickBooks Online", "Intuit Quickbooks"))) == 1
    assert ids_of(res, "Oracle Netsuite")[0] == er.entity_uri("netsuite"), "NetSuite went to Oracle"
    assert ids_of(res, "ERP systems")[0] == er.entity_uri("enterprise-resource-planning")
    salesforce = next(n for n in res.nodes if n.id == er.entity_uri("salesforce"))
    assert salesforce.wikidata_id == "Q941127" and salesforce.canonical_name == "Salesforce"
    print("    %d raw -> %d nodes; Salesforce carries %s" % (len(nodes), len(res.nodes), salesforce.wikidata_id))
    print("  PASS")


def test_no_merge_on_similarity_alone():
    print("\n[9] 'Google Cloud' does not become 'Google'; 'Acme API' does become 'Acme' ...")
    nodes = [raw("Google"), raw("Google Cloud"), raw("Acme Widgets"), raw("Acme Widgets API"),
             raw("CRM"), raw("CRMs")]
    res = resolve_site(nodes, [], "example.org", "test_vertical", seed_registry())
    g, gc = ids_of(res, "Google", "Google Cloud")
    assert g != gc, "A product word merged two unregistered names"
    assert len(set(ids_of(res, "Acme Widgets", "Acme Widgets API"))) == 1, "The connective suffix merge was lost"
    assert len(set(ids_of(res, "CRM", "CRMs"))) == 1
    assert ids_of(res, "CRM")[0] != er.entity_uri("salesforce")
    print("    Google %s | Google Cloud %s" % (g.rsplit("/", 1)[-1], gc.rsplit("/", 1)[-1]))
    print("  PASS")


def test_a_concept_the_registry_names_has_one_identity():
    print("\n[10] A concept reached by its alternate label joins the registry topic ...")
    onto = industry(IndustryConcept(id="revenue-recognition", pref_label="Revenue Recognition",
                                    kind="process", alt_labels=["Automated Revenue Recognition"]),
                    IndustryConcept(id="dunning", pref_label="Dunning", kind="feature",
                                    alt_labels=["Dunning Management"]))
    nodes = [raw("revenue recognition"), raw("Automated Revenue Recognition"),
             raw("Dunning Management")]
    res = resolve_site(nodes, [], "acme.com", "test_vertical", seed_registry(), onto)
    rr = ids_of(res, "revenue recognition", "Automated Revenue Recognition")
    assert len(set(rr)) == 1 and rr[0] == er.entity_uri("revenue-recognition"), rr
    topic = next(n for n in res.nodes if n.id == rr[0])
    assert topic.concept_uri and topic.concept_uri.endswith("revenue-recognition"), topic.concept_uri
    dunning = ids_of(res, "Dunning Management")[0]
    assert "/concept" in dunning or "test_vertical" in dunning, dunning
    print("    one node %s, exactMatch %s" % (rr[0].rsplit("/", 1)[-1], topic.concept_uri))
    print("  PASS")


def test_the_brand_is_found_under_its_own_name():
    print("\n[11] 'Ordway' on ordwaylabs.com is the brand, named as the site writes it ...")
    nodes = [raw("Ordwaylabs"), raw("Ordway", count=9), raw("Ordway Billing", count=12)]
    edges = [KGEdge(id="e", source="Ordwaylabs", target="Stripe", predicate="integratesWith")]
    res = resolve_site(nodes, edges, "ordwaylabs.com", "test_vertical", seed_registry())
    assert len(set(ids_of(res, "Ordwaylabs", "Ordway", "Ordway Billing"))) == 1
    assert res.brand.canonical_name == "Ordway", res.brand.canonical_name
    assert res.nodes[0].id == res.brand.id
    assert res.edges[0].source_id == res.brand.id
    assert res.edges[0].target_id == er.entity_uri("stripe")
    print("    brand %s named %r" % (res.brand.id, res.brand.canonical_name))
    print("  PASS")


def test_edges_deduplicate_on_identity():
    print("\n[12] Edges written under different spellings are one claim ...")
    edges = [
        KGEdge(id="1", source="Acme", target="Salesforce", predicate="integratesWith", confidence=0.8),
        KGEdge(id="2", source="Acme", target="Salesforce.com", predicate="integratesWith", confidence=0.9,
               provenance_sentence="Acme syncs with Salesforce.com in real time."),
        KGEdge(id="3", source="Acme", target="SOC 2", predicate="compliesWith"),
    ]
    res = resolve_site([], edges, "acme.com", "test_vertical", seed_registry())
    integrations = [e for e in res.edges if e.predicate == "integratesWith"]
    assert len(integrations) == 1, [e.target for e in res.edges]
    assert integrations[0].confidence == 0.9 and "real time" in integrations[0].provenance_sentence
    assert any(n.id == er.entity_uri("soc-2") for n in res.nodes), "An edge-only target has no node"
    print("    %d raw edges -> %d" % (len(edges), len(res.edges)))
    print("  PASS")


def test_mentions_leave_the_export_but_not_coverage():
    print("\n[13] Mentions are not exported, and coverage still reads them ...")
    from industry_ontology import align_graph_with_industry
    from site_graph import assemble_site_kg

    onto = industry(IndustryConcept(id="usage-billing", pref_label="Usage Billing", kind="feature"))
    state = {
        "start_url": "https://acme.com", "crawled": ["https://acme.com"], "seen": ["acme.com/"],
        "failed": [], "priority": [], "regular": [], "attempts": 1,
        "nodes": [n.model_dump() for n in (raw("usage billing"), raw("new customers"),
                                           raw("Stripe"), raw("Acme"))],
        "edges": [],
    }
    kg = assemble_site_kg(state, "test_vertical", 1, persist=False, registry=seed_registry())
    exported = {g["name"] for g in kg.export_jsonld["@graph"]}
    assert "new customers" not in exported and "usage billing" not in exported
    assert "Stripe" in exported
    stripe = next(g for g in kg.export_jsonld["@graph"] if g["name"] == "Stripe")
    assert stripe["sameAs"].endswith("Q7624104")
    assert {m.canonical_name for m in kg.mentions} >= {"new customers", "usage billing"}

    alignment = align_graph_with_industry(kg, industry=onto)
    assert "Usage Billing" in alignment.covered_concepts, alignment.category_whitespace

    from rdflib import Graph
    Graph().parse(data=kg.export_turtle, format="turtle")
    print("    exported %s | mentions %s | covered %s"
          % (sorted(exported), sorted(m.canonical_name for m in kg.mentions), alignment.covered_concepts))
    print("  PASS")


if __name__ == "__main__":
    print("=" * 78)
    print("ENTITY REGISTRY & RESOLUTION")
    print("=" * 78)
    test_keys_fold_what_no_reader_keeps_apart()
    test_common_noun_phrases_are_told_from_names()
    test_seed_is_internally_consistent()
    test_events_extend_and_merges_redirect()
    test_a_reviewer_outranks_automation_in_any_order()
    test_distinct_from_outranks_even_a_reviewer_merge()
    test_the_store_is_append_only_and_survives_a_new_process()
    test_spellings_and_product_words_resolve_to_one_entity()
    test_no_merge_on_similarity_alone()
    test_a_concept_the_registry_names_has_one_identity()
    test_the_brand_is_found_under_its_own_name()
    test_edges_deduplicate_on_identity()
    test_mentions_leave_the_export_but_not_coverage()
    print("\n" + "=" * 78)
    print("ALL ENTITY REGISTRY TESTS PASSED")
    print("=" * 78)
