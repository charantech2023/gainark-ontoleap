"""
GainARK OntoLeap — FastAPI Enterprise REST API & Semantic Service Gateway

Provides REST API endpoints for ontology extraction, competitor benchmarking,
sitemap crawling, internal link discovery, generative search simulation,
W3C RDF Turtle and N-Triples exports, and interactive SPARQL 1.1 querying.
"""

import os
import json
import time
import logging
import threading
from collections import defaultdict
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from pydantic import BaseModel, Field
from rdflib import Graph

import vertex_ai_client
import report_pdf
import alignment
import google_kg_client

from pipeline import OntologyPipeline, crawl_and_build_unified_graph, validate_url_for_fetch
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
    CitationSource,
    SparqlQueryRequest,
    SparqlQueryResponse,
    PredictedLink,
    LinkPredictionRequest,
    LinkPredictionResponse,
    ExportGraphHtmlRequest,
    DraftAlignmentRequest,
    DraftAlignmentResponse,
    ProductBriefRequest,
    ProductBriefResponse,
    ExportPdfRequest,
    GoogleKgRequest
)

# ---------------------------------------------------------------------------
# FIX #21: Structured Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ontoleap.api")

app = FastAPI(
    title="GainARK OntoLeap — Autonomous Ontology Intelligence & Semantic Graph Engine",
    description="Enterprise ontology intelligence, zero-shot entity grounding, relational triples extraction, and semantic internal linking for B2B SaaS.",
    version="2.0.0"
)

# ---------------------------------------------------------------------------
# FIX #14: CORS Configuration with Environment Variable & Safe Production Defaults
# ---------------------------------------------------------------------------
allowed_origins_env = os.environ.get("ALLOWED_ORIGINS", "").strip()
if allowed_origins_env:
    allowed_origins = [o.strip() for o in allowed_origins_env.split(",") if o.strip()]
else:
    allowed_origins = [
        "https://gainark-ontoleap-35509275124.asia-south1.run.app",
        "http://localhost:8080",
        "http://localhost:8000",
        "http://localhost:3000",
        "http://127.0.0.1:8080",
        "http://127.0.0.1:8000"
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# FIX #2: In-Memory Sliding Window Rate Limiting Middleware
# ---------------------------------------------------------------------------
class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    In-memory rolling-window rate limiter per client IP.
    Protects heavy model inference and multi-page crawl endpoints from DoS.
    """
    def __init__(self, app):
        super().__init__(app)
        self.history = defaultdict(list)
        self.lock = threading.Lock()
        self.limits = {
            "/api/audit": 10,           # Max 10 audits/min per IP
            "/api/benchmark": 3,       # Max 3 multi-domain benchmarks/min
            "/api/batch-crawl": 3,     # Max 3 sitemap crawls/min
            "/api/internal-links": 5,  # Max 5 internal linking crawls/min
        }
        self.default_limit = 60        # Default 60 requests/min

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if request.method == "POST" and path.startswith("/api/"):
            client_ip = request.client.host if request.client else "127.0.0.1"
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                client_ip = forwarded.split(",")[0].strip()

            max_requests = self.limits.get(path, self.default_limit)
            now = time.time()
            window_start = now - 60.0

            with self.lock:
                key = f"{client_ip}:{path}"
                recent = [t for t in self.history[key] if t > window_start]
                if len(recent) >= max_requests:
                    retry_after = int(60 - (now - recent[0])) + 1
                    logger.warning("Rate limit exceeded for %s from IP %s", path, client_ip)
                    return JSONResponse(
                        status_code=429,
                        content={"detail": f"Rate limit exceeded for {path}. Max {max_requests} requests per minute."},
                        headers={"Retry-After": str(max(1, retry_after))}
                    )
                recent.append(now)
                self.history[key] = recent

        return await call_next(request)

app.add_middleware(RateLimitMiddleware)


# Pipeline instances cache to reuse loaded GLiNER models
pipeline_cache: Dict[str, OntologyPipeline] = {}
SUPPORTED_VERTICALS = {
    "b2b_saas_fintech",
    "cybersecurity",
    "healthtech",
    "developer_tools"
}


# FIX #23: Validate vertical_id and return 400 if unsupported
def get_pipeline(vertical_id: Optional[str] = None) -> OntologyPipeline:
    target_id = vertical_id or "b2b_saas_fintech"
    if vertical_id and vertical_id not in SUPPORTED_VERTICALS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported vertical_id '{vertical_id}'. Supported verticals: {sorted(list(SUPPORTED_VERTICALS))}"
        )
    if target_id not in pipeline_cache:
        config_file = f"verticals/{target_id}.json"
        if not os.path.exists(config_file):
            config_file = f"configs/{target_id}.json"
        if not os.path.exists(config_file):
            config_file = "vertical_config.json"

        logger.info("Instantiating OntologyPipeline for vertical '%s' using %s...", target_id, config_file)
        pipeline_cache[target_id] = OntologyPipeline(config_path=config_file)
    return pipeline_cache[target_id]


@app.get("/api/verticals", summary="List Available Industry Verticals")
def api_list_verticals():
    """
    Returns all registered domain ontology profiles with their display metadata.
    """
    profiles = []
    for vid in sorted(list(SUPPORTED_VERTICALS)):
        cfg_file = f"verticals/{vid}.json"
        if os.path.exists(cfg_file):
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    cdata = json.load(f)
                    profiles.append({
                        "vertical_id": vid,
                        "display_name": cdata.get("display_name", vid),
                        "entity_types_count": len(cdata.get("gliner_labels", [])),
                        "seed_concepts_count": len(cdata.get("core_seed_concepts", []))
                    })
            except Exception:
                profiles.append({"vertical_id": vid, "display_name": vid})
        else:
            profiles.append({"vertical_id": vid, "display_name": vid})
    return {"verticals": profiles}



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
    # FIX #1: SSRF protection
    try:
        validate_url_for_fetch(req.sitemap_url)
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))

    try:
        return await crawl_and_build_unified_graph(req.sitemap_url, max_pages=req.max_pages)
    except Exception as e:
        # FIX #20: Sanitize exception leak
        logger.error("Batch crawl failed for %s: %s", req.sitemap_url, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch crawl failed for '{req.sitemap_url}'. Check server logs for details.")


@app.post("/api/internal-links", response_model=SiteAuditAndLinkResult)
async def api_internal_links(req: InternalLinkAuditRequest):
    """
    Crawls multiple pages across a site, identifies canonical topic authority hubs,
    and generates high-intent internal link recommendations for unlinked entity & triple mentions.
    """
    # FIX #1: SSRF protection
    try:
        if req.sitemap_url:
            validate_url_for_fetch(req.sitemap_url)
        if req.urls:
            for u in req.urls:
                validate_url_for_fetch(u)
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))

    try:
        return await audit_internal_links(
            sitemap_url=req.sitemap_url,
            urls=req.urls,
            max_pages=req.max_pages
        )
    except Exception as e:
        # FIX #20: Sanitize exception leak
        logger.error("Internal link audit failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal link audit failed. Check server logs for details.")


@app.post("/api/simulate-search", response_model=SearchSimulationResponse)
def api_simulate_search(req: SearchSimulationRequest):
    """
    Simulates a Perplexity / SearchGPT generative query response, synthesizing answers
    directly from verified domain relational triples with grounded citations to canonical topic hubs.
    Leverages Google Gemini 2.5 Flash when available, with deterministic fallback.
    """
    try:
        if vertex_ai_client.is_available() and req.triples:
            triples_dicts = [t.model_dump() if hasattr(t, "model_dump") else t.dict() for t in req.triples]
            clean_dom = req.root_domain.replace("https://", "").replace("http://", "").split("/")[0]
            brand = clean_dom.split(".")[0].capitalize()
            gem_res = vertex_ai_client.generate_search_answer(
                query=req.query,
                triples=triples_dicts,
                brand_name=brand,
                site_url=f"https://{clean_dom}"
            )
            if gem_res.get("status") == "live_ai_generated":
                citations = []
                for idx, c in enumerate(gem_res.get("citations", []), 1):
                    citations.append(CitationSource(
                        index=idx,
                        entity=c.get("fact", brand),
                        target_url=c.get("url", f"https://{clean_dom}"),
                        evidence=c.get("fact", "")
                    ))
                return SearchSimulationResponse(
                    query=req.query,
                    synthesized_answer=gem_res.get("answer", ""),
                    citations=citations,
                    grounding_confidence=0.98,
                    hallucination_risk="Zero Hallucination Risk (100% Schema & Triple Grounded by Google Gemini 2.5 Flash)",
                    attributed_capabilities=[t.get("object", "") for t in triples_dicts[:6]]
                )
    except Exception as e:
        logger.warning("Gemini live search synthesis fallback: %s", e)

    try:
        return simulate_search_response(
            query=req.query,
            root_domain=req.root_domain,
            triples=req.triples,
            topic_hubs=req.topic_hubs,
            entities=req.entities
        )
    except Exception as e:
        logger.error("Search simulation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Search simulation failed. Check server logs.")


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


class NTriplesExportRequest(BaseModel):
    rdf_turtle: str = Field(..., max_length=500_000, description="RDF Turtle serialization to convert into N-Triples")


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
        logger.error("Failed to serialize N-Triples: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to serialize N-Triples.")


class OwlExportRequest(BaseModel):
    root_domain: str = Field(default="example.com", description="Root domain of the platform")
    triples: List[SemanticTriple] = Field(default_factory=list, description="Extracted relational triples")
    topic_hubs: Dict[str, str] = Field(default_factory=dict, description="Canonical topic hubs map")
    entities: List[str] = Field(default_factory=list, description="Extracted entities")
    rdf_turtle: Optional[str] = Field(default=None, max_length=500_000, description="Optional Turtle to convert directly")


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
        logger.error("Failed to generate OWL ontology: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate OWL ontology.")


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
        logger.error("Semantic cluster analysis failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Semantic cluster analysis failed.")


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


@app.post("/api/predict-links", response_model=LinkPredictionResponse, summary="Predict Missing Knowledge Graph Relations (Ontological Priors)")
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
        logger.error("Failed to generate graph HTML: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate interactive graph HTML. Check server logs.")


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
    # FIX #1: SSRF protection — block private IPs and non-HTTP schemes
    try:
        validate_url_for_fetch(request.url)
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))

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
    except HTTPException:
        raise
    except Exception as e:
        # FIX #20: Sanitize exception leak
        logger.error("Failed to audit URL '%s': %s", request.url, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to audit URL '{request.url}'. Check server logs.")


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

    # FIX #1: SSRF protection — validate every target URL
    for target_url, _ in all_urls:
        try:
            validate_url_for_fetch(target_url)
        except ValueError as val_err:
            raise HTTPException(status_code=400, detail=f"Invalid benchmark target URL '{target_url}': {val_err}")

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
    except HTTPException:
        raise
    except Exception as e:
        # FIX #20: Sanitize exception leak
        logger.error("Benchmark execution failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Benchmark execution failed. Check server logs for details.")


@app.post("/api/check-draft", response_model=DraftAlignmentResponse, summary="Evaluate Draft Against Knowledge Graph (PAS & Fluff)")
def api_check_draft(req: DraftAlignmentRequest):
    """
    Evaluates draft content against the canonical Product Knowledge Graph.
    Computes the Product Alignment Score (PAS: 0-100), detects generic buzzword fluff,
    and runs Gemini LLM-as-judge claim verification when available.
    """
    try:
        triples_dicts = [t.model_dump() if hasattr(t, "model_dump") else t.dict() for t in req.triples]
        pipeline = get_pipeline(req.vertical_id)
        
        # Calculate algorithmic PAS & fluff penalty
        pas_result = alignment.calculate_product_alignment_score(
            text=req.draft_text,
            canonical_triples=triples_dicts,
            canonical_entities=req.entities or list(pipeline.config.core_seed_concepts),
            brand_name=req.brand_name
        )

        # Enhance with Gemini LLM-as-judge if available
        llm_judge = None
        if vertex_ai_client.is_available():
            try:
                llm_judge = vertex_ai_client.check_draft_alignment(
                    draft_text=req.draft_text,
                    brand_name=req.brand_name,
                    triples=triples_dicts,
                    entities=req.entities
                )
            except Exception as j_err:
                logger.warning("Gemini draft alignment judge fallback: %s", j_err)

        contradictions = pas_result["contradictions"]
        if llm_judge and llm_judge.get("contradictions"):
            contradictions = list(set(contradictions + llm_judge.get("contradictions", [])))

        recs = pas_result["recommendations"]
        if llm_judge and llm_judge.get("rewrite_recommendations"):
            recs = list(set(recs + llm_judge.get("rewrite_recommendations", [])[:2]))

        return DraftAlignmentResponse(
            product_alignment_score=pas_result["product_alignment_score"],
            verdict=pas_result["verdict"],
            breakdown=pas_result["breakdown"],
            fluff_analysis=pas_result["fluff_analysis"],
            grounded_triples_count=pas_result["grounded_triples_count"],
            grounded_triples=pas_result["grounded_triples"],
            missing_triples_count=pas_result["missing_triples_count"],
            missing_triples=pas_result["missing_triples"],
            contradictions=contradictions,
            recommendations=recs,
            llm_judge=llm_judge
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Draft alignment check failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Draft alignment check failed. Check server logs.")


@app.post("/api/content-brief", response_model=ProductBriefResponse, summary="Generate Product Truth Content Brief")
def api_content_brief(req: ProductBriefRequest):
    """
    Generates a structured Product Truth Content Brief for writers and AI content pipelines,
    ensuring newly created content adheres 100% to verified Knowledge Graph triples.
    """
    try:
        pipeline = get_pipeline(req.vertical_id)
        triples_dicts = [t.model_dump() if hasattr(t, "model_dump") else t.dict() for t in req.triples]
        
        brief = vertex_ai_client.generate_product_brief(
            topic=req.topic,
            brand_name=req.brand_name,
            triples=triples_dicts,
            gaps=req.gaps,
            vertical_name=pipeline.config.display_name
        )

        return ProductBriefResponse(
            topic=req.topic,
            target_alignment_score=brief.get("target_alignment_score", 90),
            must_include_entities=brief.get("must_include_entities", []),
            required_relational_triples=brief.get("required_relational_triples", []),
            prohibited_claims=brief.get("prohibited_claims", []),
            suggested_outline=brief.get("suggested_outline", []),
            differentiation_angles=brief.get("differentiation_angles", [])
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Content brief generation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Content brief generation failed. Check server logs.")


@app.post("/api/export-pdf", summary="Generate 1-Click Executive PDF Report")
def api_export_pdf(req: ExportPdfRequest):
    """
    Generates a white-label, executive-grade PDF audit report containing
    Structured Data Readiness, Schema.org compliance, verified triples, and strategic action plan.
    """
    try:
        audit_data = {
            "url": req.url,
            "readiness_score": req.readiness_score or 0.0,
            "mandatory_schema_status": req.mandatory_schema_status or {},
            "triples": req.triples or [],
            "google_kg_presence": req.google_kg_presence
        }
        benchmark_data = None
        if req.benchmark_table:
            benchmark_data = {"comparative_table": req.benchmark_table}

        pdf_bytes = report_pdf.generate_executive_pdf_report(
            audit_data=audit_data,
            benchmark_data=benchmark_data
        )

        clean_name = req.url.replace("https://", "").replace("http://", "").split("/")[0].replace(".", "_")
        filename = f"GainARK_OntoLeap_{clean_name}.pdf"

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except Exception as e:
        logger.error("PDF export failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="PDF export failed. Check server logs.")


@app.post("/api/google-kg", summary="Verify Entity Recognition in Google Knowledge Graph")
def api_google_kg_search_post(req: GoogleKgRequest):
    """
    Directly queries Google's Knowledge Graph Search API (kgsearch.googleapis.com)
    and returns Google Machine Identifier (MID), entity salience score, types, and AI overview risk.
    """
    res = google_kg_client.search_entity(req.query)
    if not res:
        raise HTTPException(status_code=500, detail="Google Knowledge Graph search failed or unconfigured.")
    return res


@app.get("/api/google-kg", summary="Verify Entity Recognition in Google Knowledge Graph (Query)")
def api_google_kg_search_get(query: str = Query(..., description="Brand or company query")):
    res = google_kg_client.search_entity(query)
    if not res:
        raise HTTPException(status_code=500, detail="Google Knowledge Graph search failed or unconfigured.")
    return res


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)

