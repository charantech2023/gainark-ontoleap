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
from pipeline import validate_url_for_fetch
from knowledge_graph import build_page_knowledge_graph
from routers.deps import get_pipeline
from models import (
    SemanticTriple,
    SparqlQueryRequest,
    SparqlQueryResponse,
    LinkPredictionRequest,
    LinkPredictionResponse,
    ExportGraphHtmlRequest,
    PageKnowledgeGraphRequest,
    PageKnowledgeGraphResult
)

logger = logging.getLogger("ontoleap.api.kg")

router = APIRouter(tags=["Knowledge Graph Operations"])


class NTriplesExportRequest(BaseModel):
    rdf_turtle: str = Field(..., max_length=500_000, description="RDF Turtle serialization to convert into N-Triples")


class OwlExportRequest(BaseModel):
    root_domain: str = Field(default="example.com", description="Root domain of the platform")
    triples: List[SemanticTriple] = Field(default_factory=list, max_length=5000, description="Extracted relational triples")
    topic_hubs: Dict[str, str] = Field(default_factory=dict, description="Canonical topic hubs map")
    entities: List[str] = Field(default_factory=list, max_length=5000, description="Extracted entities")
    rdf_turtle: Optional[str] = Field(default=None, max_length=500_000, description="Optional Turtle to convert directly")


class SemanticClustersRequest(BaseModel):
    urls: List[str] = Field(default_factory=list, max_length=100)
    sitemap_url: Optional[str] = Field(default=None, max_length=2048)
    # Bounded for the same reason as /api/batch-crawl: every page is a fetch plus a
    # model inference pass.
    max_pages: int = Field(default=10, ge=1, le=100)


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
    # This endpoint crawls whatever it is given, exactly as /api/internal-links does,
    # and so needs the same SSRF guard. It previously had none.
    try:
        if req.sitemap_url:
            validate_url_for_fetch(req.sitemap_url)
        for u in req.urls or []:
            validate_url_for_fetch(u)
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))

    if not req.sitemap_url and not req.urls:
        raise HTTPException(status_code=400, detail="Provide either 'sitemap_url' or a non-empty 'urls' list.")

    try:
        audit_res = await audit_internal_links(sitemap_url=req.sitemap_url, urls=req.urls, max_pages=req.max_pages)
        return audit_res.semantic_clustering or {}
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
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


@router.post("/api/page-knowledge-graph", response_model=PageKnowledgeGraphResult, summary="Extract Page-Level Knowledge Graph & W3C RDF Ontology")
def api_extract_page_knowledge_graph(req: PageKnowledgeGraphRequest):
    """
    Constructs an atomic, W3C-compliant Page-Level / Document-Level Knowledge Graph.
    Accepts arbitrary B2B text or URL, extracts entities and open relational triples with
    verbatim sentence provenance, dynamically organizes concepts into a W3C SKOS
    concept scheme using Universal B2B Facets (or an optional vertical profile),
    grounds entities against Wikidata, asserts W3C PROV-O evidentiary lineage, and
    serializes to Turtle (.ttl) and JSON-LD.
    """
    url = req.url
    text = req.text
    title = req.title
    subject = req.subject or "Platform"
    entities = None
    triples = None

    if url and not text:
        try:
            validate_url_for_fetch(url)
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err))
        try:
            pipeline = get_pipeline(req.vertical_id)
            proc_res = pipeline.process(url=url, deep_crawl=False)
            text = proc_res.raw_text or proc_res.clean_text or ""
            if not text.strip():
                raise HTTPException(status_code=400, detail="Could not extract readable text from the provided URL.")
            entities = proc_res.entities
            triples = proc_res.triples
            title = title or proc_res.title
            if subject == "Platform":
                from urllib.parse import urlparse
                netloc = urlparse(url).netloc.replace("www.", "")
                domain_part = netloc.split(".")[0].capitalize() if netloc else "Platform"
                if domain_part:
                    subject = domain_part
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to fetch or process URL %s for page knowledge graph: %s", url, e, exc_info=True)
            raise HTTPException(status_code=500, detail=f"Failed to process URL: {e}")

    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="No text available to build knowledge graph. Provide either 'text' or a valid 'url'.")

    try:
        res = build_page_knowledge_graph(
            text=text,
            url=url,
            title=title,
            subject=subject,
            vertical_id=req.vertical_id,
            entities=entities,
            triples=triples
        )
        return res
    except Exception as e:
        logger.error("Page knowledge graph generation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Knowledge graph generation failed: {e}")

