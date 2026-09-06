"""
GainARK OntoLeap — Product Truth, Governance, SHACL Validation, and Tri-Ontology Routes
"""

import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

import product_truth
import competitive_alignment
from pipeline import validate_url_for_fetch
from models import (
    ProductTruthRequest,
    ProductTruthMatrixResponse,
    TriOntologyAlignmentRequest,
    TriOntologyAlignmentResponse,
    CompetitorOntologyRequest,
    CompetitorOntologyResponse
)
from routers.deps import get_pipeline, get_default_pipeline

logger = logging.getLogger("ontoleap.api.governance")

router = APIRouter(tags=["Product Truth & Governance"])


class ShaclValidationRequest(BaseModel):
    rdf_turtle: str = Field(..., max_length=1_000_000, description="RDF Turtle serialization to validate against SHACL governance shapes")
    shapes_turtle: Optional[str] = Field(default=None, max_length=500_000, description="Optional custom SHACL shapes Turtle. Defaults to built-in OntoLeap governance shapes.")


class ShaclValidationResponse(BaseModel):
    conforms: bool
    violations_count: int
    violations: List[Dict[str, Any]]
    report_text: str
    evaluated_triples_count: int
    status: str = "success"


@router.get("/api/competitor-changes", summary="What a brand has started or stopped claiming")
def competitor_changes(brand: Optional[str] = None):
    """
    Reports how a brand's claims have moved between audits, from the append-only
    history the platform already writes on every Product Truth run.
    """
    try:
        from truth_ledger.history import brand_timeline, tracked_brands
        if not brand:
            return {"brands": tracked_brands()}
        return brand_timeline(brand)
    except Exception as exc:
        logger.error("Competitor change tracking failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Could not read audit history.")


@router.post("/api/export-product-truth-prov", summary="Export Product Truth Matrix as W3C PROV-O & SKOS RDF Turtle")
def api_export_product_truth_prov(matrix: ProductTruthMatrixResponse):
    """
    Exports a Product Truth Matrix as an auditable W3C PROV-O and SKOS RDF Turtle graph (.ttl).
    Asserts formal prov:wasDerivedFrom links, primary sources, and SKOS concept hierarchies.
    """
    try:
        from product_truth import export_product_truth_to_prov_ttl
        pipeline = get_default_pipeline()
        ttl_content = export_product_truth_to_prov_ttl(matrix, getattr(pipeline, "config", None))
        return PlainTextResponse(content=ttl_content, media_type="text/turtle")
    except Exception as e:
        logger.error("Failed to export Product Truth PROV Turtle: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to export Product Truth PROV Turtle.")


@router.post("/api/validate-kg-shacl", response_model=ShaclValidationResponse, summary="Validate RDF Knowledge Graph with W3C SHACL")
def api_validate_kg_shacl(req: ShaclValidationRequest):
    """
    Validates an RDF Turtle knowledge graph against W3C SHACL governance shapes.
    Enforces that:
    - Every claim entity has an evidence quote (prov:wasQuotedFrom).
    - Every claim has a primary source (prov:hadPrimarySource) or derivation (prov:wasDerivedFrom).
    - Claims marked as VERIFIED_TRUTH derive from at least 2 sources (marketing + technical).
    - SKOS Concepts have a prefLabel and are in a ConceptScheme.
    """
    if not req.rdf_turtle or not req.rdf_turtle.strip():
        raise HTTPException(status_code=400, detail="Must provide 'rdf_turtle' string to validate.")
    try:
        from shacl_validator import validate_rdf_graph_shacl
        rep = validate_rdf_graph_shacl(req.rdf_turtle, shapes_graph_or_ttl=req.shapes_turtle)
        return ShaclValidationResponse(
            conforms=rep.conforms,
            violations_count=rep.violations_count,
            violations=[v.model_dump() for v in rep.violations],
            report_text=rep.report_text,
            evaluated_triples_count=rep.evaluated_triples_count,
            status="success"
        )
    except Exception as e:
        logger.error("SHACL validation failure: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"SHACL validation error: {str(e)}")


@router.post(
    "/api/product-truth",
    response_model=ProductTruthMatrixResponse,
    summary="Company Product Truth Matrix: Marketing Claims vs. Technical Reality"
)
def api_product_truth_audit(req: ProductTruthRequest):
    """
    Cross-examines a brand's marketing website against its technical documentation or OpenAPI spec.
    1. Extracts marketing claims from marketing_url
    2. Parses technical capabilities from tech_docs_url, openapi_spec, or documentation text
    3. Computes the Product Truth Matrix
    4. Calculates the Marketing Grounding Index (MGI) and emits actionable governance alerts.
    """
    validate_url_for_fetch(req.marketing_url)
    if req.tech_docs_url:
        validate_url_for_fetch(req.tech_docs_url)

    pipeline = get_pipeline(req.vertical_id)

    try:
        matrix_result = product_truth.execute_product_truth_audit(req, pipeline)
        return matrix_result
    except Exception as e:
        logger.exception("Product Truth audit failed for %s: %s", req.marketing_url, e)
        raise HTTPException(status_code=500, detail="Product Truth audit failed. Check server logs.")


@router.post(
    "/api/tri-ontology-align",
    response_model=TriOntologyAlignmentResponse,
    summary="Tri-Ontology Competitive Alignment: Company vs. Competitors vs. Industry Standards"
)
def api_tri_ontology_align(req: TriOntologyAlignmentRequest):
    """
    Performs full Tri-Ontology comparative alignment across:
    1. Industry Ontology (Domain standards and expected capabilities)
    2. Company Product Truth (Verified capabilities vs. marketing claims)
    3. Competitor Product Truth (Competitor verified reality vs. competitor marketing claims)
    """
    validate_url_for_fetch(req.company.marketing_url)
    for comp in req.competitors:
        validate_url_for_fetch(comp.marketing_url)

    pipeline = get_pipeline(req.vertical_id)

    try:
        alignment_report = competitive_alignment.execute_tri_ontology_alignment(req, pipeline)
        return alignment_report
    except Exception as e:
        logger.exception("Tri-Ontology alignment failed: %s", e)
        raise HTTPException(status_code=500, detail="Tri-Ontology alignment failed. Check server logs.")


@router.post(
    "/api/competitor-ontology",
    response_model=CompetitorOntologyResponse,
    summary="Crawl and extract the public ontology and capabilities of a competitor"
)
def api_competitor_ontology(req: CompetitorOntologyRequest):
    """
    Crawls a competitor's domain and high-signal subpages (/pricing, /features, /integrations)
    using the SmartScraper (Chrome TLS impersonation).
    Extracts relational triples, Schema.org nodes, and entity grounding.
    """
    validate_url_for_fetch(req.url)
    pipeline = get_pipeline()
    try:
        return competitive_alignment.extract_competitor_ontology(req, pipeline)
    except Exception as e:
        logger.exception("Competitor ontology extraction failed: %s", e)
        raise HTTPException(status_code=500, detail="Competitor ontology extraction failed. Check server logs.")
