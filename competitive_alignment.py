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
from typing import Dict, Any, List, Optional, Set, Tuple
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup

import vertex_ai_client
import google_kg_client
from models import (
    SemanticTriple,
    ProductTruthRequest,
    ProductTruthMatrixResponse,
    ComparativeCapability,
    CounterPositioningAngle,
    TriOntologyAlignmentRequest,
    TriOntologyAlignmentResponse,
    CompetitorOntologyRequest,
    CompetitorOntologyResponse
)
from pipeline import OntologyPipeline
from scraper import smart_fetch, validate_url_for_fetch
from product_truth import execute_product_truth_audit, _concepts_match
from constants import DEEP_CRAWL_PATHS, WIKIDATA_KB

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

    # Append to Compounding Truth Ledger
    try:
        from truth_ledger.recorder import record_alignment_to_ledger
        record_alignment_to_ledger(
            company_name=company_name,
            competitors=competitor_names,
            vertical=industry_category,
            advantages_count=len(company_advantages),
            vulnerabilities_count=len(competitor_vulnerabilities),
            table_stakes_count=len(table_stakes_set),
            briefs_count=len(briefs)
        )
    except Exception as rec_err:
        logger.debug("Truth ledger append error: %s", rec_err)

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
    "capability": "Exact technical capability or feature (e.g. 'ASC 606 Revenue Recognition' or 'NetSuite ERP Sync')",
    "comparative_status": "company_advantage (if our verified strength) or competitor_fluff_vulnerability (if competitor unbacked claim)",
    "predicate": "automates, integratesWith, or compliesWith",
    "angle_title": "Short, punchy campaign title (e.g., 'Real-Time Sync vs. Manual CSV Export')",
    "attack_angle": "Exact messaging angle showing why buyers should choose our brand over the competitor based strictly on verified technical reality.",
    "discovery_question": "High-impact question sales reps ask the buyer during demos or RFP reviews to expose competitor gap.",
    "fud_counter_defense": "Rebuttal and proof points sales reps use when competitors spread doubts about this capability.",
    "core_narrative": "2-3 sentences explaining the strategic positioning.",
    "company_differentiator": "The specific verified capability our engineering actually ships.",
    "competitor_vulnerability": "The exact competitor limitation or unbacked marketing claim."
  }}
]
"""

    resp_text = vertex_ai_client._call_gemini(
        prompt=prompt,
        system_instruction="You are an enterprise B2B competitive intelligence strategist. Output valid JSON array only.",
        temperature=0.2,
        max_output_tokens=3000,
        thinking_budget=256
    )

    if not resp_text:
        # Fallback battlecard if LLM is unavailable
        briefs = []
        for adv in company_advantages[:3]:
            briefs.append(CounterPositioningAngle(
                target_competitor=adv.competitor_name,
                angle_title=f"Verified {adv.concept} Leadership",
                capability=adv.concept,
                comparative_status="company_advantage",
                predicate=adv.predicate,
                attack_angle=f"{company_name} delivers native, production-verified {adv.predicate} for {adv.concept}. Position this as a core operational requirement that {adv.competitor_name} fails to substantiate.",
                discovery_question=f"When evaluating {adv.concept}, does {adv.competitor_name} support native API endpoints and automated reconciliation, or does it require manual workflows?",
                fud_counter_defense=f"If competitors claim equivalence on {adv.concept}, challenge them to demonstrate their live API endpoints. Our architecture provides full programmatic support.",
                core_narrative=f"{company_name} delivers native, API-backed {adv.concept}, whereas {adv.competitor_name} fails to provide verifiable production support.",
                company_differentiator=f"Production {adv.predicate} {adv.concept}",
                competitor_vulnerability=f"Missing or unbacked {adv.concept} in documentation",
                suggested_campaign_topics=[f"Why Native {adv.concept} Matters for Enterprise", f"{company_name} vs {adv.competitor_name}: The Technical Truth"]
            ))
        for vuln in competitor_vulnerabilities[:2]:
            briefs.append(CounterPositioningAngle(
                target_competitor=vuln.competitor_name,
                angle_title=f"Exposing {vuln.competitor_name}'s {vuln.concept} Fluff",
                capability=vuln.concept,
                comparative_status="competitor_fluff_vulnerability",
                predicate=vuln.predicate,
                attack_angle=f"{vuln.competitor_name} aggressively markets {vuln.concept}, but technical docs and API specs show no proof of production support.",
                discovery_question=f"Have you verified whether {vuln.competitor_name}'s {vuln.concept} is a native production capability or just a marketing roadmap promise?",
                fud_counter_defense=f"Competitors may market roadmap features as live capabilities. Demand to see API documentation or customer references before committing.",
                core_narrative=f"{vuln.competitor_name} markets {vuln.concept} as a feature, but lacks verified API endpoints.",
                company_differentiator=f"Documented truth vs unbacked marketing claims",
                competitor_vulnerability=f"Unbacked claim of {vuln.predicate} {vuln.concept}",
                suggested_campaign_topics=[f"Evaluating {vuln.concept}: Marketing Claims vs Technical Reality"]
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
            cap = item.get("capability") or item.get("angle_title") or "Key Differentiator"
            status = item.get("comparative_status") or "company_advantage"
            pred = item.get("predicate") or "automates"
            attack = item.get("attack_angle") or item.get("core_narrative") or ""
            disc = item.get("discovery_question") or f"How does the vendor support {cap} in production?"
            fud = item.get("fud_counter_defense") or item.get("company_differentiator") or ""
            
            briefs.append(CounterPositioningAngle(
                target_competitor=item.get("target_competitor", "Competitor"),
                angle_title=item.get("angle_title", f"Verified {cap} Leadership"),
                capability=cap,
                comparative_status=status,
                predicate=pred,
                attack_angle=attack,
                discovery_question=disc,
                fud_counter_defense=fud,
                core_narrative=item.get("core_narrative", attack),
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


def extract_competitor_ontology(
    req: CompetitorOntologyRequest,
    pipeline: OntologyPipeline
) -> CompetitorOntologyResponse:
    """
    Crawls and extracts the public ontology of a competitor using SmartScraper:
    1. Fetches homepage and discovers high-signal subpages (/pricing, /features, /integrations).
    2. Mines semantic triples (automates, integratesWith, compliesWith, supportsPricingModel).
    3. Gathers Schema.org markup types.
    4. Evaluates Google Knowledge Graph and Wikidata grounding.
    5. Synthesizes a structured CompetitorOntologyResponse.
    """
    validate_url_for_fetch(req.url)
    parsed = urlparse(req.url)
    clean_domain = parsed.netloc.replace("www.", "")
    brand_name = req.brand_name or clean_domain.split(".")[0].capitalize()

    logger.info("Extracting competitor ontology for %s (%s)...", brand_name, req.url)

    pages_to_crawl = [req.url]

    # Discover subpages if requested
    if req.crawl_subpages:
        base_origin = f"{parsed.scheme}://{parsed.netloc}"
        for p in DEEP_CRAWL_PATHS[:req.max_subpages]:
            pages_to_crawl.append(urljoin(base_origin, p))

    collected_triples: List[SemanticTriple] = []
    seen_triple_keys: Set[Tuple[str, str, str]] = set()
    schema_types: Set[str] = set()
    successful_pages = 0

    for page_url in pages_to_crawl:
        try:
            html = smart_fetch(page_url, timeout=12)
            if not html or len(html.strip()) < 100:
                continue

            successful_pages += 1
            res = pipeline.process(html=html, text="")

            # Extract triples
            for t in (res.triples or []):
                key = (t.subject.lower().strip(), t.predicate.lower().strip(), t.object.lower().strip())
                if key not in seen_triple_keys:
                    seen_triple_keys.add(key)
                    t.subject = brand_name
                    collected_triples.append(t)

            # Extract schema types
            for s in (res.schema_org or []):
                schema_types.add(s.schema_type)

            # Also check if page had explicit links to deep subpages we haven't visited
            if len(pages_to_crawl) <= req.max_subpages + 1:
                soup = BeautifulSoup(html, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = a["href"].strip()
                    full = urljoin(page_url, href)
                    if urlparse(full).netloc == parsed.netloc:
                        path_lower = urlparse(full).path.lower()
                        if any(k in path_lower for k in ["pricing", "features", "integrations", "product", "solutions"]):
                            if full not in pages_to_crawl and len(pages_to_crawl) < req.max_subpages + 1:
                                pages_to_crawl.append(full)
        except Exception as e:
            logger.warning("Failed to crawl competitor page %s: %s", page_url, e)

    # Group extracted triples by predicate
    capabilities_automated = sorted(list({t.object for t in collected_triples if t.predicate == "automates"}))
    integrations_claimed = sorted(list({t.object for t in collected_triples if t.predicate == "integratesWith"}))
    compliance_claimed = sorted(list({t.object for t in collected_triples if t.predicate == "compliesWith"}))
    pricing_models = sorted(list({t.object for t in collected_triples if t.predicate == "supportsPricingModel"}))

    # Google KG check
    google_kg_grounded = False
    try:
        kg_res = google_kg_client.search_entity(brand_name)
        if kg_res and kg_res.get("is_recognized"):
            google_kg_grounded = True
    except Exception:
        pass

    # Wikidata check
    wikidata_grounded = brand_name.lower() in WIKIDATA_KB

    summary = (
        f"Competitor Ontology for {brand_name} ({req.url}): "
        f"Analyzed {successful_pages} pages. Extracted {len(collected_triples)} semantic triples "
        f"({len(capabilities_automated)} automated capabilities, {len(integrations_claimed)} ecosystem integrations, "
        f"{len(compliance_claimed)} compliance frameworks, {len(pricing_models)} pricing models). "
        f"Entity Grounding: Google KG {'[VERIFIED]' if google_kg_grounded else '[UNGROUNDED]'}, "
        f"Wikidata {'[VERIFIED]' if wikidata_grounded else '[UNGROUNDED]'}."
    )

    return CompetitorOntologyResponse(
        brand_name=brand_name,
        url=req.url,
        pages_analyzed=successful_pages,
        total_claims=len(collected_triples),
        capabilities_automated=capabilities_automated,
        integrations_claimed=integrations_claimed,
        compliance_claimed=compliance_claimed,
        pricing_models=pricing_models,
        triples=collected_triples,
        schema_org_types=sorted(list(schema_types)),
        google_kg_grounded=google_kg_grounded,
        wikidata_grounded=wikidata_grounded,
        ontology_summary=summary
    )

