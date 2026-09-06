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
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Set, Tuple
from urllib.parse import urlparse

from rdflib import Graph, Literal, RDF, RDFS, URIRef, Namespace, OWL, XSD
from models import SemanticTriple
from constants import WIKIDATA_KB

# Canonical Wikidata Knowledge Base for Zero-Latency Entity Grounding
WIKIDATA_KNOWLEDGE_BASE: Dict[str, str] = WIKIDATA_KB


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

    # Root Organization & Software Application definitions
    g.add((root_uri, RDF.type, SCHEMA.SoftwareApplication))
    g.add((root_uri, RDF.type, SCHEMA.Organization))
    g.add((root_uri, SCHEMA.name, Literal(brand)))
    g.add((root_uri, SCHEMA.url, URIRef(f"https://{domain}/")))

    # Check if brand matches Wikidata
    if brand.lower() in WIKIDATA_KNOWLEDGE_BASE:
        g.add((root_uri, SCHEMA.sameAs, URIRef(WIKIDATA_KNOWLEDGE_BASE[brand.lower()])))

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
    v_id = vertical_id or "b2b_saas_fintech"
    scheme_uri = URIRef(f"https://{domain}/taxonomy/{v_id}")
    g.add((scheme_uri, RDF.type, SKOS.ConceptScheme))
    g.add((scheme_uri, SKOS.prefLabel, Literal(f"{domain} Category Taxonomy ({v_id})")))
    g.add((scheme_uri, PROV.wasGeneratedBy, activity_uri))
    g.add((scheme_uri, PROV.wasAttributedTo, agent_uri))

    # Load concept_hierarchy if not passed explicitly
    if not concept_hierarchy:
        try:
            curr_dir = os.path.dirname(os.path.abspath(__file__))
            v_specific = os.path.join(curr_dir, "verticals", f"{v_id}.json")
            if os.path.exists(v_specific):
                with open(v_specific, "r", encoding="utf-8") as vf:
                    cdata = json.load(vf)
                    concept_hierarchy = cdata.get("concept_hierarchy", {})
            if not concept_hierarchy:
                v_file = os.path.join(curr_dir, "vertical_config.json")
                if os.path.exists(v_file):
                    with open(v_file, "r", encoding="utf-8") as vf:
                        cdata = json.load(vf)
                        concept_hierarchy = cdata.get("concept_hierarchy", {})
        except Exception:
            concept_hierarchy = {}

    concept_hierarchy = concept_hierarchy or {}
    skos_concepts_created: Set[str] = set()

    def _make_concept_uri(c_name: str) -> URIRef:
        c_clean = re.sub(r'[^a-zA-Z0-9]+', '', c_name) or "Concept"
        return URIRef(f"https://{domain}/concept/{c_clean}")

    for child_c, parent_c in concept_hierarchy.items():
        child_uri = _make_concept_uri(child_c)
        parent_uri = _make_concept_uri(parent_c)

        if child_c not in skos_concepts_created:
            g.add((child_uri, RDF.type, SKOS.Concept))
            g.add((child_uri, SKOS.inScheme, scheme_uri))
            g.add((child_uri, SKOS.prefLabel, Literal(child_c)))
            skos_concepts_created.add(child_c)

        if parent_c not in skos_concepts_created:
            g.add((parent_uri, RDF.type, SKOS.Concept))
            g.add((parent_uri, SKOS.inScheme, scheme_uri))
            g.add((parent_uri, SKOS.prefLabel, Literal(parent_c)))
            skos_concepts_created.add(parent_c)

        g.add((child_uri, SKOS.broader, parent_uri))
        g.add((parent_uri, SKOS.narrower, child_uri))

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


def execute_sparql_query_on_ttl(turtle_data: str, sparql_query: str) -> Dict[str, Any]:
    """
    Executes a W3C SPARQL 1.1 query against an RDF Turtle knowledge graph
    using RDFLib's native SPARQL engine and returns structured tabular results.

    Query sanitization & memory safety:
    - Enforces read-only SELECT queries (rejects UPDATE, INSERT, DELETE, DROP, CLEAR)
    - Enforces a strict maximum limit of 1000 rows to prevent memory exhaustion
    """
    cleaned_query = sparql_query.strip()
    query_body = re.sub(r"#.*", "", cleaned_query)
    query_body = re.sub(r"PREFIX\s+[\w\-]+:\s*<[^>]+>", "", query_body, flags=re.IGNORECASE).strip()

    for kw in ["INSERT", "DELETE", "DROP", "CLEAR", "CREATE", "LOAD", "COPY", "MOVE", "ADD"]:
        if re.search(rf"\b{kw}\b", query_body, re.IGNORECASE):
            raise ValueError(f"SPARQL mutation command '{kw}' is not permitted. Only read-only SELECT queries are supported.")

    if not re.search(r"\bSELECT\b", query_body, re.IGNORECASE):
        raise ValueError("Only SPARQL SELECT queries are supported.")

    limit_match = re.search(r"\bLIMIT\s+(\d+)", cleaned_query, re.IGNORECASE)
    if not limit_match:
        cleaned_query = f"{cleaned_query}\nLIMIT 1000"
    elif int(limit_match.group(1)) > 1000:
        cleaned_query = re.sub(r"\bLIMIT\s+\d+", "LIMIT 1000", cleaned_query, flags=re.IGNORECASE)

    g = Graph()
    g.parse(data=turtle_data, format="turtle")
    qres = g.query(cleaned_query)

    cols = [str(v) for v in qres.vars] if hasattr(qres, "vars") and qres.vars else []
    rows: List[List[str]] = []
    for row in qres:
        if len(rows) >= 1000:
            break
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
        ("TopicAuthorityHub", "Canonical topic cluster landing page anchoring topical authority")
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
        (ONTO.ComplianceStandard, ONTO.PricingModel)
    ]
    for c1, c2 in disjoint_pairs:
        g.add((c1, OWL.disjointWith, c2))

    # 3. OWL Object Properties with Domain, Range & owl:inverseOf Axioms
    obj_props = [
        ("automatesWorkflow", "isAutomatedBy", ONTO.EnterprisePlatform, ONTO.PlatformCapability, "Relates platform to automated workflows", "Relates workflow/capability back to platform"),
        ("integratesWithSystem", "isIntegratedInto", ONTO.EnterprisePlatform, ONTO.SoftwareIntegration, "Relates platform to integrated systems", "Relates integration back to host platform"),
        ("compliesWithStandard", "isCompliedWithBy", ONTO.EnterprisePlatform, ONTO.ComplianceStandard, "Relates platform to compliance frameworks", "Relates compliance standard back to certified platform"),
        ("supportsPricingArchitecture", "isPricingModelOf", ONTO.EnterprisePlatform, ONTO.PricingModel, "Relates platform to monetization models", "Relates monetization model back to platform"),
        ("anchorsTopicHub", "isTopicHubOf", ONTO.EnterprisePlatform, ONTO.TopicAuthorityHub, "Relates platform to its canonical topic hubs", "Relates topic hub back to anchoring platform")
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
