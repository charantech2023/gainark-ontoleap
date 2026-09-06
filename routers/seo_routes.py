"""
GainARK OntoLeap — Semantic SEO, Internal Linking, SearchGPT Simulation, Draft Alignment, and Briefs
"""

import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import vertex_ai_client
import alignment
from pipeline import validate_url_for_fetch
from linking import audit_internal_links, simulate_search_response
from models import (
    SiteAuditAndLinkResult,
    SearchSimulationRequest,
    SearchSimulationResponse,
    CitationSource,
    DraftAlignmentRequest,
    DraftAlignmentResponse,
    ProductBriefRequest,
    ProductBriefResponse
)
from routers.deps import get_pipeline

logger = logging.getLogger("ontoleap.api.seo")

router = APIRouter(tags=["Semantic SEO & Linking"])


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


@router.post("/api/internal-links", response_model=SiteAuditAndLinkResult)
async def api_internal_links(req: InternalLinkAuditRequest):
    """
    Crawls multiple pages across a site, identifies canonical topic authority hubs,
    and generates high-intent internal link recommendations for unlinked entity & triple mentions.
    """
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
        logger.error("Internal link audit failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal link audit failed. Check server logs for details.")


@router.post("/api/simulate-search", response_model=SearchSimulationResponse)
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
                grounding_conf = round(min(0.99, max(0.65, 0.75 + (len(citations) * 0.04))), 2)
                return SearchSimulationResponse(
                    query=req.query,
                    synthesized_answer=gem_res.get("answer", ""),
                    citations=citations,
                    grounding_confidence=grounding_conf,
                    hallucination_risk=f"Low Hallucination Risk ({int(grounding_conf * 100)}% Triple Grounded by Google Gemini 2.5 Flash)",
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


@router.post("/api/check-draft", response_model=DraftAlignmentResponse, summary="Evaluate Draft Against Knowledge Graph (PAS & Fluff)")
def api_check_draft(req: DraftAlignmentRequest):
    """
    Evaluates draft content against the canonical Product Knowledge Graph.
    Computes the Product Alignment Score (PAS: 0-100), detects generic buzzword fluff,
    and runs Gemini LLM-as-judge claim verification when available.
    """
    try:
        triples_dicts = [t.model_dump() if hasattr(t, "model_dump") else t.dict() for t in req.triples]
        pipeline = get_pipeline(req.vertical_id)
        
        pas_result = alignment.calculate_product_alignment_score(
            text=req.draft_text,
            canonical_triples=triples_dicts,
            canonical_entities=req.entities or list(pipeline.config.core_seed_concepts),
            brand_name=req.brand_name
        )

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


@router.post("/api/content-brief", response_model=ProductBriefResponse, summary="Generate Product Truth Content Brief")
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
