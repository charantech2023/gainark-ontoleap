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


# ==============================================================================
# PURE KNOWLEDGE GRAPH & ONTOLOGY MODELS
# ==============================================================================

class KGNode(BaseModel):
    """
    An entity or concept node in the Knowledge Graph.
    """
    id: str = Field(..., description="Unique node URI or slug, e.g. 'entity:stripe'")
    canonical_name: str = Field(..., description="Canonical display name, e.g. 'Stripe'")
    entity_type: str = Field(default="Entity", description="Ontological class/label, e.g. 'IntegrationPartner', 'BillingFeature', 'Standard'")
    aliases: List[str] = Field(default_factory=list, description="Observed surface forms / synonyms")
    wikidata_id: Optional[str] = Field(default=None, description="Wikidata identity if grounded: a Q-ID or a full Wikidata URL. Render it with entity_grounding.wikidata_uri()")
    description: Optional[str] = Field(default=None, description="Discovered or linked entity definition")
    mentions_count: int = Field(default=1, description="Number of times this node was observed")
    source_urls: List[str] = Field(default_factory=list, description="URLs where this entity was found")


class KGEdge(BaseModel):
    """
    A semantic relation (triple edge) connecting two KGNode entities.
    """
    id: str = Field(..., description="Unique edge identifier, e.g. 'edge:ordway-integrateswith-stripe'")
    source: str = Field(..., description="Source node ID or canonical name (Subject)")
    target: str = Field(..., description="Target node ID or canonical name (Object)")
    predicate: str = Field(..., description="Relationship type, e.g. 'integratesWith', 'compliesWith', 'automates', 'subClassOf'")
    source_type: Optional[str] = Field(default=None, description="Subject entity class")
    target_type: Optional[str] = Field(default=None, description="Object entity class")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Extraction confidence")
    provenance_sentence: Optional[str] = Field(default=None, description="Exact text context proving the relationship")
    source_url: Optional[str] = Field(default=None, description="URL where relation was discovered")


class PageKnowledgeGraph(BaseModel):
    """
    Complete semantic graph extracted from a single web page.
    """
    url: str
    title: Optional[str] = None
    nodes: List[KGNode] = Field(default_factory=list, description="Extracted entity nodes")
    edges: List[KGEdge] = Field(default_factory=list, description="Extracted semantic relations")
    classes_discovered: List[str] = Field(default_factory=list, description="Unique ontological classes present on page")
    predicates_discovered: List[str] = Field(default_factory=list, description="Unique predicates present on page")
    embedded_schemas: List[str] = Field(default_factory=list, description="Existing Schema.org types found in page markup")
    # The vertical decides which entity labels are looked for, so it decides what this
    # graph contains. Reporting it lets a caller see the vocabulary that shaped the
    # answer - and, when it was inferred, judge how firm the inference was.
    vertical_id: Optional[str] = Field(default=None, description="Reference vertical whose vocabulary shaped this extraction")
    vertical_source: Optional[str] = Field(default=None, description="'requested' when the caller named it, 'auto-detected' when inferred from the content")
    vertical_reason: Optional[str] = Field(default=None, description="Evidence count and margin behind an auto-detected vertical")
    vertical_evidence: List[str] = Field(default_factory=list, description="Vocabulary terms found in the content that decided an auto-detected vertical")
    export_jsonld: Optional[Dict[str, Any]] = Field(default=None, description="Standard W3C Schema.org / JSON-LD @graph payload")
    export_turtle: Optional[str] = Field(default=None, description="W3C RDF Turtle serialization")


class InducedClassRelation(BaseModel):
    """
    A discovered structural relationship between ontological classes in a domain.
    """
    source_class: str
    predicate: str
    target_class: str
    count: int = 1


class TopicCluster(BaseModel):
    """
    A semantic topic cluster discovered across the website.
    """
    cluster_id: str
    cluster_label: str
    representative_entities: List[str] = Field(default_factory=list)
    page_urls: List[str] = Field(default_factory=list)


class PageFailure(BaseModel):
    """
    A page that was selected for crawling but produced no graph.
    """
    url: str
    error: str


class SiteKnowledgeGraph(BaseModel):
    """
    Unified, canonicalized Knowledge Graph synthesized across an entire domain.
    """
    domain: str
    pages_crawled: int = Field(description="Pages that were fetched and produced a graph")
    page_urls: List[str] = Field(default_factory=list)
    # Without these, a crawl that lost most of its pages is indistinguishable from a small
    # site: both return a low page count and a low coverage score, and they call for
    # opposite responses from the reader.
    pages_discovered: int = Field(default=0, description="Distinct internal pages seen while crawling. Only pages linked from somewhere already read can be counted, so this is a floor on the size of the site, not its size.")
    pages_requested: int = Field(default=0, description="Page budget for this run. Counts attempts, so pages_crawled + pages_failed reaches it when the crawl is limited rather than exhausted.")
    pages_failed: int = Field(default=0, description="Pages attempted that could not be fetched or extracted")
    failed_pages: List[PageFailure] = Field(default_factory=list, description="Why each page failed, first 25")
    nodes: List[KGNode] = Field(default_factory=list, description="Canonical, coreference-resolved entity nodes")
    edges: List[KGEdge] = Field(default_factory=list, description="Deduplicated semantic relations")
    induced_class_hierarchy: List[InducedClassRelation] = Field(default_factory=list, description="Induced domain ontology schema")
    topic_clusters: List[TopicCluster] = Field(default_factory=list, description="High-level topic silos")
    top_authority_hubs: List[str] = Field(default_factory=list, description="Central topic hubs by PageRank")
    # Coverage is scored against this vertical's concepts, so a caller that cannot read it
    # back cannot align against the vertical the crawl actually used.
    vertical_id: Optional[str] = Field(default=None, description="Reference vertical whose vocabulary shaped this crawl")
    vertical_source: Optional[str] = Field(default=None, description="'requested' when the caller named it, 'auto-detected' when inferred from the site")
    vertical_reason: Optional[str] = Field(default=None, description="Evidence count and margin behind an auto-detected vertical")
    vertical_evidence: List[str] = Field(default_factory=list, description="Vocabulary terms found on the site that decided an auto-detected vertical")
    export_jsonld: Optional[Dict[str, Any]] = Field(default=None, description="Site-wide JSON-LD @graph")
    export_turtle: Optional[str] = Field(default=None, description="Site-wide RDF Turtle export")


class IndustryConcept(BaseModel):
    """
    A canonical concept defined within an industry reference ontology.
    """
    id: str
    pref_label: str
    kind: str = "concept"
    definition: Optional[str] = None
    alt_labels: List[str] = Field(default_factory=list)
    broader: Optional[str] = None


class IndustryOntologyModel(BaseModel):
    """
    Reference vertical ontology containing industry taxonomies and expected standards.
    """
    vertical_id: str
    display_name: str
    classes: List[str] = Field(default_factory=list, description="Standard entity types, e.g. 'SoftwarePlatform', 'BillingFeature'")
    core_seed_concepts: List[str] = Field(default_factory=list)
    concepts: List[IndustryConcept] = Field(default_factory=list, description="SKOS-style hierarchy of domain concepts")
    known_integrations: List[str] = Field(default_factory=list)
    known_compliance: List[str] = Field(default_factory=list)
    standard_predicates: List[str] = Field(
        default_factory=lambda: ["automates", "integratesWith", "compliesWith", "supportsPricingModel", "subClassOf", "partOf"]
    )


class GraphAlignmentResult(BaseModel):
    """
    Result of aligning a Page or Site Knowledge Graph against an Industry Reference Ontology.
    """
    subject_identifier: str = Field(..., description="Target URL or domain evaluated")
    vertical_id: str
    industry_name: str
    total_industry_concepts: int
    covered_concepts: List[str] = Field(default_factory=list, description="Industry standard concepts verified in the graph")
    category_whitespace: List[str] = Field(default_factory=list, description="Industry standard concepts unclaimed by the graph")
    proprietary_concepts: List[str] = Field(default_factory=list, description="Concepts in the graph not defined in the standard industry taxonomy")
    coverage_score: float = Field(..., ge=0.0, le=100.0, description="Percentage of the full industry reference ontology covered (0-100%)")
    seed_coverage_score: float = Field(default=0.0, ge=0.0, le=100.0, description="Percentage of the vertical's core seed concepts covered (0-100%)")
    compliance_standards_covered: List[str] = Field(default_factory=list)
    integrations_covered: List[str] = Field(default_factory=list)
    # An unclaimed concept means "absent from the graph", and the graph holds only the
    # pages that were read. Without this, a gap on an unread page reads as missing content.
    pages_sampled: int = Field(default=0, description="Pages that contributed to the graph this was measured on")
    pages_found: int = Field(default=0, description="Distinct internal pages the crawl saw, read or not")
    whitespace_caveat: Optional[str] = Field(default=None, description="Set when the unclaimed list may overstate, because the graph covers less than the whole site")


# API Request Models
class PageKGRequest(BaseModel):
    url: Optional[str] = Field(default=None, max_length=2048, description="Target webpage URL to parse")
    html_content: Optional[str] = Field(default=None, description="Optional raw HTML content to parse")
    # Omitted means "work it out". Defaulting measured every page against a billing
    # vocabulary regardless of what the site sells.
    vertical_id: Optional[str] = Field(default=None, description="Industry vertical. Omit to route automatically from the page content.")


class SiteKGRequest(BaseModel):
    domain_or_url: str = Field(..., max_length=2048, description="Target domain or starting URL, e.g. 'https://www.ordwaylabs.com'")
    # Measured: about 32s of model load on a cold instance plus 16.3s per page. The
    # Cloud Run request timeout is 900s, so 40 pages is roughly 11 minutes and fits with
    # margin. Raising this without raising that timeout only produces slower 504s.
    #
    # The default is the ceiling because the crawl now recurses, and a small budget spent
    # on a deep site is what produced false gaps: an unread page reports every concept on
    # it as missing content. ordwaylabs.com offers 106 pages, so even 40 is a sample - but
    # reading 15 of them was measuring an eighth of the site and reporting the rest as
    # absent. A slow site can still exhaust the timeout and lose the whole crawl, which is
    # what background jobs are for.
    max_pages: int = Field(default=40, ge=1, le=40, description="Maximum sub-pages to crawl and aggregate (a 40-page crawl takes roughly 11 minutes)")
    # Omitted means "work it out from the site". Defaulting to a vertical measured every
    # domain against a billing vocabulary, so a security vendor that omitted this was told
    # it covered almost nothing - a wrong answer that looked like a real result.
    vertical_id: Optional[str] = Field(default=None, description="Industry vertical. Omit to route automatically from the site content.")


class KGAlignmentRequest(BaseModel):
    domain_or_url: Optional[str] = Field(default=None, max_length=2048, description="Target domain or URL to evaluate")
    page_kg: Optional[PageKnowledgeGraph] = Field(default=None, description="Direct page knowledge graph to evaluate")
    site_kg: Optional[SiteKnowledgeGraph] = Field(default=None, description="Direct site knowledge graph to evaluate")
    # Omitted means "use the vertical the graph was built with, or work it out". It was
    # a plain default, so omitting it scored every graph against billing regardless of
    # what the graph itself said.
    vertical_id: Optional[str] = Field(default=None, description="Industry vertical to align against. Omit to use the graph's own vertical, or to route from the domain.")
    max_pages: int = Field(default=5, ge=1, le=25, description="Pages to crawl if not cached")



