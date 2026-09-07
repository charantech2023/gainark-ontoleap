"""
GainARK OntoLeap — FastAPI Enterprise REST API & Semantic Service Gateway

Modular gateway mounting decoupled domain routers:
- System & Info (health, cache, discovery, verticals, dashboard)
- Product Truth & Governance (audits, alignment, SHACL, competitor tracking)
- Knowledge Graph Operations (SPARQL 1.1, N-Triples, OWL 2 DL, link prediction)
- Semantic SEO & Linking (internal links, SearchGPT simulation, draft alignment)
- Site Audit & Benchmarking (sitemap crawl, audits, benchmark matrix, PDF reports)
"""

import os
import time
import logging
import threading
from collections import defaultdict
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from routers.deps import (
    get_pipeline,
    get_default_pipeline,
    pipeline_cache,
    SUPPORTED_VERTICALS
)
from routers.system_routes import (
    router as system_router,
    api_info,
    health,
    api_list_verticals,
    api_cache_stats,
    api_cache_clear,
    api_google_kg_search_post,
    api_google_kg_search_get,
    api_discover_industry
)
from routers.governance_routes import (
    router as governance_router,
    ShaclValidationRequest,
    ShaclValidationResponse,
    competitor_changes,
    api_export_product_truth_prov,
    api_validate_kg_shacl,
    api_product_truth_audit,
    api_tri_ontology_align,
    api_competitor_ontology
)
from routers.kg_routes import (
    router as kg_router,
    NTriplesExportRequest,
    OwlExportRequest,
    SemanticClustersRequest,
    api_execute_sparql,
    api_export_ntriples,
    api_export_owl,
    api_predict_links,
    api_semantic_clusters,
    api_export_graph_html
)
from routers.seo_routes import (
    router as seo_router,
    InternalLinkAuditRequest,
    api_internal_links,
    api_simulate_search,
    api_check_draft,
    api_content_brief
)
from routers.audit_routes import (
    router as audit_router,
    BatchCrawlRequest,
    AuditRequest,
    AuditResponse,
    BenchmarkRequest,
    BenchmarkItem,
    BenchmarkResponse,
    BenchmarkCsvExportRequest,
    api_batch_crawl,
    audit_endpoint,
    benchmark_endpoint,
    api_export_benchmark_csv,
    api_export_pdf,
    api_export_battlecards_pdf,
    api_download_pitch_pdf
)

# Re-export models for 100% backward compatibility
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
    GoogleKgRequest,
    IndustryDiscoveryRequest,
    IndustryDiscoveryResponse,
    ProductTruthRequest,
    ProductTruthMatrixResponse,
    TriOntologyAlignmentRequest,
    TriOntologyAlignmentResponse,
    CompetitorOntologyRequest,
    CompetitorOntologyResponse
)

# ---------------------------------------------------------------------------
# Structured Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("ontoleap.api")

app = FastAPI(
    title="GainARK OntoLeap — Autonomous Ontology Intelligence & Semantic Graph Engine",
    description="Enterprise ontology intelligence, zero-shot entity grounding, relational triples extraction, and semantic internal linking for B2B SaaS.",
    version="2.2.0"
)

# ---------------------------------------------------------------------------
# CORS Configuration with Environment Variable & Safe Production Defaults
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
# In-Memory Sliding Window Rate Limiting Middleware with Auto-Pruning
# ---------------------------------------------------------------------------
class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    In-memory rolling-window rate limiter per client IP.
    Protects heavy model inference, crawling, and PDF generation from DoS.
    Includes active key pruning to eliminate memory leaks over long lifecycles.
    """
    def __init__(self, app):
        super().__init__(app)
        self.history = defaultdict(list)
        self.lock = threading.Lock()
        self.request_count = 0
        self.limits = {
            "/api/audit": 10,                 # Max 10 audits/min per IP
            "/api/benchmark": 3,             # Max 3 multi-domain benchmarks/min
            "/api/batch-crawl": 3,           # Max 3 sitemap crawls/min
            "/api/internal-links": 5,        # Max 5 internal linking crawls/min
            "/api/discover-industry": 5,     # Max 5 zero-shot discoveries/min
            "/api/product-truth": 5,         # Max 5 truth audits/min
            "/api/tri-ontology-align": 5,    # Max 5 alignment runs/min
            "/api/download-pitch-pdf": 10,   # Max 10 pitch downloads/min
            "/api/export-pdf": 10,           # Max 10 audit PDF exports/min
            "/api/export-battlecards-pdf": 10# Max 10 battlecard PDF exports/min
        }
        self.default_limit = 60              # Default 60 requests/min

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path not in ("/api/health", "/api/info"):
            client_ip = request.client.host if request.client else "127.0.0.1"
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                client_ip = forwarded.split(",")[0].strip()

            max_requests = self.limits.get(path, self.default_limit)
            now = time.time()
            window_start = now - 60.0

            with self.lock:
                self.request_count += 1
                if self.request_count % 100 == 0 or len(self.history) > 2000:
                    stale_keys = [k for k, timestamps in self.history.items() if not timestamps or timestamps[-1] <= window_start]
                    for k in stale_keys:
                        del self.history[k]

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

# ---------------------------------------------------------------------------
# Mount Decoupled Routers
# ---------------------------------------------------------------------------
app.include_router(system_router)
app.include_router(governance_router)
app.include_router(kg_router)
app.include_router(seo_router)
app.include_router(audit_router)


@app.get("/api/v1/mcp-info", tags=["MCP & Agent Integration"])
def get_mcp_info():
    """Returns the Model Context Protocol (MCP) server specifications, tools, and client configs."""
    return {
        "mcp_server": "OntoLeap Knowledge Graph & Graph-Diff Engine",
        "version": "1.0.0",
        "protocol": "Model Context Protocol (MCP) 2.x",
        "transports": ["stdio", "sse"],
        "tools": [
            {
                "name": "ontoleap_extract_facts",
                "description": "Extracts verified semantic triples <S, P, O>, entities, and schemas from a URL or raw text."
            },
            {
                "name": "ontoleap_cross_examine_diff",
                "description": "Calculates the discrete set-theoretic graph diff (A ∩ B, A \\ B, B \\ A) between two sources."
            },
            {
                "name": "ontoleap_probe_ai_sov",
                "description": "Probes live AI answer engines with buyer queries to measure Share of Voice (SOV %) and audit hallucinations."
            },
            {
                "name": "ontoleap_map_site_topology",
                "description": "Crawls sitemaps, calculates PageRank authority hubs, and generates in-context internal link opportunities."
            }
        ],
        "claude_desktop_config": {
            "mcpServers": {
                "ontoleap": {
                    "command": "python",
                    "args": ["<path_to_ontology>/mcp_server.py"]
                }
            }
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api:app", host="0.0.0.0", port=8000, reload=True)
