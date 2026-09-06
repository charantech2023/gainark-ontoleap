"""
GainARK OntoLeap — Semantic Linking, Topic Cluster Silos & Knowledge Graph Facade

This module re-exports all capabilities from the modularized subsystems for 100%
backward compatibility:
- knowledge_graph.py: W3C RDF graphs, PROV-O, SKOS, OWL 2 DL, SPARQL 1.1 engine
- graph_analytics.py: NetworkX PageRank & Cluster Topologies
- semantic_seo.py: SemanticLinkingEngine, In-Content Links, AI Readiness, Search Simulation
"""

from knowledge_graph import (
    WIKIDATA_KNOWLEDGE_BASE,
    build_rdf_graph,
    export_to_rdf_turtle,
    export_to_rdf_ntriples,
    execute_sparql_query_on_ttl,
    build_owl_ontology,
    export_to_owl_xml
)
from graph_analytics import (
    compute_graph_pagerank,
    build_cluster_topology
)
from semantic_seo import (
    PageData,
    SemanticLinkingEngine,
    extract_existing_links,
    audit_internal_links,
    compute_ai_citation_readiness,
    generate_wordpress_php_hook,
    simulate_search_response,
    generate_llms_txt,
    generate_robots_txt_ai
)

__all__ = [
    "WIKIDATA_KNOWLEDGE_BASE",
    "build_rdf_graph",
    "export_to_rdf_turtle",
    "export_to_rdf_ntriples",
    "execute_sparql_query_on_ttl",
    "build_owl_ontology",
    "export_to_owl_xml",
    "compute_graph_pagerank",
    "build_cluster_topology",
    "PageData",
    "SemanticLinkingEngine",
    "extract_existing_links",
    "audit_internal_links",
    "compute_ai_citation_readiness",
    "generate_wordpress_php_hook",
    "simulate_search_response",
    "generate_llms_txt",
    "generate_robots_txt_ai"
]
