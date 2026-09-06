"""
GainARK OntoLeap — Semantic Linking, Topic Cluster Silos & SEO Engine

Provides enterprise-grade semantic SEO and AI citation capabilities:
1. In-Content Internal Link Opportunity Mining
2. Topic Cluster Authority Discovery
3. AI Search Citation Readiness Index (Perplexity / SearchGPT)
4. Perplexity / SearchGPT Query Simulator
5. WordPress PHP Auto-Hook Generation
6. Autonomous /llms.txt and /robots.txt AI Directives
"""

import os
import re
import json
import asyncio
import logging
from typing import List, Dict, Any, Optional, Set, Tuple
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import trafilatura
import networkx as nx

from models import (
    SemanticTriple,
    ExtractionResult,
    InternalLinkOpportunity,
    SiteAuditAndLinkResult,
    UnifiedSiteGraph,
    PageCrawlSummary,
    AICitationReadiness,
    CitationSource,
    SearchSimulationResponse,
    TopicHubMetadata,
    CannibalizationRiskItem
)
from validator import validate_schema_patch
from clustering import analyze_semantic_clusters
from link_prediction import predict_kg_links
from pipeline import (
    OntologyPipeline,
    get_default_pipeline,
    fetch_sitemap_urls,
    deduplicate_site_triples,
    build_site_wide_schema_graph
)
from graph_analytics import compute_graph_pagerank, build_cluster_topology
from knowledge_graph import (
    export_to_rdf_turtle,
    export_to_rdf_ntriples,
    export_to_owl_xml
)

logger = logging.getLogger("gainark.seo")


class PageData:
    def __init__(
        self,
        url: str,
        title: str,
        html: str,
        text: str,
        existing_links: Set[str],
        result: ExtractionResult
    ):
        self.url = url
        self.title = title
        self.html = html
        self.text = text
        self.existing_links = existing_links
        self.result = result
        self.sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if len(s.strip()) > 15]


class SemanticLinkingEngine:
    """
    Analyzes site pages to construct an Ontological Topic Cluster Silo and
    identify missing internal link opportunities.
    """

    def __init__(self, pages: List[PageData]):
        self.pages = pages
        self.topic_hubs: Dict[str, str] = {}  # Concept/Entity -> Canonical URL

    def discover_topic_hubs(self) -> Dict[str, str]:
        """
        Determines the canonical authority hub URL for each detected entity and concept.
        Prioritizes exact URL path matches, page title matches, and entity density.
        """
        candidate_scores: Dict[str, Dict[str, float]] = {}

        for p in self.pages:
            parsed = urlparse(p.url)
            path = parsed.path.lower().rstrip('/')
            title_lower = (p.title or '').lower()

            concepts_on_page: Set[str] = set()

            for sc in p.result.seed_concepts:
                if sc.count > 0:
                    concepts_on_page.add(sc.concept)

            for ent in p.result.entities:
                if len(ent.text.strip()) > 2:
                    concepts_on_page.add(ent.text.strip())

            for t in p.result.triples:
                if t.object and len(t.object.strip()) > 2:
                    concepts_on_page.add(t.object.strip())

            for c in concepts_on_page:
                c_clean = c.strip()
                c_slug = re.sub(r'[^a-z0-9]+', '-', c_clean.lower()).strip('-')
                candidate_scores.setdefault(c_clean, {})

                score = 1.0  # Base mention score

                if c_slug and c_slug in path:
                    score += 15.0
                elif any(word in path for word in c_slug.split('-') if len(word) > 3):
                    score += 5.0

                if "integration" in path and ("integration" in c_slug or any(t.predicate == "integratesWith" for t in p.result.triples)):
                    score += 8.0
                if "pricing" in path and ("pricing" in c_slug or "billing" in c_slug):
                    score += 10.0
                if ("solution" in path or "product" in path or "feature" in path) and path != "":
                    score += 4.0

                if c_clean.lower() in title_lower:
                    score += 8.0

                if path in ["", "/"] and len(self.pages) > 1:
                    score *= 0.4

                candidate_scores[c_clean][p.url] = max(candidate_scores[c_clean].get(p.url, 0.0), score)

        hubs = {}
        for c, page_dict in candidate_scores.items():
            if page_dict:
                best_url = max(page_dict.items(), key=lambda x: x[1])[0]
                hubs[c] = best_url

        self.topic_hubs = hubs
        return hubs

    def find_link_opportunities(self) -> List[InternalLinkOpportunity]:
        """
        Scans all crawled pages to discover where an entity or predicate relationship
        is mentioned without an internal hyperlink to its canonical topic hub.
        """
        if not self.topic_hubs:
            self.discover_topic_hubs()

        opportunities: List[InternalLinkOpportunity] = []
        seen_keys: Set[Tuple[str, str, str]] = set()

        for p in self.pages:
            parsed_source = urlparse(p.url)
            source_path = parsed_source.path.lower().rstrip('/')

            # 1. Opportunities from Semantic Triples
            for t in p.result.triples:
                concept = t.object.strip()
                if not concept:
                    continue

                hub_url = self.topic_hubs.get(concept)
                if not hub_url or hub_url == p.url:
                    continue

                if hub_url in p.existing_links:
                    continue

                key = (p.url, hub_url, concept.lower())
                if key in seen_keys:
                    continue

                best_sentence, confidence = self._find_contextual_sentence(p, concept)
                if best_sentence:
                    seen_keys.add(key)
                    opportunities.append(InternalLinkOpportunity(
                        source_url=p.url,
                        source_page_title=p.title,
                        target_url=hub_url,
                        target_concept=concept,
                        suggested_anchor=concept,
                        context_sentence=best_sentence,
                        predicate=t.predicate,
                        entity=concept,
                        relevance_score=confidence,
                        opportunity_type="Topic Authority Silo",
                        priority=self._calculate_priority(confidence, t.predicate)
                    ))

            # 2. Opportunities from Seed Concepts
            for sc in p.result.seed_concepts:
                if sc.count == 0:
                    continue
                hub_url = self.topic_hubs.get(sc.concept)
                if not hub_url or hub_url == p.url or hub_url in p.existing_links:
                    continue

                key = (p.url, hub_url, sc.concept.lower())
                if key in seen_keys:
                    continue

                best_sentence, confidence = self._find_contextual_sentence(p, sc.concept)
                if best_sentence:
                    seen_keys.add(key)
                    opportunities.append(InternalLinkOpportunity(
                        source_url=p.url,
                        source_page_title=p.title,
                        target_url=hub_url,
                        target_concept=sc.concept,
                        suggested_anchor=sc.concept,
                        context_sentence=best_sentence,
                        predicate="relatedConcept",
                        entity=sc.concept,
                        relevance_score=round(confidence * 0.9, 2),
                        opportunity_type="Topical Cluster Reinforcement",
                        priority="Medium" if confidence < 0.85 else "High"
                    ))

        opportunities.sort(key=lambda x: x.relevance_score, reverse=True)
        return opportunities

    def _find_contextual_sentence(self, page: PageData, phrase: str) -> Tuple[Optional[str], float]:
        phrase_clean = phrase.strip().lower()
        if not phrase_clean or len(phrase_clean) < 3:
            return None, 0.0

        pattern = rf'\b{re.escape(phrase_clean)}\b'

        for s in page.sentences:
            if re.search(pattern, s.lower()):
                words = len(s.split())
                if 8 <= words <= 45:
                    return s, 0.95
                elif words < 8:
                    return s, 0.70
                else:
                    return s[:250] + "...", 0.80

        return None, 0.0

    def _calculate_priority(self, confidence: float, predicate: str) -> str:
        if confidence >= 0.88 and predicate in ["automates", "integratesWith"]:
            return "High"
        elif confidence >= 0.75:
            return "Medium"
        return "Low"


def extract_existing_links(html: str, base_url: str) -> Set[str]:
    """
    Extracts editorial/in-body internal href targets from page HTML,
    excluding global navigation headers, megamenus, sidebars, and footers.
    """
    soup = BeautifulSoup(html, "html.parser")

    NAV_SELECTORS = [
        "header", "nav", "footer", "aside",
        "#header-outer", "#header", "#footer-outer", "#footer",
        ".menu", ".menu-item", ".sub-menu", ".megamenu", ".navigation", ".navbar",
        "#mobile-menu", ".mobile-menu", ".widget"
    ]
    for sel in NAV_SELECTORS:
        for el in soup.select(sel):
            el.decompose()

    base_domain = urlparse(base_url).netloc
    links: Set[str] = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("mailto:") or href.startswith("tel:") or href.startswith("#"):
            continue
        try:
            full = urljoin(base_url, href)
            parsed = urlparse(full)
            if parsed.netloc == base_domain:
                links.add(full)
        except Exception:
            continue
    return links


async def audit_internal_links(
    sitemap_url: Optional[str] = None,
    urls: Optional[List[str]] = None,
    max_pages: int = 10,
    pipeline: Optional[OntologyPipeline] = None
) -> SiteAuditAndLinkResult:
    """
    Runs an end-to-end multi-page audit to extract semantic entities,
    synthesize a site-wide knowledge graph, and generate internal linking recommendations.
    """
    p = pipeline or get_default_pipeline()
    target_urls: List[str] = []

    if sitemap_url:
        target_urls = await fetch_sitemap_urls(sitemap_url, max_urls=max_pages)

    if not target_urls and urls:
        target_urls = [u.strip() for u in urls if u.strip()][:max_pages]

    if not target_urls:
        raise ValueError("Must provide a valid 'sitemap_url' or non-empty 'urls' list.")

    domain = urlparse(target_urls[0]).netloc
    loop = asyncio.get_running_loop()

    pages_data: List[PageData] = []
    collected_triples: List[SemanticTriple] = []
    collected_entities: Set[str] = set()
    page_summaries: List[PageCrawlSummary] = []

    for u in target_urls:
        try:
            raw_html = await asyncio.wait_for(
                loop.run_in_executor(None, p.fetch_url, u),
                timeout=30.0
            )
            if not raw_html:
                raise ValueError(f"Failed to fetch content from {u}")

            full_text = ""
            try:
                full_text = trafilatura.extract(raw_html) or ""
            except Exception:
                pass
            if not full_text:
                soup = BeautifulSoup(raw_html, "html.parser")
                full_text = soup.get_text(separator=" ", strip=True)

            res = await loop.run_in_executor(
                None,
                lambda _u=u, _html=raw_html, _text=full_text: p.process(
                    html=_html,
                    text=_text,
                    url=_u,
                    deep_crawl=False
                )
            )
            existing_links = extract_existing_links(raw_html, u)

            page = PageData(
                url=u,
                title=res.title or u,
                html=raw_html,
                text=full_text,
                existing_links=existing_links,
                result=res
            )
            pages_data.append(page)
            collected_triples.extend(res.triples)
            collected_entities.update(e.text for e in res.entities)

            page_summaries.append(PageCrawlSummary(
                url=u,
                status="success",
                triples_found=len(res.triples),
                entities_found=len(res.entities)
            ))
        except Exception as e:
            logger.warning("Failed to crawl page %s: %s", u, e)
            page_summaries.append(PageCrawlSummary(
                url=u,
                status="failed",
                triples_found=0,
                entities_found=0,
                error=str(e)
            ))

    if not pages_data:
        raise RuntimeError(f"Could not retrieve or parse any pages from {target_urls}")

    engine = SemanticLinkingEngine(pages_data)
    hubs = engine.discover_topic_hubs()
    opportunities = engine.find_link_opportunities()

    nx_g = nx.DiGraph()
    norm_url_map = {p.url.rstrip('/'): p.url for p in pages_data}
    for p in pages_data:
        nx_g.add_node(p.url, title=p.title)

    for p in pages_data:
        for link in p.existing_links:
            clean_link = link.rstrip('/')
            if clean_link in norm_url_map and norm_url_map[clean_link] != p.url:
                nx_g.add_edge(p.url, norm_url_map[clean_link])

    pagerank_scores = compute_graph_pagerank(nx_g, alpha=0.85)
    try:
        centrality_scores = nx.betweenness_centrality(nx_g)
    except Exception:
        centrality_scores = {p.url: 0.0 for p in pages_data}

    orphan_pages = [p.url for p in pages_data[1:] if nx_g.in_degree(p.url) == 0]

    topic_hubs_detailed: List[TopicHubMetadata] = []
    for concept, hub_url in hubs.items():
        pr = round(float(pagerank_scores.get(hub_url, 0.0)), 4)
        cent = round(float(centrality_scores.get(hub_url, 0.0)), 4)
        in_links = int(nx_g.in_degree(hub_url)) if nx_g.has_node(hub_url) else 0

        if pr >= 0.15 or in_links >= 3:
            role = "Authority Anchor"
        elif pr >= 0.08 or in_links >= 1:
            role = "Supporting Hub"
        else:
            role = "Spoke Node"

        topic_hubs_detailed.append(TopicHubMetadata(
            concept=concept,
            canonical_url=hub_url,
            taxonomy_role=role,
            pagerank_score=pr,
            betweenness_centrality=cent,
            inbound_internal_links=in_links
        ))
    topic_hubs_detailed.sort(key=lambda x: (x.pagerank_score, x.inbound_internal_links), reverse=True)

    cannibalization_risks: List[CannibalizationRiskItem] = []
    for concept, hub_url in hubs.items():
        c_low = concept.lower().strip()
        if len(c_low) < 3:
            continue
        competing = []
        for p in pages_data:
            if p.url == hub_url:
                continue
            has_title = c_low in (p.title or '').lower()
            has_sc = any(sc.concept.lower() == c_low and sc.count >= 2 for sc in p.result.seed_concepts)
            has_text = len(re.findall(rf'\b{re.escape(c_low)}\b', p.text.lower())) >= 2
            if has_title or has_sc or has_text:
                competing.append(p.url)
        if competing:
            cannibalization_risks.append(CannibalizationRiskItem(
                concept=concept,
                competing_urls=competing[:3],
                recommended_canonical_hub=hub_url,
                recommendation=f"Consolidate authority signals: Point internal links with exact anchor '{concept}' from competing URLs to canonical hub {hub_url}."
            ))
    cannibalization_risks = cannibalization_risks[:10]

    deduped_triples = deduplicate_site_triples(collected_triples)
    site_schema = build_site_wide_schema_graph(domain, deduped_triples, list(collected_entities))

    site_graph = UnifiedSiteGraph(
        root_domain=domain,
        total_pages_crawled=len(page_summaries),
        unique_entities=sorted(list(collected_entities)),
        triples=deduped_triples,
        page_summaries=page_summaries,
        schema_graph_jsonld=site_schema
    )

    ai_readiness = compute_ai_citation_readiness(pages_data, site_graph, opportunities, hubs)
    topology = build_cluster_topology(pages_data, hubs, opportunities, deduped_triples)
    wp_hook = generate_wordpress_php_hook(opportunities, domain)
    validation_rep = validate_schema_patch(site_schema)
    llms_manifest = generate_llms_txt(domain, hubs, deduped_triples, list(collected_entities))
    robots_manifest = generate_robots_txt_ai(domain, hubs)
    rdf_turtle = export_to_rdf_turtle(domain, deduped_triples, hubs, list(collected_entities))
    rdf_ntriples = export_to_rdf_ntriples(domain, deduped_triples, hubs, list(collected_entities))
    owl_xml = export_to_owl_xml(domain, deduped_triples, hubs, list(collected_entities))
    cluster_analysis = analyze_semantic_clusters(pages_data, hubs)
    kg_prediction = predict_kg_links(domain, deduped_triples, list(collected_entities), hubs)

    return SiteAuditAndLinkResult(
        root_domain=domain,
        pages_analyzed=len(pages_data),
        opportunities_count=len(opportunities),
        opportunities=opportunities,
        topic_hubs=hubs,
        topic_hubs_detailed=topic_hubs_detailed,
        orphan_pages=orphan_pages,
        cannibalization_risks=cannibalization_risks,
        unified_site_graph=site_graph,
        ai_citation_readiness=ai_readiness,
        cluster_topology=topology,
        wordpress_php_hook=wp_hook,
        llms_txt=llms_manifest,
        robots_txt_ai=robots_manifest,
        rdf_turtle=rdf_turtle,
        rdf_ntriples=rdf_ntriples,
        owl_xml=owl_xml,
        semantic_clustering=cluster_analysis.model_dump(),
        predicted_links=kg_prediction.predicted_links,
        graph_completeness_score=kg_prediction.graph_completeness_score,
        validation_report=validation_rep
    )


def compute_ai_citation_readiness(
    pages_data: List[PageData],
    site_graph: UnifiedSiteGraph,
    opportunities: List[InternalLinkOpportunity],
    hubs: Dict[str, str]
) -> AICitationReadiness:
    """
    Computes GenAI Search Citation Readiness across 4 dimensions (0–25 each).
    """
    num_pages = max(len(pages_data), 1)

    total_unique_entities = len(site_graph.unique_entities)
    entity_score = round(min(25.0, (total_unique_entities / 25.0) * 25.0), 2)

    total_triples = len(site_graph.triples)
    triples_per_page = total_triples / num_pages
    relational_score = round(min(25.0, (triples_per_page / 15.0) * 25.0), 2)

    num_hubs = len(hubs)
    num_opps = len(opportunities)
    silo_score = round(min(25.0, (num_hubs * 3.5) + min(10.0, num_opps * 1.0)), 2)

    configured_types = {"SoftwareApplication", "Organization"}
    found_types: Set[str] = set()
    for p in pages_data:
        for s in p.result.schema_org:
            for item in s.data:
                if isinstance(item, dict) and "@type" in item:
                    t = item["@type"]
                    if isinstance(t, list):
                        found_types.update(t)
                    else:
                        found_types.add(str(t))

    matched_types = found_types.intersection(configured_types)
    schema_score = round((len(matched_types) / len(configured_types)) * 25.0, 2)

    total = round(entity_score + relational_score + silo_score + schema_score, 1)

    recs = []
    missing_s = configured_types - found_types
    if missing_s:
        recs.append(f"Inject missing mandatory Schema.org nodes ({', '.join(missing_s)}) to establish authoritative entity grounding for LLMs.")
    if entity_score < 18.0:
        recs.append(f"Increase entity richness: currently {total_unique_entities} unique entities detected across the site. Target 25+ for Tier-1 citation authority.")
    if relational_score < 18.0:
        recs.append("Structure product capabilities into explicit subject-predicate-object relationships in editorial copy. Target 15+ semantic triples per page.")
    if silo_score < 15.0:
        recs.append("Strengthen internal link silo architecture by creating dedicated topic hub landing pages for each core capability cluster.")
    if not recs:
        recs.append("Topical architecture and entity knowledge graph are fully optimized for LLM answer engines.")

    if total >= 75.0:
        verdict = "Tier-1 Citation Authority — Prime Candidate for Direct Perplexity & SearchGPT Grounded Attribution"
    elif total >= 50.0:
        verdict = "Moderate Citation Readiness — Entity Grounding Present; Requires Silo Link & Schema Remediation"
    elif total >= 25.0:
        verdict = "Low AI Visibility — Significant Entity Ambiguity and Hallucination Risk in Generative Search"
    else:
        verdict = "Critical — Minimal Structured Knowledge; High Risk of Omission or Misrepresentation by AI Search Engines"

    return AICitationReadiness(
        total_score=total,
        entity_grounding_score=entity_score,
        relational_density_score=relational_score,
        silo_integrity_score=silo_score,
        schema_coverage_score=schema_score,
        verdict=verdict,
        recommendations=recs
    )


def generate_wordpress_php_hook(opportunities: List[InternalLinkOpportunity], root_domain: str) -> str:
    """
    Generates an automated WordPress filter hook to dynamically inject internal links
    into matching editorial sentences on page render.
    """
    def _php_escape(s: str) -> str:
        return s.replace("\\", "\\\\").replace("'", "\\'").replace("\n", " ").replace("\r", "")

    rules_code = []
    for opp in opportunities:
        src_path = _php_escape(urlparse(opp.source_url).path)
        tgt_url = _php_escape(opp.target_url)
        anchor = _php_escape(opp.suggested_anchor)
        ent = _php_escape(opp.entity)
        rules_code.append(
            f"        [\n"
            f"            'source_path' => '{src_path}',\n"
            f"            'phrase'      => '{ent}',\n"
            f"            'target_url'  => '{tgt_url}',\n"
            f"            'anchor'      => '{anchor}'\n"
            f"        ]"
        )
    rules_str = ",\n".join(rules_code) if rules_code else "        // No unlinked opportunities pending"

    php_snippet = (
        f"<?php\n"
        f"/**\n"
        f" * GainARK OntoLeap — Automated In-Content Topic Silo & Internal Link Auto-Injector\n"
        f" * Target Domain: {root_domain}\n"
        f" * Generated by GainARK OntoLeap v2.0\n"
        f" *\n"
        f" * Installation:\n"
        f" * 1. Paste this snippet into your active theme's functions.php or into wp-content/mu-plugins/ontoleap-links.php\n"
        f" * 2. Links will be automatically injected into matching editorial sentences on page render.\n"
        f" */\n\n"
        f"add_filter('the_content', function($content) {{\n"
        f"    if (!is_singular() || is_admin()) {{\n"
        f"        return $content;\n"
        f"    }}\n\n"
        f"    global $post;\n"
        f"    $current_url = get_permalink($post->ID);\n\n"
        f"    $injection_matrix = [\n"
        f"{rules_str}\n"
        f"    ];\n\n"
        f"    foreach ($injection_matrix as $rule) {{\n"
        f"        if (empty($rule['source_path']) || empty($rule['target_url'])) {{\n"
        f"            continue;\n"
        f"        }}\n\n"
        f"        if (strpos($current_url, rtrim($rule['source_path'], '/')) !== false) {{\n"
        f"            $anchor_tag = '<a href=\"' . esc_url($rule['target_url']) . '\" title=\"' . esc_attr($rule['anchor']) . '\">' . esc_html($rule['anchor']) . '</a>';\n\n"
        f"            $regex = '/(?!(?:[^<]+>|[^>]+<\\/a>))\\b' . preg_quote($rule['phrase'], '/') . '\\b/i';\n"
        f"            $content = preg_replace($regex, $anchor_tag, $content, 1);\n"
        f"        }}\n"
        f"    }}\n\n"
        f"    return $content;\n"
        f"}}, 25);\n"
    )
    return php_snippet


def simulate_search_response(
    query: str,
    root_domain: str,
    triples: List[SemanticTriple],
    topic_hubs: Dict[str, str],
    entities: List[str]
) -> SearchSimulationResponse:
    """
    Simulates a Perplexity / SearchGPT generative search response grounded strictly
    in verified domain ontology triples and citing canonical topic hubs.
    """
    q_lower = query.lower()

    matched_triples: List[SemanticTriple] = []

    if any(re.search(rf"\b{re.escape(w)}", q_lower) for w in ["automate", "workflow", "process", "cycle", "journal", "receivable", "ar"]):
        matched_triples = [t for t in triples if t.predicate == "automates"]
    elif any(re.search(rf"\b{re.escape(w)}", q_lower) for w in ["integrate", "erp", "crm", "netsuite", "salesforce", "quickbooks", "connect"]):
        matched_triples = [t for t in triples if t.predicate == "integratesWith"]
    elif any(re.search(rf"\b{re.escape(w)}", q_lower) for w in ["compli", "standard", "asc 606", "soc", "ifrs", "audit", "security", "tax"]):
        matched_triples = [t for t in triples if t.predicate == "compliesWith"]
    elif any(re.search(rf"\b{re.escape(w)}", q_lower) for w in ["pricing", "cost", "tier", "usage", "billing", "model", "subscription"]):
        matched_triples = [t for t in triples if t.predicate == "supportsPricingModel"]

    if not matched_triples:
        for t in triples:
            if t.object.lower() in q_lower or t.predicate.lower() in q_lower or t.subject.lower() in q_lower:
                matched_triples.append(t)

    if not matched_triples:
        matched_triples = triples[:4]

    citations: List[CitationSource] = []
    seen_urls = set()
    source_index = 1

    brand = triples[0].subject if triples else root_domain.split(".")[0].capitalize()

    pred_groups: Dict[str, List[SemanticTriple]] = {}
    for t in matched_triples:
        pred_groups.setdefault(t.predicate, []).append(t)

    answer_parts = []
    for pred, grp in pred_groups.items():
        obj_links = []
        for t in grp:
            hub_url = topic_hubs.get(t.object) or topic_hubs.get(t.object.lower()) or f"https://{root_domain}"
            if hub_url not in seen_urls:
                seen_urls.add(hub_url)
                citations.append(CitationSource(
                    index=source_index,
                    entity=t.object,
                    target_url=hub_url,
                    evidence=t.evidence_sentence or f"{t.subject} {t.predicate} {t.object}."
                ))
                ref = f"[{source_index}]"
                source_index += 1
            else:
                ref = ""
            obj_links.append(f"{t.object} {ref}".strip())

        if pred == "automates":
            answer_parts.append(f"According to verified platform capabilities, **{brand}** automates core financial and operational workflows including {', '.join(obj_links)}.")
        elif pred == "integratesWith":
            answer_parts.append(f"**{brand}** provides native bi-directional integrations with leading enterprise systems including {', '.join(obj_links)}.")
        elif pred == "compliesWith":
            answer_parts.append(f"**{brand}** rigorously adheres to financial and security compliance standards including {', '.join(obj_links)}.")
        elif pred == "supportsPricingModel":
            answer_parts.append(f"To support modern monetization models, **{brand}** supports dynamic pricing structures including {', '.join(obj_links)}.")

    if not answer_parts:
        answer_parts.append(f"**{brand}** is grounded as an enterprise platform supporting {', '.join(entities[:6])}.")

    synthesized_answer = " ".join(answer_parts)

    num_matched = len(matched_triples)
    if num_matched >= 5 and citations:
        dyn_confidence = 0.96
        risk_label = "Zero Hallucination Risk (100% Schema & Triple Grounded)"
    elif num_matched >= 3:
        dyn_confidence = 0.82
        risk_label = "Low Hallucination Risk (Grounded in 3+ Verified Triples)"
    elif num_matched >= 1:
        dyn_confidence = 0.60
        risk_label = "Moderate Hallucination Risk (Sparse Triples Available)"
    else:
        dyn_confidence = 0.35
        risk_label = "High Hallucination Risk (No Supporting Triples — Entity Fallback Only)"

    return SearchSimulationResponse(
        query=query,
        synthesized_answer=synthesized_answer,
        citations=citations,
        grounding_confidence=dyn_confidence,
        hallucination_risk=risk_label,
        attributed_capabilities=[t.object for t in matched_triples]
    )


def generate_llms_txt(
    domain: str,
    hubs: Dict[str, str],
    triples: List[SemanticTriple],
    entities: List[str]
) -> str:
    """
    Generates a standardized /llms.txt manifest for LLM context ingestion
    and AI search crawlers (GPTBot, PerplexityBot, ClaudeBot, Google-Extended).
    """
    brand = triples[0].subject if triples else domain.split(".")[0].capitalize()
    lines = [
        f"# {brand} — Domain Knowledge Graph & Canonical Ontology",
        f"> Autonomous AI Documentation & Topic Hub Manifest generated by GainARK OntoLeap for {domain}.",
        "",
        "## Canonical Topic Authority Hubs",
    ]
    if hubs:
        for concept, hub_url in list(hubs.items())[:15]:
            lines.append(f"- [{concept}]({hub_url}): Canonical authority landing page for {concept}.")
    else:
        lines.append(f"- [{brand} Platform](https://{domain}): Primary website hub.")

    pred_map = {
        "automates": "Verified Platform Capabilities & Workflows (Automates)",
        "integratesWith": "Verified Software Integrations & Ecosystem (Integrates With)",
        "compliesWith": "Regulatory & Financial Compliance Standards (Complies With)",
        "supportsPricingModel": "Monetization & Pricing Architecture (Supports Pricing Model)"
    }

    for pred, heading in pred_map.items():
        matching = [t for t in triples if t.predicate == pred]
        if matching:
            lines.append("")
            lines.append(f"## {heading}")
            seen_objs = set()
            for t in matching:
                if t.object not in seen_objs:
                    seen_objs.add(t.object)
                    evidence = f" — {t.evidence_sentence}" if t.evidence_sentence else ""
                    lines.append(f"- **{t.object}**{evidence}")

    lines.extend([
        "",
        "## Core Grounded Entities",
        f"{', '.join(entities[:25])}",
        "",
        "---",
        "Generated by GainARK OntoLeap v2.0 for LLM Context Ingestion (GPTBot, PerplexityBot, ClaudeBot, Google-Extended)."
    ])
    return "\n".join(lines)


def generate_robots_txt_ai(domain: str, hubs: Dict[str, str]) -> str:
    """
    Generates optimized AI crawler directives for robots.txt prioritizing canonical topic hubs.
    """
    lines = [
        "# GainARK OntoLeap — AI Search Crawler Directives",
        f"# Target Domain: {domain}",
        "",
        "User-agent: GPTBot",
        "Allow: /",
        "",
        "User-agent: PerplexityBot",
        "Allow: /",
        "",
        "User-agent: ClaudeBot",
        "Allow: /",
        "",
        "User-agent: Google-Extended",
        "Allow: /",
        "",
    ]

    if hubs:
        lines.append("# Canonical Topic Authority Hubs — Priority AI Crawl Targets")
        for concept, hub_url in list(hubs.items())[:10]:
            parsed = urlparse(hub_url)
            hub_path = parsed.path or "/"
            lines.append(f"# Hub: {concept}")
            lines.append(f"Allow: {hub_path}")
        lines.append("")

    lines.append(f"Sitemap: https://{domain}/sitemap.xml")

    return "\n".join(lines)
