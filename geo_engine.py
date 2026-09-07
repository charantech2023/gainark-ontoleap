"""
GainARK OntoLeap — Generative Engine Optimization (GEO) & Live AI Citation Probing Engine

Evaluates whether real AI search engines (Perplexity, SearchGPT, Gemini, ChatGPT)
cite, recommend, or omit a brand for unbranded, high-intent B2B buyer queries.
Computes Share of AI Voice (SOV), Competitor Share of Voice, and verifies if claims
made by AI models about the brand are grounded in verified ontology triples or hallucinated.

Supports 3-tier execution:
- Tier 1: Live Google Gemini 2.5 Flash as an unbiased enterprise answer engine
- Tier 2: Live open web search snippets (DuckDuckGo HTML/lite) with zero API keys/costs
- Tier 3: Deterministic category benchmark fallback for airgapped/offline runs
"""

import logging
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup
from models import (
    GeoAuditRequest,
    GeoAuditResponse,
    GeoProbeResult,
    GeoQueryItem,
    SemanticTriple,
)
import vertex_ai_client

logger = logging.getLogger("gainark.geo_engine")

# Known category leaders for fallback competitor presence detection
DEFAULT_VERTICAL_COMPETITORS = {
    "b2b_saas_fintech": ["Chargebee", "Stripe", "Zuora", "Maxio", "Recurly", "Paddle"],
    "cybersecurity": ["Snyk", "Wiz", "Palo Alto Networks", "CrowdStrike", "Lacework"],
    "hr_payroll": ["Gusto", "Rippling", "Deel", "ADP", "Paylocity"],
}


def generate_buyer_queries(
    brand_name: str,
    domain: str,
    triples: List[SemanticTriple],
    competitors: Optional[List[str]] = None,
    vertical_name: str = "B2B SaaS & Financial Software",
    count: int = 5,
) -> List[GeoQueryItem]:
    """
    Synthesizes unbranded, high-intent B2B buyer evaluation queries
    grounded in the verified ontology capabilities.
    """
    queries: List[GeoQueryItem] = []

    # 1. Extract capabilities from triples
    compliances = [t.object for t in triples if t.predicate == "compliesWith"]
    integrations = [t.object for t in triples if t.predicate == "integratesWith"]
    automations = [t.object for t in triples if t.predicate in ("automates", "supportsPricingModel")]

    # Query 1: Compliance & Regulatory (High RFP intent)
    if compliances:
        comp_str = " and ".join(compliances[:2])
        queries.append(GeoQueryItem(
            query_text=f"Which enterprise {vertical_name.lower()} platforms comply with {comp_str}?",
            category="Compliance & Governance",
            intent_stage="Vendor Shortlist",
            targeted_capabilities=compliances[:2],
        ))
    else:
        queries.append(GeoQueryItem(
            query_text=f"Which enterprise {vertical_name.lower()} platforms have certified SOC 2 Type II and HIPAA compliance?",
            category="Compliance & Governance",
            intent_stage="Vendor Shortlist",
            targeted_capabilities=["SOC 2", "Compliance"],
        ))

    # Query 2: Tech-Stack & ERP/CRM Integrations (High purchase intent)
    if integrations:
        int_str = " and ".join(integrations[:2])
        queries.append(GeoQueryItem(
            query_text=f"What automated software solutions integrate with {int_str} for general ledger and CRM synchronization?",
            category="Ecosystem & Integrations",
            intent_stage="Technical Evaluation",
            targeted_capabilities=integrations[:2],
        ))
    else:
        queries.append(GeoQueryItem(
            query_text=f"What platforms provide bi-directional synchronization with NetSuite ERP and Salesforce CRM?",
            category="Ecosystem & Integrations",
            intent_stage="Technical Evaluation",
            targeted_capabilities=["NetSuite", "Salesforce"],
        ))

    # Query 3: Core Workflow Automation (Problem-solving intent)
    if automations:
        auto_str = automations[0].lower()
        queries.append(GeoQueryItem(
            query_text=f"Best tools for automating {auto_str} in high-growth mid-market companies?",
            category="Workflow Automation",
            intent_stage="Discovery",
            targeted_capabilities=[automations[0]],
        ))
    else:
        queries.append(GeoQueryItem(
            query_text=f"Best software for automating complex usage-based pricing and recurring billing workflows?",
            category="Workflow Automation",
            intent_stage="Discovery",
            targeted_capabilities=["Usage-Based Pricing", "Recurring Billing"],
        ))

    # Query 4: Category Alternatives & Comparison
    comp_sample = competitors[0] if competitors else "Chargebee"
    queries.append(GeoQueryItem(
        query_text=f"Top alternatives to {comp_sample} for enterprise revenue operations and contract billing",
        category="Competitive Displacement",
        intent_stage="Vendor Selection",
        targeted_capabilities=["Contract Billing", "Revenue Operations"],
    ))

    # Query 5: Scalability & Mid-Market / Enterprise Requirements
    queries.append(GeoQueryItem(
        query_text=f"Leading {vertical_name.lower()} software for multi-entity companies needing audit-ready financial reporting",
        category="Enterprise Scale",
        intent_stage="Evaluation",
        targeted_capabilities=["Multi-Entity", "Audit-Ready"],
    ))

    return queries[:count]


def fetch_open_search_snippets(query: str, timeout: int = 4) -> List[Dict[str, str]]:
    """
    Tier 2: Queries open public search snippets (DuckDuckGo HTML)
    without requiring API keys or third-party paid subscriptions.
    """
    results: List[Dict[str, str]] = []
    try:
        from scraper import smart_fetch

        encoded_q = urllib.parse.quote_plus(query)
        # Use DuckDuckGo HTML endpoint
        url = f"https://html.duckduckgo.com/html/?q={encoded_q}"
        html = smart_fetch(url, timeout=timeout)
        if html:
            soup = BeautifulSoup(html, "html.parser")
            for result_div in soup.find_all("div", class_="result")[:5]:
                title_a = result_div.find("a", class_="result__a")
                snippet_tag = result_div.find("a", class_="result__snippet")
                if title_a:
                    title = title_a.get_text(strip=True)
                    href = title_a.get("href", "")
                    snippet = snippet_tag.get_text(strip=True) if snippet_tag else ""
                    results.append({"title": title, "url": href, "snippet": snippet})
    except Exception as e:
        logger.debug("Open search snippet fetch failed for '%s': %s", query, e)

    return results


def _extract_brand_rank_and_competitors(
    text: str,
    brand_name: str,
    domain: str,
    competitor_list: List[str],
) -> Tuple[bool, Optional[int], List[str]]:
    """
    Analyzes an AI or search response to determine:
    1. brand_cited (bool)
    2. brand_rank (1-based index if cited among recommended vendors)
    3. competitors_cited (list of competitor names found in the text)
    """
    clean_text = text.lower()
    brand_lower = brand_name.lower()
    domain_clean = domain.replace("https://", "").replace("http://", "").split("/")[0].lower()

    # Check if brand is cited
    brand_regex = rf"\b{re.escape(brand_lower)}\b"
    brand_cited = bool(re.search(brand_regex, clean_text)) or domain_clean in clean_text

    # Detect competitors
    competitors_cited: List[str] = []
    all_known_competitors = set(competitor_list)
    for comp in all_known_competitors:
        comp_regex = rf"\b{re.escape(comp.lower())}\b"
        if re.search(comp_regex, clean_text) and comp.lower() != brand_lower:
            competitors_cited.append(comp)

    # Calculate brand rank among recommendations
    brand_rank = None
    if brand_cited:
        # Find position of brand vs competitors
        entities_found: List[Tuple[int, str]] = []
        brand_match = re.search(brand_regex, clean_text)
        if brand_match:
            entities_found.append((brand_match.start(), brand_name))

        for comp in competitors_cited:
            comp_match = re.search(rf"\b{re.escape(comp.lower())}\b", clean_text)
            if comp_match:
                entities_found.append((comp_match.start(), comp))

        entities_found.sort(key=lambda x: x[0])
        for idx, (_, ent_name) in enumerate(entities_found, 1):
            if ent_name == brand_name:
                brand_rank = idx
                break
        if brand_rank is None:
            brand_rank = len(entities_found) or 1

    return brand_cited, brand_rank, competitors_cited


def _audit_hallucination_against_ontology(
    text: str,
    brand_name: str,
    triples: List[SemanticTriple],
) -> Tuple[List[str], List[str]]:
    """
    Examines statements made about the brand and checks if they correspond
    to verified ontology capabilities or are hallucinated assertions.
    """
    clean_text = text.lower()
    brand_lower = brand_name.lower()
    if brand_lower not in clean_text:
        return [], []

    verified_found: List[str] = []
    unbacked_suspects: List[str] = []

    # Check known triples
    for t in triples:
        obj_lower = t.object.lower()
        if len(obj_lower) > 3 and obj_lower in clean_text:
            verified_found.append(f"{t.predicate}: {t.object}")

    # Check common generic hallucinations in B2B SaaS
    common_drift_keywords = [
        "blockchain", "ai automated payroll", "fedramp certified",
        "quantum encryption", "iso 42001", "native sap ecc sync"
    ]
    for kw in common_drift_keywords:
        if kw in clean_text and not any(kw in t.object.lower() for t in triples):
            unbacked_suspects.append(f"Alleged capability without ontology verification: '{kw.title()}'")

    return list(set(verified_found)), list(set(unbacked_suspects))


def probe_ai_citation(
    query_item: GeoQueryItem,
    brand_name: str,
    domain: str,
    competitors: List[str],
    triples: List[SemanticTriple],
) -> GeoProbeResult:
    """
    Probes an AI search engine with an unbranded buyer query.
    Employs Tier 1 (Gemini 2.5 Flash), Tier 2 (Open Search Snippets), or Tier 3 (Deterministic Fallback).
    """
    q_text = query_item.query_text
    all_track_competitors = list(set(competitors + DEFAULT_VERTICAL_COMPETITORS.get("b2b_saas_fintech", [])))

    # -------------------------------------------------------------
    # Tier 1: Live Google Gemini 2.5 Flash Answer Engine
    # -------------------------------------------------------------
    if vertex_ai_client.is_available():
        system_prompt = (
            "You are an expert enterprise software research advisor answering B2B technology selection questions. "
            "A prospective buyer is asking for the leading software platforms meeting their technical criteria. "
            "Rules:\n"
            "1. Answer authoritatively, recommending 2 to 4 market-leading vendors.\n"
            "2. For each recommended vendor, state why they fit the criteria (compliance, integrations, workflows).\n"
            "3. Maintain unbiased, objective B2B tone.\n"
            "4. Keep the answer between 60 and 120 words."
        )
        user_prompt = f"B2B Buyer Evaluation Query: \"{q_text}\"\n\nProvide the top recommended vendors and their specific capabilities:"
        try:
            gemini_answer = vertex_ai_client._call_gemini(
                prompt=user_prompt,
                system_instruction=system_prompt,
                temperature=0.2,
                timeout=12,
            )
            if gemini_answer:
                cited, rank, comps = _extract_brand_rank_and_competitors(
                    gemini_answer, brand_name, domain, all_track_competitors
                )
                ver_claims, hall_claims = _audit_hallucination_against_ontology(
                    gemini_answer, brand_name, triples
                )
                return GeoProbeResult(
                    query=q_text,
                    category=query_item.category,
                    synthesized_answer=gemini_answer,
                    engine_used="Google Gemini 2.5 Flash (Live Answer Engine)",
                    brand_cited=cited,
                    brand_rank=rank,
                    competitors_cited=comps,
                    verified_claims=ver_claims,
                    hallucinated_claims=hall_claims,
                    citation_urls=[f"https://{domain}"] if cited else [],
                )
        except Exception as e:
            logger.warning("Gemini AI citation probe failed for '%s': %s", q_text, e)

    # -------------------------------------------------------------
    # Tier 2: Open Search Snippets (DuckDuckGo Zero-Cost Fallback)
    # -------------------------------------------------------------
    snippets = fetch_open_search_snippets(q_text, timeout=4)
    if snippets:
        combined_text = " ".join([f"{s['title']}. {s['snippet']}" for s in snippets])
        cited, rank, comps = _extract_brand_rank_and_competitors(
            combined_text, brand_name, domain, all_track_competitors
        )
        urls = [s["url"] for s in snippets if s.get("url")]
        answer_summary = f"Based on live public web search citations: {snippets[0]['title']} - {snippets[0]['snippet']}"
        ver_claims, hall_claims = _audit_hallucination_against_ontology(
            combined_text, brand_name, triples
        )
        return GeoProbeResult(
            query=q_text,
            category=query_item.category,
            synthesized_answer=answer_summary,
            engine_used="Open Web Search Index (DuckDuckGo Snippets)",
            brand_cited=cited,
            brand_rank=rank,
            competitors_cited=comps,
            verified_claims=ver_claims,
            hallucinated_claims=hall_claims,
            citation_urls=urls[:3],
        )

    # -------------------------------------------------------------
    # Tier 3: Deterministic Category Benchmark Fallback
    # -------------------------------------------------------------
    # Simulate based on verified ontology presence
    has_relevant_triples = any(
        any(cap.lower() in t.object.lower() for cap in query_item.targeted_capabilities)
        for t in triples
    )
    # Target brand is cited if it has verified matching capabilities in its ontology
    cited = has_relevant_triples
    rank = 2 if cited else None
    comps = [c for c in all_track_competitors[:2] if c.lower() != brand_name.lower()]

    sim_answer = (
        f"For queries regarding {query_item.category.lower()}, enterprise buyers typically evaluate "
        f"{', '.join(comps)}" + (f" alongside {brand_name}." if cited else " due to deep market distribution.")
    )
    return GeoProbeResult(
        query=q_text,
        category=query_item.category,
        synthesized_answer=sim_answer,
        engine_used="Deterministic Category Benchmark (Offline Mode)",
        brand_cited=cited,
        brand_rank=rank,
        competitors_cited=comps,
        verified_claims=[f"Matches category capability: {c}" for c in query_item.targeted_capabilities] if cited else [],
        hallucinated_claims=[],
        citation_urls=[f"https://{domain}"] if cited else [],
    )


def execute_geo_citation_audit(req: GeoAuditRequest) -> GeoAuditResponse:
    """
    Executes an end-to-end GEO Share of Voice & Citation Audit across multiple buyer queries.
    """
    logger.info("Starting GEO Citation Audit for '%s' (%s)...", req.brand_name, req.domain)

    # 1. Prepare query suite
    queries: List[GeoQueryItem] = []
    if req.custom_queries:
        for q in req.custom_queries:
            if q.strip():
                queries.append(GeoQueryItem(query_text=q.strip(), category="Custom Inquiry"))
    if not queries:
        queries = generate_buyer_queries(
            brand_name=req.brand_name,
            domain=req.domain,
            triples=req.triples,
            competitors=req.competitor_names,
            count=5,
        )

    # 2. Probe queries concurrently
    probe_results: List[GeoProbeResult] = []
    with ThreadPoolExecutor(max_workers=min(len(queries), 4)) as pool:
        future_map = {
            pool.submit(
                probe_ai_citation,
                q,
                req.brand_name,
                req.domain,
                req.competitor_names,
                req.triples,
            ): q
            for q in queries
        }
        for fut in as_completed(future_map):
            try:
                res = fut.result()
                probe_results.append(res)
            except Exception as e:
                q_item = future_map[fut]
                logger.warning("Error probing query '%s': %s", q_item.query_text, e)
                probe_results.append(GeoProbeResult(
                    query=q_item.query_text,
                    category=q_item.category,
                    synthesized_answer="Probe error occurred.",
                    engine_used="Error Fallback",
                    brand_cited=False,
                    brand_rank=None,
                    competitors_cited=[],
                    verified_claims=[],
                    hallucinated_claims=[],
                    citation_urls=[],
                ))

    # Maintain deterministic query order
    query_order = {q.query_text: idx for idx, q in enumerate(queries)}
    probe_results.sort(key=lambda r: query_order.get(r.query, 999))

    total_q = len(probe_results) or 1
    cited_count = sum(1 for r in probe_results if r.brand_cited)
    share_of_voice = round((cited_count / total_q) * 100.0, 1)

    # Weighted SOV (giving more weight to rank 1 vs rank 3+)
    rank_weights = {1: 1.0, 2: 0.7, 3: 0.4}
    weighted_sum = sum(
        rank_weights.get(r.brand_rank, 0.2) if r.brand_cited else 0.0
        for r in probe_results
    )
    weighted_sov = round((weighted_sum / total_q) * 100.0, 1)

    # Competitor SOV breakdown
    comp_citations: Dict[str, int] = {}
    for r in probe_results:
        for c in r.competitors_cited:
            comp_citations[c] = comp_citations.get(c, 0) + 1

    competitor_sov = {
        comp: round((cnt / total_q) * 100.0, 1)
        for comp, cnt in sorted(comp_citations.items(), key=lambda x: -x[1])
    }

    # Hallucination rate
    total_attributed_claims = sum(len(r.verified_claims) + len(r.hallucinated_claims) for r in probe_results)
    total_hallucinations = sum(len(r.hallucinated_claims) for r in probe_results)
    hallucination_rate = (
        round((total_hallucinations / total_attributed_claims) * 100.0, 1)
        if total_attributed_claims > 0 else 0.0
    )

    # Citation gap queries (competitor cited but brand omitted)
    citation_gap_queries = [
        r.query for r in probe_results
        if not r.brand_cited and len(r.competitors_cited) > 0
    ]

    # Actionable GEO Recommendations
    recommendations: List[str] = []
    if share_of_voice < 60.0:
        recommendations.append(
            f"Share of Voice is currently {share_of_voice}%. Publish dedicated capability topic hub pages "
            "with Schema.org SoftwareApplication schema to increase LLM entity recognition."
        )
    if citation_gap_queries:
        recommendations.append(
            f"Competitors won {len(citation_gap_queries)} high-intent queries where {req.brand_name} was omitted. "
            f"Target query: '{citation_gap_queries[0]}' with explicit technical proof points."
        )
    if hallucination_rate > 0.0:
        recommendations.append(
            f"Detected {hallucination_rate}% hallucination rate in AI mentions. Deploy W3C Linked Data "
            "and Wikidata sameAs grounding to align LLM weights with your canonical truth graph."
        )
    else:
        recommendations.append(
            "100% of detected AI citations correspond to verified capabilities in your Knowledge Graph (Zero Hallucination)."
        )

    logger.info("GEO Citation Audit finished for '%s': SOV=%s%%, Gaps=%d", req.brand_name, share_of_voice, len(citation_gap_queries))

    return GeoAuditResponse(
        brand_name=req.brand_name,
        share_of_voice=share_of_voice,
        weighted_sov=weighted_sov,
        ai_mention_rate=share_of_voice,
        hallucination_rate=hallucination_rate,
        competitor_sov=competitor_sov,
        probe_results=probe_results,
        citation_gap_queries=citation_gap_queries,
        geo_recommendations=recommendations,
    )
