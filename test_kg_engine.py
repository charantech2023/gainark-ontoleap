"""
test_kg_engine.py — Comprehensive Test Suite for Pure Knowledge Graph & Industry Ontology Engine
"""

import unittest
from fastapi.testclient import TestClient

from models import (
    PageKnowledgeGraph, SiteKnowledgeGraph, KGNode, KGEdge,
    IndustryOntologyModel, GraphAlignmentResult
)
from page_graph import build_page_kg
from site_graph import _canonicalize_nodes, _canonicalize_edges, _induce_domain_ontology
from industry_ontology import (
    load_industry_ontology,
    list_available_industries,
    align_graph_with_industry
)
import api

client = TestClient(api.app)

SAMPLE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Ordway Labs — Cloud Revenue Automation & Invoicing</title>
    <script type="application/ld+json">
    {
        "@context": "https://schema.org",
        "@type": "SoftwareApplication",
        "name": "Ordway",
        "applicationCategory": "FinanceApplication"
    }
    </script>
</head>
<body>
    <h1>Enterprise Billing and Revenue Recognition</h1>
    <p>Ordway automates Revenue Recognition, Invoicing Automation, and Complex Contract Billing.</p>
    <p>The platform natively integrates with NetSuite, Salesforce, and Stripe.</p>
    <p>Ordway complies with ASC 606, IFRS 15, and SOC 2 security standards.</p>
    <p>Supports flexible usage-based pricing models and tiered recurring billing.</p>
</body>
</html>
"""


def test_industry_ontology_loading():
    print("\n[1] Testing Industry Reference Ontology loading & discovery...")
    industries = list_available_industries()
    print(f"  Available Industries ({len(industries)}): {[i['vertical_id'] for i in industries]}")
    assert len(industries) >= 4
    assert any(i["vertical_id"] == "b2b_saas_fintech" for i in industries)

    onto = load_industry_ontology("b2b_saas_fintech")
    print(f"  Loaded Ontology: {onto.display_name}")
    print(f"  Core Seed Concepts: {len(onto.core_seed_concepts)}")
    print(f"  Hierarchy Concepts: {len(onto.concepts)}")
    print(f"  Known Compliance  : {len(onto.known_compliance)}")
    print(f"  Known Integrations: {len(onto.known_integrations)}")

    assert "ASC 606" in onto.known_compliance
    assert "NetSuite" in onto.known_integrations
    assert len(onto.core_seed_concepts) >= 5
    print("  PASS - Industry Ontology loaded successfully.")


def test_page_kg_extraction():
    print("\n[2] Testing Page-Level Knowledge Graph extraction from HTML...")
    pkg = build_page_kg(url_or_html=SAMPLE_HTML, url="https://www.ordwaylabs.com")

    print(f"  Title              : {pkg.title}")
    print(f"  Entities Extracted : {len(pkg.nodes)}")
    for n in pkg.nodes[:5]:
        print(f"    - {n.canonical_name} ({n.entity_type}) [Wikidata: {n.wikidata_id or 'none'}]")

    print(f"  Edges Extracted    : {len(pkg.edges)}")
    for e in pkg.edges:
        print(f"    - [{e.source}] --({e.predicate})--> [{e.target}]")

    print(f"  Embedded Schemas   : {pkg.embedded_schemas}")

    assert len(pkg.nodes) >= 3
    assert len(pkg.edges) >= 3
    assert "SoftwareApplication" in pkg.embedded_schemas

    # Verify W3C serializations
    assert pkg.export_jsonld is not None
    assert pkg.export_jsonld["@context"] == "https://schema.org"
    assert "@graph" in pkg.export_jsonld
    assert pkg.export_turtle is not None
    assert "@prefix schema:" in pkg.export_turtle
    print("  PASS - Page KG extracted and formatted into W3C JSON-LD and Turtle.")


def test_site_kg_canonicalization():
    print("\n[3] Testing Multi-Page Entity Canonicalization & Induced Ontology...")
    raw_nodes = [
        KGNode(id="n1", canonical_name="Stripe API", entity_type="IntegrationPartner", aliases=["Stripe API"], mentions_count=2, source_urls=["https://example.com/p1"]),
        KGNode(id="n2", canonical_name="Stripe", entity_type="IntegrationPartner", aliases=["Stripe"], mentions_count=5, source_urls=["https://example.com/p2"], wikidata_id="Q24067"),
        KGNode(id="n3", canonical_name="Salesforce Connector", entity_type="IntegrationPartner", aliases=["Salesforce Connector"], mentions_count=1, source_urls=["https://example.com/p1"]),
        KGNode(id="n4", canonical_name="Salesforce", entity_type="IntegrationPartner", aliases=["Salesforce"], mentions_count=4, source_urls=["https://example.com/p3"]),
    ]

    canonical_nodes = _canonicalize_nodes(raw_nodes)
    print(f"  Raw Nodes: {len(raw_nodes)} -> Canonical Nodes: {len(canonical_nodes)}")
    for cn in canonical_nodes:
        print(f"    * {cn.canonical_name} (Mentions: {cn.mentions_count}, Sources: {len(cn.source_urls)}, Wikidata: {cn.wikidata_id})")

    assert len(canonical_nodes) == 2
    stripe_node = next(n for n in canonical_nodes if "stripe" in n.canonical_name.lower())
    assert stripe_node.mentions_count == 7
    assert stripe_node.wikidata_id is not None
    assert len(stripe_node.source_urls) == 2

    # Test edge canonicalization and schema induction
    raw_edges = [
        KGEdge(id="e1", source="Ordway", target="Stripe API", predicate="integratesWith", source_type="SoftwarePlatform", target_type="IntegrationPartner"),
        KGEdge(id="e2", source="Ordway", target="Stripe", predicate="integratesWith", source_type="SoftwarePlatform", target_type="IntegrationPartner"),
        KGEdge(id="e3", source="Ordway", target="ASC 606", predicate="compliesWith", source_type="SoftwarePlatform", target_type="Standard")
    ]
    canonical_edges = _canonicalize_edges(raw_edges, canonical_nodes)
    print(f"  Raw Edges: {len(raw_edges)} -> Canonical Edges: {len(canonical_edges)}")
    for ce in canonical_edges:
        print(f"    * {ce.source} --({ce.predicate})--> {ce.target}")

    assert len(canonical_edges) == 2  # Stripe API and Stripe merged

    induced_schema = _induce_domain_ontology(canonical_edges)
    print(f"  Induced Class Relations: {[f'{r.source_class} -({r.predicate})-> {r.target_class} ({r.count})' for r in induced_schema]}")
    assert len(induced_schema) == 2
    print("  PASS - Entity coreference canonicalization and ontology induction verified.")


def test_industry_alignment():
    print("\n[4] Testing Knowledge Graph Alignment against Industry Reference Ontology...")
    pkg = build_page_kg(url_or_html=SAMPLE_HTML, url="https://www.ordwaylabs.com")
    alignment = align_graph_with_industry(pkg, vertical_id="b2b_saas_fintech")

    print(f"  Subject             : {alignment.subject_identifier}")
    print(f"  Industry            : {alignment.industry_name}")
    print(f"  Coverage Score      : {alignment.coverage_score}%")
    print(f"  Covered Concepts ({len(alignment.covered_concepts)}): {alignment.covered_concepts[:6]}")
    print(f"  Category Whitespace ({len(alignment.category_whitespace)}): {alignment.category_whitespace[:4]}")
    print(f"  Compliance Covered  : {alignment.compliance_standards_covered}")
    print(f"  Integrations Covered: {alignment.integrations_covered}")

    assert "ASC 606" in alignment.compliance_standards_covered or "SOC 2" in alignment.compliance_standards_covered
    assert any("Revenue Recognition" in c for c in alignment.covered_concepts)
    assert len(alignment.category_whitespace) > 0  # Unclaimed industry space detected
    assert alignment.coverage_score > 0.0
    print("  PASS - Industry alignment correctly detected covered concepts and whitespace.")


def test_kg_api_endpoints():
    print("\n[5] Testing API Endpoints under /api/kg/ ...")

    # 1. GET /api/kg/industries
    res1 = client.get("/api/kg/industries")
    assert res1.status_code == 200, f"Error {res1.status_code}: {res1.text}"
    inds = res1.json()
    assert len(inds) >= 4
    print(f"  GET /api/kg/industries -> 200 OK ({len(inds)} verticals)")

    # 2. GET /api/kg/industry/b2b_saas_fintech
    res2 = client.get("/api/kg/industry/b2b_saas_fintech")
    assert res2.status_code == 200, f"Error {res2.status_code}: {res2.text}"
    ind_data = res2.json()
    assert ind_data["vertical_id"] == "b2b_saas_fintech"
    print(f"  GET /api/kg/industry/b2b_saas_fintech -> 200 OK (Name: {ind_data['display_name']})")

    # 3. POST /api/kg/page
    payload_page = {
        "html_content": SAMPLE_HTML,
        "url": "https://www.ordwaylabs.com",
        "vertical_id": "b2b_saas_fintech"
    }
    res3 = client.post("/api/kg/page", json=payload_page)
    assert res3.status_code == 200, f"Error {res3.status_code}: {res3.text}"
    p_data = res3.json()
    assert len(p_data["nodes"]) >= 3
    assert len(p_data["edges"]) >= 3
    print(f"  POST /api/kg/page -> 200 OK ({len(p_data['nodes'])} nodes, {len(p_data['edges'])} edges)")

    # 4. POST /api/kg/align
    payload_align = {
        "domain_or_url": "https://www.ordwaylabs.com",
        "vertical_id": "b2b_saas_fintech",
        "max_pages": 1
    }
    from unittest.mock import patch
    with patch("page_graph.smart_fetch", return_value=SAMPLE_HTML):
        res4 = client.post("/api/kg/align", json=payload_align)
    assert res4.status_code == 200, f"Error {res4.status_code}: {res4.text}"
    a_data = res4.json()
    assert a_data["vertical_id"] == "b2b_saas_fintech"
    assert "covered_concepts" in a_data
    assert "category_whitespace" in a_data
    print(f"  POST /api/kg/align -> 200 OK (Covered: {len(a_data['covered_concepts'])}, Whitespace: {len(a_data['category_whitespace'])})")


def test_alignment_matches_alt_labels():
    """A page written in the industry's abbreviations must score as the industry's concepts.

    align_graph_with_industry used to compare against prefLabel only, so a vendor writing
    "EDR" rather than "Endpoint Detection and Response" was reported as never having
    mentioned the capability - and the abbreviation was then listed as a proprietary
    invention. The curated altLabels existed the whole time and were simply never read.
    """
    print("\n[6] Alignment reads altLabels, not prefLabel alone ...")

    spelled = KGNode(id="n1", canonical_name="Endpoint Detection and Response",
                     entity_type="Feature")
    abbreviated = KGNode(id="n1", canonical_name="EDR", entity_type="Feature")

    def covered(node):
        kg = PageKnowledgeGraph(url="https://vendor.example.com", title="t",
                                nodes=[node], edges=[])
        return set(align_graph_with_industry(kg, vertical_id="cybersecurity").covered_concepts)

    long_form, short_form = covered(spelled), covered(abbreviated)
    print("    spelled out : %s" % sorted(long_form))
    print("    abbreviated : %s" % sorted(short_form))
    assert "Endpoint Detection and Response" in long_form
    assert "Endpoint Detection and Response" in short_form, (
        "An abbreviation the ontology curates as an altLabel was not matched.")

    # An abbreviation the ontology knows is not a proprietary invention.
    kg = PageKnowledgeGraph(url="https://vendor.example.com", title="t",
                            nodes=[abbreviated], edges=[])
    result = align_graph_with_industry(kg, vertical_id="cybersecurity")
    assert "EDR" not in result.proprietary_concepts, result.proprietary_concepts

    # A short alias must not match on a bare substring: "IR" is inside "firewall".
    noise = PageKnowledgeGraph(
        url="https://vendor.example.com", title="t",
        nodes=[KGNode(id="n2", canonical_name="firewall appliance", entity_type="Feature")],
        edges=[])
    assert "Incident Response" not in align_graph_with_industry(
        noise, vertical_id="cybersecurity").covered_concepts, (
        "Short alias matched a raw substring.")

    # Aliases only ADD forward matches: a graph term must not claim a longer known name.
    partial = PageKnowledgeGraph(
        url="https://vendor.example.com", title="t",
        nodes=[KGNode(id="n3", canonical_name="Sentinel", entity_type="SoftwarePlatform")],
        edges=[])
    assert "SentinelOne" not in align_graph_with_industry(
        partial, vertical_id="cybersecurity").integrations_covered, (
        "A graph term claimed an integration it is merely a prefix of.")

    print("  PASS - altLabels are matched, and neither short forms nor prefixes over-match.")


if __name__ == "__main__":
    test_industry_ontology_loading()
    test_page_kg_extraction()
    test_site_kg_canonicalization()
    test_industry_alignment()
    test_kg_api_endpoints()
    test_alignment_matches_alt_labels()
    print("\n=======================================================")
    print("ALL PURE KNOWLEDGE GRAPH & ONTOLOGY TESTS PASSED!")
    print("=======================================================")
