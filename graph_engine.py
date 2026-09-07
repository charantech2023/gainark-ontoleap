"""
GainARK OntoLeap — Core Knowledge Graph & Graph-Diff Engine

This module provides the deterministic computer-science primitives of the OntoLeap platform:
1. Fact Extraction: Compiles unstructured text, HTML, or specs into Subject-Predicate-Object triples.
2. Graph Diff: Computes exact set differences (A ∩ B, A \\ B, B \\ A) between any two sources.
3. Site Topology: Analyzes multi-page network graphs, computing PageRank and internal linking silos.
4. AI Search Probing: Audits external AI answer engines (Perplexity, Gemini, SearchGPT) for citations.

Designed for headless execution, MCP tool bindings, and automated agent workflows.
"""

import json
import logging
import asyncio
from typing import Dict, Any, List, Optional, Union
from urllib.parse import urlparse

from models import (
    SemanticTriple,
    SiteAuditAndLinkResult,
    ExtractionResult,
    GeoAuditResponse,
    GeoAuditRequest,
)
from pipeline import get_default_pipeline, OntologyPipeline
from semantic_seo import audit_internal_links
from geo_engine import execute_geo_citation_audit
from product_truth import (
    parse_openapi_spec,
    build_product_truth_matrix,
)
from scraper import smart_fetch

logger = logging.getLogger("ontoleap.graph_engine")


class GraphEngine:
    """
    Decoupled engine for Knowledge Graph Extraction, Comparison, and Structural Analysis.
    """

    def __init__(self, pipeline: Optional[OntologyPipeline] = None):
        self.pipeline = pipeline or get_default_pipeline()

    async def extract_knowledge_graph(
        self,
        source: str,
        is_url: Optional[bool] = None,
        vertical_id: str = "b2b_saas_fintech"
    ) -> Dict[str, Any]:
        """
        Extracts entities, Schema.org types, and relational semantic triples from a URL or raw text.
        """
        if is_url is None:
            is_url = source.strip().startswith(("http://", "https://"))

        text = ""
        html = None
        url = None

        if is_url:
            url = source.strip()
            loop = asyncio.get_running_loop()
            html = await loop.run_in_executor(None, self.pipeline.fetch_url, url)
            if not html:
                raise ValueError(f"Failed to fetch content from URL: {url}")
        else:
            text = source.strip()

        loop = asyncio.get_running_loop()
        extraction: ExtractionResult = await loop.run_in_executor(
            None,
            lambda: self.pipeline.process(html=html, text=text if text else None, url=url, deep_crawl=False)
        )

        triples_data = [
            {
                "subject": t.subject,
                "predicate": t.predicate,
                "object": t.object,
                "confidence": t.confidence,
                "evidence": t.evidence_sentence
            }
            for t in extraction.triples
        ]

        entities_data = [
            {
                "text": e.text,
                "label": e.label,
                "score": round(e.score, 3)
            }
            for e in extraction.entities
        ]

        schemas_data = [s.schema_type for s in extraction.schema_org]

        return {
            "source": url or "raw_text",
            "subject_entity": extraction.title or "Domain Entity",
            "readiness_score": extraction.readiness_score,
            "entities_count": len(entities_data),
            "triples_count": len(triples_data),
            "entities": entities_data,
            "triples": triples_data,
            "detected_schemas": list(set(schemas_data)),
            "mandatory_schema_status": extraction.mandatory_schema_status,
        }

    async def diff_knowledge_graphs(
        self,
        source_a: str,
        source_b: str,
        source_a_is_url: Optional[bool] = None,
        source_b_is_url: Optional[bool] = None,
        vertical_id: str = "b2b_saas_fintech"
    ) -> Dict[str, Any]:
        """
        Calculates the discrete mathematical graph difference between Source A and Source B:
        - Grounded Facts (A ∩ B): Claims in A backed by B.
        - Unbacked Claims (A \\ B): Claims asserted in A with zero evidence in B (Drift / Hallucinations).
        - Omitted Capabilities (B \\ A): Features present in B that A fails to communicate.
        """
        # 1. Extract Source A (e.g. Marketing Copy / Landing Page / Draft)
        graph_a = await self.extract_knowledge_graph(source_a, is_url=source_a_is_url, vertical_id=vertical_id)
        triples_a = [
            SemanticTriple(
                subject=t["subject"],
                predicate=t["predicate"],
                object=t["object"],
                confidence=t.get("confidence", 0.9),
                evidence_sentence=t.get("evidence")
            )
            for t in graph_a["triples"]
        ]

        # 2. Extract Source B (e.g. OpenAPI spec JSON or technical URL or competitor)
        triples_b: List[SemanticTriple] = []
        is_b_spec = False

        # Test if Source B is raw OpenAPI JSON
        if not (source_b.strip().startswith("http://") or source_b.strip().startswith("https://")):
            try:
                spec_json = json.loads(source_b.strip())
                if isinstance(spec_json, dict) and ("paths" in spec_json or "openapi" in spec_json or "swagger" in spec_json):
                    triples_b = parse_openapi_spec(spec_json, brand_name=graph_a["subject_entity"])
                    is_b_spec = True
            except Exception:
                pass

        if not is_b_spec:
            graph_b = await self.extract_knowledge_graph(source_b, is_url=source_b_is_url, vertical_id=vertical_id)
            triples_b = [
                SemanticTriple(
                    subject=t["subject"],
                    predicate=t["predicate"],
                    object=t["object"],
                    confidence=t.get("confidence", 0.9),
                    evidence_sentence=t.get("evidence")
                )
                for t in graph_b["triples"]
            ]

        # 3. Perform Discrete Graph Diff via build_product_truth_matrix
        matrix_res = build_product_truth_matrix(
            brand_name=graph_a["subject_entity"],
            marketing_triples=triples_a,
            technical_triples=triples_b,
            marketing_url=graph_a.get("source", ""),
            tech_docs_url="OpenAPI Spec" if is_b_spec else graph_b.get("source", "")
        )

        grounded_facts = [
            {"subject": t.subject, "predicate": t.predicate, "object": t.object, "evidence": t.evidence_sentence}
            for t in matrix_res.verified_triples
        ]
        unbacked_claims = [
            {"subject": t.subject, "predicate": t.predicate, "object": t.object, "evidence": t.evidence_sentence}
            for t in matrix_res.unbacked_claims
        ]
        omitted_capabilities = [
            {"subject": t.subject, "predicate": t.predicate, "object": t.object, "evidence": t.evidence_sentence}
            for t in matrix_res.hidden_capabilities
        ]

        mgi = matrix_res.marketing_grounding_index
        verdict = matrix_res.evidence_status or "evaluated"

        return {
            "source_a": source_a if len(source_a) < 150 else source_a[:150] + "...",
            "source_b": source_b if len(source_b) < 150 else source_b[:150] + "...",
            "grounding_score": round(mgi, 1) if mgi is not None else None,
            "verdict": verdict,
            "summary": {
                "total_claims_in_a": len(triples_a),
                "total_proof_in_b": len(triples_b),
                "grounded_count": len(grounded_facts),
                "unbacked_count": len(unbacked_claims),
                "omitted_count": len(omitted_capabilities),
            },
            "grounded_facts": grounded_facts,
            "unbacked_claims": unbacked_claims,
            "omitted_capabilities": omitted_capabilities,
        }

    async def analyze_site_topology(
        self,
        sitemap_url: Optional[str] = None,
        urls: Optional[List[str]] = None,
        max_pages: int = 10
    ) -> Dict[str, Any]:
        """
        Crawls target pages across a domain, calculates PageRank authority hubs,
        and generates high-intent internal link insertion recommendations.
        """
        res: SiteAuditAndLinkResult = await audit_internal_links(
            sitemap_url=sitemap_url,
            urls=urls,
            max_pages=max_pages,
            pipeline=self.pipeline
        )

        detailed_hubs = [
            {
                "concept": h.concept,
                "canonical_url": h.canonical_url,
                "role": h.taxonomy_role,
                "pagerank": round(h.pagerank_score, 4),
                "inbound_links": h.inbound_internal_links
            }
            for h in res.topic_hubs_detailed
        ]

        opportunities = [
            {
                "source_url": o.source_url,
                "target_url": o.target_url,
                "entity": o.entity,
                "predicate": o.predicate,
                "suggested_anchor": o.suggested_anchor,
                "context_sentence": o.context_sentence,
                "html_snippet": o.html_snippet,
                "priority": o.priority
            }
            for o in res.opportunities[:25]
        ]

        return {
            "root_domain": res.root_domain,
            "pages_analyzed": res.pages_analyzed,
            "opportunities_count": res.opportunities_count,
            "topic_hubs": detailed_hubs,
            "orphan_pages": res.orphan_pages,
            "cannibalization_risks": [
                {
                    "concept": c.concept,
                    "competing_urls": c.competing_urls,
                    "recommended_hub": c.recommended_canonical_hub
                }
                for c in res.cannibalization_risks
            ],
            "internal_link_opportunities": opportunities
        }

    async def probe_ai_search_sov(
        self,
        brand_name: str,
        domain: str,
        competitor_names: Optional[List[str]] = None,
        vertical_id: str = "b2b_saas_fintech",
        custom_queries: Optional[List[str]] = None,
        triples: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Probes live AI answer engines with buyer queries, measures Share of Voice (SOV %),
        identifies competitor citations, and audits AI hallucinations.
        """
        typed_triples = []
        if triples:
            typed_triples = [
                SemanticTriple(
                    subject=t.get("subject", brand_name),
                    predicate=t.get("predicate", "provides"),
                    object=t.get("object", ""),
                    confidence=t.get("confidence", 0.9),
                    evidence_sentence=t.get("evidence")
                )
                for t in triples
            ]

        loop = asyncio.get_running_loop()
        geo_req = GeoAuditRequest(
            brand_name=brand_name,
            domain=domain,
            competitor_names=competitor_names or [],
            vertical_id=vertical_id,
            triples=typed_triples,
            custom_queries=custom_queries
        )
        res: GeoAuditResponse = await loop.run_in_executor(None, execute_geo_citation_audit, geo_req)

        query_feed = [
            {
                "query": q.query,
                "category": q.category,
                "engine_used": q.engine_used,
                "brand_cited": q.brand_cited,
                "brand_rank": q.brand_rank,
                "competitors_mentioned": q.competitors_cited,
                "hallucinations_detected": q.hallucinated_claims,
                "ai_excerpt": q.synthesized_answer[:200] + ("..." if len(q.synthesized_answer) > 200 else "")
            }
            for q in res.probe_results
        ]

        return {
            "brand_name": res.brand_name,
            "domain": domain,
            "share_of_voice_pct": res.share_of_voice,
            "weighted_sov_pct": res.weighted_sov,
            "ai_mention_rate_pct": res.ai_mention_rate,
            "hallucination_rate_pct": res.hallucination_rate,
            "competitor_breakdown": res.competitor_sov,
            "citation_gaps_count": len(res.citation_gap_queries),
            "citation_gap_queries": res.citation_gap_queries,
            "recommendations": res.geo_recommendations,
            "queries_audited": query_feed
        }


# Global singleton instance
_global_engine: Optional[GraphEngine] = None


def get_graph_engine() -> GraphEngine:
    global _global_engine
    if _global_engine is None:
        _global_engine = GraphEngine()
    return _global_engine
