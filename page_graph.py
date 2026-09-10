"""
page_graph.py — Pure Page-Level Knowledge Graph & Ontology Extractor

Extracts a clean, W3C-compliant Knowledge Graph from a single web page:
1. Clean text & document structure extraction (trafilatura).
2. Existing Schema.org / Microdata / RDFa extraction (extruct).
3. Zero-shot Named Entity Recognition with vertical classes (GLiNER).
4. Relational semantic triple extraction with sentence-level provenance.
5. Entity linking with Wikidata QIDs.
6. Exports W3C JSON-LD @graph and RDF Turtle (.ttl).
"""

import re
import logging
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import trafilatura
import extruct

from models import PageKnowledgeGraph, KGNode, KGEdge
from scraper import smart_fetch, validate_url_for_fetch
from constants import (
    WIKIDATA_KB, KNOWN_INTEGRATIONS, KNOWN_COMPLIANCE,
    KNOWN_PRICING, KNOWN_AUTOMATION, KNOWN_FEATURES,
    resolve_surface_forms
)
from entity_grounding import wikidata_uri
from industry_ontology import load_industry_ontology
from pipeline import _load_shared_gliner

logger = logging.getLogger("gainark.page_graph")

# Only a guard against a pathological page. A real page yields tens of entities.
_MAX_EXPORT_NODES = 200


def _extract_text_and_title(html: str) -> Tuple[str, str]:
    """Extract clean readability text and page title from raw HTML."""
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    elif soup.find("meta", property="og:title"):
        title = soup.find("meta", property="og:title").get("content", "").strip()

    # Trafilatura extracts clean article/page text stripping nav and footer boilerplates
    clean_text = trafilatura.extract(html, include_links=False, include_tables=True)
    if not clean_text or len(clean_text.strip()) < 50:
        # Fallback to BeautifulSoup get_text
        clean_text = soup.get_text(separator=" ", strip=True)

    return clean_text, title


def _extract_embedded_schemas(html: str, url: str) -> List[str]:
    """Extract Schema.org types already embedded in the HTML markup."""
    try:
        data = extruct.extract(html, base_url=url, uniform=True)
        schema_types = set()
        for block in data.get("json-ld", []):
            stype = block.get("@type")
            if isinstance(stype, list):
                schema_types.update(stype)
            elif isinstance(stype, str):
                schema_types.add(stype)

        for block in data.get("microdata", []):
            stype = block.get("type")
            if stype:
                schema_types.add(stype.split("/")[-1])

        return sorted(list(schema_types))
    except Exception as e:
        logger.debug("Schema extraction error for %s: %s", url, e)
        return []


def _extract_entities_gliner(text: str, vertical_id: str = "b2b_saas_fintech") -> List[KGNode]:
    """Extract named entities using GLiNER zero-shot model and ground to Wikidata."""
    onto = load_industry_ontology(vertical_id)
    gliner_labels = onto.classes or [
        "Software Platform", "Billing Feature", "Accounting Standard",
        "Integration Partner", "Security Standard", "Pricing Model", "Product Feature"
    ]

    model = _load_shared_gliner("urchade/gliner_small-v2.1")
    words = text.split()
    chunks = []
    chunk_size = 200
    chunk_overlap = 25
    step = chunk_size - chunk_overlap

    for i in range(0, len(words), step):
        c_words = words[i:i + chunk_size]
        if not c_words:
            break
        chunks.append(" ".join(c_words))
        if i + chunk_size >= len(words):
            break

    seen_entities: Dict[str, KGNode] = {}

    for chunk in chunks:
        preds = model.predict_entities(chunk, gliner_labels, threshold=0.50)
        for p in preds:
            raw_text = p["text"].strip()
            if len(raw_text) < 2:
                continue

            canonical = raw_text
            clean_key = canonical.lower()

            # Wikidata lookup
            wiki_qid = WIKIDATA_KB.get(clean_key)
            if not wiki_qid:
                # check partial match
                for kb_name, qid in WIKIDATA_KB.items():
                    if kb_name.lower() == clean_key:
                        wiki_qid = qid
                        break

            node_id = f"entity:{re.sub(r'[^a-zA-Z0-9_-]', '_', clean_key)}"

            if clean_key in seen_entities:
                seen_entities[clean_key].mentions_count += 1
                if raw_text not in seen_entities[clean_key].aliases:
                    seen_entities[clean_key].aliases.append(raw_text)
            else:
                seen_entities[clean_key] = KGNode(
                    id=node_id,
                    canonical_name=canonical,
                    entity_type=p["label"],
                    aliases=[raw_text],
                    wikidata_id=wiki_qid,
                    mentions_count=1,
                    confidence=round(float(p["score"]), 3)
                )

    return list(seen_entities.values())


def _extract_semantic_edges(
    text: str,
    subject_name: str,
    nodes: List[KGNode],
    url: str,
    vertical_id: str = "b2b_saas_fintech"
) -> List[KGEdge]:
    """Extract relational semantic edges with exact sentence provenance."""
    onto = load_industry_ontology(vertical_id)
    vocab_integrations = onto.known_integrations or KNOWN_INTEGRATIONS
    vocab_compliance = onto.known_compliance or KNOWN_COMPLIANCE
    vocab_pricing = KNOWN_PRICING
    vocab_automation = KNOWN_AUTOMATION
    vocab_features = KNOWN_FEATURES
    forms_features = resolve_surface_forms(None, 'known_features', KNOWN_FEATURES)

    # Split into clean sentences
    raw_sentences = re.split(r'(?<=[.!?])\s+', text)
    sentences = [s.strip() for s in raw_sentences if len(s.strip()) > 15]

    edges: List[KGEdge] = []
    seen_edges = set()

    def add_edge(predicate: str, target: str, conf: float, sentence: str, target_type: str = "Entity"):
        edge_key = (subject_name.lower(), predicate.lower(), target.lower())
        if edge_key in seen_edges:
            return
        seen_edges.add(edge_key)

        target_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', target.lower())
        subject_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', subject_name.lower())
        edge_id = f"edge:{subject_slug}-{predicate.lower()}-{target_slug}"

        edges.append(KGEdge(
            id=edge_id,
            source=subject_name,
            target=target,
            predicate=predicate,
            source_type="SoftwarePlatform",
            target_type=target_type,
            confidence=conf,
            provenance_sentence=sentence[:280],
            source_url=url
        ))

    # 1. automates
    auto_cues = ["automate", "automates", "automated", "streamline", "streamlines", "orchestrate", "auto-", "elimina"]
    for sent in sentences:
        s_lower = sent.lower()
        if any(cue in s_lower for cue in auto_cues):
            for cap in vocab_automation:
                if re.search(rf'\b{re.escape(cap.lower())}\b', s_lower):
                    add_edge("automates", cap, 0.90, sent, target_type="Process")

    # 2. integratesWith
    int_cues = ["integrat", "connect", "sync", "export to", "import from", "native connector", "api for"]
    for sent in sentences:
        s_lower = sent.lower()
        if any(cue in s_lower for cue in int_cues):
            for partner in vocab_integrations:
                if re.search(rf'\b{re.escape(partner.lower())}\b', s_lower):
                    add_edge("integratesWith", partner, 0.92, sent, target_type="IntegrationPartner")

    # 3. compliesWith
    for sent in sentences:
        s_lower = sent.lower()
        for std in vocab_compliance:
            if re.search(rf'\b{re.escape(std.lower())}\b', s_lower):
                add_edge("compliesWith", std, 0.95, sent, target_type="Standard")

    # 4. supportsPricingModel
    pricing_cues = ["pricing", "bill", "billing", "model", "monetiz", "plans", "tier"]
    for sent in sentences:
        s_lower = sent.lower()
        for pm in vocab_pricing:
            pm_simple = pm.lower().replace(" pricing", "").replace(" billing", "")
            if pm.lower() in s_lower or (pm_simple in s_lower and any(cue in s_lower for cue in pricing_cues)):
                add_edge("supportsPricingModel", pm, 0.90, sent, target_type="PricingModel")

    # 5. hasFeature
    for sent in sentences:
        s_lower = sent.lower()
        for canonical, surface_forms in forms_features:
            for form in surface_forms:
                if re.search(rf'\b{re.escape(form.lower())}\b', s_lower):
                    add_edge("hasFeature", canonical, 0.85, sent, target_type="Feature")
                    break

    return edges


def _build_jsonld_graph(url: str, title: str, subject_name: str, nodes: List[KGNode], edges: List[KGEdge]) -> Dict[str, Any]:
    """Serializes the page knowledge graph into standard W3C JSON-LD @graph."""
    graph_items = []

    # Subject entity (SoftwareApplication / Organization)
    subject_item: Dict[str, Any] = {
        "@type": "SoftwareApplication",
        "@id": f"{url}#{subject_name.lower()}",
        "name": subject_name,
        "url": url
    }
    if title:
        subject_item["description"] = title

    # Collect relations
    integrations = [e.target for e in edges if e.predicate == "integratesWith"]
    features = [e.target for e in edges if e.predicate in ("hasFeature", "automates")]

    if integrations:
        subject_item["availableOnDevice"] = integrations
    if features:
        subject_item["featureList"] = features

    graph_items.append(subject_item)

    # Add entity nodes into about / mentions
    for n in order_nodes_for_export(nodes, _MAX_EXPORT_NODES):
        entity_item: Dict[str, Any] = {
            "@type": "Thing",
            "@id": n.id,
            "name": n.canonical_name,
            "additionalType": n.entity_type
        }
        same_as = wikidata_uri(n.wikidata_id)
        if same_as:
            entity_item["sameAs"] = same_as
        graph_items.append(entity_item)

    return {
        "@context": "https://schema.org",
        "@graph": graph_items
    }


def order_nodes_for_export(nodes: List[KGNode], limit: int) -> List[KGNode]:
    """Order nodes by how much they are worth publishing, then bound the list.

    A grounded entity carries an external identity, which is the one thing a consumer of
    the export cannot derive for itself, so those come first. Salience breaks the
    remaining ties, and the name keeps the order stable across runs of the same page.

    The bound is a guard against pathological input, not a payload budget: the caller
    returns the complete node list alongside this export regardless.
    """
    return sorted(
        nodes,
        key=lambda n: (0 if n.wikidata_id else 1, -n.mentions_count, n.canonical_name.lower()),
    )[:limit]


def _build_turtle_graph(url: str, subject_name: str, edges: List[KGEdge], nodes: List[KGNode]) -> str:
    """Serializes the knowledge graph into W3C RDF Turtle format."""
    sub_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', subject_name.lower())
    lines = [
        "@prefix schema: <http://schema.org/> .",
        "@prefix ex: <https://gainark.com/kg/> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
        f"ex:{sub_slug} a schema:SoftwareApplication ;",
        f'    schema:name "{subject_name}" ;',
        f'    schema:url <{url}> .'
    ]

    for e in edges:
        tgt_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', e.target.lower())
        lines.append(f'ex:{sub_slug} ex:{e.predicate} ex:{tgt_slug} .')
        lines.append(f'ex:{tgt_slug} schema:name "{e.target}" .')

    return "\n".join(lines)


def build_page_kg(
    url_or_html: str,
    url: Optional[str] = None,
    vertical_id: str = "b2b_saas_fintech"
) -> PageKnowledgeGraph:
    """
    Build a complete, standalone Knowledge Graph from a single web page or HTML.
    """
    actual_url = url or "https://example.com"
    html_content = ""

    if url_or_html.startswith("http://") or url_or_html.startswith("https://"):
        actual_url = url_or_html
        validate_url_for_fetch(actual_url)
        html_content = smart_fetch(actual_url)
    else:
        html_content = url_or_html

    clean_text, title = _extract_text_and_title(html_content)

    # Determine subject name (brand)
    domain_brand = urlparse(actual_url).netloc.replace("www.", "").split(".")[0].capitalize()
    if not domain_brand or domain_brand == "Example":
        domain_brand = title.split("|")[0].split("-")[0].strip() if title else "Subject"

    # 1. Embedded Schema extraction
    embedded_schemas = _extract_embedded_schemas(html_content, actual_url)

    # 2. Named Entity Recognition
    nodes = _extract_entities_gliner(clean_text, vertical_id=vertical_id)
    for n in nodes:
        n.source_urls = [actual_url]

    # Ensure subject node exists
    subject_id = f"entity:{domain_brand.lower()}"
    if not any(n.canonical_name.lower() == domain_brand.lower() for n in nodes):
        nodes.insert(0, KGNode(
            id=subject_id,
            canonical_name=domain_brand,
            entity_type="SoftwarePlatform",
            mentions_count=1,
            source_urls=[actual_url]
        ))

    # 3. Relational Semantic Edges
    edges = _extract_semantic_edges(clean_text, domain_brand, nodes, actual_url, vertical_id=vertical_id)

    # 4. Classes and Predicates discovered
    classes_discovered = sorted(list(set(n.entity_type for n in nodes)))
    predicates_discovered = sorted(list(set(e.predicate for e in edges)))

    # 5. W3C Serializations
    export_jsonld = _build_jsonld_graph(actual_url, title, domain_brand, nodes, edges)
    export_turtle = _build_turtle_graph(actual_url, domain_brand, edges, nodes)

    return PageKnowledgeGraph(
        url=actual_url,
        title=title,
        nodes=nodes,
        edges=edges,
        classes_discovered=classes_discovered,
        predicates_discovered=predicates_discovered,
        embedded_schemas=embedded_schemas,
        export_jsonld=export_jsonld,
        export_turtle=export_turtle
    )
