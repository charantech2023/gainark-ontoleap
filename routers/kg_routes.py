"""
GainARK OntoLeap — Knowledge Graph Operations, SPARQL, OWL 2 DL, and Link Prediction
"""

import logging
from typing import List, Dict, Optional
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field
from rdflib import Graph

from linking import (
    audit_internal_links,
    execute_sparql_query_on_ttl,
    export_to_owl_xml
)
from link_prediction import predict_kg_links
from graph_export import generate_standalone_graph_html
from models import (
    SemanticTriple,
    SparqlQueryRequest,
    SparqlQueryResponse,
    LinkPredictionRequest,
    LinkPredictionResponse,
    ExportGraphHtmlRequest
)

logger = logging.getLogger("ontoleap.api.kg")

router = APIRouter(tags=["Knowledge Graph Operations"])


class NTriplesExportRequest(BaseModel):
    rdf_turtle: str = Field(..., max_length=500_000, description="RDF Turtle serialization to convert into N-Triples")


class OwlExportRequest(BaseModel):
    root_domain: str = Field(default="example.com", description="Root domain of the platform")
    triples: List[SemanticTriple] = Field(default_factory=list, description="Extracted relational triples")
    topic_hubs: Dict[str, str] = Field(default_factory=dict, description="Canonical topic hubs map")
    entities: List[str] = Field(default_factory=list, description="Extracted entities")
    rdf_turtle: Optional[str] = Field(default=None, max_length=500_000, description="Optional Turtle to convert directly")


class SemanticClustersRequest(BaseModel):
    urls: List[str] = Field(default_factory=list)
    sitemap_url: Optional[str] = None
    max_pages: int = 10


@router.post("/api/sparql", response_model=SparqlQueryResponse, summary="Execute SPARQL 1.1 Query on Knowledge Graph")
def api_execute_sparql(req: SparqlQueryRequest):
    """
    Executes a W3C SPARQL 1.1 query against an in-memory RDF knowledge graph
    using RDFLib's native SPARQL engine and returns structured column and row bindings.
    """
    if not req.rdf_turtle or not req.rdf_turtle.strip():
        raise HTTPException(status_code=400, detail="Must provide 'rdf_turtle' to query against. Please run an internal links audit first.")
    try:
        res = execute_sparql_query_on_ttl(req.rdf_turtle, req.query)
        return SparqlQueryResponse(
            query=req.query,
            columns=res["columns"],
            rows=res["rows"],
            row_count=res["row_count"],
            execution_status="success"
        )
    except ValueError as val_err:
        return SparqlQueryResponse(
            query=req.query,
            columns=[],
            rows=[],
            row_count=0,
            execution_status="failed",
            error=str(val_err)
        )
    except Exception as e:
        logger.error("SPARQL execution error: %s", e, exc_info=True)
        return SparqlQueryResponse(
            query=req.query,
            columns=[],
            rows=[],
            row_count=0,
            execution_status="failed",
            error="SPARQL query failed during evaluation. Please verify syntax and prefixes."
        )


@router.post("/api/export-ntriples", summary="Convert RDF Turtle to W3C N-Triples")
def api_export_ntriples(req: NTriplesExportRequest):
    """
    Parses an RDF Turtle knowledge graph and converts it to W3C N-Triples (.nt) format.
    Ideal for triple-store ingestion, SPARQL endpoints, and streaming graph analytics.
    """
    if not req.rdf_turtle or not req.rdf_turtle.strip():
        raise HTTPException(status_code=400, detail="Must provide 'rdf_turtle' string.")
    try:
        g = Graph()
        g.parse(data=req.rdf_turtle, format="turtle")
        nt_data = g.serialize(format="nt")
        return PlainTextResponse(content=nt_data, media_type="application/n-triples")
    except Exception as e:
        logger.error("Failed to serialize N-Triples: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to serialize N-Triples.")


@router.post("/api/export-owl", summary="Generate and Export W3C OWL 2 DL Ontology")
def api_export_owl(req: OwlExportRequest):
    """
    Generates a formal W3C OWL 2 DL RDF/XML (.owl) ontology defining classes,
    object properties, data properties, and named individuals with Wikidata grounding.
    """
    try:
        if req.rdf_turtle and req.rdf_turtle.strip():
            g = Graph()
            g.parse(data=req.rdf_turtle, format="turtle")
            owl_xml = g.serialize(format="xml")
            return PlainTextResponse(content=owl_xml, media_type="application/rdf+xml")

        owl_xml = export_to_owl_xml(req.root_domain, req.triples, req.topic_hubs, req.entities)
        return PlainTextResponse(content=owl_xml, media_type="application/rdf+xml")
    except Exception as e:
        logger.error("Failed to generate OWL ontology: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate OWL ontology.")


@router.post("/api/predict-links", response_model=LinkPredictionResponse, summary="Predict Missing Knowledge Graph Relations (Ontological Priors)")
def api_predict_links(req: LinkPredictionRequest):
    """
    Infers missing high-probability relational links across the knowledge graph,
    computes graph completeness, and grounds predicted entities to Wikidata Q-IDs.
    """
    try:
        return predict_kg_links(req.domain, req.triples, req.entities, req.topic_hubs)
    except Exception as e:
        logger.error("Link prediction failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Link prediction failed.")


@router.post("/api/semantic-clusters", summary="Compute TF-IDF Cosine Similarity & Topic Clusters")
async def api_semantic_clusters(req: SemanticClustersRequest):
    """
    Analyzes content across site pages, computing TF-IDF vectors, pairwise cosine
    similarity matrix, cannibalization overlaps (>=0.70), and thematic topic silos.
    """
    try:
        audit_res = await audit_internal_links(sitemap_url=req.sitemap_url, urls=req.urls, max_pages=req.max_pages)
        return audit_res.semantic_clustering or {}
    except Exception as e:
        logger.error("Semantic cluster analysis failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Semantic cluster analysis failed.")


@router.post("/api/export-graph-html", summary="Export Standalone Interactive PyVis/Vis.js Graph HTML")
def api_export_graph_html(req: ExportGraphHtmlRequest):
    """
    Generates a standalone, fully-interactive Vis.js HTML document with physics
    simulation, node search, filtering, and entity metadata drawer.
    """
    try:
        html = generate_standalone_graph_html(
            domain=req.domain,
            topology=req.cluster_topology,
            triples=req.triples,
            topic_hubs=req.topic_hubs,
            predicted_links=req.predicted_links
        )
        return HTMLResponse(content=html, media_type="text/html")
    except Exception as e:
        logger.error("Failed to generate graph HTML: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate interactive graph HTML. Check server logs.")
