"""
GainARK OntoLeap — Semantic Linking, Topic Cluster Silos & Knowledge Graph Facade

This module re-exports all capabilities from the modularized subsystems for 100%
backward compatibility:
- knowledge_graph.py: W3C RDF graphs, PROV-O, SKOS, OWL 2 DL, SPARQL 1.1 engine
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

__all__ = [
    "WIKIDATA_KNOWLEDGE_BASE",
    "build_rdf_graph",
    "export_to_rdf_turtle",
    "export_to_rdf_ntriples",
    "execute_sparql_query_on_ttl",
    "build_owl_ontology",
    "export_to_owl_xml"
]
