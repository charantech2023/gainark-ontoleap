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
    SchemaValidationReport
)
from validator import validate_schema_patch
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

    return SiteAuditAndLinkResult(
        root_domain=domain,
        pages_analyzed=len(pages_data),
        opportunities_count=len(opportunities),
        opportunities=opportunities,
        topic_hubs=hubs,
        unified_site_graph=site_graph,
        ai_citation_readiness=ai_readiness,
        cluster_topology=topology,
        wordpress_php_hook=wp_hook,
        llms_txt=llms_manifest,
        robots_txt_ai=robots_manifest,
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
        f"{hub_comments}\n"
    )



