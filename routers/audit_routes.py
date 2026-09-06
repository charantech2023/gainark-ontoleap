"""
GainARK OntoLeap — Site Audit, Sitemap Batch Crawl, Competitor Benchmark, and Executive PDF Exports
"""

import logging
import csv
import io
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

import report_pdf
from pipeline import crawl_and_build_unified_graph, validate_url_for_fetch
from models import (
    ReadinessBreakdown,
    EntityMatch,
    SeedConceptMatch,
    SchemaOrgData,
    CompetitiveGapAnalysis,
    SemanticTriple,
    UnifiedSiteGraph,
    ExportPdfRequest
)
from routers.deps import get_pipeline

logger = logging.getLogger("ontoleap.api.audit")

router = APIRouter(tags=["Audit & Benchmarking"])


class BatchCrawlRequest(BaseModel):
    sitemap_url: str = Field(..., description="Target XML sitemap or sitemap index URL", example="https://www.ordwaylabs.com/sitemap.xml")
    max_pages: int = Field(default=10, description="Maximum number of pages to crawl and synthesize")


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


class BenchmarkCsvExportRequest(BaseModel):
    comparative_table: List[Dict[str, Any]]


@router.post("/api/batch-crawl", response_model=UnifiedSiteGraph)
async def api_batch_crawl(req: BatchCrawlRequest):
    """
    Crawls pages discovered in the target XML sitemap and synthesizes a site-wide knowledge graph
    with deduplicated relational triples and Schema.org @graph JSON-LD.
    """
    try:
        validate_url_for_fetch(req.sitemap_url)
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))

    try:
        return await crawl_and_build_unified_graph(req.sitemap_url, max_pages=req.max_pages)
    except Exception as e:
        logger.error("Batch crawl failed for %s: %s", req.sitemap_url, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch crawl failed for '{req.sitemap_url}'. Check server logs for details.")


@router.post("/api/audit", response_model=AuditResponse)
def audit_endpoint(request: AuditRequest):
    """
    Runs the ontology pipeline against a target URL:
    - Verifies Schema.org mandatory types
    - Runs GLiNER zero-shot entity recognition
    - Matches core seed concepts
    - Calculates the single-page structured data readiness score
    - Dynamically generates the Schema.org remediation patch
    - Optionally deep-crawls up to 3 high-value sub-pages (pricing, features, integrations)
    """
    try:
        validate_url_for_fetch(request.url)
    except ValueError as val_err:
        raise HTTPException(status_code=400, detail=str(val_err))

    try:
        pipeline = get_pipeline(request.vertical_id)
        result = pipeline.process(url=request.url, deep_crawl=request.deep_crawl)

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
        logger.error("Failed to audit URL '%s': %s", request.url, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to audit URL '{request.url}'. Check server logs.")


@router.post("/api/benchmark", response_model=BenchmarkResponse)
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

                top_missing = ", ".join(missing_concepts[:3])
                if len(missing_concepts) > 3:
                    top_missing += f" (+{len(missing_concepts) - 3} more)"
                elif not missing_concepts:
                    top_missing = "None (100% Coverage)"

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
        logger.error("Benchmark execution failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Benchmark execution failed. Check server logs for details.")


@router.post("/api/benchmark/export-csv", summary="Export Competitor Benchmark Matrix as CSV")
def api_export_benchmark_csv(req: BenchmarkCsvExportRequest):
    """
    Converts competitive benchmark data table into standardized CSV format for pandas/spreadsheet ingestion.
    """
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


@router.post("/api/export-pdf", summary="Generate 1-Click Executive PDF Report")
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


@router.post("/api/export-battlecards-pdf", summary="Generate 1-Click Executive Battlecards PDF")
def api_export_battlecards_pdf(req: Dict[str, Any]):
    """
    Generates a high-impact, executive sales battlecards deck in PDF from Tri-Ontology alignment data.
    """
    try:
        pdf_bytes = report_pdf.generate_battlecards_pdf_report(req)
        comp = req.get("company_name", "Brand").replace(" ", "_").replace("/", "_")
        filename = f"GainARK_{comp}_Competitive_Battlecards.pdf"

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except Exception as e:
        logger.error("Battlecards PDF export failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Battlecards PDF export failed. Check server logs.")


@router.get("/api/download-pitch-pdf", summary="Download 1-Page Executive Pitch One-Pager PDF")
def api_download_pitch_pdf():
    """
    Generates and returns the official 1-Page Executive Pitch PDF for OntoLeap.
    Built in-memory for zero disk I/O and stateless concurrency safety.
    """
    try:
        from generate_pitch_pdf import build_one_pager_pdf_bytes
        pdf_bytes = build_one_pager_pdf_bytes()

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": 'inline; filename="OntoLeap_Executive_Pitch_OnePager.pdf"'
            }
        )
    except Exception as e:
        logger.error("Pitch PDF generation failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Pitch PDF generation failed. Check server logs.")
