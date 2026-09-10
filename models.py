"""
GainARK OntoLeap — Enterprise Domain Models & Schema Definitions

This module defines Pydantic data models representing the full lifecycle of
ontological SEO analysis, structured data extraction, semantic knowledge graphs,
topic silo clustering, NetworkX topological metrics, generative AI search simulations,
and W3C standard RDF/SPARQL representations.

Changes:
- FIX #17: datetime.utcnow() replaced with timezone-aware datetime.now(timezone.utc)
- FIX #3:  max_length validators on raw string inputs (SPARQL, RDF Turtle) to
           prevent memory-bomb payloads from crashing the container.
"""

from typing import List, Dict, Any, Optional, Union
from datetime import datetime, timezone
from pydantic import BaseModel, Field, model_validator


class Concept(BaseModel):
    """One concept in a vertical ontology, identified independently of its name.

    Before this existed, a concept WAS its display string: the string was the key in
    every vocabulary list, the key in concept_hierarchy, and - via a slugify of the
    label - the thing a graph URI was built from. Three consequences:

      * Renaming a concept silently created a different resource, orphaning every
        triple previously emitted about it.
      * Slugging the label is lossy and collides: "Auto-Pay" and "Auto Pay" produce
        one URI, and a punctuation change produces a new one.
      * Nothing could carry a definition, so no reviewer or model could check whether
        two similar concepts were the same thing or a match was correct.

    `id` is assigned once and then frozen. It is never re-derived from prefLabel, so
    the label is free to change without breaking identity.
    """
    id: str = Field(
        ...,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
        description="Stable slug, assigned once and never regenerated from the label.",
    )
    prefLabel: str = Field(..., description="Canonical display label, in documentation register.")
    kind: str = Field(
        ...,
        description=(
            "Which vocabulary bucket this concept belongs to: feature, process, "
            "pricing, standard, or domain. Determines the predicate it is extracted "
            "under - see ontology_schema.RELATIONS."
        ),
    )
    definition: str = Field(
        default="",
        description=(
            "One sentence saying what the concept is, in the product's own terms. "
            "This is what makes review possible: without it neither a person nor a "
            "model can judge whether 'Revenue Schedules' and 'Schedule Lines' are the "
            "same thing, or whether a match against a page was correct."
        ),
    )
    altLabels: List[str] = Field(
        default_factory=list,
        description="Other surface forms meaning this concept, typically marketing register.",
    )
    broader: Optional[str] = Field(
        default=None,
        description="id of the parent concept. None for the scheme root.",
    )
    governs: List[str] = Field(
        default_factory=list,
        description=(
            "ids of concepts this one sets requirements for, when it is a standard or "
            "regulation.\n\n"
            "This exists because `broader` was carrying two incompatible meanings. "
            "ASC 606 used to sit under Revenue Recognition as though it were a narrower "
            "kind of it; it is not, it is a rule that governs it. Coverage inference "
            "walking `broader` therefore treated complying with a standard and shipping "
            "a capability as the same class of evidence. Standards now live in their own "
            "branch and point at what they govern through this relation instead."
        ),
    )


class VerticalConfig(BaseModel):
    vertical_id: str = Field(..., description="Unique identifier for the vertical domain")
    display_name: str = Field(..., description="Human-readable domain name")
    gliner_labels: List[str] = Field(..., description="Target entity types for GLiNER zero-shot NER")
    mandatory_schema_types: List[str] = Field(..., description="Schema.org types required for this domain")
    core_seed_concepts: List[str] = Field(..., description="Key domain concepts and terminology")

    # Per-vertical extraction vocabulary. The industry profiler already generates these
    # and the vertical JSON files already store them - healthtech.json carries Epic,
    # Cerner, HIPAA and HITECH - but until these fields existed Pydantic discarded them
    # on load, and triple extraction fell back to the billing-flavoured globals in
    # constants.py. So a healthcare site was being read for "Dunning Automation".
    # Empty means "no vertical-specific vocabulary": callers fall back to the generic
    # B2B defaults via resolve_vocabulary() in constants.py.
    known_compliance: List[str] = Field(
        default_factory=list,
        description="Regulations and standards this vertical is judged against (e.g. HIPAA, HITECH).",
    )
    known_integrations: List[str] = Field(
        default_factory=list,
        description="Ecosystem platforms buyers in this vertical expect (e.g. Epic Systems, Cerner).",
    )
    known_pricing: List[str] = Field(
        default_factory=list,
        description="Monetisation models common to this vertical (e.g. Per-Provider Pricing).",
    )
    known_automation: List[str] = Field(
        default_factory=list,
        description="Core capabilities this vertical automates (e.g. Clinical Documentation).",
    )
    concept_hierarchy: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Maps a specific concept to the broader one it belongs under, e.g. "
            '{"Prior Authorization": "Revenue Cycle Management"}. Without it every concept '
            "is an unrelated string, so a competitor writing only about Prior Authorization "
            "reads as not covering Revenue Cycle Management at all - and category whitespace "
            "reports territory as unclaimed when a rival already owns it under a narrower "
            "name. Empty means flat, which is the previous behaviour."
        ),
    )
    known_features: List[str] = Field(
        default_factory=list,
        description="Discrete product features and capabilities (e.g. Automated Invoicing, SSO).",
    )
    known_segments: List[str] = Field(
        default_factory=list,
        description="Target customer segments (e.g. Enterprise, Mid-Market, SMB).",
    )
    known_industries: List[str] = Field(
        default_factory=list,
        description="Target industry verticals served (e.g. SaaS, FinTech, HealthTech).",
    )
    known_deployment: List[str] = Field(
        default_factory=list,
        description="Hosting and deployment architectures (e.g. Cloud-Native, Multi-Tenant, On-Premise).",
    )
    known_certifications: List[str] = Field(
        default_factory=list,
        description="Trust and compliance certifications (e.g. SOC 2 Type II, ISO 27001).",
    )
    known_api_types: List[str] = Field(
        default_factory=list,
        description="Supported API standards and integration protocols (e.g. REST API, Webhooks).",
    )
    known_locales: List[str] = Field(
        default_factory=list,
        description="Supported geographic regions and locales (e.g. United States, European Union).",
    )
    known_sla: List[str] = Field(
        default_factory=list,
        description="Contractual reliability and uptime SLA commitments (e.g. 99.99% Uptime).",
    )
    known_replaces: List[str] = Field(
        default_factory=list,
        description="Legacy manual workflows eliminated or replaced (e.g. Manual Spreadsheets).",
    )
    known_competitors: List[str] = Field(
        default_factory=list,
        description="Direct market competitors operating in this vertical (e.g. Zuora, Chargebee).",
    )
    known_customers: List[str] = Field(
        default_factory=list,
        description=(
            "Named customers of the product under audit. Normally empty and filled from "
            "the page by entity extraction, since customers are specific to one vendor "
            "rather than shared across the vertical. Its purpose is to give a company "
            "named in a testimonial somewhere correct to go - without it, zero-shot NER "
            "files it under the nearest available label and a customer becomes an "
            "integration partner."
        ),
    )
    alt_labels: Dict[str, List[str]] = Field(
        default_factory=dict,
        description=(
            "Maps a canonical concept to the other surface forms that mean it, e.g. "
            '{"Renewal": ["Renewal Management", "Automated Renewals"]}. The canonical '
            "label is the one technical documentation actually uses; the alternates are "
            "the compound phrasings marketing prefers.\n\n"
            "Without this the two sides of an audit never meet. Validated against 818 "
            "Ordway support articles, 28 of 29 drafted feature labels were absent from "
            "the docs while their base concept was present in volume - 'Renewal "
            "Management' nowhere, 'renewal' 335 times. Marketing copy then raised a "
            "claim that documentation could not verify, and the tool reported drift "
            "where the capability plainly exists.\n\n"
            "Extraction matches any surface form and always emits the canonical, so "
            "marketing and technical text converge on one string. Empty means every "
            "concept has exactly one spelling, which is the previous behaviour."
        ),
    )
    concepts: List[Concept] = Field(
        default_factory=list,
        description=(
            "The vertical's ontology as identified concepts. When present this is the "
            "single source of truth, and the flat vocabulary lists plus alt_labels and "
            "concept_hierarchy are DERIVED from it (see derive_legacy_views) so existing "
            "extraction code keeps working unchanged.\n\n"
            "Empty means this vertical predates concept identity and is read straight "
            "from its flat lists, exactly as before."
        ),
    )

    # Which vocabulary list each Concept.kind feeds. 'domain' is deliberately absent:
    # domains organise the tree but are not themselves extracted as claims.
    _KIND_TO_FIELD = {
        "feature": "known_features",
        "process": "known_automation",
        "pricing": "known_pricing",
        "standard": "known_compliance",
    }

    @model_validator(mode="after")
    def derive_legacy_views(self):
        """Project `concepts` onto the flat fields the extractor already reads.

        Keeping one authoritative record per concept while deriving the old shapes means
        identity and definitions can land without touching pipeline.py, constants.py or
        any consumer of concept_hierarchy. A vertical with no `concepts` is untouched.
        """
        if not self.concepts:
            return self

        by_id = {c.id: c for c in self.concepts}

        buckets: Dict[str, List[str]] = {f: [] for f in set(self._KIND_TO_FIELD.values())}
        alts: Dict[str, List[str]] = {}
        hierarchy: Dict[str, str] = {}

        for c in self.concepts:
            field = self._KIND_TO_FIELD.get(c.kind)
            if field:
                buckets[field].append(c.prefLabel)
            if c.altLabels:
                alts[c.prefLabel] = list(c.altLabels)
            if c.broader:
                parent = by_id.get(c.broader)
                if parent is None:
                    raise ValueError(
                        "Concept %r has broader=%r, which is not a concept id in this "
                        "vertical. A dangling parent would silently drop the concept out "
                        "of the hierarchy and out of ancestor inference." % (c.id, c.broader)
                    )
                # Consumers (concept_ancestors, knowledge_graph) key the hierarchy by
                # label, so the derived view stays label-to-label.
                hierarchy[c.prefLabel] = parent.prefLabel

        for field, values in buckets.items():
            if values:
                setattr(self, field, values)
        self.alt_labels = alts
        self.concept_hierarchy = hierarchy
        return self

    def concept_by_label(self, label: str) -> Optional[Concept]:
        """The concept whose prefLabel or any altLabel matches `label`, case-insensitively."""
        target = (label or "").strip().lower()
        if not target:
            return None
        for c in self.concepts:
            if c.prefLabel.strip().lower() == target:
                return c
        for c in self.concepts:
            if any(a.strip().lower() == target for a in c.altLabels):
                return c
        return None


class EntityMatch(BaseModel):
    text: str
    label: str
    score: float
    start: int
    end: int

    @property
    def name(self) -> str:
        return self.text


class SchemaOrgData(BaseModel):
    schema_type: str
    syntax: str  # json-ld, microdata, rdfa, opengraph
    data: Dict[str, Any]
    is_mandatory: bool = False


class SeedConceptMatch(BaseModel):
    concept: str
    count: int
    matched_phrases: List[str] = Field(default_factory=list)


class ReadinessBreakdown(BaseModel):
    schema_score: float = Field(..., description="Score based on mandatory schema types presence (max 40 pts)")
    concept_score: float = Field(..., description="Score based on core seed concepts coverage (max 30 pts)")
    entity_score: float = Field(..., description="Score based on GLiNER entity diversity and confidence (max 30 pts)")
    total_score: float = Field(..., description="Single-page structured data readiness score out of 100. Measures schema.org implementation, seed concept coverage and entity richness on one page. Not a predictor of AI citation frequency - see test_calibration.py.")
    details: Dict[str, Any] = Field(default_factory=dict)


class SemanticTriple(BaseModel):
    subject: str = Field(..., description="Entity subject (e.g. company or platform name)")
    predicate: str = Field(..., description="Predicate relation (automates, integratesWith, compliesWith, supportsPricingModel)")
    object: str = Field(..., description="Object entity, standard, system, or capability")
    confidence: float = Field(default=0.85, description="Extraction confidence score (0-1)")
    evidence_sentence: Optional[str] = Field(default=None, description="Source context sentence")
    source_type: Optional[str] = Field(default="marketing_claim", description="'marketing_claim', 'technical_truth', or 'industry_standard'")
    provenance: Optional[str] = Field(default=None, description="Exact source URI, endpoint path, or document section")
    match_strength: Optional[str] = Field(default=None, description="Semantic match strength: 'exact', 'substring', 'multi_token', 'head_token'")


class ExtractionResult(BaseModel):
    url: Optional[str] = None
    title: Optional[str] = None
    vertical_id: str
    extracted_text_snippet: Optional[str] = None
    entities: List[EntityMatch] = Field(default_factory=list)
    schema_org: List[SchemaOrgData] = Field(default_factory=list)
    seed_concepts: List[SeedConceptMatch] = Field(default_factory=list)
    mandatory_schema_status: Dict[str, bool] = Field(
        default_factory=dict,
        description="Whether each mandatory schema type was detected on the page"
    )
    readiness_score: float = Field(default=0.0, description="Single-page structured data readiness score (0-100)")
    readiness_breakdown: Optional[ReadinessBreakdown] = Field(
        default=None,
        description="Detailed scoring breakdown across schemas, concepts, and entities"
    )
    recommended_patch: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Tailored Schema.org remediation patch addressing missing mandatory schemas"
    )
    crawled_subpages: List[str] = Field(
        default_factory=list,
        description="Sub-page URLs fetched during deep crawl"
    )
    triples: List[SemanticTriple] = Field(
        default_factory=list,
        description="Extracted relational entity triples (Subject, Predicate, Object)"
    )
    meta_description: Optional[str] = Field(default=None, description="Page meta or OpenGraph description")
    site_name: Optional[str] = Field(default=None, description="OpenGraph site name or brand")
    headings: List[str] = Field(default_factory=list, description="Extracted H1 and H2 headings")
    google_kg_presence: Optional[Dict[str, Any]] = Field(
        default=None,
        description="Google Knowledge Graph verification data, MID, salience score, and AI Overview risk"
    )

    @property
    def extraction(self) -> "ExtractionResult":
        return self


class PageKnowledgeGraphRequest(BaseModel):
    """
    Request model for extracting a page-level or text-level W3C knowledge graph.
    """
    url: Optional[str] = Field(default=None, max_length=2048, description="URL of the web page to extract from")
    text: Optional[str] = Field(default=None, max_length=500_000, description="Raw text or copy to extract from")
    title: Optional[str] = Field(default=None, max_length=500, description="Page title or document name")
    subject: Optional[str] = Field(default="Platform", max_length=200, description="Primary subject / entity name")
    vertical_id: Optional[str] = Field(default=None, max_length=100, description="Optional vertical profile ID")

    @model_validator(mode="after")
    def validate_input_present(self) -> "PageKnowledgeGraphRequest":
        if not (self.url and self.url.strip()) and not (self.text and self.text.strip()):
            raise ValueError("Must provide either 'url' or 'text' to generate a knowledge graph.")
        return self


class PageKnowledgeGraphResult(BaseModel):
    """
    Formal data contract for a Page-Level / Document-Level Knowledge Graph.
    Contains W3C RDFLib graph serializations, extracted semantic triples with sentence
    provenance, dynamic SKOS concept scheme definitions, and graph topology metrics.
    """
    url: Optional[str] = Field(default=None, description="Source page URL")
    title: Optional[str] = Field(default=None, description="Page title or document name")
    subject: str = Field(default="Platform", description="Primary subject / entity name")
    vertical_id: Optional[str] = Field(default=None, description="Vertical ID if bound to a profile")
    triples: List[SemanticTriple] = Field(default_factory=list, description="Extracted relational triples with evidence")
    concepts: List[Dict[str, Any]] = Field(default_factory=list, description="Minted SKOS concepts and hierarchy")
    turtle: str = Field(default="", description="W3C Turtle (.ttl) serialization")
    json_ld: Union[Dict[str, Any], List[Any]] = Field(default_factory=dict, description="W3C JSON-LD structure")
    node_count: int = Field(default=0, description="Total distinct RDF nodes in the page graph")
    edge_count: int = Field(default=0, description="Total RDF triples / edges asserted")
    predicate_counts: Dict[str, int] = Field(default_factory=dict, description="Count of assertions per ontology predicate")
    entities: List[EntityMatch] = Field(default_factory=list, description="Extracted NER entity mentions")


class KeywordGapItem(BaseModel):
    concept: str
    competitor_count: int
    competitors: List[str] = Field(default_factory=list)
    priority: str = "High"


class CompetitiveGapAnalysis(BaseModel):
    primary_url: str
    primary_score: float
    leader_url: str
    leader_score: float
    score_gap: float
    schema_gaps: List[str] = Field(default_factory=list)
    keyword_gaps: List[KeywordGapItem] = Field(default_factory=list)
    action_plan: List[str] = Field(default_factory=list)
    leapfrog_patch: Optional[Dict[str, Any]] = None


class PageCrawlSummary(BaseModel):
    url: str
    status: str
    triples_found: int
    entities_found: int
    error: Optional[str] = None


class UnifiedSiteGraph(BaseModel):
    root_domain: str
    # FIX #17: timezone-aware UTC timestamp
    crawled_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    total_pages_crawled: int
    unique_entities: List[str]
    triples: List[SemanticTriple]
    page_summaries: List[PageCrawlSummary]
    schema_graph_jsonld: Dict


class InternalLinkOpportunity(BaseModel):
    source_url: str = Field(..., description="Source page containing the entity/triple mention")
    target_url: str = Field(..., description="Canonical topic hub URL for that entity")
    entity: str = Field(..., description="The entity or concept being referenced")
    predicate: Optional[str] = Field(default=None, description="Relation if derived from a triple (automates, integratesWith, compliesWith, supportsPricingModel)")
    suggested_anchor: str = Field(..., description="Recommended anchor text for the link")
    context_sentence: str = Field(..., description="The sentence containing the opportunity")
    html_snippet: str = Field(..., description="Copyable <a href='...'> anchor tag")
    priority: str = Field(default="High", description="Priority level: High, Medium, or Standard")


class AICitationReadiness(BaseModel):
    total_score: float = Field(..., description="Overall GenAI search citation readiness score out of 100")
    entity_grounding_score: float = Field(..., description="Score out of 25 for entity grounding depth")
    relational_density_score: float = Field(..., description="Score out of 25 for relational semantic triple density")
    silo_integrity_score: float = Field(..., description="Score out of 25 for topic cluster siloing & link coverage")
    schema_coverage_score: float = Field(..., description="Score out of 25 for structured schema.org coverage")
    verdict: str = Field(..., description="Readiness summary verdict")
    recommendations: List[str] = Field(default_factory=list, description="Actionable GenAI optimization recommendations")


class GraphNode(BaseModel):
    id: str
    label: str
    type: str  # 'hub', 'spoke', 'entity'
    url: Optional[str] = None
    group: str
    value: int = 10


class GraphEdge(BaseModel):
    source: str
    target: str
    label: Optional[str] = None
    relation_type: str  # 'hub_for', 'recommends_link', 'has_triple'


class ClusterTopology(BaseModel):
    nodes: List[GraphNode] = Field(default_factory=list)
    edges: List[GraphEdge] = Field(default_factory=list)


class SchemaValidationReport(BaseModel):
    is_valid: bool = True
    google_rich_results_eligible: bool = True
    validated_types: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    compliance_percentage: float = 100.0


class TopicHubMetadata(BaseModel):
    concept: str
    canonical_url: str
    taxonomy_role: str
    pagerank_score: float = 0.0
    betweenness_centrality: float = 0.0
    inbound_internal_links: int = 0


class CannibalizationRiskItem(BaseModel):
    concept: str
    competing_urls: List[str]
    recommended_canonical_hub: str
    recommendation: str


class PredictedLink(BaseModel):
    """
    AI Knowledge Graph Link Prediction item derived from rule-based ontological priors.
    Represents an ontologically inferred relation missing from crawled content.
    """
    subject: str = Field(..., description="Subject entity (e.g. Domain Platform)")
    predicate: str = Field(..., description="Inferred semantic predicate (e.g. integratesWith, compliesWith)")
    object: str = Field(..., description="Predicted object entity")
    confidence: float = Field(..., description="Algorithmic confidence score (0.0 to 1.0)")
    reasoning: str = Field(..., description="Ontological rationale for link prediction")
    wikidata_id: Optional[str] = Field(default=None, description="Wikidata Q-ID if entity is grounded")
    wikidata_url: Optional[str] = Field(default=None, description="Direct URL to Wikidata resource")
    recommended_action: str = Field(default="", description="Recommended schema/linking action")


class SiteAuditAndLinkResult(BaseModel):
    root_domain: str
    pages_analyzed: int
    opportunities_count: int
    opportunities: List[InternalLinkOpportunity] = Field(default_factory=list)
    topic_hubs: Dict[str, str] = Field(default_factory=dict, description="Map of entity/topic -> canonical URL")
    topic_hubs_detailed: List[TopicHubMetadata] = Field(default_factory=list)
    orphan_pages: List[str] = Field(default_factory=list)
    cannibalization_risks: List[CannibalizationRiskItem] = Field(default_factory=list)
    unified_site_graph: Optional[UnifiedSiteGraph] = None
    ai_citation_readiness: Optional[AICitationReadiness] = None
    cluster_topology: Optional[ClusterTopology] = None
    wordpress_php_hook: Optional[str] = None
    llms_txt: Optional[str] = None
    robots_txt_ai: Optional[str] = None
    rdf_turtle: Optional[str] = None
    rdf_ntriples: Optional[str] = Field(default=None, description="Serialized W3C N-Triples (.nt) triple store dump")
    owl_xml: Optional[str] = Field(default=None, description="Serialized W3C OWL 2 DL Ontology in RDF/XML format")
    semantic_clustering: Optional[Dict[str, Any]] = Field(default=None, description="TF-IDF cosine similarity clusters and cannibalization matrix")
    predicted_links: List[PredictedLink] = Field(default_factory=list, description="AI-predicted high-confidence missing KG relations")
    graph_completeness_score: Optional[float] = Field(default=None, description="Knowledge graph completeness score against vertical benchmark")
    validation_report: Optional[SchemaValidationReport] = None


class CitationSource(BaseModel):
    index: int
    entity: str
    target_url: str
    evidence: str


class SearchSimulationRequest(BaseModel):
    """
    Request model for simulating Perplexity / SearchGPT generative query answering.
    """
    query: str = Field(..., max_length=2000, description="User search query, e.g., 'What accounting standards does the platform comply with?'")
    root_domain: str = Field(..., max_length=2048, description="The audited domain")
    triples: List[SemanticTriple] = Field(default_factory=list, max_length=5000, description="Extracted domain triples")
    topic_hubs: Dict[str, str] = Field(default_factory=dict, description="Canonical topic hubs map")
    entities: List[str] = Field(default_factory=list, max_length=5000, description="Extracted key entities")


class SearchSimulationResponse(BaseModel):
    """
    Simulated AI engine generative response grounded in domain ontology triples.
    """
    query: str
    synthesized_answer: str
    citations: List[CitationSource] = Field(default_factory=list)
    grounding_confidence: float = 0.95
    hallucination_risk: str = "Zero Hallucination Risk (100% Schema & Triple Grounded)"
    attributed_capabilities: List[str] = Field(default_factory=list)


class SparqlQueryRequest(BaseModel):
    """
    Request model for querying the RDF knowledge graph via W3C SPARQL 1.1.
    FIX #3: max_length=500_000 (~500 KB) prevents memory-bomb payloads.
    """
    query: str = Field(
        ...,
        max_length=5000,
        description="W3C SPARQL 1.1 SELECT query string (max 5000 chars)",
        example="PREFIX schema: <https://schema.org/>\nSELECT ?pred ?obj WHERE { ?sub ?pred ?obj } LIMIT 25"
    )
    root_domain: Optional[str] = Field(default="site", description="Target domain context")
    rdf_turtle: Optional[str] = Field(
        default=None,
        max_length=500_000,
        description="Turtle RDF string to query against (max 500 KB)"
    )


class SparqlQueryResponse(BaseModel):
    """
    Structured response containing tabular results from a SPARQL 1.1 execution.
    """
    query: str
    columns: List[str] = Field(default_factory=list, description="Bound variable column names")
    rows: List[List[str]] = Field(default_factory=list, description="Row values for each bound variable")
    row_count: int = Field(default=0, description="Total rows returned")
    execution_status: str = Field(default="success", description="'success' or 'failed'")
    error: Optional[str] = Field(default=None, description="Detailed error message if query execution failed")


class LinkPredictionRequest(BaseModel):
    """
    Request model for inferring missing knowledge graph relations from ontological priors.
    """
    domain: str = Field(default="example.com", max_length=2048, description="Target domain of the knowledge graph")
    triples: List[SemanticTriple] = Field(default_factory=list, max_length=5000, description="Existing extracted relational triples")
    entities: List[str] = Field(default_factory=list, max_length=5000, description="Recognized key entities")
    topic_hubs: Dict[str, str] = Field(default_factory=dict, description="Canonical topic hubs map")


class LinkPredictionResponse(BaseModel):
    """
    Structured prediction report identifying missing knowledge graph links and graph completion score.
    """
    domain: str
    existing_triples_count: int
    predicted_links_count: int
    predicted_links: List[PredictedLink] = Field(default_factory=list)
    graph_completeness_score: float = Field(..., description="Overall relational completeness (0-100)")
    model_name: str = Field(default="Rule-Based Relation Inference (Ontological Priors)")


class ExportGraphHtmlRequest(BaseModel):
    """
    Request model for generating a standalone, interactive PyVis / Vis.js network graph HTML file.
    """
    domain: str = Field(default="example.com")
    cluster_topology: Optional[ClusterTopology] = None
    triples: List[SemanticTriple] = Field(default_factory=list)
    topic_hubs: Dict[str, str] = Field(default_factory=dict)
    predicted_links: List[PredictedLink] = Field(default_factory=list)


class NTriplesExportRequest(BaseModel):
    """
    FIX #3: max_length prevents memory-bomb payloads.
    """
    rdf_turtle: str = Field(
        ...,
        max_length=500_000,
        description="RDF Turtle serialization to convert into N-Triples (max 500 KB)"
    )


class DraftAlignmentRequest(BaseModel):
    """
    Request model for analyzing draft content against canonical Product Knowledge Graph.
    """
    draft_text: str = Field(..., max_length=20_000, description="Draft blog post, landing page, or PR copy")
    brand_name: str = Field(default="The Platform", max_length=200, description="Brand under evaluation")
    site_url: Optional[str] = Field(default="https://example.com")
    vertical_id: str = Field(default="b2b_saas_fintech")
    triples: List[SemanticTriple] = Field(default_factory=list)
    entities: List[str] = Field(default_factory=list)


class DraftAlignmentResponse(BaseModel):
    """
    Scored alignment report with LLM-as-judge claims verification and fluff analysis.
    """
    product_alignment_score: float = Field(..., description="0-100 Product Alignment Score (PAS)")
    verdict: str
    breakdown: Dict[str, Any]
    fluff_analysis: Dict[str, Any]
    grounded_triples_count: int
    grounded_triples: List[Dict[str, Any]]
    missing_triples_count: int
    missing_triples: List[Dict[str, Any]]
    contradictions: List[str]
    recommendations: List[str]
    llm_judge: Optional[Dict[str, Any]] = None


class ProductBriefRequest(BaseModel):
    """
    Request model for generating a Product Truth Content Brief.
    """
    topic: str = Field(..., max_length=500, description="Content topic or target keyword")
    brand_name: str = Field(default="The Platform")
    vertical_id: str = Field(default="b2b_saas_fintech")
    triples: List[SemanticTriple] = Field(default_factory=list)
    gaps: List[str] = Field(default_factory=list)


class ProductBriefResponse(BaseModel):
    """
    Structured Product Truth Content Brief for writers and AI generation.
    """
    topic: str
    target_alignment_score: int
    must_include_entities: List[str]
    required_relational_triples: List[str]
    prohibited_claims: List[str]
    suggested_outline: List[str]
    differentiation_angles: List[str]


class ExportPdfRequest(BaseModel):
    """
    Request model for 1-Click Executive PDF report generation.
    """
    url: str = Field(..., max_length=2048)
    vertical_id: str = Field(default="b2b_saas_fintech")
    readiness_score: Optional[float] = 0.0
    mandatory_schema_status: Optional[Dict[str, bool]] = Field(default_factory=dict)
    triples: Optional[List[Dict[str, Any]]] = Field(default_factory=list, max_length=5000)
    benchmark_table: Optional[List[Dict[str, Any]]] = Field(default_factory=list, max_length=500)
    google_kg_presence: Optional[Dict[str, Any]] = Field(default=None, description="Google Knowledge Graph verification data")


class GoogleKgRequest(BaseModel):
    """
    Request model for Google Knowledge Graph search.
    """
    query: str = Field(..., max_length=500, description="Brand, company, or concept to search")


class GroundedConcept(BaseModel):
    name: str
    wikidata_id: Optional[str] = None
    wikidata_url: Optional[str] = None
    description: Optional[str] = None


class IndustryDiscoveryRequest(BaseModel):
    """
    Request model for autonomous industry and vertical ontology discovery.
    """
    url: str = Field(..., max_length=2048, description="Target company or product website URL")
    brand_hint: Optional[str] = Field(default=None, max_length=200, description="Optional brand name hint")


class IndustryDiscoveryResponse(BaseModel):
    """
    Structured industry ontology profile dynamically bootstrapped from a domain.
    """
    url: str
    brand_name: str
    vertical_id: str
    display_name: str
    category: str
    category_name: Optional[str] = None
    core_seed_concepts: List[str] = Field(default_factory=list)
    known_compliance: List[str] = Field(default_factory=list)
    compliance_frameworks: List[str] = Field(default_factory=list)
    known_integrations: List[str] = Field(default_factory=list)
    ecosystem_integrations: List[str] = Field(default_factory=list)
    known_pricing: List[str] = Field(default_factory=list)
    known_features: List[str] = Field(default_factory=list)
    known_segments: List[str] = Field(default_factory=list)
    known_deployment: List[str] = Field(default_factory=list)
    known_sla: List[str] = Field(default_factory=list)
    known_replaces: List[str] = Field(default_factory=list)
    gliner_labels: List[str] = Field(default_factory=list)
    suggested_competitors: List[str] = Field(default_factory=list)
    direct_competitors: List[Any] = Field(default_factory=list)
    grounded_entities: List[GroundedConcept] = Field(default_factory=list)
    config_file: Optional[str] = None
    confidence_score: float = Field(default=0.95)
    discovery_summary: str
    domain_scope_description: Optional[str] = None


class ProductTruthRequest(BaseModel):
    """
    Request model for generating the Company Product Truth Matrix.
    """
    marketing_url: str = Field(..., max_length=2048, description="Brand marketing website or landing page")
    brand_name: Optional[str] = Field(default=None, max_length=200, description="Brand name (optional, will auto-detect if omitted)")
    company_name: Optional[str] = Field(default=None, max_length=200, description="Company name alias for brand_name")
    vertical_id: Optional[str] = Field(default=None, description="Industry vertical ID (optional)")
    tech_docs_url: Optional[str] = Field(default=None, max_length=2048, description="Public documentation, developer portal, or OpenAPI URL")
    openapi_spec: Optional[Dict[str, Any]] = Field(default=None, description="Optional raw OpenAPI / Swagger JSON specification")
    tech_docs_text: Optional[str] = Field(default=None, max_length=1_000_000, description="Optional raw markdown or text documentation (max 1 MB)")
    secondary_tech_urls: Optional[List[str]] = Field(
        default=None,
        description=(
            "Optional secondary tech evidence URLs (e.g. integrations page, security/trust page). "
            "Auto-discovered from the marketing site if omitted."
        ),
    )


class ProductTruthMatrixResponse(BaseModel):
    """
    Structured Product Truth Matrix comparing Marketing Claims vs. Technical Ground Truth.
    """
    brand_name: str
    marketing_url: str
    tech_docs_url: Optional[str] = None
    marketing_grounding_index: Optional[float] = Field(
        default=None,
        description=(
            "Percentage of marketing claims backed by verified technical truth (0-100). "
            "None when the technical documentation could not be read: with no evidence to "
            "compare against, an unverified claim is unknown, not disproven. Check "
            "evidence_status before presenting this number."
        ),
    )
    evidence_status: str = Field(
        default="conclusive",
        description=(
            "'conclusive' - enough technical evidence to judge claims. "
            "'low_confidence' - some evidence, too little to rely on; score is provisional. "
            "'inconclusive' - no technical capabilities extracted; no score, no drift alerts."
        ),
    )
    evidence_note: Optional[str] = Field(
        default=None,
        description="Plain-language explanation when evidence is insufficient to judge claims.",
    )
    tech_docs_discovered: bool = Field(
        default=False,
        description=(
            "True when tech_docs_url was found automatically rather than supplied by the "
            "caller. A discovered source may not be the brand's primary documentation, so "
            "results carry more uncertainty than an explicitly provided spec."
        ),
    )
    total_marketing_claims: int
    total_technical_capabilities: int
    verified_claims_count: int
    unbacked_claims_count: int
    hidden_capabilities_count: int
    verified_claims_breakdown: Dict[str, int] = Field(default_factory=dict, description="Counts of verified claims grouped by match strength ('exact', 'substring', 'multi_token', 'head_token')")
    verified_triples: List[SemanticTriple] = Field(default_factory=list, description="Claims proven in both marketing and technical documentation")
    unbacked_claims: List[SemanticTriple] = Field(default_factory=list, description="Marketing claims with no technical backing (Product Drift / Fluff)")
    hidden_capabilities: List[SemanticTriple] = Field(default_factory=list, description="Real technical capabilities omitted from marketing copy")
    drift_alerts: List[str] = Field(default_factory=list, description="Actionable governance risk alerts")
    growth_recommendations: List[str] = Field(default_factory=list, description="Recommendations to market hidden technical gems")
    executive_summary: str
    rdf_turtle: Optional[str] = Field(default=None, description="W3C PROV-O and SKOS compliant RDF Turtle serialization of the Product Truth Graph")
    proof_sources: List[Dict[str, Any]] = Field(default_factory=list, description="Autonomous technical proof sources evaluated (e.g. Trust Centers, Public SDKs, Changelog, Specs)")


class ComparativeCapability(BaseModel):
    """
    Detailed comparison of a capability between Company and Competitor.
    """
    concept: str
    predicate: str
    company_status: str = Field(..., description="'verified', 'unbacked_claim', or 'missing'")
    competitor_status: str = Field(..., description="'verified', 'unbacked_claim', or 'missing'")
    competitor_name: str
    insight: str
    company_evidence: Optional[str] = None
    competitor_evidence: Optional[str] = None


class CounterPositioningAngle(BaseModel):
    """
    Strategic sales & marketing battlecard angle exploiting verified capability gaps.
    """
    target_competitor: str = Field(default="Competitor")
    angle_title: str = Field(default="Competitive Battlecard")
    capability: str = Field(default="Verified Capability", description="Key capability or feature differentiator")
    comparative_status: str = Field(default="company_advantage", description="'company_advantage', 'competitor_fluff_vulnerability', or 'industry_table_stakes'")
    predicate: str = Field(default="automates", description="Predicate relation")
    attack_angle: str = Field(default="", description="High-impact attack angle and messaging")
    discovery_question: str = Field(default="", description="Killer discovery question for RFP / demo")
    fud_counter_defense: str = Field(default="", description="FUD counter-defense backed by technical truth")
    core_narrative: str = Field(default="", description="2-3 sentence strategic executive narrative")
    company_differentiator: str = Field(default="", description="Verified technical truth")
    competitor_vulnerability: str = Field(default="", description="Competitor limitation or fluff")
    suggested_campaign_topics: List[str] = Field(default_factory=list)


class CategoryWhitespace(BaseModel):
    """A concept the category expects that nobody in the analysed set is claiming.

    Derived from the industry ontology rather than from any competitor, so unlike the
    other outputs it can surface an opportunity no rival has named yet.
    """
    concept: str = Field(..., description="The industry concept nobody markets")
    source: str = Field(
        ...,
        description="Which part of the industry ontology expects it: 'seed_concept' or 'automation'",
    )
    company_markets_it: bool = Field(
        default=False,
        description="Always False for whitespace; kept explicit so the claim is auditable.",
    )
    competitors_marketing_it: List[str] = Field(
        default_factory=list,
        description="Always empty for whitespace; present so a near-miss can be reported later.",
    )
    insight: str = Field(..., description="What a marketer should do with it")


class TriOntologyAlignmentRequest(BaseModel):
    """
    Request model for full Tri-Ontology alignment across Company, Competitors, and Industry standards.
    """
    company: ProductTruthRequest
    competitors: List[ProductTruthRequest] = Field(..., min_length=1, max_length=5)
    vertical_id: Optional[str] = Field(default="b2b_saas_fintech")


class TriOntologyAlignmentResponse(BaseModel):
    """
    Structured Tri-Ontology alignment report revealing competitive advantages and marketing angles.
    """
    company_name: str
    competitor_names: List[str]
    vertical_id: str
    industry_category: str
    company_advantages: List[ComparativeCapability] = Field(default_factory=list, description="Capabilities verified in Company where competitor is unbacked or missing")
    competitor_vulnerabilities: List[ComparativeCapability] = Field(default_factory=list, description="Competitor marketing claims unbacked by technical docs")
    competitor_advantages: List[ComparativeCapability] = Field(default_factory=list, description="Real technical capabilities verified in competitor that company lacks")
    table_stakes: List[str] = Field(default_factory=list, description="Baseline capabilities expected by industry ontology and shared by all")
    category_whitespace: List[CategoryWhitespace] = Field(
        default_factory=list,
        description=(
            "Concepts the industry ontology expects of this category that neither the "
            "company nor any analysed competitor markets. Unclaimed positioning: the only "
            "output here derived from the industry layer rather than from a rival."
        ),
    )
    counter_positioning_briefs: List[CounterPositioningAngle] = Field(default_factory=list, description="Actionable sales and marketing battlecard angles")
    executive_summary: str


class CompetitorOntologyRequest(BaseModel):
    """
    Request model to crawl and extract the ontology of a competitor website.
    """
    url: str = Field(..., max_length=2048, description="Competitor homepage or product URL")
    brand_name: Optional[str] = Field(default=None, max_length=200, description="Competitor brand name (optional)")
    crawl_subpages: bool = Field(default=True, description="Whether to discover and crawl /pricing, /features, /integrations")
    max_subpages: int = Field(default=3, ge=1, le=10, description="Max subpages to crawl")


class CompetitorOntologyResponse(BaseModel):
    """
    Structured ontology of a competitor extracted from their public digital presence.
    """
    brand_name: str
    url: str
    pages_analyzed: int
    total_claims: int
    capabilities_automated: List[str] = Field(default_factory=list)
    integrations_claimed: List[str] = Field(default_factory=list)
    compliance_claimed: List[str] = Field(default_factory=list)
    pricing_models: List[str] = Field(default_factory=list)
    triples: List[SemanticTriple] = Field(default_factory=list)
    schema_org_types: List[str] = Field(default_factory=list)
    google_kg_grounded: bool = False
    wikidata_grounded: bool = False
    ontology_summary: str


class GeoQueryItem(BaseModel):
    """A synthesized or user-supplied high-intent buyer query."""
    query_text: str = Field(..., description="High-intent buyer query, e.g. 'Which billing software supports ASC 606?'")
    category: str = Field(default="Feature & Compliance", description="Intent category (Compliance, Integration, Automation, Shortlist)")
    intent_stage: str = Field(default="Evaluation", description="Buyer journey stage (Discovery, Evaluation, Vendor Selection)")
    targeted_capabilities: List[str] = Field(default_factory=list, description="Targeted ontology concepts/capabilities")


class GeoProbeResult(BaseModel):
    """Audit result for a single buyer query probed against an AI search engine."""
    query: str
    category: str = "Evaluation"
    synthesized_answer: str = Field(..., description="Answer text from the AI search engine")
    engine_used: str = Field(default="Google Gemini 2.5 Flash", description="AI search engine or fallback tier used")
    brand_cited: bool = Field(..., description="Whether the evaluated brand was cited/recommended")
    brand_rank: Optional[int] = Field(default=None, description="Rank/position of the brand if mentioned (1 = top recommendation)")
    competitors_cited: List[str] = Field(default_factory=list, description="Competitor brands cited in the response")
    verified_claims: List[str] = Field(default_factory=list, description="Claims about the brand backed by the ontology truth graph")
    hallucinated_claims: List[str] = Field(default_factory=list, description="Claims about the brand that lack ontology backing")
    citation_urls: List[str] = Field(default_factory=list, description="URLs cited or referenced by the engine")


class GeoAuditRequest(BaseModel):
    """Request payload for executing a multi-query GEO citation audit."""
    brand_name: str = Field(..., max_length=200, description="Target brand to evaluate, e.g. 'Ordway'")
    domain: str = Field(..., max_length=2048, description="Target brand website domain, e.g. 'ordwaylabs.com'")
    competitor_names: List[str] = Field(default_factory=list, max_length=25, description="Competitors to track, e.g. ['Chargebee', 'Stripe']")
    triples: List[SemanticTriple] = Field(default_factory=list, max_length=5000, description="Verified ontology triples for grounding check")
    vertical_id: Optional[str] = Field(default="b2b_saas_fintech", description="Industry vertical ID")
    custom_queries: Optional[List[str]] = Field(default=None, max_length=10, description="Optional custom buyer queries to probe (max 10 outbound probes)")


class GeoAuditResponse(BaseModel):
    """Aggregated GEO Share of Voice & Citation Audit Report."""
    brand_name: str
    share_of_voice: float = Field(..., description="Percentage of queries where brand was cited (0-100%)")
    weighted_sov: float = Field(..., description="Rank-weighted Share of Voice score (0-100%)")
    ai_mention_rate: float = Field(..., description="Percentage of queries with positive brand mention")
    hallucination_rate: float = Field(default=0.0, description="Percentage of AI-attributed claims lacking ontology verification")
    competitor_sov: Dict[str, float] = Field(default_factory=dict, description="Share of Voice for each competitor (0-100%)")
    probe_results: List[GeoProbeResult] = Field(default_factory=list, description="Detailed probe results for each buyer query")
    citation_gap_queries: List[str] = Field(default_factory=list, description="High-intent queries where competitors were cited but brand was omitted")
    geo_recommendations: List[str] = Field(default_factory=list, description="Strategic recommendations to improve AI search citation rate")


