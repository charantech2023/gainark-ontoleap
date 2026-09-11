"""
GainARK OntoLeap — FastAPI Enterprise REST API & Semantic Service Gateway

Modular gateway mounting decoupled domain routers:
- System & Info (health, cache, discovery, verticals, dashboard)
- Knowledge Graph Operations (Page KG, Site KG, SPARQL 1.1, N-Triples, OWL 2 DL, Link Prediction)
- Industry & Regulatory Ontology (vertical taxonomy, compliance frameworks, industry alignment)

Cross-cutting controls applied here, outermost first:
  CORS -> security headers -> request size ceiling -> rate limit -> API key auth
"""

import os
import time
import logging
import threading
from collections import defaultdict
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from security import API_KEY_HEADER, api_key_matches, configured_api_key

# get_pipeline is re-exported here: test_industry_profiler imports it from api.
from routers.deps import get_pipeline
from routers.system_routes import router as system_router
from routers.kg_routes import router as kg_router
from routers.ontology_routes import router as ontology_router


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

# Endpoints that must stay reachable without credentials: liveness probes, the capability
# manifest, and the dashboard shell itself.
PUBLIC_PATHS = frozenset({
    "/", "/dashboard", "/api/health", "/api/info", "/api/v1/mcp-info",
    "/docs", "/redoc", "/openapi.json",
})


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

# "*" with allow_credentials is rejected by browsers and, if a middleware ever echoed
# the request Origin instead, would let any site read authenticated responses. Refuse
# the combination explicitly rather than shipping a config that silently does nothing.
allow_credentials = True
if "*" in allowed_origins:
    logger.warning(
        "ALLOWED_ORIGINS contains '*'; disabling credentialed CORS. "
        "Set an explicit origin list to allow credentials."
    )
    allow_credentials = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=allow_credentials,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", API_KEY_HEADER],
)


# ---------------------------------------------------------------------------
# API Key Authentication
# ---------------------------------------------------------------------------
class ApiKeyAuthMiddleware(BaseHTTPMiddleware):
    """
    Enforces a shared API key on every non-public endpoint when ONTOLEAP_API_KEY is set.

    Auth is opt-in so an existing deployment keeps working after upgrade, but the
    absence of a key is logged loudly at startup: without it, anyone on the internet can
    make this service crawl arbitrary URLs and spend the operator's Gemini and Google
    Knowledge Graph quota.
    """

    async def dispatch(self, request: Request, call_next):
        expected = configured_api_key()
        path = request.url.path

        if not expected or request.method == "OPTIONS" or path in PUBLIC_PATHS:
            return await call_next(request)

        presented = request.headers.get(API_KEY_HEADER)
        if not presented:
            auth_header = request.headers.get("authorization", "")
            if auth_header.lower().startswith("bearer "):
                presented = auth_header[7:].strip()

        if not api_key_matches(presented):
            logger.warning("Rejected unauthenticated request to %s", path)
            return JSONResponse(
                status_code=401,
                content={"detail": f"Missing or invalid API key. Supply it in the '{API_KEY_HEADER}' header."},
            )

        return await call_next(request)


# ---------------------------------------------------------------------------
# In-Memory Sliding Window Rate Limiting Middleware with Auto-Pruning
# ---------------------------------------------------------------------------
class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    In-memory rolling-window rate limiter per client IP.
    Protects heavy model inference, crawling, and PDF generation from DoS.
    Includes active key pruning to eliminate memory leaks over long lifecycles.

    Note this is per-process state. Behind more than one replica the effective limit is
    the configured value multiplied by the replica count; a shared store would be needed
    for an exact global limit.
    """

    # X-Forwarded-For is client-controlled. Honouring it blindly lets an attacker rotate
    # the header and bypass the limiter entirely. Only the hop appended by our own proxy
    # can be trusted, so we index from the right by however many proxies sit in front.
    # Cloud Run appends exactly one, hence the default of 1.
    TRUSTED_PROXY_COUNT = int(os.environ.get("TRUSTED_PROXY_COUNT", "1") or 0)

    def __init__(self, app):
        super().__init__(app)
        self.history = defaultdict(list)
        self.lock = threading.Lock()
        self.request_count = 0
        self.limits = {
            "/api/kg/page": 15,              # Max 15 page KG extractions/min
            "/api/kg/site": 5,               # Max 5 site-wide crawl & synthesis/min
            "/api/kg/site/jobs": 10,         # Job creation fetches the site to route it
            #   Advancing a job is not listed: its path carries the job id, so no exact
            #   match is possible, and the default applies. A slice takes about a hundred
            #   seconds, so a caller cannot poll fast enough for the limit to matter.
            "/api/kg/align": 10,             # Max 10 industry alignment runs/min
            "/api/sparql": 30,               # Max 30 graph queries/min
            "/api/google-kg": 15,            # Fans out to Google KG API
            "/api/cache/clear": 2,           # Flushes shared state
        }
        self.default_limit = 60              # Default 60 requests/min

    def _client_ip(self, request: Request) -> str:
        """Resolve the caller's address, trusting only proxies we sit behind."""
        peer = request.client.host if request.client else "127.0.0.1"
        if self.TRUSTED_PROXY_COUNT <= 0:
            return peer

        forwarded = request.headers.get("x-forwarded-for")
        if not forwarded:
            return peer

        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        if not hops:
            return peer
        # The rightmost entries were appended by infrastructure we control. Anything
        # further left was supplied by the client and is not evidence of anything.
        index = len(hops) - self.TRUSTED_PROXY_COUNT
        return hops[max(0, min(index, len(hops) - 1))]

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/") and path not in ("/api/health", "/api/info"):
            client_ip = self._client_ip(request)

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


# ---------------------------------------------------------------------------
# Request Body Size Ceiling
# ---------------------------------------------------------------------------
class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """
    Rejects request bodies above MAX_REQUEST_BYTES.

    Several endpoints accept free-form RDF Turtle, OpenAPI specs and triple lists.
    Without a ceiling a single request can be made large enough to exhaust process
    memory before any handler or validator sees it.
    """

    MAX_REQUEST_BYTES = int(os.environ.get("MAX_REQUEST_BYTES", str(4 * 1024 * 1024)) or 4 * 1024 * 1024)

    async def dispatch(self, request: Request, call_next):
        declared = request.headers.get("content-length")
        if declared:
            try:
                if int(declared) > self.MAX_REQUEST_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"Request body exceeds the {self.MAX_REQUEST_BYTES} byte limit."},
                    )
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": "Malformed Content-Length header."})
        elif request.headers.get("transfer-encoding", "").lower() == "chunked":
            # No declared length: buffer under the cap and hand the body onward.
            body = b""
            async for chunk in request.stream():
                body += chunk
                if len(body) > self.MAX_REQUEST_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"Request body exceeds the {self.MAX_REQUEST_BYTES} byte limit."},
                    )

            async def replay():
                return {"type": "http.request", "body": body, "more_body": False}

            request._receive = replay  # type: ignore[attr-defined]

        return await call_next(request)


# ---------------------------------------------------------------------------
# Security Response Headers
# ---------------------------------------------------------------------------
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Applies baseline hardening headers to every response."""

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response


# Starlette runs the most recently added middleware first, so these are registered in
# reverse of the intended execution order:
#   CORS -> security headers -> size ceiling -> rate limit -> auth -> route
app.add_middleware(ApiKeyAuthMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestSizeLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)


@app.on_event("startup")
async def _warn_if_unauthenticated() -> None:
    """Make an open deployment impossible to miss in the logs."""
    if not configured_api_key():
        logger.warning(
            "ONTOLEAP_API_KEY is not set: every endpoint is reachable without "
            "credentials. Anyone who can reach this service can make it crawl arbitrary "
            "URLs and consume metered Gemini / Google Knowledge Graph quota. Set "
            "ONTOLEAP_API_KEY before exposing this service publicly."
        )


@app.on_event("startup")
async def _restore_knowledge_graph() -> None:
    """Index the durable archive into this instance's local store.

    An instance boots with an empty SQLite file - a fresh container, or a new replica
    scaled up alongside others - while the archive holds every run any instance has
    written. Without this the service would answer "no history" with total confidence,
    which is the failure worth avoiding: a store that has silently forgotten looks
    exactly like one that is working.

    Never fatal. Serving audits without history is degraded; refusing to boot is worse.
    """
    try:
        import graph_archive
        import graph_store

        graph_archive.warn_if_ephemeral()
        archive = graph_archive.open_archive()
        if archive is None:
            logger.info("Knowledge graph archive not configured; history is local only.")
            return
        result = graph_store.sync_from_archive(archive)
        logger.info(
            "Knowledge graph restored from %s: %d graph(s) pulled, %d already indexed.",
            archive.describe(), result["pulled"], result["skipped"])
    except Exception as err:
        logger.error("Knowledge graph could not be restored from the archive: %s", err)


# ---------------------------------------------------------------------------
# Mount Decoupled Routers
# ---------------------------------------------------------------------------
app.include_router(system_router)
app.include_router(ontology_router)
app.include_router(kg_router)


@app.get("/api/v1/mcp-info", tags=["MCP & Agent Integration"])
def get_mcp_info():
    """Returns the Model Context Protocol (MCP) server specifications, tools, and client configs."""
    return {
        "mcp_server": "OntoLeap Knowledge Graph & Domain Ontology Engine",
        "version": "2.0.0",
        "protocol": "Model Context Protocol (MCP) 2.x",
        "transports": ["stdio", "sse"],
        "tools": [
            {
                "name": "ontoleap_build_page_kg",
                "description": "Extracts entities, Wikidata QIDs, semantic triples, and JSON-LD/Turtle from a page."
            },
            {
                "name": "ontoleap_build_site_kg",
                "description": "Crawls a domain, canonicalizes entities, and induces domain ontology schema."
            },
            {
                "name": "ontoleap_align_industry_ontology",
                "description": "Ground page or site KG against industry reference taxonomy."
            },
            {
                "name": "ontoleap_list_industry_ontologies",
                "description": "Enumerates all supported industry reference models."
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
    # reload=True is a development convenience and must not be the deployed entrypoint;
    # the Dockerfile runs uvicorn directly without it.
    uvicorn.run(
        "api:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        reload=os.environ.get("DEV_RELOAD", "").lower() in ("1", "true", "yes"),
    )
