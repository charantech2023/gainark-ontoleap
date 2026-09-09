"""
GainARK OntoLeap — W3C Knowledge Graph & Semantic Modeling Engine

Provides standard W3C knowledge graph capabilities:
1. RDFLib Graph generation with Schema.org, W3C PROV-O, SKOS, DCAT, and Dublin Core.
2. Serialization to W3C Turtle (.ttl) and N-Triples (.nt).
3. W3C SPARQL 1.1 query execution engine with LIMIT 1000 and mutation guards.
4. Formal W3C OWL 2 DL ontology construction with inverse properties, disjoint classes,
   and Protégé-compatible OWL/XML export.
"""

import os
import re
import json
import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Set, Tuple
from urllib.parse import urlparse

from rdflib import Graph, Literal, RDF, RDFS, URIRef, Namespace, OWL, XSD
from models import SemanticTriple
from constants import WIKIDATA_KB
from entity_grounding import ground_url, prefetch as prefetch_grounding
from ontology_schema import (
    scheme_uri as onto_scheme_uri,
    concept_uri as onto_concept_uri,
    slug_for_label,
)

# The curated Q-IDs, still exported under the old name for anything importing it.
# Grounding itself goes through entity_grounding, which keeps this dict as its fast path
# and adds live resolution for the phrases it has never heard of.
WIKIDATA_KNOWLEDGE_BASE: Dict[str, str] = WIKIDATA_KB



# ---------------------------------------------------------------------------
# Entity Alias Deduplication — owl:sameAs
# Prevents "Salesforce" vs "Salesforce CRM" vs "Salesforce, Inc." being
# treated as 3 different entities in the knowledge graph.
# ---------------------------------------------------------------------------

# Known entity aliases for B2B SaaS ecosystem
_ENTITY_ALIASES: Dict[str, List[str]] = {
    "salesforce": ["salesforce crm", "salesforce inc", "salesforce.com", "sfdc"],
    "netsuite": ["oracle netsuite", "netsuite erp"],
    "quickbooks": ["quickbooks online", "intuit quickbooks", "qbo"],
    "hubspot": ["hubspot crm", "hubspot marketing"],
    "microsoft dynamics": ["dynamics 365", "ms dynamics", "dynamics crm"],
    "sage intacct": ["intacct", "sage intacct cloud"],
    "stripe": ["stripe payments", "stripe billing"],
    "workday": ["workday hcm", "workday financials"],
    "oracle": ["oracle cloud", "oracle erp", "oracle financials"],
    "sap": ["sap erp", "sap s/4hana", "sap business one"],
    "xero": ["xero accounting"],
    "avalara": ["avalara avatax"],
}

# Reverse lookup: alias → canonical name
_ALIAS_TO_CANONICAL: Dict[str, str] = {}
for _canonical, _aliases in _ENTITY_ALIASES.items():
    for _alias in _aliases:
        _ALIAS_TO_CANONICAL[_alias] = _canonical


def link_entity_aliases(g: Graph, domain: str) -> int:
    """
    Asserts owl:sameAs links between known entity aliases and their canonical URIs.
    Call this after building the RDF graph to stitch duplicate entity nodes together.
    Returns the number of sameAs triples added.
    """
    added = 0
    for canonical_name, aliases in _ENTITY_ALIASES.items():
        canonical_clean = re.sub(r"[^a-zA-Z0-9]+", "", canonical_name.title())
        canonical_uri = URIRef(f"https://{domain}/entity/{canonical_clean}")
        for alias in aliases:
            alias_clean = re.sub(r"[^a-zA-Z0-9]+", "", alias.title())
            alias_uri = URIRef(f"https://{domain}/entity/{alias_clean}")
            if alias_uri != canonical_uri:
                g.add((alias_uri, OWL.sameAs, canonical_uri))
                added += 1
    return added


def resolve_canonical_name(entity_name: str) -> str:
    """
    Returns the canonical form of an entity name if it's a known alias.
    E.g. 'Salesforce CRM' → 'salesforce', 'Stripe Payments' → 'stripe'.
    Returns the original (lowercased) if no alias match found.
    """
    normalized = entity_name.strip().lower()
    return _ALIAS_TO_CANONICAL.get(normalized, normalized)


def build_rdf_graph(
    domain: str,
    triples: List[SemanticTriple],
    hubs: Dict[str, str],
    entities: List[str],
    concept_hierarchy: Optional[Dict[str, str]] = None,
    vertical_id: Optional[str] = None
) -> Graph:
    """
    Constructs an in-memory RDFLib Graph with standard W3C (Schema.org, PROV-O, SKOS, DCAT, DCTERMS) namespaces,
    connecting the organization root, capabilities, integrations, canonical topic hubs,
    hierarchical category taxonomies (SKOS), and evidentiary provenance chains (PROV-O).
    """
    g = Graph()
    SCHEMA = Namespace("https://schema.org/")
    LOCAL = Namespace(f"https://{domain}/ontology/")
    PROV = Namespace("http://www.w3.org/ns/prov#")
    SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")
    DCTERMS = Namespace("http://purl.org/dc/terms/")
    DCAT = Namespace("http://www.w3.org/ns/dcat#")

    g.bind("schema", SCHEMA)
    g.bind("onto", LOCAL)
    g.bind("rdfs", RDFS)
    g.bind("rdf", RDF)
    g.bind("prov", PROV)
    g.bind("skos", SKOS)
    g.bind("dcterms", DCTERMS)
    g.bind("dcat", DCAT)

    brand = triples[0].subject if triples else domain.split(".")[0].capitalize()
    brand_clean = re.sub(r'[^a-zA-Z0-9]+', '', brand) or "Platform"
    root_uri = URIRef(f"https://{domain}/#{brand_clean}")

    # Ground everything this build will ask about, in one concurrent batch, before the
    # loops below start asking. ground_url() never touches the network, so without this
    # the graph is grounded by the 40 curated Q-IDs alone; with it, phrases outside that
    # dict get an external identity too. Costs one round trip, and nothing if the
    # resolver is unreachable.
    prefetch_grounding([brand] + [t.object for t in triples] + list(hubs.keys()))

    # Root Organization & Software Application definitions
    g.add((root_uri, RDF.type, SCHEMA.SoftwareApplication))
    g.add((root_uri, RDF.type, SCHEMA.Organization))
    g.add((root_uri, SCHEMA.name, Literal(brand)))
    g.add((root_uri, SCHEMA.url, URIRef(f"https://{domain}/")))

    # Check if brand matches Wikidata
    brand_url = ground_url(brand)
    if brand_url:
        g.add((root_uri, SCHEMA.sameAs, URIRef(brand_url)))

    # W3C PROV-O: SoftwareAgent & Activity Definitions
    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()
    now_tag = now_utc.strftime("%Y%m%d%H%M%S")

    agent_uri = URIRef(f"https://{domain}/#ontoleap-agent")
    g.add((agent_uri, RDF.type, PROV.SoftwareAgent))
    g.add((agent_uri, RDFS.label, Literal("GainARK OntoLeap Engine")))
    g.add((agent_uri, SCHEMA.name, Literal("OntoLeap Knowledge Graph & Governance Engine")))

    activity_uri = URIRef(f"https://{domain}/activity/audit-{now_tag}")
    g.add((activity_uri, RDF.type, PROV.Activity))
    g.add((activity_uri, RDFS.label, Literal(f"Ontology Extraction and Audit for {domain}")))
    g.add((activity_uri, PROV.wasAssociatedWith, agent_uri))
    g.add((activity_uri, PROV.startedAtTime, Literal(now_iso, datatype=XSD.dateTime)))
    g.add((activity_uri, PROV.endedAtTime, Literal(now_iso, datatype=XSD.dateTime)))

    # W3C DCAT & Dublin Core Dataset Cataloging & Governance Metadata
    dataset_uri = URIRef(f"https://{domain}/dataset/knowledge-graph")
    g.add((dataset_uri, RDF.type, DCAT.Dataset))
    g.add((dataset_uri, DCTERMS.title, Literal(f"{brand} Enterprise Knowledge Graph")))
    g.add((dataset_uri, DCTERMS.description, Literal(f"Formal semantic knowledge graph and competitive intelligence for {brand}.")))
    g.add((dataset_uri, DCTERMS.creator, agent_uri))
    g.add((dataset_uri, DCTERMS.created, Literal(now_iso, datatype=XSD.dateTime)))
    g.add((dataset_uri, DCTERMS.modified, Literal(now_iso, datatype=XSD.dateTime)))
    g.add((dataset_uri, DCTERMS.license, URIRef("https://creativecommons.org/licenses/by/4.0/")))
    g.add((dataset_uri, PROV.wasGeneratedBy, activity_uri))

    # W3C SKOS: Category Taxonomy Concept Scheme & Hierarchy
    #
    # The scheme and its concepts belong to the VERTICAL, not to the site being
    # audited. They used to be minted under the client's own domain, which made
    # "Revenue Schedules" in an Ordway graph a different resource from the same
    # concept in a Chargebee graph: the shared ontology existed only as a private
    # copy per client, and no question could span companies. Instance data below
    # (the product, its claims, the pages they came from) stays under `domain`,
    # because that genuinely is client-specific.
    v_id = vertical_id or "b2b_saas_fintech"
    scheme_uri = URIRef(onto_scheme_uri(v_id))
    g.add((scheme_uri, RDF.type, SKOS.ConceptScheme))
    g.add((scheme_uri, SKOS.prefLabel, Literal(f"OntoLeap Category Taxonomy ({v_id})")))
    g.add((scheme_uri, PROV.wasGeneratedBy, activity_uri))
    g.add((scheme_uri, PROV.wasAttributedTo, agent_uri))

    # Load the vertical profile for the hierarchy and, where the vertical has been
    # promoted to identified concepts, the frozen ids and definitions.
    # Loaded through VerticalConfig rather than raw JSON so the hierarchy comes from
    # the same derivation the extractor uses. Reading the file's concept_hierarchy key
    # directly gave a stale tree - it is a derived view, regenerated from `concepts`
    # on load - and ASC 606 was emitted under Revenue Recognition after having been
    # moved to the standards branch.
    concept_records: Dict[str, dict] = {}
    try:
        from models import VerticalConfig
        curr_dir = os.path.dirname(os.path.abspath(__file__))
        v_specific = os.path.join(curr_dir, "verticals", f"{v_id}.json")
        cfg_path = v_specific if os.path.exists(v_specific) else os.path.join(
            curr_dir, "vertical_config.json")
        cdata = {}
        if os.path.exists(cfg_path):
            with open(cfg_path, "r", encoding="utf-8") as vf:
                cdata = json.load(vf)
        cfg = VerticalConfig(**cdata) if cdata else None
        if cfg is not None:
            for rec in cfg.concepts:
                d = rec.model_dump()
                concept_records[rec.prefLabel.strip().lower()] = d
                for alt in rec.altLabels:
                    concept_records.setdefault(alt.strip().lower(), d)
            if not concept_hierarchy:
                concept_hierarchy = dict(cfg.concept_hierarchy or {})
    except Exception:
        # A missing or malformed profile must not stop a graph being emitted; concepts
        # simply fall back to slugged labels and carry no definitions.
        concept_hierarchy = concept_hierarchy or {}

    concept_hierarchy = concept_hierarchy or {}
    skos_concepts_created: Set[str] = set()

    def _concept_record(c_name: str) -> Optional[dict]:
        return concept_records.get((c_name or "").strip().lower())

    def _make_concept_uri(c_name: str) -> URIRef:
        """URI of a concept, from its frozen id where the vertical defines one.

        Falling back to a slug of the label keeps verticals that have not been promoted
        to identified concepts working, but such a URI moves if the label is ever
        reworded - which is the whole reason ids exist.
        """
        rec = _concept_record(c_name)
        cid = rec["id"] if rec else slug_for_label(c_name)
        return URIRef(onto_concept_uri(v_id, cid))

    for child_c, parent_c in concept_hierarchy.items():
        child_uri = _make_concept_uri(child_c)
        parent_uri = _make_concept_uri(parent_c)

        def _declare_concept(label: str, uri: URIRef) -> None:
            """Type a concept and attach everything the vertical knows about it.

            The definition and alternate labels are the point of this: without
            skos:definition nothing downstream - reviewer or model - can check whether
            a match against a page was the right concept, and without skos:altLabel the
            marketing spelling and the documentation spelling stay unlinked in the graph.
            """
            if label in skos_concepts_created:
                return
            g.add((uri, RDF.type, SKOS.Concept))
            g.add((uri, SKOS.inScheme, scheme_uri))
            g.add((uri, SKOS.prefLabel, Literal(label)))
            rec = _concept_record(label)
            if rec:
                if rec.get("definition"):
                    g.add((uri, SKOS.definition, Literal(rec["definition"])))
                for alt in rec.get("altLabels") or []:
                    g.add((uri, SKOS.altLabel, Literal(alt)))
                if rec.get("kind"):
                    g.add((uri, SKOS.notation, Literal(rec["kind"])))
                # A standard does not subsume what it regulates, so the governing link
                # is its own relation rather than another skos:broader edge.
                for governed_id in rec.get("governs") or []:
                    g.add((uri, LOCAL.governs, URIRef(onto_concept_uri(v_id, governed_id))))
            skos_concepts_created.add(label)

        _declare_concept(child_c, child_uri)
        _declare_concept(parent_c, parent_uri)

        g.add((child_uri, SKOS.broader, parent_uri))
        g.add((parent_uri, SKOS.narrower, child_uri))

    pred_map = {
        "automates": SCHEMA.potentialAction,
        "integratesWith": SCHEMA.isRelatedTo,
        "compliesWith": SCHEMA.legislationApplies,
        "supportsPricingModel": SCHEMA.priceSpecification,
        "hasFeature": SCHEMA.featureList,
        "replacesWorkflow": SCHEMA.actionOption,
        "targetsSegment": SCHEMA.audience,
        "servesIndustry": SCHEMA.industry,
        "deployedAs": SCHEMA.deliveryLeadTime,
        "certifiedBy": SCHEMA.award,
        "hasAPI": SCHEMA.interface,
        "supportsLocale": SCHEMA.availableLanguage,
        "guarantees": SCHEMA.serviceOutput,
        "competesAgainst": SCHEMA.isSimilarTo
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

            # W3C PROV-O Lineage & Evidentiary Grounding
            g.add((obj_uri, RDF.type, PROV.Entity))
            g.add((obj_uri, PROV.wasGeneratedBy, activity_uri))
            g.add((obj_uri, PROV.wasAttributedTo, agent_uri))
            g.add((obj_uri, PROV.generatedAtTime, Literal(now_iso, datatype=XSD.dateTime)))

            if t.evidence_sentence:
                g.add((obj_uri, PROV.wasQuotedFrom, Literal(t.evidence_sentence)))

            if t.provenance:
                if " ⟷ " in t.provenance:
                    m_src, t_src = t.provenance.split(" ⟷ ", 1)
                    m_src = m_src.strip()
                    t_src = t_src.strip()
                    if m_src:
                        m_clean = re.sub(r'[^a-zA-Z0-9]+', '', m_src) or "mktg"
                        m_uri = URIRef(m_src) if m_src.startswith("http") else URIRef(f"https://{domain}/source/{m_clean}")
                        g.add((obj_uri, PROV.wasDerivedFrom, m_uri))
                        g.add((m_uri, RDF.type, PROV.Entity))
                        g.add((m_uri, RDFS.label, Literal(f"Marketing Source: {m_src}")))
                    if t_src:
                        t_clean = re.sub(r'[^a-zA-Z0-9]+', '', t_src) or "tech"
                        t_uri = URIRef(t_src) if t_src.startswith("http") else URIRef(f"https://{domain}/source/{t_clean}")
                        g.add((obj_uri, PROV.wasDerivedFrom, t_uri))
                        g.add((t_uri, RDF.type, PROV.Entity))
                        g.add((t_uri, RDFS.label, Literal(f"Technical Spec: {t_src}")))
                else:
                    src = t.provenance.strip()
                    src_clean = re.sub(r'[^a-zA-Z0-9]+', '', src) or "source"
                    src_uri = URIRef(src) if src.startswith("http") else URIRef(f"https://{domain}/source/{src_clean}")
                    g.add((obj_uri, PROV.hadPrimarySource, src_uri))
                    g.add((src_uri, RDF.type, PROV.Entity))
                    g.add((src_uri, RDFS.label, Literal(f"Primary Source: {src}")))

            # Link capability to SKOS concept if applicable
            matched_skos = None
            if t.object in skos_concepts_created:
                matched_skos = t.object
            else:
                for sc in skos_concepts_created:
                    if sc.lower() == t.object.lower():
                        matched_skos = sc
                        break
            if matched_skos:
                c_uri = _make_concept_uri(matched_skos)
                g.add((obj_uri, SKOS.related, c_uri))

            # Canonical Wikidata Entity Grounding
            obj_url = ground_url(t.object)
            if obj_url:
                g.add((obj_uri, SCHEMA.sameAs, URIRef(obj_url)))
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

            # PROV-O & SKOS for topic hub
            g.add((hub_uri, RDF.type, PROV.Entity))
            g.add((hub_uri, PROV.hadPrimarySource, hub_uri))
            c_uri = _make_concept_uri(concept)
            if concept not in skos_concepts_created:
                g.add((c_uri, RDF.type, SKOS.Concept))
                g.add((c_uri, SKOS.inScheme, scheme_uri))
                g.add((c_uri, SKOS.prefLabel, Literal(concept)))
                skos_concepts_created.add(concept)
            g.add((hub_uri, SKOS.related, c_uri))

            concept_url = ground_url(concept)
            if concept_url:
                g.add((hub_uri, SCHEMA.sameAs, URIRef(concept_url)))
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
    entities: List[str],
    concept_hierarchy: Optional[Dict[str, str]] = None,
    vertical_id: Optional[str] = None
) -> str:
    """
    Serializes the unified site knowledge graph, semantic triples, canonical topic hubs,
    and W3C PROV-O / SKOS structures into W3C standard RDF Turtle (.ttl) format with
    canonical Wikidata entity grounding.
    """
    g = build_rdf_graph(domain, triples, hubs, entities, concept_hierarchy=concept_hierarchy, vertical_id=vertical_id)
    return g.serialize(format="turtle")


def export_to_rdf_ntriples(
    domain: str,
    triples: List[SemanticTriple],
    hubs: Dict[str, str],
    entities: List[str],
    concept_hierarchy: Optional[Dict[str, str]] = None,
    vertical_id: Optional[str] = None
) -> str:
    """
    Serializes the unified site knowledge graph into W3C standard N-Triples (.nt) format
    with full PROV-O provenance and SKOS taxonomy triples.
    Ideal for high-throughput streaming triple stores and bulk database ingestion.
    """
    g = build_rdf_graph(domain, triples, hubs, entities, concept_hierarchy=concept_hierarchy, vertical_id=vertical_id)
    return g.serialize(format="nt")


# SPARQL keywords that mutate the graph. Rejected outright.
_SPARQL_MUTATION_KEYWORDS = (
    "INSERT", "DELETE", "DROP", "CLEAR", "CREATE", "LOAD", "COPY", "MOVE", "ADD",
)

# SPARQL keywords that make the query engine open a network connection of its own.
# RDFLib evaluates SERVICE by issuing a real HTTP request to the given endpoint, which
# turns this endpoint into a full server-side request forgery primitive: a caller could
# reach the cloud metadata service or any internal host from inside the container.
# FROM / FROM NAMED name remote graphs for the same reason.
_SPARQL_NETWORK_KEYWORDS = ("SERVICE", "FROM")

# Evaluation is bounded so a deliberately expensive query (cartesian joins, unbounded
# property paths) cannot pin a worker. LIMIT caps rows returned, not work performed.
SPARQL_TIMEOUT_SECONDS = float(os.environ.get("SPARQL_TIMEOUT_SECONDS", "10") or 10)


def _strip_sparql_noise(query: str) -> str:
    """
    Remove comments, string literals and IRIs from a query so keyword scanning sees only
    syntax.

    Without this the guard both under- and over-blocks: it misses nothing real, but it
    rejects a perfectly legal `FILTER(CONTAINS(?o, "drop"))` because the word appears
    inside a literal.
    """
    body = re.sub(r"#[^\n]*", " ", query)
    # Triple-quoted, then single/double quoted literals.
    body = re.sub(r'"""(?:[^\\]|\\.)*?"""', ' "" ', body, flags=re.DOTALL)
    body = re.sub(r"'''(?:[^\\]|\\.)*?'''", " '' ", body, flags=re.DOTALL)
    body = re.sub(r'"(?:[^"\\\n]|\\.)*"', ' "" ', body)
    body = re.sub(r"'(?:[^'\\\n]|\\.)*'", " '' ", body)
    # IRIs, including those introduced by PREFIX and BASE declarations.
    body = re.sub(r"<[^<>\s]*>", " <> ", body)
    return body


def _assert_sparql_is_safe(sparql_query: str) -> None:
    """Reject any query that mutates the graph or makes the engine talk to the network."""
    body = _strip_sparql_noise(sparql_query)

    for kw in _SPARQL_MUTATION_KEYWORDS:
        if re.search(rf"\b{kw}\b", body, re.IGNORECASE):
            raise ValueError(
                f"SPARQL mutation command '{kw}' is not permitted. Only read-only SELECT queries are supported."
            )

    for kw in _SPARQL_NETWORK_KEYWORDS:
        if re.search(rf"\b{kw}\b", body, re.IGNORECASE):
            raise ValueError(
                f"SPARQL '{kw}' clauses are not permitted: this endpoint evaluates queries only "
                f"against the RDF graph supplied in the request and never fetches remote data."
            )

    if not re.search(r"\bSELECT\b", body, re.IGNORECASE):
        raise ValueError("Only SPARQL SELECT queries are supported.")


def execute_sparql_query_on_ttl(turtle_data: str, sparql_query: str) -> Dict[str, Any]:
    """
    Executes a W3C SPARQL 1.1 query against an RDF Turtle knowledge graph
    using RDFLib's native SPARQL engine and returns structured tabular results.

    Query sanitization & memory safety:
    - Enforces read-only SELECT queries (rejects UPDATE, INSERT, DELETE, DROP, CLEAR)
    - Rejects SERVICE / FROM clauses, which would let the engine fetch remote graphs (SSRF)
    - Enforces a strict maximum limit of 1000 rows to prevent memory exhaustion
    - Bounds total evaluation time to SPARQL_TIMEOUT_SECONDS
    """
    cleaned_query = sparql_query.strip()
    _assert_sparql_is_safe(cleaned_query)

    limit_match = re.search(r"\bLIMIT\s+(\d+)", cleaned_query, re.IGNORECASE)
    if not limit_match:
        cleaned_query = f"{cleaned_query}\nLIMIT 1000"
    elif int(limit_match.group(1)) > 1000:
        cleaned_query = re.sub(r"\bLIMIT\s+\d+", "LIMIT 1000", cleaned_query, flags=re.IGNORECASE)

    g = Graph()
    g.parse(data=turtle_data, format="turtle")

    deadline = time.monotonic() + SPARQL_TIMEOUT_SECONDS
    qres = g.query(cleaned_query)

    cols = [str(v) for v in qres.vars] if hasattr(qres, "vars") and qres.vars else []
    rows: List[List[str]] = []
    for row in qres:
        if len(rows) >= 1000:
            break
        # RDFLib evaluates lazily, so the cost of a pathological query is paid here.
        if time.monotonic() > deadline:
            raise ValueError(
                f"SPARQL query exceeded the {SPARQL_TIMEOUT_SECONDS:.0f}s evaluation budget "
                f"and was cancelled. Narrow the query or add a smaller LIMIT."
            )
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
    DCTERMS = Namespace("http://purl.org/dc/terms/")

    g.bind("owl", OWL)
    g.bind("onto", ONTO)
    g.bind("schema", SCHEMA)
    g.bind("rdfs", RDFS)
    g.bind("rdf", RDF)
    g.bind("xsd", XSD)
    g.bind("dcterms", DCTERMS)

    now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()

    # 1. Ontology Declaration with Dublin Core & OWL Versioning
    onto_uri = URIRef(f"https://{domain}/ontology")
    g.add((onto_uri, RDF.type, OWL.Ontology))
    g.add((onto_uri, RDFS.label, Literal(f"{domain} Enterprise Domain Ontology")))
    g.add((onto_uri, DCTERMS.title, Literal(f"{domain} Enterprise Domain Ontology")))
    g.add((onto_uri, DCTERMS.description, Literal(f"Formal W3C OWL 2 DL enterprise domain ontology for {domain} with reasoning axioms and semantic constraints.")))
    g.add((onto_uri, DCTERMS.creator, Literal("GainARK OntoLeap Engine")))
    g.add((onto_uri, DCTERMS.created, Literal(now_iso, datatype=XSD.dateTime)))
    g.add((onto_uri, DCTERMS.modified, Literal(now_iso, datatype=XSD.dateTime)))
    g.add((onto_uri, DCTERMS.license, URIRef("https://creativecommons.org/licenses/by/4.0/")))
    g.add((onto_uri, OWL.versionInfo, Literal("2.2.0")))

    # 2. OWL Classes
    classes = [
        ("EnterprisePlatform", "Root SaaS or enterprise business platform entity"),
        ("PlatformCapability", "Core automated feature, workflow, or architectural service"),
        ("SoftwareIntegration", "External enterprise application or ecosystem integration"),
        ("ComplianceStandard", "Regulatory, accounting, or security compliance framework"),
        ("PricingModel", "Commercial monetization, billing, or pricing structure"),
        ("TopicAuthorityHub", "Canonical topic cluster landing page anchoring topical authority"),
        ("ProductFeature", "Specific discrete functional feature of the platform"),
        ("LegacyWorkflow", "Manual or legacy business process replaced by the platform"),
        ("CustomerSegment", "Target enterprise or market demographic segment"),
        ("IndustryVertical", "Industry market sector served by the software"),
        ("DeploymentModel", "Infrastructure deployment or hosting architecture"),
        ("TrustCertification", "Audited security, privacy, or trust certification"),
        ("APIStandard", "Developer API or programmatic interface protocol"),
        ("GeographicMarket", "Geographic locale or regional market supported"),
        ("SLAGuarantee", "Contractual uptime or reliability SLA commitment"),
        ("CompetitorEntity", "Direct or indirect industry competitor organization")
    ]
    for c_name, c_desc in classes:
        c_uri = ONTO[c_name]
        g.add((c_uri, RDF.type, OWL.Class))
        g.add((c_uri, RDFS.label, Literal(c_name)))
        g.add((c_uri, RDFS.comment, Literal(c_desc)))

    # Disjoint Class Axioms (Prevent semantic confusion between core entity types)
    disjoint_pairs = [
        (ONTO.EnterprisePlatform, ONTO.PlatformCapability),
        (ONTO.EnterprisePlatform, ONTO.ComplianceStandard),
        (ONTO.EnterprisePlatform, ONTO.PricingModel),
        (ONTO.EnterprisePlatform, ONTO.TopicAuthorityHub),
        (ONTO.PlatformCapability, ONTO.ComplianceStandard),
        (ONTO.PlatformCapability, ONTO.PricingModel),
        (ONTO.PlatformCapability, ONTO.TopicAuthorityHub),
        (ONTO.ComplianceStandard, ONTO.PricingModel),
        (ONTO.EnterprisePlatform, ONTO.CompetitorEntity),
        (ONTO.CustomerSegment, ONTO.IndustryVertical),
        (ONTO.DeploymentModel, ONTO.PricingModel)
    ]
    for c1, c2 in disjoint_pairs:
        g.add((c1, OWL.disjointWith, c2))

    # 3. OWL Object Properties with Domain, Range & owl:inverseOf Axioms
    obj_props = [
        ("automatesWorkflow", "isAutomatedBy", ONTO.EnterprisePlatform, ONTO.PlatformCapability, "Relates platform to automated workflows", "Relates workflow/capability back to platform"),
        ("integratesWithSystem", "isIntegratedInto", ONTO.EnterprisePlatform, ONTO.SoftwareIntegration, "Relates platform to integrated systems", "Relates integration back to host platform"),
        ("compliesWithStandard", "isCompliedWithBy", ONTO.EnterprisePlatform, ONTO.ComplianceStandard, "Relates platform to compliance frameworks", "Relates compliance standard back to certified platform"),
        ("supportsPricingArchitecture", "isPricingModelOf", ONTO.EnterprisePlatform, ONTO.PricingModel, "Relates platform to monetization models", "Relates monetization model back to platform"),
        ("anchorsTopicHub", "isTopicHubOf", ONTO.EnterprisePlatform, ONTO.TopicAuthorityHub, "Relates platform to its canonical topic hubs", "Relates topic hub back to anchoring platform"),
        ("hasFeature", "isFeatureOf", ONTO.EnterprisePlatform, ONTO.ProductFeature, "Relates platform to discrete product features", "Relates feature back to parent platform"),
        ("replacesWorkflow", "isReplacedBy", ONTO.EnterprisePlatform, ONTO.LegacyWorkflow, "Relates platform to legacy workflows replaced", "Relates legacy workflow back to replacing platform"),
        ("targetsSegment", "isTargetedBy", ONTO.EnterprisePlatform, ONTO.CustomerSegment, "Relates platform to intended customer segments", "Relates segment back to targeting platform"),
        ("servesIndustry", "isServedBy", ONTO.EnterprisePlatform, ONTO.IndustryVertical, "Relates platform to business industry verticals served", "Relates industry vertical back to serving platform"),
        ("deployedAs", "isDeploymentModelOf", ONTO.EnterprisePlatform, ONTO.DeploymentModel, "Relates platform to deployment infrastructure architectures", "Relates deployment architecture back to platform"),
        ("certifiedBy", "certifies", ONTO.EnterprisePlatform, ONTO.TrustCertification, "Relates platform to trust and compliance certifications", "Relates certification back to certified platform"),
        ("hasAPI", "isAPIOf", ONTO.EnterprisePlatform, ONTO.APIStandard, "Relates platform to supported API and integration standards", "Relates API standard back to platform"),
        ("supportsLocale", "isLocaleSupportedBy", ONTO.EnterprisePlatform, ONTO.GeographicMarket, "Relates platform to supported geographic regions or locales", "Relates locale back to supporting platform"),
        ("guarantees", "isGuaranteedBy", ONTO.EnterprisePlatform, ONTO.SLAGuarantee, "Relates platform to contractual reliability SLAs and guarantees", "Relates SLA back to guaranteeing platform"),
        ("competesAgainst", "competesWith", ONTO.EnterprisePlatform, ONTO.CompetitorEntity, "Relates platform to named market competitor entities", "Relates competitor back to platform")
    ]
    for forward_name, inv_name, domain_uri, range_uri, f_comment, inv_comment in obj_props:
        p_uri = ONTO[forward_name]
        inv_uri = ONTO[inv_name]

        # Forward property
        g.add((p_uri, RDF.type, OWL.ObjectProperty))
        g.add((p_uri, RDFS.domain, domain_uri))
        g.add((p_uri, RDFS.range, range_uri))
        g.add((p_uri, RDFS.comment, Literal(f_comment)))

        # Inverse property
        g.add((inv_uri, RDF.type, OWL.ObjectProperty))
        g.add((inv_uri, RDFS.domain, range_uri))
        g.add((inv_uri, RDFS.range, domain_uri))
        g.add((inv_uri, RDFS.comment, Literal(inv_comment)))

        # Symmetrical inverseOf assertions
        g.add((p_uri, OWL.inverseOf, inv_uri))
        g.add((inv_uri, OWL.inverseOf, p_uri))

    # Transitive Taxonomical Property (owl:TransitiveProperty for multi-hop category inference)
    sub_cat_uri = ONTO.subCategoryOf
    g.add((sub_cat_uri, RDF.type, OWL.ObjectProperty))
    g.add((sub_cat_uri, RDF.type, OWL.TransitiveProperty))
    g.add((sub_cat_uri, RDFS.label, Literal("subCategoryOf")))
    g.add((sub_cat_uri, RDFS.comment, Literal("Transitive category hierarchy relationship for taxonomy rollups.")))

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
        "supportsPricingModel": (ONTO.supportsPricingArchitecture, ONTO.PricingModel),
        "hasFeature": (ONTO.hasFeature, ONTO.ProductFeature),
        "replacesWorkflow": (ONTO.replacesWorkflow, ONTO.LegacyWorkflow),
        "targetsSegment": (ONTO.targetsSegment, ONTO.CustomerSegment),
        "servesIndustry": (ONTO.servesIndustry, ONTO.IndustryVertical),
        "deployedAs": (ONTO.deployedAs, ONTO.DeploymentModel),
        "certifiedBy": (ONTO.certifiedBy, ONTO.TrustCertification),
        "hasAPI": (ONTO.hasAPI, ONTO.APIStandard),
        "supportsLocale": (ONTO.supportsLocale, ONTO.GeographicMarket),
        "guarantees": (ONTO.guarantees, ONTO.SLAGuarantee),
        "competesAgainst": (ONTO.competesAgainst, ONTO.CompetitorEntity)
    }

    # Same one-batch warm-up as build_rdf_graph. Usually free: an audit that built the
    # RDF graph first has already cached these phrases, positives and negatives alike.
    prefetch_grounding([t.object for t in triples])

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
        ind_url = ground_url(obj_lower)
        if ind_url:
            g.add((ind_uri, SCHEMA.sameAs, URIRef(ind_url)))

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
