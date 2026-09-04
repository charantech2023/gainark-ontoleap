"""
GainARK OntoLeap — FastAPI Enterprise REST API & Semantic Service Gateway

Provides REST API endpoints for ontology extraction, competitor benchmarking,
sitemap crawling, internal link discovery, generative search simulation,
W3C RDF Turtle and N-Triples exports, and interactive SPARQL 1.1 querying.
"""

import os
import json
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from rdflib import Graph

from pipeline import OntologyPipeline, crawl_and_build_unified_graph
from linking import (
    audit_internal_links,
    simulate_search_response,
    execute_sparql_query_on_ttl,
    export_to_owl_xml
)
from link_prediction import predict_kg_links
from graph_export import generate_standalone_graph_html
from models import (
    ReadinessBreakdown,
    EntityMatch,
    SeedConceptMatch,
    SchemaOrgData,
    CompetitiveGapAnalysis,
    KeywordGapItem,
    SemanticTriple,
    UnifiedSiteGraph,
    PageCrawlSummary,
    InternalLinkOpportunity,
    SiteAuditAndLinkResult,
    SearchSimulationRequest,
    SearchSimulationResponse,
    SparqlQueryRequest,
    SparqlQueryResponse,
    PredictedLink,
    LinkPredictionRequest,
    LinkPredictionResponse,
    ExportGraphHtmlRequest
)

app = FastAPI(
    title="GainARK OntoLeap — Autonomous Ontology Intelligence & Semantic Graph Engine",
    description="Enterprise ontology intelligence, zero-shot entity grounding, relational triples extraction, and semantic internal linking for B2B SaaS.",
    version="2.0.0"
)

# Enable CORS for local and web frontends
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows all origins (localhost:3000, 5173, etc.)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pipeline instances cache to reuse loaded GLiNER models
pipeline_cache: Dict[str, OntologyPipeline] = {}


def get_pipeline(vertical_id: Optional[str] = None) -> OntologyPipeline:
    target_id = vertical_id or "b2b_saas_fintech"
    if target_id not in pipeline_cache:
        # Check if custom config exists or use default vertical_config.json
        config_file = f"configs/{target_id}.json"
        if not os.path.exists(config_file):
            config_file = "vertical_config.json"
        
        print(f"Instantiating OntologyPipeline for vertical '{target_id}' using {config_file}...")
        pipeline_cache[target_id] = OntologyPipeline(config_path=config_file)
    return pipeline_cache[target_id]


# ---------------------------------------------------------------------------
# Request and Response Models
# ---------------------------------------------------------------------------

class BatchCrawlRequest(BaseModel):
    sitemap_url: str = Field(..., description="Target XML sitemap or sitemap index URL", example="https://www.ordwaylabs.com/sitemap.xml")
    max_pages: int = Field(default=10, description="Maximum number of pages to crawl and synthesize")


class InternalLinkAuditRequest(BaseModel):
    sitemap_url: Optional[str] = Field(
        default=None,
        description="Target XML sitemap or sitemap index URL",
        example="https://www.ordwaylabs.com/sitemap.xml"
    )
    urls: Optional[List[str]] = Field(
        default=None,
        description="Optional explicit list of target URLs to audit and link across"
    )
    max_pages: int = Field(default=10, description="Maximum number of pages to crawl and analyze")


class AuditRequest(BaseModel):
    url: str = Field(..., description="Target webpage URL to analyze", example="https://www.ordwaylabs.com")
    vertical_id: Optional[str] = Field(default=None, description="Vertical ontology domain ID", example="b2b_saas_fintech")
    deep_crawl: bool = Field(default=False, description="When true, fetch up to 3 high-value sub-pages (pricing, features, integrations) and merge their signals")


class AuditResponse(BaseModel):
    url: str
    title: Optional[str] = None
    vertical_id: str
    readiness_score: float
    readiness_breakdown: Optional[ReadinessBreakdown] = None
    mandatory_schema_status: Dict[str, bool]
    entity_breakdown: Dict[str, List[Dict[str, Any]]]
    entities: List[EntityMatch]
    seed_concepts: List[SeedConceptMatch]
    schema_org: List[SchemaOrgData] = Field(
        default_factory=list,
        description="Parsed Schema.org nodes detected on the page"
    )
    recommended_patch: Optional[Dict[str, Any]] = None
    crawled_subpages: List[str] = Field(default_factory=list, description="Sub-pages fetched during deep crawl")
    triples: List[SemanticTriple] = Field(
        default_factory=list,
        description="Extracted relational semantic triples (Subject, Predicate, Object)"
    )
    meta_description: Optional[str] = Field(default=None, description="Page meta or OpenGraph description")
    site_name: Optional[str] = Field(default=None, description="OpenGraph site name or brand")


class BenchmarkRequest(BaseModel):
    primary_url: Optional[str] = Field(
        default=None,
        description="Target domain/URL to audit against competitors",
        example="https://www.ordwaylabs.com"
    )
    competitor_urls: Optional[List[str]] = Field(
        default=None,
        description="List of competitor URLs to compare against",
        example=[
            "https://www.chargebee.com",
            "https://www.maxio.com",
            "https://stripe.com/billing"
        ]
    )
    urls: Optional[List[str]] = Field(
        default=None,
        description="Fallback list of URLs to benchmark (if primary_url is omitted)"
    )
    vertical_id: Optional[str] = Field(default=None, description="Vertical ontology domain ID", example="b2b_saas_fintech")


class BenchmarkItem(BaseModel):
    url: str
    title: Optional[str] = None
    readiness_score: float
    compliance_str: str
    passed_schemas: List[str]
    missing_schemas: List[str]
    entity_count: int
    detected_concepts: List[str]
    missing_concepts: List[str]
    top_missing_concepts: str
    top_entities: List[Dict[str, Any]]
    recommended_patch: Optional[Dict[str, Any]] = None
    is_primary: bool = False


class BenchmarkResponse(BaseModel):
    vertical_id: str
    total_analyzed: int
    primary_url: Optional[str] = None
    comparative_table: List[BenchmarkItem]
    gap_analysis: Optional[CompetitiveGapAnalysis] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/", response_class=HTMLResponse)
def get_dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard template not found</h1>", status_code=404)


@app.get("/api/info")
def api_info():
    return {
        "status": "online",
        "name": "GainARK OntoLeap Platform",
        "service": "GainARK OntoLeap Platform",
        "version": "2.0.0",
        "features": {
            "llms_txt_manifest": True,
            "google_rich_results_validation": True,
            "perplexity_searchgpt_simulator": True,
            "topic_silo_canvas": True,
            "visual_diff_modal": True,
            "wordpress_cms_hook": True,
            "sparql_query_engine": True,
            "w3c_rdf_turtle_export": True,
            "w3c_ntriples_export": True,
            "wikidata_entity_grounding": True,
            "owl_2_dl_export": True,
            "semantic_clustering": True,
            "benchmark_csv_export": True,
            "kg_link_prediction": True,
            "pyvis_graph_html_export": True
        },
        "endpoints": {
            "dashboard": "GET /dashboard",
            "audit": "POST /api/audit",
            "benchmark": "POST /api/benchmark",
            "batch-crawl": "POST /api/batch-crawl",
            "internal-links": "POST /api/internal-links",
            "simulate-search": "POST /api/simulate-search",
            "sparql": "POST /api/sparql",
            "export-ntriples": "POST /api/export-ntriples",
            "export-owl": "POST /api/export-owl",
            "semantic-clusters": "POST /api/semantic-clusters",
            "benchmark-export-csv": "POST /api/benchmark/export-csv",
            "predict-links": "POST /api/predict-links",
            "export-graph-html": "POST /api/export-graph-html",
            "health": "GET /api/health"
        }
    }


@app.get("/api/health")
def health():
    return {
        "status": "healthy",
        "cached_pipelines": list(pipeline_cache.keys())
    }


@app.post("/api/batch-crawl", response_model=UnifiedSiteGraph)
async def api_batch_crawl(req: BatchCrawlRequest):
    """
    Crawls pages discovered in the target XML sitemap and synthesizes a site-wide knowledge graph
    with deduplicated relational triples and Schema.org @graph JSON-LD.
    """
    try:
        return await crawl_and_build_unified_graph(req.sitemap_url, max_pages=req.max_pages)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Batch crawl failed for '{req.sitemap_url}': {str(e)}")


@app.post("/api/internal-links", response_model=SiteAuditAndLinkResult)
async def api_internal_links(req: InternalLinkAuditRequest):
    """
    Crawls multiple pages across a site, identifies canonical topic authority hubs,
    and generates high-intent internal link recommendations for unlinked entity & triple mentions.
    """
    try:
        return await audit_internal_links(
            sitemap_url=req.sitemap_url,
            urls=req.urls,
            max_pages=req.max_pages
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal link audit failed: {str(e)}")


@app.post("/api/simulate-search", response_model=SearchSimulationResponse)
def api_simulate_search(req: SearchSimulationRequest):
    """
    Simulates a Perplexity / SearchGPT generative query response, synthesizing answers
    directly from verified domain relational triples with grounded citations to canonical topic hubs.
    """
    try:
        return simulate_search_response(
            query=req.query,
            root_domain=req.root_domain,
            triples=req.triples,
            topic_hubs=req.topic_hubs,
            entities=req.entities
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search simulation failed: {str(e)}")


@app.post("/api/sparql", response_model=SparqlQueryResponse, summary="Execute SPARQL 1.1 Query on Knowledge Graph")
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
    except Exception as e:
        return SparqlQueryResponse(
            query=req.query,
            columns=[],
            rows=[],
            row_count=0,
            execution_status="failed",
            error=str(e)
        )


class NTriplesExportRequest(BaseModel):
    rdf_turtle: str = Field(..., description="RDF Turtle serialization to convert into N-Triples")


@app.post("/api/export-ntriples", summary="Convert RDF Turtle to W3C N-Triples")
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
        raise HTTPException(status_code=500, detail=f"Failed to serialize N-Triples: {str(e)}")


class OwlExportRequest(BaseModel):
    root_domain: str = Field(default="example.com", description="Root domain of the platform")
    triples: List[SemanticTriple] = Field(default_factory=list, description="Extracted relational triples")
    topic_hubs: Dict[str, str] = Field(default_factory=dict, description="Canonical topic hubs map")
    entities: List[str] = Field(default_factory=list, description="Extracted entities")
    rdf_turtle: Optional[str] = Field(default=None, description="Optional Turtle to convert directly")


@app.post("/api/export-owl", summary="Generate and Export W3C OWL 2 DL Ontology")
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
        raise HTTPException(status_code=500, detail=f"Failed to generate OWL ontology: {str(e)}")


class SemanticClustersRequest(BaseModel):
    urls: List[str] = Field(default_factory=list)
    sitemap_url: Optional[str] = None
    max_pages: int = 10


@app.post("/api/semantic-clusters", summary="Compute TF-IDF Cosine Similarity & Topic Clusters")
async def api_semantic_clusters(req: SemanticClustersRequest):
    """
    Analyzes content across site pages, computing TF-IDF vectors, pairwise cosine
    similarity matrix, cannibalization overlaps (>=0.70), and thematic topic silos.
    """
    try:
        audit_res = await audit_internal_links(sitemap_url=req.sitemap_url, urls=req.urls, max_pages=req.max_pages)
        return audit_res.semantic_clustering or {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Semantic cluster analysis failed: {str(e)}")


class BenchmarkCsvExportRequest(BaseModel):
    comparative_table: List[Dict[str, Any]]


@app.post("/api/benchmark/export-csv", summary="Export Competitor Benchmark Matrix as CSV")
def api_export_benchmark_csv(req: BenchmarkCsvExportRequest):
    """
    Converts competitive benchmark data table into standardized CSV format for pandas/spreadsheet ingestion.
    """
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Rank", "Domain", "Readiness Score", "Schema Valid", "Entities Found", "Triples Extracted", "Key Gaps"])
    for item in req.comparative_table:
        writer.writerow([
            item.get("rank", ""),
            item.get("url", ""),
            item.get("readiness_score", 0),
            "Yes" if item.get("schema_valid") else "No",
            item.get("entity_count", 0),
            item.get("triple_count", 0),
            "; ".join(item.get("gaps", [])) if isinstance(item.get("gaps"), list) else str(item.get("gaps", ""))
        ])
    return PlainTextResponse(content=output.getvalue(), media_type="text/csv")


@app.post("/api/predict-links", response_model=LinkPredictionResponse, summary="Predict Missing Knowledge Graph Relations (PyKEEN Paradigm)")
def api_predict_links(req: LinkPredictionRequest):
    """
    Infers missing high-probability relational links across the knowledge graph,
    computes graph completeness, and grounds predicted entities to Wikidata Q-IDs.
    """
    try:
        return predict_kg_links(req.domain, req.triples, req.entities, req.topic_hubs)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Link prediction failed: {str(e)}")


@app.post("/api/export-graph-html", summary="Export Standalone Interactive PyVis/Vis.js Graph HTML")
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
        raise HTTPException(status_code=500, detail=f"Failed to generate interactive graph HTML: {str(e)}")





@app.post("/api/audit", response_model=AuditResponse)
def audit_endpoint(request: AuditRequest):
    """
    Runs the ontology pipeline against a target URL:
    - Verifies Schema.org mandatory types
    - Runs GLiNER zero-shot entity recognition
    - Matches core seed concepts
    - Calculates the ontology readiness score
    - Dynamically generates the Schema.org remediation patch
    - Optionally deep-crawls up to 3 high-value sub-pages (pricing, features, integrations)
    """
    try:
        pipeline = get_pipeline(request.vertical_id)
        result = pipeline.process(url=request.url, deep_crawl=request.deep_crawl)

        # Build entity breakdown grouped by entity label
        breakdown: Dict[str, List[Dict[str, Any]]] = {}
        for ent in result.entities:
            breakdown.setdefault(ent.label, []).append({
                "text": ent.text,
                "score": ent.score,
                "start": ent.start,
                "end": ent.end
            })

        return AuditResponse(
            url=result.url or request.url,
            title=result.title,
            vertical_id=result.vertical_id,
            readiness_score=result.readiness_score,
            readiness_breakdown=result.readiness_breakdown,
            mandatory_schema_status=result.mandatory_schema_status,
            entity_breakdown=breakdown,
            entities=result.entities,
            seed_concepts=result.seed_concepts,
            schema_org=result.schema_org,
            recommended_patch=result.recommended_patch,
            crawled_subpages=result.crawled_subpages,
            triples=result.triples,
            meta_description=result.meta_description,
            site_name=result.site_name
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to audit URL '{request.url}': {str(e)}")


@app.post("/api/benchmark", response_model=BenchmarkResponse)
def benchmark_endpoint(request: BenchmarkRequest):
    """
    Runs comparative analysis across multiple URLs and returns structured benchmark data
    along with an intelligent Competitive Gap Analysis and Leapfrog Action Plan.
    """
    primary_url = request.primary_url
    competitor_urls = list(request.competitor_urls or [])

    if not primary_url and request.urls:
        primary_url = request.urls[0]
        competitor_urls = request.urls[1:]

    all_urls = []
    if primary_url:
        all_urls.append((primary_url, True))
    for c_url in competitor_urls:
        if c_url and c_url not in [u[0] for u in all_urls]:
            all_urls.append((c_url, False))

    if not all_urls:
        raise HTTPException(status_code=400, detail="Must provide 'primary_url' and 'competitor_urls' (or 'urls').")

    try:
        pipeline = get_pipeline(request.vertical_id)
        all_seed_concepts = pipeline.config.core_seed_concepts
        items: List[BenchmarkItem] = []
        raw_results = {}

        for target_url, is_prim in all_urls:
            try:
                res = pipeline.process(url=target_url)
                raw_results[target_url] = res
                
                mand = res.mandatory_schema_status
                passed = [k for k, v in mand.items() if v]
                missing_schemas = [k for k, v in mand.items() if not v]
                compliance_str = f"{len(passed)}/{len(mand)}"

                detected_concepts = [c.concept for c in res.seed_concepts if c.count > 0]
                detected_set = set(detected_concepts)
                missing_concepts = [c for c in all_seed_concepts if c not in detected_set]

                # Top missing string preview
                top_missing = ", ".join(missing_concepts[:3])
                if len(missing_concepts) > 3:
                    top_missing += f" (+{len(missing_concepts) - 3} more)"
                elif not missing_concepts:
                    top_missing = "None (100% Coverage)"

                # Top entities sorted by score
                top_entities = [
                    {"label": e.label, "text": e.text, "score": e.score}
                    for e in sorted(res.entities, key=lambda x: -x.score)[:8]
                ]

                items.append(BenchmarkItem(
                    url=target_url,
                    title=res.title,
                    readiness_score=res.readiness_score,
                    compliance_str=compliance_str,
                    passed_schemas=passed,
                    missing_schemas=missing_schemas,
                    entity_count=len(res.entities),
                    detected_concepts=detected_concepts,
                    missing_concepts=missing_concepts,
                    top_missing_concepts=top_missing,
                    top_entities=top_entities,
                    recommended_patch=res.recommended_patch,
                    is_primary=is_prim
                ))
            except Exception as e:
                items.append(BenchmarkItem(
                    url=target_url,
                    title="Error processing URL",
                    readiness_score=0.0,
                    compliance_str="0/3",
                    passed_schemas=[],
                    missing_schemas=list(pipeline.config.mandatory_schema_types),
                    entity_count=0,
                    detected_concepts=[],
                    missing_concepts=list(all_seed_concepts),
                    top_missing_concepts="All Concepts Missing",
                    top_entities=[],
                    recommended_patch=None,
                    is_primary=is_prim
                ))

        gap_analysis = None
        if primary_url and primary_url in raw_results:
            prim_res = raw_results[primary_url]
            comp_res_list = [raw_results[u] for u, is_p in all_urls if not is_p and u in raw_results]
            gap_analysis = pipeline.run_competitive_gap_analysis(prim_res, comp_res_list)

        return BenchmarkResponse(
            vertical_id=pipeline.config.vertical_id,
            total_analyzed=len(items),
            primary_url=primary_url,
            comparative_table=items,
            gap_analysis=gap_analysis
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Benchmark execution failed: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
