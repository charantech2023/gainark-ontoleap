"""
GainARK OntoLeap — System, Health, Info, Cache, and Discovery Endpoints
"""

import os
import json
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse

import google_kg_client
import industry_profiler
from pipeline import validate_url_for_fetch
from models import (
    GoogleKgRequest,
    IndustryDiscoveryRequest,
    IndustryDiscoveryResponse
)
from routers.deps import SUPPORTED_VERTICALS, pipeline_cache, VERTICALS_DIR
from security import is_valid_vertical_id

logger = logging.getLogger("ontoleap.api.system")

router = APIRouter(tags=["System & Info"])


@router.get("/dashboard", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def get_dashboard():
    """Renders the OntoLeap interactive web dashboard."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    html_path = os.path.join(base_dir, "templates", "dashboard.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard template not found</h1>", status_code=404)


@router.get("/api/info")
def api_info():
    """Returns platform capability manifest and feature flags."""
    return {
        "status": "online",
        "name": "GainARK OntoLeap Platform",
        "service": "GainARK OntoLeap Platform",
        "version": "2.2.0",
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
            "pyvis_graph_html_export": True,
            "google_gemini_2_5_flash_hybrid": True,
            "google_knowledge_graph_search_api": True,
            "multi_vertical_expansion": True,
            "product_truth_draft_alignment_pas": True,
            "executive_pdf_export": True,
            "autonomous_industry_discovery": True,
            "product_truth_matrix": True,
            "tri_ontology_alignment": True,
            "battlecards_pdf_export": True
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
            "check-draft": "POST /api/check-draft",
            "content-brief": "POST /api/content-brief",
            "export-pdf": "POST /api/export-pdf",
            "export-battlecards-pdf": "POST /api/export-battlecards-pdf",
            "verticals": "GET /api/verticals",
            "google-kg": "POST /api/google-kg",
            "discover-industry": "POST /api/discover-industry",
            "product-truth": "POST /api/product-truth",
            "tri-ontology-align": "POST /api/tri-ontology-align",
            "health": "GET /api/health"
        }
    }


@router.get("/api/health")
def health():
    """Health check endpoint returning active loaded models."""
    return {
        "status": "healthy",
        "cached_pipelines": list(pipeline_cache.keys())
    }


@router.get("/api/verticals", summary="List Available Industry Verticals")
def api_list_verticals():
    """
    Returns all registered domain ontology profiles with their display metadata.
    Automatically discovers and includes any newly discovered profiles from verticals/.
    """
    if os.path.isdir(VERTICALS_DIR):
        for fname in os.listdir(VERTICALS_DIR):
            if fname.endswith(".json") and is_valid_vertical_id(fname[:-5]):
                SUPPORTED_VERTICALS.add(fname[:-5])

    profiles = []
    for vid in sorted(SUPPORTED_VERTICALS):
        if not is_valid_vertical_id(vid):
            continue
        cfg_file = os.path.join(VERTICALS_DIR, f"{vid}.json")
        if os.path.isfile(cfg_file):
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


@router.post("/api/google-kg", summary="Verify Entity Recognition in Google Knowledge Graph")
def api_google_kg_search_post(req: GoogleKgRequest):
    """
    Directly queries Google's Knowledge Graph Search API (kgsearch.googleapis.com)
    and returns Google Machine Identifier (MID), entity salience score, types, and AI overview risk.
    """
    res = google_kg_client.search_entity(req.query)
    if not res:
        raise HTTPException(status_code=500, detail="Google Knowledge Graph search failed or unconfigured.")
    return res


@router.get("/api/google-kg", summary="Verify Entity Recognition in Google Knowledge Graph (Query)")
def api_google_kg_search_get(query: str = Query(..., description="Brand or company query")):
    res = google_kg_client.search_entity(query)
    if not res:
        raise HTTPException(status_code=500, detail="Google Knowledge Graph search failed or unconfigured.")
    return res


@router.post(
    "/api/discover-industry",
    response_model=IndustryDiscoveryResponse,
    summary="Zero-Shot Autonomous Industry & Vertical Discovery",
    tags=["Industry Ontology"]
)
async def api_discover_industry(req: IndustryDiscoveryRequest):
    """
    Autonomously bootstraps an Industry Ontology profile for any B2B SaaS domain.
    1. Crawls homepage title, headings, and metadata
    2. Synthesizes vertical taxonomy, seed concepts, compliance standards, integrations, and competitors
    3. Grounds concepts against canonical Wikidata Q-IDs
    4. Automatically saves and registers the vertical profile into the live pipeline
    """
    try:
        validate_url_for_fetch(req.url)
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))

    try:
        res = await industry_profiler.discover_industry_profile_async(
            url=req.url,
            brand_hint=req.brand_hint,
            save_config=True
        )
        # Register new vertical in live memory. The profiler slugifies the ID, but this
        # set is used to build filesystem paths, so re-check rather than trust.
        if is_valid_vertical_id(res.vertical_id):
            SUPPORTED_VERTICALS.add(res.vertical_id)
        return res
    except Exception as e:
        logger.exception("Failed to discover industry for %s: %s", req.url, e)
        raise HTTPException(status_code=500, detail="Industry discovery failed. Check server logs.")


@router.get(
    "/api/cache/stats",
    summary="Get content and response cache telemetry stats",
    tags=["System & Performance"]
)
def api_cache_stats():
    """
    Returns telemetry for the dual-tier (memory + disk) cache,
    including hit counts, miss counts, hit ratio %, and disk usage.
    """
    from cache import get_content_cache
    return get_content_cache().stats()


@router.post(
    "/api/cache/clear",
    summary="Flush memory and disk caches",
    tags=["System & Performance"]
)
def api_cache_clear():
    """
    Flushes all in-memory and disk cached pages and specs.
    Returns the count of purged disk cache entries.
    """
    from cache import get_content_cache
    purged_count = get_content_cache().clear()
    return {
        "status": "cleared",
        "purged_disk_entries": purged_count,
        "message": f"Successfully cleared cache ({purged_count} entries purged)."
    }
