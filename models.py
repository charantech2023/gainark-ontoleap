"""
GainARK OntoLeap — Enterprise Domain Models & Schema Definitions

This module defines Pydantic data models representing the full lifecycle of
ontological SEO analysis, structured data extraction, semantic knowledge graphs,
topic silo clustering, NetworkX topological metrics, generative AI search simulations,
and W3C standard RDF/SPARQL representations.
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from pydantic import BaseModel, Field


class VerticalConfig(BaseModel):
    vertical_id: str = Field(..., description="Unique identifier for the vertical domain")
    display_name: str = Field(..., description="Human-readable domain name")
    gliner_labels: List[str] = Field(..., description="Target entity types for GLiNER zero-shot NER")
    mandatory_schema_types: List[str] = Field(..., description="Schema.org types required for this domain")
    core_seed_concepts: List[str] = Field(..., description="Key domain concepts and terminology")


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
    total_score: float = Field(..., description="Overall ontology readiness score out of 100")
    details: Dict[str, Any] = Field(default_factory=dict)


class SemanticTriple(BaseModel):
    subject: str = Field(..., description="Entity subject (e.g. company or platform name)")
    predicate: str = Field(..., description="Predicate relation (automates, integratesWith, compliesWith, supportsPricingModel)")
    object: str = Field(..., description="Object entity, standard, system, or capability")
    confidence: float = Field(default=0.85, description="Extraction confidence score (0-1)")
    evidence_sentence: Optional[str] = Field(default=None, description="Source context sentence")


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
    readiness_score: float = Field(default=0.0, description="Ontology readiness score (0-100)")
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

    @property
    def extraction(self) -> "ExtractionResult":
        return self


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
    crawled_at: datetime = Field(default_factory=datetime.utcnow)
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
    query: str = Field(..., description="User search query, e.g., 'What accounting standards does the platform comply with?'")
    root_domain: str = Field(..., description="The audited domain")
    triples: List[SemanticTriple] = Field(default_factory=list, description="Extracted domain triples")
    topic_hubs: Dict[str, str] = Field(default_factory=dict, description="Canonical topic hubs map")
    entities: List[str] = Field(default_factory=list, description="Extracted key entities")


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
    """
    query: str = Field(
        ...,
        description="W3C SPARQL 1.1 query string",
        example="PREFIX schema: <https://schema.org/>\nSELECT ?pred ?obj WHERE { ?sub ?pred ?obj } LIMIT 25"
    )
    root_domain: Optional[str] = Field(default="site", description="Target domain context")
    rdf_turtle: Optional[str] = Field(default=None, description="Turtle RDF string to query against")


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





