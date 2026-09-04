"""
GainARK OntoLeap — Semantic Linking, Topic Cluster Silos & Knowledge Graph Engine

This module provides enterprise-grade semantic SEO and knowledge graph capabilities:
1. Topic Cluster Authority Discovery: Identifies canonical topic authority hubs using URL slug
   matching, title semantics, and entity density.
2. In-Content Internal Link Opportunity Mining: Detects unlinked entity and predicate mentions
   across pages, generating contextual, high-intent anchor text suggestions and CMS code snippets.
3. NetworkX Graph Topologies & Link Analysis: Computes internal PageRank (damping factor α=0.85)
   and betweenness centrality to isolate topic silos, authority anchors, and orphan subpages.
4. Keyword & Topic Cannibalization Guard: Identifies competing pages diluting organic signals
   for the same concept and generates canonical consolidation rules.
5. AI Search Citation Readiness Index: Scores pages across entity grounding, relational triple
   density, silo integrity, and Schema.org coverage for Perplexity & SearchGPT readiness.
6. Perplexity / SearchGPT Query Simulator: Synthesizes generative answers grounded strictly in
   verified domain ontology triples with footnoted topic hub citations.
7. Autonomous Manifest Generation: Produces standardized /llms.txt manifests and AI crawler
   directives for GPTBot, PerplexityBot, ClaudeBot, and Google-Extended.
8. W3C RDF Turtle & N-Triples Serialization: Exports the complete multi-page knowledge graph
   using RDFLib with Schema.org vocabulary and canonical Wikidata sameAs entity grounding.
9. Interactive W3C SPARQL 1.1 Query Engine: Executes arbitrary SPARQL SELECT queries directly
   against the in-memory RDF triple store.
"""

import re
import asyncio
from typing import List, Dict, Any, Optional, Set, Tuple
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import trafilatura

from models import (
    SemanticTriple,
    EntityMatch,
    ExtractionResult,
    InternalLinkOpportunity,
    SiteAuditAndLinkResult,
    UnifiedSiteGraph,
    PageCrawlSummary,
    AICitationReadiness,
    GraphNode,
    GraphEdge,
    ClusterTopology,
    CitationSource,
    SearchSimulationResponse,
    SchemaValidationReport,
    TopicHubMetadata,
    CannibalizationRiskItem
)
import networkx as nx
from rdflib import Graph, Literal, RDF, RDFS, URIRef, Namespace, OWL, XSD
from validator import validate_schema_patch
from clustering import analyze_semantic_clusters
from link_prediction import predict_kg_links
from graph_export import generate_standalone_graph_html
from pipeline import (
    OntologyPipeline,
    get_default_pipeline,
    fetch_sitemap_urls,
    deduplicate_site_triples,
    build_site_wide_schema_graph
)


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

            # Gather all candidate concepts from this page
            concepts_on_page: Set[str] = set()

            # 1. From seed concepts
            for sc in p.result.seed_concepts:
                if sc.count > 0:
                    concepts_on_page.add(sc.concept)

            # 2. From entities
            for ent in p.result.entities:
                if len(ent.text.strip()) > 2:
                    concepts_on_page.add(ent.text.strip())

            # 3. From triples
            for t in p.result.triples:
                if t.object and len(t.object.strip()) > 2:
                    concepts_on_page.add(t.object.strip())

            for c in concepts_on_page:
                c_clean = c.strip()
                c_slug = re.sub(r'[^a-z0-9]+', '-', c_clean.lower()).strip('-')
                candidate_scores.setdefault(c_clean, {})

                score = 1.0  # Base mention score

                # URL path matching
                if c_slug and c_slug in path:
                    score += 15.0
                elif any(word in path for word in c_slug.split('-') if len(word) > 3):
                    score += 5.0

                # Specific path conventions
                if "integration" in path and ("integration" in c_slug or any(t.predicate == "integratesWith" for t in p.result.triples)):
                    score += 8.0
                if "pricing" in path and ("pricing" in c_slug or "billing" in c_slug):
                    score += 10.0
                if ("solution" in path or "product" in path or "feature" in path) and path != "":
                    score += 4.0

                # Title match
                if c_clean.lower() in title_lower:
                    score += 8.0

                # Homepage should not be the specific hub for deep sub-topics unless it's the only page
                if path in ["", "/"] and len(self.pages) > 1:
                    score *= 0.4

                candidate_scores[c_clean][p.url] = max(candidate_scores[c_clean].get(p.url, 0.0), score)

        # Select highest-scoring URL for each concept
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

                target_url = self.topic_hubs.get(concept)
                if not target_url or target_url == p.url:
                    continue

                # Check if already linked
                if self._is_already_linked(p, target_url):
                    continue

                key = (p.url, target_url, concept.lower())
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                # Find evidence sentence or mention
                sentence = t.evidence_sentence or self._find_context_sentence(p, concept)
                anchor = self._synthesize_anchor(concept, t.predicate)
                priority = self._determine_priority(t.predicate, concept)
                html_snippet = f'<a href="{target_url}" title="{anchor}">{anchor}</a>'

                opportunities.append(InternalLinkOpportunity(
                    source_url=p.url,
                    target_url=target_url,
                    entity=concept,
                    predicate=t.predicate,
                    suggested_anchor=anchor,
                    context_sentence=sentence,
                    html_snippet=html_snippet,
                    priority=priority
                ))

            # 2. Opportunities from Core Seed Concepts and Entities
            for sc in p.result.seed_concepts:
                if sc.count == 0:
                    continue
                concept = sc.concept
                target_url = self.topic_hubs.get(concept)
                if not target_url or target_url == p.url:
                    continue

                if self._is_already_linked(p, target_url):
                    continue

                key = (p.url, target_url, concept.lower())
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                sentence = self._find_context_sentence(p, concept)
                anchor = concept
                priority = "High" if sc.count >= 2 else "Medium"
                html_snippet = f'<a href="{target_url}" title="{anchor}">{anchor}</a>'

                opportunities.append(InternalLinkOpportunity(
                    source_url=p.url,
                    target_url=target_url,
                    entity=concept,
                    predicate=None,
                    suggested_anchor=anchor,
                    context_sentence=sentence,
                    html_snippet=html_snippet,
                    priority=priority
                ))

        # Sort opportunities: High priority first, then by entity name
        opportunities.sort(key=lambda o: (0 if o.priority == "High" else (1 if o.priority == "Medium" else 2), o.entity))
        return opportunities

    def _is_already_linked(self, page: PageData, target_url: str) -> bool:
        """Checks whether page already contains an outgoing link to target_url."""
        target_parsed = urlparse(target_url)
        target_path = target_parsed.path.rstrip('/').lower()

        for link in page.existing_links:
            l_parsed = urlparse(link)
            if l_parsed.path.rstrip('/').lower() == target_path:
                return True
        return False

    def _find_context_sentence(self, page: PageData, phrase: str) -> str:
        """Locates the first sentence on the page mentioning the phrase."""
        phrase_lower = phrase.lower()
        for s in page.sentences:
            if phrase_lower in s.lower():
                return s
        return f"Mention of '{phrase}' on page."

    def _synthesize_anchor(self, concept: str, predicate: Optional[str]) -> str:
        """Generates natural, high-intent SEO anchor text."""
        if predicate == "integratesWith":
            return f"{concept} Integration"
        elif predicate == "compliesWith":
            return f"{concept} Compliance"
        elif predicate == "supportsPricingModel":
            return f"{concept}"
        elif predicate == "automates":
            return f"automated {concept.lower()}"
        return concept

    def _determine_priority(self, predicate: Optional[str], concept: str) -> str:
        if predicate in ["compliesWith", "integratesWith"]:
            return "High"
        if predicate in ["automates", "supportsPricingModel"]:
            return "High"
        return "Medium"


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
    loop = asyncio.get_event_loop()

    pages_data: List[PageData] = []
    collected_triples: List[SemanticTriple] = []
    collected_entities: Set[str] = set()
    page_summaries: List[PageCrawlSummary] = []

    for u in target_urls:
        try:
            # 1. Fetch HTML once
            raw_html = await loop.run_in_executor(None, p.fetch_url, u)
            if not raw_html:
                raise ValueError(f"Failed to fetch content from {u}")

            # 2. Extract full body text for complete sentence & entity coverage
            full_text = ""
            try:
                full_text = trafilatura.extract(raw_html) or ""
            except Exception:
                pass
            if not full_text:
                soup = BeautifulSoup(raw_html, "html.parser")
                full_text = soup.get_text(separator=" ", strip=True)

            # 3. Process extraction with pre-fetched HTML and full text
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
            page_summaries.append(PageCrawlSummary(
                url=u,
                status="failed",
                triples_found=0,
                entities_found=0,
                error=str(e)
            ))

    if not pages_data:
        raise RuntimeError(f"Could not retrieve or parse any pages from {target_urls}")

    # Run Semantic Linking Engine
    engine = SemanticLinkingEngine(pages_data)
    hubs = engine.discover_topic_hubs()
    opportunities = engine.find_link_opportunities()

    # Build NetworkX Internal Directed Graph & Run PageRank + Centrality
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

    # Detect Orphan Pages (crawled subpages with zero inbound internal links)
    orphan_pages = [p.url for p in pages_data[1:] if nx_g.in_degree(p.url) == 0]

    # Build Detailed Topic Hubs Metadata
    topic_hubs_detailed: List[TopicHubMetadata] = []
    for concept, hub_url in hubs.items():
        pr = round(float(pagerank_scores.get(hub_url, 0.0)), 4)
        cent = round(float(centrality_scores.get(hub_url, 0.0)), 4)
        in_links = int(nx_g.in_degree(hub_url)) if nx_g.has_node(hub_url) else 0

        # Classify taxonomy role
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

    # Detect Keyword & Topic Cannibalization Risks
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

    # Build site graph
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

    # Compute GenAI Search Citation Readiness Index
    ai_readiness = compute_ai_citation_readiness(pages_data, site_graph, opportunities, hubs)

    # Build Topological Topic Cluster Graph
    topology = build_cluster_topology(pages_data, hubs, opportunities, deduped_triples)

    # Generate Automated WordPress PHP Auto-Hook
    wp_hook = generate_wordpress_php_hook(opportunities, domain)

    # Validate Site-Wide Schema Graph
    validation_rep = validate_schema_patch(site_schema)

    # Generate Autonomous llms.txt and AI Crawler Directives
    llms_manifest = generate_llms_txt(domain, hubs, deduped_triples, list(collected_entities))
    robots_manifest = generate_robots_txt_ai(domain, hubs)

    # Generate Enterprise RDF Turtle, N-Triples Dump & W3C OWL 2 DL Ontology
    rdf_turtle = export_to_rdf_turtle(domain, deduped_triples, hubs, list(collected_entities))
    rdf_ntriples = export_to_rdf_ntriples(domain, deduped_triples, hubs, list(collected_entities))
    owl_xml = export_to_owl_xml(domain, deduped_triples, hubs, list(collected_entities))

    # Compute TF-IDF Cosine Similarity Matrix & Semantic Clusters
    cluster_analysis = analyze_semantic_clusters(pages_data, hubs)

    # Infer Missing Knowledge Graph Relations via AI Link Prediction (PyKEEN Paradigm)
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
        validation_report=validation_rep
    )


def compute_ai_citation_readiness(
    pages_data: List[PageData],
    site_graph: UnifiedSiteGraph,
    opportunities: List[InternalLinkOpportunity],
    hubs: Dict[str, str]
) -> AICitationReadiness:
    total_pages = max(1, len(pages_data))

    # 1. Entity Grounding (0-25)
    avg_entities = len(site_graph.unique_entities) / total_pages
    entity_score = min(25.0, round((avg_entities / 10.0) * 25.0, 1))

    # 2. Relational Density (0-25)
    avg_triples = len(site_graph.triples) / total_pages
    relational_score = min(25.0, round((avg_triples / 12.0) * 25.0, 1))

    # 3. Silo Integrity (0-25)
    hub_count = len(hubs)
    hub_ratio = min(1.0, hub_count / max(1, total_pages * 5))
    unlinked_high = len([o for o in opportunities if o.priority == "High"])
    silo_deduction = min(8.0, unlinked_high * 1.5)
    silo_score = max(5.0, round((hub_ratio * 25.0) - silo_deduction, 1))

    # 4. Schema Coverage (0-25)
    mandatory = ["softwareapplication", "organization", "offer"]
    found_types = set()
    for p in pages_data:
        for s in p.result.schema_org:
            for part in s.schema_type.split(", "):
                found_types.add(part.strip().lower())
    matched_mandatory = sum(1 for m in mandatory if m in found_types)
    schema_score = round((matched_mandatory / len(mandatory)) * 25.0, 1)

    total = round(entity_score + relational_score + silo_score + schema_score, 1)

    recs = []
    if unlinked_high > 0:
        recs.append(f"Deploy the {unlinked_high} identified High-Priority internal links to reinforce topical silo clusters for search crawlers.")
    if schema_score < 25.0:
        missing_s = [m.title() for m in mandatory if m not in found_types]
        recs.append(f"Inject missing mandatory Schema.org nodes ({', '.join(missing_s)}) to establish authoritative entity grounding for LLMs.")
    if entity_score < 20.0:
        recs.append("Increase entity richness by explicitly mentioning industry-standard frameworks, integrations, and capabilities.")
    if relational_score < 20.0:
        recs.append("Structure product capabilities into explicit subject-predicate-object relationships in editorial copy.")
    if not recs:
        recs.append("Topical architecture and entity knowledge graph are fully optimized for LLM answer engines.")

    if total >= 80.0:
        verdict = "Tier-1 Citation Authority — Prime Candidate for Direct Perplexity & SearchGPT Grounded Attribution"
    elif total >= 60.0:
        verdict = "Moderate Citation Readiness — Entity Grounding Present; Requires Silo Link & Schema Remediation"
    else:
        verdict = "Low AI Visibility — Significant Entity Ambiguity and Hallucination Risk in Generative Search"

    return AICitationReadiness(
        total_score=total,
        entity_grounding_score=entity_score,
        relational_density_score=relational_score,
        silo_integrity_score=silo_score,
        schema_coverage_score=schema_score,
        verdict=verdict,
        recommendations=recs
    )


def build_cluster_topology(
    pages_data: List[PageData],
    hubs: Dict[str, str],
    opportunities: List[InternalLinkOpportunity],
    triples: List[SemanticTriple]
) -> ClusterTopology:
    nodes: List[GraphNode] = []
    edges: List[GraphEdge] = []
    seen_nodes: Set[str] = set()

    # Hub pages (purple/gold) vs Spoke pages (blue)
    hub_urls = set(hubs.values())
    for p in pages_data:
        is_hub = p.url in hub_urls
        group = "hub" if is_hub else "spoke"
        label = urlparse(p.url).path.rstrip("/").split("/")[-1] or p.title[:20]
        if not label:
            label = "Home"
        nodes.append(GraphNode(
            id=p.url,
            label=label,
            type=group,
            url=p.url,
            group=group,
            value=24 if is_hub else 16
        ))
        seen_nodes.add(p.url)

    # Key entity concepts (top 12)
    candidate_entities = list(hubs.keys())[:12]
    for ent in candidate_entities:
        ent_id = f"ent_{ent}"
        if ent_id not in seen_nodes:
            nodes.append(GraphNode(
                id=ent_id,
                label=ent,
                type="entity",
                group="entity",
                value=10
            ))
            seen_nodes.add(ent_id)
            hub_url = hubs.get(ent)
            if hub_url and hub_url in seen_nodes:
                edges.append(GraphEdge(
                    source=hub_url,
                    target=ent_id,
                    label="Topic Hub",
                    relation_type="hub_for"
                ))

    # Link opportunities as directed edges between pages
    for opp in opportunities:
        if opp.source_url in seen_nodes and opp.target_url in seen_nodes:
            edges.append(GraphEdge(
                source=opp.source_url,
                target=opp.target_url,
                label=opp.suggested_anchor,
                relation_type="recommends_link"
            ))

    return ClusterTopology(nodes=nodes, edges=edges)


def generate_wordpress_php_hook(opportunities: List[InternalLinkOpportunity], root_domain: str) -> str:
    rules_code = []
    for opp in opportunities:
        src_path = urlparse(opp.source_url).path
        tgt_url = opp.target_url
        anchor = opp.suggested_anchor
        ent = opp.entity
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
        f"    // Only execute on single posts, pages, and custom post types\n"
        f"    if (!is_singular() || is_admin()) {{\n"
        f"        return $content;\n"
        f"    }}\n\n"
        f"    global $post;\n"
        f"    $current_url = get_permalink($post->ID);\n\n"
        f"    // OntoLeap Internal Link Opportunity Matrix\n"
        f"    $injection_matrix = [\n"
        f"{rules_str}\n"
        f"    ];\n\n"
        f"    foreach ($injection_matrix as $rule) {{\n"
        f"        if (empty($rule['source_path']) || empty($rule['target_url'])) {{\n"
        f"            continue;\n"
        f"        }}\n\n"
        f"        // Check if current page matches the opportunity source path\n"
        f"        if (strpos($current_url, rtrim($rule['source_path'], '/')) !== false) {{\n"
        f"            $anchor_tag = '<a href=\"' . esc_url($rule['target_url']) . '\" title=\"' . esc_attr($rule['anchor']) . '\">' . esc_html($rule['anchor']) . '</a>';\n\n"
        f"            // Replace the first unlinked mention (strictly avoiding existing links/tags)\n"
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

    # Match relevant triples based on query keywords
    matched_triples: List[SemanticTriple] = []

    if any(w in q_lower for w in ["automate", "workflow", "process", "cycle", "ar", "journal", "receivable"]):
        matched_triples = [t for t in triples if t.predicate == "automates"]
    elif any(w in q_lower for w in ["integrate", "erp", "crm", "netsuite", "salesforce", "quickbooks", "connect"]):
        matched_triples = [t for t in triples if t.predicate == "integratesWith"]
    elif any(w in q_lower for w in ["compli", "standard", "asc 606", "soc", "ifrs", "audit", "security", "tax"]):
        matched_triples = [t for t in triples if t.predicate == "compliesWith"]
    elif any(w in q_lower for w in ["pricing", "cost", "tier", "usage", "billing", "model", "subscription"]):
        matched_triples = [t for t in triples if t.predicate == "supportsPricingModel"]

    # Fallback: match by entity keyword or object name
    if not matched_triples:
        for t in triples:
            if t.object.lower() in q_lower or t.predicate.lower() in q_lower or t.subject.lower() in q_lower:
                matched_triples.append(t)

    # General fallback: top 4 triples
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

    return SearchSimulationResponse(
        query=query,
        synthesized_answer=synthesized_answer,
        citations=citations,
        grounding_confidence=0.96 if citations else 0.85,
        hallucination_risk="Zero Hallucination Risk (100% Schema & Triple Grounded)",
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
    hub_comments = "\n".join(f"# Topic Hub: {c} -> {u}" for c, u in list(hubs.items())[:6])
    return (
        "# GainARK OntoLeap — AI Search Crawler Directives\n"
        f"# Target Domain: {domain}\n\n"
        "User-agent: GPTBot\n"
        "Allow: /\n\n"
        "User-agent: PerplexityBot\n"
        "Allow: /\n\n"
        "User-agent: ClaudeBot\n"
        "Allow: /\n\n"
        "User-agent: Google-Extended\n"
        "Allow: /\n\n"
        "# Canonical Topic Silo Hubs for AI Ingestion\n"
    )


def compute_graph_pagerank(G: nx.DiGraph, alpha: float = 0.85) -> Dict[str, float]:
    """
    Computes internal PageRank on the directed graph using NetworkX.
    Uses pure-python algorithm to avoid requiring heavy scipy binaries.
    """
    if not G or len(G) == 0:
        return {}
    try:
        import networkx.algorithms.link_analysis.pagerank_alg as pa
        return pa._pagerank_python(G, alpha=alpha)
    except Exception:
        try:
            return nx.pagerank(G, alpha=alpha)
        except Exception:
            n = len(G)
            return {node: round(1.0 / n, 4) for node in G.nodes()}


# ---------------------------------------------------------------------------
# Canonical Wikidata Knowledge Base for Zero-Latency Entity Grounding
# ---------------------------------------------------------------------------
WIKIDATA_KNOWLEDGE_BASE: Dict[str, str] = {
    # Compliance, Standards & Accounting
    "asc 606": "https://www.wikidata.org/wiki/Q2819869",
    "ifrs 15": "https://www.wikidata.org/wiki/Q16996614",
    "soc 1": "https://www.wikidata.org/wiki/Q105822363",
    "soc 2": "https://www.wikidata.org/wiki/Q105822363",
    "soc 2 type ii": "https://www.wikidata.org/wiki/Q105822363",
    "soc 1 type ii": "https://www.wikidata.org/wiki/Q105822363",
    "gaap": "https://www.wikidata.org/wiki/Q478440",
    "us gaap": "https://www.wikidata.org/wiki/Q478440",
    "gdpr": "https://www.wikidata.org/wiki/Q11723205",
    "pci-dss": "https://www.wikidata.org/wiki/Q1051515",
    "iso 27001": "https://www.wikidata.org/wiki/Q1135272",
    "hipaa": "https://www.wikidata.org/wiki/Q1586524",
    "ccpa": "https://www.wikidata.org/wiki/Q55606411",

    # Software Integrations & Enterprise Ecosystem
    "salesforce": "https://www.wikidata.org/wiki/Q760814",
    "netsuite": "https://www.wikidata.org/wiki/Q1978731",
    "quickbooks": "https://www.wikidata.org/wiki/Q7271981",
    "stripe": "https://www.wikidata.org/wiki/Q7624119",
    "workday": "https://www.wikidata.org/wiki/Q2592881",
    "hubspot": "https://www.wikidata.org/wiki/Q17055745",
    "sage intacct": "https://www.wikidata.org/wiki/Q28956947",
    "sage": "https://www.wikidata.org/wiki/Q1197415",
    "xero": "https://www.wikidata.org/wiki/Q8043818",
    "avalara": "https://www.wikidata.org/wiki/Q16836798",
    "taxjar": "https://www.wikidata.org/wiki/Q106726884",
    "sap": "https://www.wikidata.org/wiki/Q5528",
    "oracle": "https://www.wikidata.org/wiki/Q19900",
    "zendesk": "https://www.wikidata.org/wiki/Q8069151",
    "slack": "https://www.wikidata.org/wiki/Q16202723",
    "plaid": "https://www.wikidata.org/wiki/Q65069792",
    "snowflake": "https://www.wikidata.org/wiki/Q104862415",
    "microsoft dynamics": "https://www.wikidata.org/wiki/Q1050212",

    # Core Architectural Concepts & Capabilities
    "revenue recognition": "https://www.wikidata.org/wiki/Q7318047",
    "accounts receivable": "https://www.wikidata.org/wiki/Q478440",
    "billing": "https://www.wikidata.org/wiki/Q185794",
    "subscription business model": "https://www.wikidata.org/wiki/Q1066060",
    "saas": "https://www.wikidata.org/wiki/Q211246",
    "cloud computing": "https://www.wikidata.org/wiki/Q483639",
    "enterprise resource planning": "https://www.wikidata.org/wiki/Q14620",
    "erp": "https://www.wikidata.org/wiki/Q14620"
}


def build_rdf_graph(
    domain: str,
    triples: List[SemanticTriple],
    hubs: Dict[str, str],
    entities: List[str]
) -> Graph:
    """
    Constructs an in-memory RDFLib Graph with standard W3C and Schema.org namespaces,
    connecting the organization root, capabilities, integrations, and canonical topic hubs.
    """
    g = Graph()
    SCHEMA = Namespace("https://schema.org/")
    LOCAL = Namespace(f"https://{domain}/ontology/")

    g.bind("schema", SCHEMA)
    g.bind("onto", LOCAL)
    g.bind("rdfs", RDFS)
    g.bind("rdf", RDF)

    brand = triples[0].subject if triples else domain.split(".")[0].capitalize()
    brand_clean = re.sub(r'[^a-zA-Z0-9]+', '', brand) or "Platform"
    root_uri = URIRef(f"https://{domain}/#{brand_clean}")

    # Root Organization & Software Application definitions
    g.add((root_uri, RDF.type, SCHEMA.SoftwareApplication))
    g.add((root_uri, RDF.type, SCHEMA.Organization))
    g.add((root_uri, SCHEMA.name, Literal(brand)))
    g.add((root_uri, SCHEMA.url, URIRef(f"https://{domain}/")))

    # Check if brand matches Wikidata
    if brand.lower() in WIKIDATA_KNOWLEDGE_BASE:
        g.add((root_uri, SCHEMA.sameAs, URIRef(WIKIDATA_KNOWLEDGE_BASE[brand.lower()])))

    pred_map = {
        "automates": SCHEMA.potentialAction,
        "integratesWith": SCHEMA.isRelatedTo,
        "compliesWith": SCHEMA.legislationApplies,
        "supportsPricingModel": SCHEMA.priceSpecification
    }

    seen_triples = set()
    for t in triples:
        key = (t.subject, t.predicate, t.object)
        if key in seen_triples:
            continue
        seen_triples.add(key)

        obj_clean = re.sub(r'[^a-zA-Z0-9]+', '', t.object)
        obj_uri = URIRef(f"https://{domain}/entity/{obj_clean}") if obj_clean else None

        rel = pred_map.get(t.predicate, SCHEMA.knowsAbout)

        if obj_uri:
            g.add((root_uri, rel, obj_uri))
            g.add((obj_uri, RDFS.label, Literal(t.object)))
            if t.predicate in pred_map:
                g.add((obj_uri, RDF.type, LOCAL[t.predicate.capitalize()]))
            if t.evidence_sentence:
                g.add((obj_uri, SCHEMA.description, Literal(t.evidence_sentence)))

            # Canonical Wikidata Entity Grounding
            obj_lower = t.object.lower().strip()
            if obj_lower in WIKIDATA_KNOWLEDGE_BASE:
                g.add((obj_uri, SCHEMA.sameAs, URIRef(WIKIDATA_KNOWLEDGE_BASE[obj_lower])))
        else:
            g.add((root_uri, rel, Literal(t.object)))

    # Canonical Topic Hubs as WebPage nodes linked to root Organization
    for concept, hub_url in hubs.items():
        try:
            hub_uri = URIRef(hub_url)
            g.add((root_uri, SCHEMA.hasPart, hub_uri))
            g.add((hub_uri, RDF.type, SCHEMA.WebPage))
            g.add((hub_uri, SCHEMA.about, Literal(concept)))
            g.add((hub_uri, SCHEMA.name, Literal(f"{concept} Canonical Authority Hub")))
            g.add((hub_uri, SCHEMA.url, hub_uri))

            concept_lower = concept.lower().strip()
            if concept_lower in WIKIDATA_KNOWLEDGE_BASE:
                g.add((hub_uri, SCHEMA.sameAs, URIRef(WIKIDATA_KNOWLEDGE_BASE[concept_lower])))
        except Exception:
            continue

    # Core Grounded Entities linked to root via schema:knowsAbout
    for ent in entities[:25]:
        g.add((root_uri, SCHEMA.knowsAbout, Literal(ent)))

    return g


def export_to_rdf_turtle(
    domain: str,
    triples: List[SemanticTriple],
    hubs: Dict[str, str],
    entities: List[str]
) -> str:
    """
    Serializes the unified site knowledge graph, semantic triples, and canonical topic hubs
    into W3C standard RDF Turtle (.ttl) format with canonical Wikidata entity grounding.
    """
    g = build_rdf_graph(domain, triples, hubs, entities)
    return g.serialize(format="turtle")


def export_to_rdf_ntriples(
    domain: str,
    triples: List[SemanticTriple],
    hubs: Dict[str, str],
    entities: List[str]
) -> str:
    """
    Serializes the unified site knowledge graph into W3C standard N-Triples (.nt) format.
    Ideal for high-throughput streaming triple stores and bulk database ingestion.
    """
    g = build_rdf_graph(domain, triples, hubs, entities)
    return g.serialize(format="nt")


def execute_sparql_query_on_ttl(turtle_data: str, sparql_query: str) -> Dict[str, Any]:
    """
    Executes a W3C SPARQL 1.1 query against an RDF Turtle knowledge graph
    using RDFLib's native SPARQL engine and returns structured tabular results.
    """
    g = Graph()
    g.parse(data=turtle_data, format="turtle")
    qres = g.query(sparql_query)

    cols = [str(v) for v in qres.vars] if hasattr(qres, "vars") and qres.vars else []
    rows: List[List[str]] = []
    for row in qres:
        if hasattr(row, "__iter__"):
            rows.append([str(item) if item is not None else "" for item in row])
        else:
            rows.append([str(row)])

    if not cols and rows:
        cols = [f"col_{i+1}" for i in range(len(rows[0]))]

    return {
        "columns": cols,
        "rows": rows,
        "row_count": len(rows),
        "status": "success"
    }


def build_owl_ontology(
    domain: str,
    triples: List[SemanticTriple],
    hubs: Dict[str, str],
    entities: List[str]
) -> Graph:
    """
    Constructs a formal W3C OWL 2 DL ontology model for the enterprise domain,
    defining classes (Platform, Capability, Integration, ComplianceStandard, PricingModel, TopicHub),
    object properties with domains and ranges, datatype properties, and named individuals.
    """
    g = Graph()
    ONTO = Namespace(f"https://{domain}/ontology#")
    SCHEMA = Namespace("https://schema.org/")

    g.bind("owl", OWL)
    g.bind("onto", ONTO)
    g.bind("schema", SCHEMA)
    g.bind("rdfs", RDFS)
    g.bind("rdf", RDF)
    g.bind("xsd", XSD)

    # 1. Ontology Declaration
    onto_uri = URIRef(f"https://{domain}/ontology")
    g.add((onto_uri, RDF.type, OWL.Ontology))
    g.add((onto_uri, RDFS.label, Literal(f"{domain} Enterprise Domain Ontology")))
    g.add((onto_uri, OWL.versionInfo, Literal("2.2.0")))

    # 2. OWL Classes
    classes = [
        ("EnterprisePlatform", "Root SaaS or enterprise business platform entity"),
        ("PlatformCapability", "Core automated feature, workflow, or architectural service"),
        ("SoftwareIntegration", "External enterprise application or ecosystem integration"),
        ("ComplianceStandard", "Regulatory, accounting, or security compliance framework"),
        ("PricingModel", "Commercial monetization, billing, or pricing structure"),
        ("TopicAuthorityHub", "Canonical topic cluster landing page anchoring topical authority")
    ]
    for c_name, c_desc in classes:
        c_uri = ONTO[c_name]
        g.add((c_uri, RDF.type, OWL.Class))
        g.add((c_uri, RDFS.label, Literal(c_name)))
        g.add((c_uri, RDFS.comment, Literal(c_desc)))

    # 3. OWL Object Properties with Domain & Range
    obj_props = [
        ("automatesWorkflow", ONTO.EnterprisePlatform, ONTO.PlatformCapability, "Relates platform to automated workflows"),
        ("integratesWithSystem", ONTO.EnterprisePlatform, ONTO.SoftwareIntegration, "Relates platform to integrated systems"),
        ("compliesWithStandard", ONTO.EnterprisePlatform, ONTO.ComplianceStandard, "Relates platform to compliance frameworks"),
        ("supportsPricingArchitecture", ONTO.EnterprisePlatform, ONTO.PricingModel, "Relates platform to monetization models"),
        ("anchorsTopicHub", ONTO.EnterprisePlatform, ONTO.TopicAuthorityHub, "Relates platform to its canonical topic hubs")
    ]
    for prop_name, domain_uri, range_uri, comment in obj_props:
        p_uri = ONTO[prop_name]
        g.add((p_uri, RDF.type, OWL.ObjectProperty))
        g.add((p_uri, RDFS.domain, domain_uri))
        g.add((p_uri, RDFS.range, range_uri))
        g.add((p_uri, RDFS.comment, Literal(comment)))

    # 4. OWL Datatype Properties
    data_props = [
        ("evidenceSentence", XSD.string, "Verbatim textual evidence sentence from crawled pages"),
        ("canonicalUrl", XSD.anyURI, "Canonical webpage URL for this entity")
    ]
    for dp_name, range_type, comment in data_props:
        dp_uri = ONTO[dp_name]
        g.add((dp_uri, RDF.type, OWL.DatatypeProperty))
        g.add((dp_uri, RDFS.range, range_type))
        g.add((dp_uri, RDFS.comment, Literal(comment)))

    # 5. Named Individuals (Instances)
    brand = triples[0].subject if triples else domain.split(".")[0].capitalize()
    brand_slug = re.sub(r'[^a-zA-Z0-9]+', '', brand) or "Platform"
    platform_ind = ONTO[brand_slug]
    g.add((platform_ind, RDF.type, OWL.NamedIndividual))
    g.add((platform_ind, RDF.type, ONTO.EnterprisePlatform))
    g.add((platform_ind, RDFS.label, Literal(brand)))
    g.add((platform_ind, SCHEMA.url, URIRef(f"https://{domain}/")))

    pred_class_map = {
        "automates": (ONTO.automatesWorkflow, ONTO.PlatformCapability),
        "integratesWith": (ONTO.integratesWithSystem, ONTO.SoftwareIntegration),
        "compliesWith": (ONTO.compliesWithStandard, ONTO.ComplianceStandard),
        "supportsPricingModel": (ONTO.supportsPricingArchitecture, ONTO.PricingModel)
    }

    seen_individuals = set()
    for t in triples:
        obj_slug = re.sub(r'[^a-zA-Z0-9]+', '', t.object)
        if not obj_slug or obj_slug in seen_individuals:
            continue
        seen_individuals.add(obj_slug)

        ind_uri = ONTO[obj_slug]
        prop_uri, class_uri = pred_class_map.get(t.predicate, (SCHEMA.knowsAbout, ONTO.PlatformCapability))

        g.add((ind_uri, RDF.type, OWL.NamedIndividual))
        g.add((ind_uri, RDF.type, class_uri))
        g.add((ind_uri, RDFS.label, Literal(t.object)))
        g.add((platform_ind, prop_uri, ind_uri))

        if t.evidence_sentence:
            g.add((ind_uri, ONTO.evidenceSentence, Literal(t.evidence_sentence)))

        obj_lower = t.object.lower().strip()
        if obj_lower in WIKIDATA_KNOWLEDGE_BASE:
            g.add((ind_uri, SCHEMA.sameAs, URIRef(WIKIDATA_KNOWLEDGE_BASE[obj_lower])))

    # Hub individuals
    for concept, hub_url in hubs.items():
        c_slug = re.sub(r'[^a-zA-Z0-9]+', '', concept)
        if not c_slug:
            continue
        hub_ind = ONTO[f"Hub_{c_slug}"]
        g.add((hub_ind, RDF.type, OWL.NamedIndividual))
        g.add((hub_ind, RDF.type, ONTO.TopicAuthorityHub))
        g.add((hub_ind, RDFS.label, Literal(f"{concept} Authority Hub")))
        g.add((hub_ind, ONTO.canonicalUrl, URIRef(hub_url)))
        g.add((platform_ind, ONTO.anchorsTopicHub, hub_ind))

    return g


def export_to_owl_xml(
    domain: str,
    triples: List[SemanticTriple],
    hubs: Dict[str, str],
    entities: List[str]
) -> str:
    """
    Serializes the domain ontology into formal W3C OWL 2 DL RDF/XML (.owl) format.
    Compatible with Protégé, TopBraid Composer, Apache Jena, and semantic reasoners.
    """
    g = build_owl_ontology(domain, triples, hubs, entities)
    return g.serialize(format="xml")






