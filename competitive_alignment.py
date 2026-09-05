"""
GainARK OntoLeap — Tri-Ontology Competitive Alignment Engine
Step 3 of the Tri-Ontology Marketing Governance Platform.

Cross-references:
1. Industry Ontology (Objective domain standards & baseline taxonomy)
2. Company Product Truth (Verified capabilities vs. marketing claims)
3. Competitor Product Truth (Competitor verified reality vs. competitor marketing claims)

Produces:
- Company Superiority & Unfair Advantages
- Competitor Fluff & Vulnerability Analysis
- Industry Table Stakes & Parity Analysis
- Autonomous Sales & Marketing Counter-Positioning Briefs
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional, Set
from urllib.parse import urlparse

import vertex_ai_client
from models import (
    SemanticTriple,
    ProductTruthRequest,
    ProductTruthMatrixResponse,
    ComparativeCapability,
    CounterPositioningAngle,
    TriOntologyAlignmentRequest,
    TriOntologyAlignmentResponse
)
from pipeline import OntologyPipeline
from product_truth import execute_product_truth_audit, _concepts_match

logger = logging.getLogger("gainark.competitive_alignment")


def _find_matching_triple(target_pred: str, target_obj: str, triple_list: List[SemanticTriple]) -> Optional[SemanticTriple]:
    """Finds a matching triple by predicate and concept overlap."""
    for t in triple_list:
        if t.predicate.lower() == target_pred.lower():
            if _concepts_match(t.object, target_obj):
                return t
    return None


def align_tri_ontologies(
    company_matrix: ProductTruthMatrixResponse,
    competitor_matrices: List[ProductTruthMatrixResponse],
    vertical_config: Any,
    vertical_id: str = "b2b_saas_fintech"
) -> TriOntologyAlignmentResponse:
    """
    Performs differential graph analysis across Company, Competitors, and Industry standards.
    """
    company_name = company_matrix.brand_name
    competitor_names = [c.brand_name for c in competitor_matrices]
    industry_category = getattr(vertical_config, "display_name", "Enterprise B2B SaaS")

    company_advantages: List[ComparativeCapability] = []
    competitor_vulnerabilities: List[ComparativeCapability] = []
    competitor_advantages: List[ComparativeCapability] = []
    table_stakes_set: Set[str] = set()

    for comp in competitor_matrices:
        comp_name = comp.brand_name

        # 1. Identify Competitor Vulnerabilities (Claims on competitor marketing with 0 tech backing)
        for comp_unbacked in comp.unbacked_claims:
            # Check if Company has verified capability in this exact area!
            comp_verified_in_us = _find_matching_triple(comp_unbacked.predicate, comp_unbacked.object, company_matrix.verified_triples)

            if comp_verified_in_us:
                insight = (
                    f"Prime Exploitation Vector: {comp_name} advertises '{comp_unbacked.object}' on marketing without technical proof, "
                    f"whereas {company_name} has verified engineering documentation ({comp_verified_in_us.provenance or 'API'})."
                )
            else:
                insight = f"{comp_name} claims '{comp_unbacked.object}' on their marketing site with no technical documentation backing."

            competitor_vulnerabilities.append(ComparativeCapability(
                concept=comp_unbacked.object,
                predicate=comp_unbacked.predicate,
                company_status="verified" if comp_verified_in_us else "missing",
                competitor_status="unbacked_claim",
                competitor_name=comp_name,
                insight=insight,
                company_evidence=comp_verified_in_us.evidence_sentence if comp_verified_in_us else None,
                competitor_evidence=comp_unbacked.evidence_sentence
            ))

        # 2. Identify Company Advantages (Verified in Company, but absent/unbacked in competitor)
        for comp_verified in company_matrix.verified_triples:
            rival_match_verified = _find_matching_triple(comp_verified.predicate, comp_verified.object, comp.verified_triples)
            rival_match_unbacked = _find_matching_triple(comp_verified.predicate, comp_verified.object, comp.unbacked_claims)

            if rival_match_verified:
                # Both have it verified -> Table stakes
                table_stakes_set.add(f"{comp_verified.predicate} {comp_verified.object}")
            elif rival_match_unbacked:
                company_advantages.append(ComparativeCapability(
                    concept=comp_verified.object,
                    predicate=comp_verified.predicate,
                    company_status="verified",
                    competitor_status="unbacked_claim",
                    competitor_name=comp_name,
                    insight=f"{company_name} has proven production backing for '{comp_verified.object}', while {comp_name} only markets it as an unbacked claim.",
                    company_evidence=comp_verified.evidence_sentence,
                    competitor_evidence=rival_match_unbacked.evidence_sentence
                ))
            else:
                company_advantages.append(ComparativeCapability(
                    concept=comp_verified.object,
                    predicate=comp_verified.predicate,
                    company_status="verified",
                    competitor_status="missing",
                    competitor_name=comp_name,
                    insight=f"Exclusive Capability: {company_name} verifies {comp_verified.predicate} '{comp_verified.object}', completely absent from {comp_name}.",
                    company_evidence=comp_verified.evidence_sentence,
                    competitor_evidence=None
                ))

        # 3. Identify Competitor Advantages (Verified in competitor, absent in company)
        for comp_tech in comp.verified_triples:
            our_match = _find_matching_triple(comp_tech.predicate, comp_tech.object, company_matrix.verified_triples)
            if not our_match:
                competitor_advantages.append(ComparativeCapability(
                    concept=comp_tech.object,
                    predicate=comp_tech.predicate,
                    company_status="missing",
                    competitor_status="verified",
                    competitor_name=comp_name,
                    insight=f"{comp_name} has verified technical support for '{comp_tech.object}', which {company_name} currently lacks.",
                    company_evidence=None,
                    competitor_evidence=comp_tech.evidence_sentence
                ))

    # Deduplicate table stakes with industry standards
    core_seeds = getattr(vertical_config, "core_seed_concepts", [])
    for seed in core_seeds[:6]:
        table_stakes_set.add(f"Category Standard: {seed}")

    # Synthesize Counter-Positioning Briefs via Gemini 2.5 Flash
    briefs = synthesize_counter_positioning_briefs(
        company_name=company_name,
        company_advantages=company_advantages[:4],
        competitor_vulnerabilities=competitor_vulnerabilities[:4],
        industry_category=industry_category
    )

    summary = (
        f"Tri-Ontology Alignment for {company_name} against {', '.join(competitor_names)} in {industry_category}: "
        f"Identified {len(company_advantages)} Company Advantage vectors, "
        f"{len(competitor_vulnerabilities)} Competitor Fluff vulnerabilities to exploit, and "
        f"{len(table_stakes_set)} shared table-stakes capabilities."
    )

    return TriOntologyAlignmentResponse(
        company_name=company_name,
        competitor_names=competitor_names,
        vertical_id=vertical_id,
        industry_category=industry_category,
        company_advantages=company_advantages,
        competitor_vulnerabilities=competitor_vulnerabilities,
        competitor_advantages=competitor_advantages,
        table_stakes=sorted(list(table_stakes_set)),
        counter_positioning_briefs=briefs,
        executive_summary=summary
    )


def synthesize_counter_positioning_briefs(
    company_name: str,
    company_advantages: List[ComparativeCapability],
    competitor_vulnerabilities: List[ComparativeCapability],
    industry_category: str
) -> List[CounterPositioningAngle]:
    """
    Prompts Gemini 2.5 Flash to synthesize high-converting, grounded counter-positioning angles
    and sales battlecards based on verified technical advantages and competitor fluff.
    """
    if not company_advantages and not competitor_vulnerabilities:
        return []

    adv_text = "\n".join([f"- [{a.predicate}] {a.concept}: {a.insight}" for a in company_advantages])
    vuln_text = "\n".join([f"- Against {v.competitor_name}: {v.predicate} {v.concept} ({v.insight})" for v in competitor_vulnerabilities])

    prompt = f"""
You are an elite enterprise B2B product marketing director and competitive strategist in {industry_category}.
Analyze the following verified technical capabilities and competitor marketing fluff:

Our Brand: {company_name}

Verified Brand Advantages (Backed by our code/API specs):
{adv_text}

Competitor Fluff & Vulnerabilities (Claims they market, but fail to back in technical docs):
{vuln_text}

Synthesize 2 to 3 sharp, factual, and punchy Counter-Positioning Angles / Sales Battlecards.
Return ONLY a valid, raw JSON array (or enclosed in ```json fence) of objects matching this schema:
[
  {{
    "target_competitor": "Competitor name",
    "angle_title": "Short, punchy campaign title (e.g., 'Real-Time Sync vs. Manual CSV Export')",
    "core_narrative": "2-3 sentences explaining why buyers should choose our brand over the competitor based strictly on the verified technical facts.",
    "company_differentiator": "The specific verified capability our engineering actually ships.",
    "competitor_vulnerability": "The exact competitor limitation or unbacked marketing claim.",
    "suggested_campaign_topics": ["List of 2-3 blog post or landing page headline ideas"]
  }}
]
"""

    resp_text = vertex_ai_client._call_gemini(
        prompt=prompt,
        system_instruction="You are a competitive intelligence strategist. Output valid JSON array only.",
        temperature=0.2,
        max_output_tokens=3000,
        thinking_budget=256
    )

    if not resp_text:
        # Fallback battlecard if LLM is unavailable
        briefs = []
        for adv in company_advantages[:2]:
            briefs.append(CounterPositioningAngle(
                target_competitor=adv.competitor_name,
                angle_title=f"Verified {adv.concept} Leadership",
                core_narrative=f"{company_name} delivers native, API-backed {adv.concept}, whereas {adv.competitor_name} fails to provide verifiable production support.",
                company_differentiator=f"Production {adv.predicate} {adv.concept}",
                competitor_vulnerability=f"Missing or unbacked {adv.concept} in documentation",
                suggested_campaign_topics=[f"Why Native {adv.concept} Matters for Enterprise", f"{company_name} vs {adv.competitor_name}: The Technical Truth"]
            ))
        return briefs

    cleaned = resp_text.strip()
    if cleaned.startswith("```"):
        import re
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        data = json.loads(cleaned)
        briefs = []
        for item in data:
            briefs.append(CounterPositioningAngle(
                target_competitor=item.get("target_competitor", "Competitor"),
                angle_title=item.get("angle_title", "Competitive Angle"),
                core_narrative=item.get("core_narrative", ""),
                company_differentiator=item.get("company_differentiator", ""),
                competitor_vulnerability=item.get("competitor_vulnerability", ""),
                suggested_campaign_topics=item.get("suggested_campaign_topics", [])
            ))
        return briefs
    except Exception as e:
        logger.error("Failed to parse counter-positioning briefs JSON: %s", e)
        return []


def execute_tri_ontology_alignment(
    req: TriOntologyAlignmentRequest,
    pipeline: OntologyPipeline
) -> TriOntologyAlignmentResponse:
    """
    Full orchestration pipeline for Tri-Ontology competitive alignment:
    1. Ingests and computes Company Product Truth
    2. Ingests and computes Competitor Product Truth for each competitor
    3. Cross-aligns against the Industry Ontology and synthesizes counter-positioning briefs
    """
    logger.info("Starting Tri-Ontology alignment for %s against %d competitors...", req.company.marketing_url, len(req.competitors))

    # 1. Company Product Truth
    company_matrix = execute_product_truth_audit(req.company, pipeline)

    # 2. Competitor Product Truth
    competitor_matrices: List[ProductTruthMatrixResponse] = []
    for comp_req in req.competitors:
        logger.info("Analyzing competitor %s...", comp_req.marketing_url)
        try:
            comp_matrix = execute_product_truth_audit(comp_req, pipeline)
            competitor_matrices.append(comp_matrix)
        except Exception as e:
            logger.warning("Failed to analyze competitor %s: %s", comp_req.marketing_url, e)

    # 3. Align Tri-Ontologies
    return align_tri_ontologies(
        company_matrix=company_matrix,
        competitor_matrices=competitor_matrices,
        vertical_config=pipeline.config,
        vertical_id=req.vertical_id or "b2b_saas_fintech"
    )
