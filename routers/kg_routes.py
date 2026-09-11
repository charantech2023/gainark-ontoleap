"""
GainARK OntoLeap — Knowledge Graph Operations, SPARQL, OWL 2 DL, and Link Prediction
"""

import logging
from typing import List, Dict, Optional, Any
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field
from rdflib import Graph

from knowledge_graph import (
    execute_sparql_query_on_ttl,
    export_to_owl_xml,
    build_page_knowledge_graph
)
from link_prediction import predict_kg_links
from graph_export import generate_standalone_graph_html
from scraper import validate_url_for_fetch
from routers.deps import get_pipeline
from models import (
    SemanticTriple,
    SparqlQueryRequest,
    SparqlQueryResponse,
    LinkPredictionRequest,
    LinkPredictionResponse,
    ExportGraphHtmlRequest,
    PageKnowledgeGraphRequest,
    PageKnowledgeGraphResult,
    PageKGRequest,
    PageKnowledgeGraph,
    SiteKGRequest,
    SiteKnowledgeGraph,
    KGAlignmentRequest,
    GraphAlignmentResult,
    IndustryOntologyModel
)
from page_graph import build_page_kg
from site_graph import build_site_kg, route_domain_to_vertical
from industry_ontology import classify_vertical
from industry_ontology import (
    list_available_industries,
    load_industry_ontology,
    align_graph_with_industry
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


# ==============================================================================
# PURE KNOWLEDGE GRAPH & ONTOLOGY ENDPOINTS
# ==============================================================================

@router.get("/api/kg/industries", response_model=List[Dict[str, Any]], summary="List Available Industry Reference Ontologies")
async def api_list_industries(include_unusable: bool = False):
    """
    Vertical reference ontologies that can actually measure a site.

    A vertical with no concept layer is hidden: coverage is scored against its concepts,
    so routing a site to one reports every concept as a gap. Pass `include_unusable=true`
    to see them anyway, which is an operator view rather than something to put in a picker.
    """
    return list_available_industries(include_unusable=include_unusable)


@router.get("/api/kg/industry/{vertical_id}", response_model=IndustryOntologyModel, summary="Inspect Industry Reference Ontology")
async def api_get_industry_ontology(vertical_id: str):
    """
    Retrieve full SKOS concept hierarchy, standard classes, and expected predicates for an industry.
    """
    try:
        return load_industry_ontology(vertical_id)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Industry ontology '{vertical_id}' not found: {e}")


@router.post("/api/kg/page", response_model=PageKnowledgeGraph, summary="Build Page-Level Knowledge Graph")
async def api_build_page_kg(req: PageKGRequest):
    """
    Extracts entities, semantic triples with provenance, and Schema.org markup from any URL or HTML page.
    """
    if not req.url and not req.html_content:
        raise HTTPException(status_code=400, detail="Either 'url' or 'html_content' must be provided.")

    target = req.html_content if req.html_content else req.url

    vertical_id = req.vertical_id
    routed = None
    if not vertical_id:
        try:
            if req.html_content:
                # The caller already supplied the text; classify it rather than fetch.
                routed = classify_vertical(req.html_content)
                routed["pages_read"] = 0
            else:
                routed = route_domain_to_vertical(req.url)
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=str(val_err))
        vertical_id = routed.get("vertical_id")
        if not vertical_id:
            raise HTTPException(status_code=422, detail={
                "error": "Could not identify an industry vertical for this page.",
                "reason": routed.get("reason"),
                "candidates": routed.get("candidates"),
                "hint": "Pass vertical_id explicitly, or see GET /api/kg/industries.",
            })
        logger.info("[API PageKG] Routed %s to %s (%s)",
                    req.url or "supplied HTML", vertical_id, routed.get("reason"))

    try:
        kg = build_page_kg(
            url_or_html=target,
            url=req.url,
            vertical_id=vertical_id
        )
        # How the vertical was arrived at is known here and nowhere else. Without it the
        # caller cannot tell a vocabulary it chose from one the service inferred.
        if routed is None:
            kg.vertical_source = "requested"
        else:
            kg.vertical_source = "auto-detected"
            kg.vertical_reason = routed.get("reason")
            kg.vertical_evidence = list(routed.get("evidence") or [])
        return kg
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as e:
        logger.error("[API PageKG] Failed to extract page graph: %s", e)
        raise HTTPException(status_code=500, detail=f"Knowledge graph extraction failed: {str(e)}")


@router.post("/api/kg/classify", summary="Identify Which Industry Vertical A Domain Belongs To")
def api_kg_classify(req: SiteKGRequest):
    """
    Work out which reference vertical a domain should be measured against.

    Scores the site's own words against each vertical's vocabulary and returns the match,
    the terms that decided it, and every candidate's score. Only verticals that carry a
    concept layer are considered: one without concepts cannot measure coverage, so routing
    a site there would report every concept as a gap.

    A `vertical_id` of null means no vertical fits well enough. That is deliberate - the
    alternative is measuring a site against a vocabulary that does not describe it.
    """
    try:
        return route_domain_to_vertical(req.domain_or_url)
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as e:
        logger.error("[API KG Classify] Routing failed: %s", e)
        raise HTTPException(status_code=500, detail="Vertical routing failed.")


@router.post("/api/kg/site", response_model=SiteKnowledgeGraph, summary="Synthesize Site-Wide Knowledge Graph")
async def api_build_site_kg(req: SiteKGRequest):
    """
    Crawls domain, canonicalizes entity aliases, induces class hierarchy, and maps topic clusters.
    """
    vertical_id = req.vertical_id
    routed = None
    if not vertical_id:
        try:
            routed = route_domain_to_vertical(req.domain_or_url)
        except ValueError as val_err:
            # Routing sits outside the crawl's error handling below, so an unreachable
            # domain reached the client as an unexplained 500.
            raise HTTPException(status_code=400, detail=str(val_err))
        vertical_id = routed.get("vertical_id")
        if not vertical_id:
            # Refuse rather than fall back to a default. A wrong vertical does not fail
            # loudly, it returns a plausible report measured against the wrong yardstick.
            raise HTTPException(status_code=422, detail={
                "error": "Could not identify an industry vertical for this domain.",
                "reason": routed.get("reason"),
                "candidates": routed.get("candidates"),
                "hint": "Pass vertical_id explicitly, or see GET /api/kg/industries.",
            })
        logger.info("[API SiteKG] Routed %s to %s (%s)",
                    req.domain_or_url, vertical_id, routed.get("reason"))

    try:
        kg = build_site_kg(
            start_url=req.domain_or_url,
            max_pages=req.max_pages,
            vertical_id=vertical_id
        )
        # Only this scope knows whether the vertical was asked for or worked out.
        if routed is None:
            kg.vertical_source = "requested"
        else:
            kg.vertical_source = "auto-detected"
            kg.vertical_reason = routed.get("reason")
            kg.vertical_evidence = list(routed.get("evidence") or [])
        return kg
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as e:
        logger.error("[API SiteKG] Failed to synthesize site graph: %s", e)
        raise HTTPException(status_code=500, detail=f"Site knowledge graph synthesis failed: {str(e)}")


@router.get("/api/kg/history", summary="List Stored Audit Runs For A Domain")
def api_kg_history(
    domain: str = Query(..., min_length=1, max_length=253),
    limit: int = 50,
):
    """
    Audit runs held in the durable store for one domain, newest first.

    `domain` is required. It was optional, and omitting it listed every domain the
    store had ever seen - names, timestamps and sizes. Authentication here is a single
    shared key with no notion of who is calling, so that let any caller enumerate every
    other subscriber. Requiring the domain means a caller can only ask about one it
    already knows. That is a plug, not tenancy: the real fix is an owner recorded on
    each run and reads filtered to the caller.

    Each crawl of a site is recorded as its own run graph, so this is the history a
    single page-level extraction can never provide.
    """
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=422, detail="limit must be between 1 and 200.")
    target = (domain or "").strip()
    if not target:
        # list_runs treats a falsy domain as "no filter", so an empty or whitespace-only
        # string would return every domain in the store - the leak this endpoint was just
        # changed to close. Refuse it here rather than trust the query validator alone.
        raise HTTPException(status_code=422, detail="domain must not be empty.")
    try:
        import graph_store
        return {"runs": graph_store.list_runs(domain=target, limit=limit)}
    except Exception as e:
        logger.error("[API KG History] Could not read the run store: %s", e)
        raise HTTPException(status_code=503, detail="Run history is unavailable.")


@router.get("/api/kg/diff", summary="Diff Two Stored Audit Runs")
def api_kg_diff(earlier: str, later: str, claims_only: bool = True):
    """
    What a domain claims now that it did not before, and what it has stopped claiming.

    `earlier` and `later` are graph ids from /api/kg/history. `claims_only` drops
    provenance triples, which is what makes the answer readable - the question is almost
    always what changed about the product, not that the crawl ran at a different time.
    """
    try:
        import graph_store
        known = {r["graph_id"] for r in graph_store.list_runs(limit=200)}
        missing = [g for g in (earlier, later) if g not in known]
        if missing:
            raise HTTPException(status_code=404, detail="Unknown run graph: %s" % ", ".join(missing))
        result = graph_store.diff_runs(earlier, later, claims_only=claims_only)
        return {
            "earlier": earlier,
            "later": later,
            "added": [list(t) for t in result["added"]],
            "removed": [list(t) for t in result["removed"]],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("[API KG Diff] Could not diff runs: %s", e)
        raise HTTPException(status_code=503, detail="Run history is unavailable.")


@router.post("/api/kg/align", response_model=GraphAlignmentResult, summary="Align Knowledge Graph Against Industry Ontology")
async def api_align_kg(req: KGAlignmentRequest):
    """
    Compares a page or site Knowledge Graph against an Industry Reference Ontology
    to identify covered concepts, standards, and category whitespace.
    """
    try:
        # Resolution order: what the caller asked for, then what the supplied graph was
        # built with, then what the site classifies as. Falling back to a constant scored
        # every graph against billing, whatever it actually described.
        vertical_id = req.vertical_id
        supplied = req.page_kg or req.site_kg

        if supplied is not None:
            kg = supplied
            vertical_id = vertical_id or supplied.vertical_id
        elif req.domain_or_url:
            if not vertical_id:
                try:
                    routed = route_domain_to_vertical(req.domain_or_url)
                except ValueError as val_err:
                    raise HTTPException(status_code=400, detail=str(val_err))
                vertical_id = routed.get("vertical_id")
                if not vertical_id:
                    raise HTTPException(status_code=422, detail={
                        "error": "Could not identify an industry vertical for this domain.",
                        "reason": routed.get("reason"),
                        "candidates": routed.get("candidates"),
                        "hint": "Pass vertical_id explicitly, or see GET /api/kg/industries.",
                    })
                logger.info("[API KGAlign] Routed %s to %s (%s)",
                            req.domain_or_url, vertical_id, routed.get("reason"))
            if req.max_pages > 1:
                kg = build_site_kg(req.domain_or_url, max_pages=req.max_pages, vertical_id=vertical_id)
            else:
                kg = build_page_kg(req.domain_or_url, vertical_id=vertical_id)
        else:
            raise HTTPException(status_code=400, detail="Either 'domain_or_url', 'page_kg', or 'site_kg' must be provided.")

        alignment = align_graph_with_industry(kg, vertical_id=vertical_id)
        return alignment
    except HTTPException:
        raise
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))
    except Exception as e:
        logger.error("[API KGAlign] Alignment failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Knowledge graph alignment failed: {str(e)}")


