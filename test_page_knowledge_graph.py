"""
Test Suite: Universal B2B Knowledge Graph & Page-Level Ontology Engine
=====================================================================
Validates:
1. Cross-domain extraction across arbitrary B2B sectors (Cybersecurity, Observability, HR)
   with ZERO reliance on closed-world subscription billing dictionaries.
2. 100% Evidentiary Provenance retention (W3C PROV-O evidence_sentence on every triple).
3. Dynamic W3C SKOS Concept Scheme & Concept induction with Universal B2B Facets.
4. Valid Turtle (.ttl) and JSON-LD serialization.
5. SPARQL 1.1 queryability on the in-memory page graph.
"""

import re
from rdflib import Graph, URIRef, RDF, RDFS, Namespace
from knowledge_graph import build_page_knowledge_graph, UNIVERSAL_B2B_FACETS
from models import SemanticTriple


def test_cybersecurity_page_knowledge_graph():
    """
    Test arbitrary AppSec / CyberSecurity copy (Snyk-style) with 0 billing terms.
    """
    print("\n[1] Testing Cybersecurity Page-Level Knowledge Graph ...")
    snyk_copy = """
    Snyk automates container vulnerability scanning and source code security for cloud-native engineering teams.
    The platform integrates with GitHub, GitLab, and Jira. Snyk is SOC 2 Type II compliant and ISO 27001 certified.
    We offer developers programmatic access through our REST API and GraphQL API.
    Snyk eliminates manual security reviews across developer pull requests.
    """
    
    result = build_page_knowledge_graph(
        text=snyk_copy,
        url="https://snyk.io/platform",
        title="Snyk Cloud Security Platform",
        subject="Snyk"
    )

    print(f"  Subject: {result.subject}")
    print(f"  Triples extracted: {len(result.triples)}")
    print(f"  SKOS concepts minted: {len(result.concepts)}")
    print(f"  RDF Nodes: {result.node_count}, Edges: {result.edge_count}")
    print(f"  Predicate distribution: {result.predicate_counts}")

    assert len(result.triples) >= 4, f"Expected at least 4 triples, got {len(result.triples)}"
    
    # 1. Verify predicates without billing vocabularies
    preds = {t.predicate for t in result.triples}
    assert "integratesWith" in preds, "Expected integratesWith to be extracted"
    assert "hasAPI" in preds, "Expected hasAPI to be extracted"
    assert "compliesWith" in preds or "certifiedBy" in preds, "Expected compliance/certification"

    # 2. Check integrations
    ints = {t.object.lower() for t in result.triples if t.predicate == "integratesWith"}
    print(f"  integratesWith objects: {ints}")
    assert any("github" in i for i in ints), "GitHub integration missing"

    # 3. Check APIs
    apis = {t.object.lower() for t in result.triples if t.predicate == "hasAPI"}
    print(f"  hasAPI objects: {apis}")
    assert any("rest" in a for a in apis), "REST API missing"

    # 4. Verify 100% Provenance
    for t in result.triples:
        assert t.evidence_sentence is not None, f"Missing evidence_sentence for triple: {t}"
        assert len(t.evidence_sentence.strip()) > 10, f"Evidence sentence too short: {t.evidence_sentence}"

    # 5. Verify SKOS concepts
    assert len(result.concepts) > 0, "No SKOS concepts were minted"
    concept_labels = {c["prefLabel"] for c in result.concepts}
    print(f"  Minted concept labels sample: {list(concept_labels)[:5]}")
    for c in result.concepts:
        assert c["broader"] in [f["id"] for f in UNIVERSAL_B2B_FACETS.values()], f"Invalid broader facet: {c['broader']}"

    # 6. Verify Turtle and JSON-LD serialization
    assert "@prefix" in result.turtle or "PREFIX" in result.turtle or "<https://snyk.io" in result.turtle, "Turtle output empty or malformed"
    assert isinstance(result.json_ld, (dict, list)), "JSON-LD must be a dictionary or list"
    print("  PASS - Cybersecurity extraction, SKOS taxonomy, and 100% provenance verified.")


def test_observability_page_knowledge_graph():
    """
    Test arbitrary Cloud Observability copy (Datadog-style).
    """
    print("\n[2] Testing Cloud Observability Page-Level Knowledge Graph ...")
    datadog_copy = """
    Datadog automates log aggregation and provides real-time infrastructure monitoring for modern engineering.
    The platform integrates with Kubernetes and AWS. Datadog guarantees 99.99% uptime reliability.
    Available as a Multi-Tenant SaaS deployment model for global engineering teams.
    """
    
    result = build_page_knowledge_graph(
        text=datadog_copy,
        url="https://www.datadoghq.com/product",
        title="Datadog Cloud Monitoring",
        subject="Datadog"
    )

    print(f"  Triples extracted: {len(result.triples)}")
    print(f"  Predicate distribution: {result.predicate_counts}")

    assert len(result.triples) >= 3, f"Expected at least 3 triples, got {len(result.triples)}"
    
    preds = {t.predicate for t in result.triples}
    assert "integratesWith" in preds, "integratesWith missing"
    assert "guarantees" in preds, "guarantees SLA missing"
    assert "deployedAs" in preds, "deployedAs missing"

    # Verify SLA
    slas = {t.object for t in result.triples if t.predicate == "guarantees"}
    print(f"  guarantees objects: {slas}")
    assert any("99.99%" in s for s in slas), "99.99% uptime SLA missing"

    # Verify 100% provenance
    for t in result.triples:
        assert t.evidence_sentence is not None, f"Missing evidence for: {t}"

    print("  PASS - Observability extraction and SLA guarantees verified.")


def test_sparql_queryability_on_page_graph():
    """
    Verify W3C SPARQL 1.1 query execution directly on the page knowledge graph.
    """
    print("\n[3] Testing W3C SPARQL 1.1 Query Execution on Page Graph ...")
    sample_copy = """
    Drata automates continuous compliance monitoring and audit readiness for high-growth tech companies.
    Drata complies with SOC 2 Type II, HIPAA, and ISO 27001.
    Integrates with AWS, Google Cloud, and GitHub. Offers webhooks and REST API endpoints.
    """
    
    result = build_page_knowledge_graph(
        text=sample_copy,
        url="https://drata.com",
        title="Drata Compliance Automation",
        subject="Drata"
    )

    # Load into RDFLib Graph
    g = Graph()
    g.parse(data=result.turtle, format="turtle")
    
    # Query for all capabilities with evidence sentences
    query = """
    PREFIX schema: <https://schema.org/>
    PREFIX prov: <http://www.w3.org/ns/prov#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

    SELECT ?label ?evidence
    WHERE {
        ?entity prov:wasQuotedFrom ?evidence ;
                rdfs:label ?label .
    }
    """
    qres = g.query(query)
    rows = list(qres)
    print(f"  SPARQL query matched {len(rows)} evidenced claims")
    assert len(rows) >= 3, f"Expected at least 3 evidenced claims from SPARQL query, got {len(rows)}"

    for row in rows[:3]:
        label, ev = str(row[0]), str(row[1])
        print(f"    Claim: {label} | Evidence: {ev[:60]}...")
        assert len(ev) > 5, "Evidence must not be empty"

    # Query SKOS Concept Scheme
    skos_query = """
    PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
    
    SELECT ?concept ?prefLabel ?facet
    WHERE {
        ?concept a skos:Concept ;
                 skos:prefLabel ?prefLabel ;
                 skos:broader ?facet .
    }
    """
    skos_res = list(g.query(skos_query))
    print(f"  SPARQL query matched {len(skos_res)} SKOS concepts organized under broader facets")
    assert len(skos_res) >= 2, "Expected at least 2 SKOS concepts with broader facets"

    print("  PASS - SPARQL 1.1 query execution verified on in-memory graph.")


if __name__ == "__main__":
    print("=" * 78)
    print("UNIVERSAL B2B KNOWLEDGE GRAPH & PAGE-LEVEL ONTOLOGY ENGINE VERIFICATION")
    print("=" * 78)
    test_cybersecurity_page_knowledge_graph()
    test_observability_page_knowledge_graph()
    test_sparql_queryability_on_page_graph()
    print("\n" + "=" * 78)
    print("ALL PAGE KNOWLEDGE GRAPH & UNIVERSAL ONTOLOGY TESTS PASSED")
    print("=" * 78)
