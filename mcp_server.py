"""
GainARK OntoLeap — Official Model Context Protocol (MCP) Server

Exposes the OntoLeap Knowledge Graph & Graph-Diff Engine directly to AI assistants
including Claude Desktop, Cursor, Antigravity, and autonomous agents.

Tools provided:
1. ontoleap_extract_facts: Mines relational semantic triples <S, P, O> from any URL or text.
2. ontoleap_cross_examine_diff: Computes the exact set diff (A ∩ B, A \\ B, B \\ A) between two sources.
3. ontoleap_probe_ai_sov: Probes live AI answer engines to measure Share of Voice and audit hallucinations.
4. ontoleap_map_site_topology: Calculates PageRank authority hubs and internal linking silos across a site.
"""

import os
import sys
import json
import logging
from typing import Optional, List, Dict, Any

from mcp.server.mcpserver import MCPServer
from graph_engine import get_graph_engine
import truth_ledger.history as ledger_history
from knowledge_graph import export_to_rdf_turtle, export_to_owl_xml
from models import SemanticTriple

# Set up logging to stderr so stdout remains clean for MCP stdio protocol
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger("ontoleap.mcp")

# Initialize MCP Server
app = MCPServer(
    name="OntoLeap Ground Truth & Knowledge Graph Engine",
    version="1.0.0",
    description="Deterministic Knowledge Graph Extraction, Cross-Examination Graph-Diff, and AI Citation Probing Engine."
)


@app.tool(
    name="ontoleap_extract_facts",
    description="Extracts verified semantic triples <Subject, Predicate, Object>, named entities, and Schema.org types from a web URL or raw text snippet."
)
async def ontoleap_extract_facts(
    source: str,
    vertical_id: str = "b2b_saas_fintech"
) -> str:
    """
    Extracts structured knowledge graph facts from a URL or raw text.

    Args:
        source: Web URL (e.g. 'https://www.ordwaylabs.com') or raw text/markdown copy.
        vertical_id: Category vertical (e.g. 'b2b_saas_fintech', 'cybersecurity', 'healthtech').
    """
    try:
        engine = get_graph_engine()
        result = await engine.extract_knowledge_graph(source=source, vertical_id=vertical_id)
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error("ontoleap_extract_facts failed: %s", e, exc_info=True)
        return json.dumps({"error": type(e).__name__, "status": "failed"})


@app.tool(
    name="ontoleap_cross_examine_diff",
    description="Calculates the deterministic graph difference between Source A (claims/marketing) and Source B (evidence/API spec/competitor). Identifies grounded facts (A ∩ B), unbacked claims/hallucinations (A \\ B), and omitted features (B \\ A)."
)
async def ontoleap_cross_examine_diff(
    source_a: str,
    source_b: str,
    vertical_id: str = "b2b_saas_fintech"
) -> str:
    """
    Cross-examines two sources using set-theoretic graph diffing.

    Args:
        source_a: Claim source — landing page URL, blog draft, or marketing copy.
        source_b: Truth source — OpenAPI spec JSON, technical documentation URL, codebase docs, or competitor URL.
        vertical_id: Domain vertical ID.
    """
    try:
        engine = get_graph_engine()
        result = await engine.diff_knowledge_graphs(source_a=source_a, source_b=source_b, vertical_id=vertical_id)
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error("ontoleap_cross_examine_diff failed: %s", e, exc_info=True)
        return json.dumps({"error": type(e).__name__, "status": "failed"})


@app.tool(
    name="ontoleap_probe_ai_sov",
    description="Probes live AI search engines (Google Gemini, Perplexity simulation, open web snippets) with unbranded buyer queries to calculate Share of Voice (SOV %), analyze competitor citations, and audit AI hallucinations."
)
async def ontoleap_probe_ai_sov(
    brand_name: str,
    domain: str,
    competitor_names: Optional[List[str]] = None,
    vertical_id: str = "b2b_saas_fintech",
    custom_queries: Optional[List[str]] = None
) -> str:
    """
    Probes AI search engines to audit brand presence and hallucinations.

    Args:
        brand_name: Primary brand name (e.g. 'Ordway').
        domain: Primary domain name (e.g. 'ordwaylabs.com').
        competitor_names: List of competitor brands (e.g. ['Chargebee', 'Stripe', 'Maxio']).
        vertical_id: Industry vertical ID.
        custom_queries: Optional list of custom evaluation queries to probe.
    """
    try:
        engine = get_graph_engine()
        result = await engine.probe_ai_search_sov(
            brand_name=brand_name,
            domain=domain,
            competitor_names=competitor_names,
            vertical_id=vertical_id,
            custom_queries=custom_queries
        )
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error("ontoleap_probe_ai_sov failed: %s", e, exc_info=True)
        return json.dumps({"error": type(e).__name__, "status": "failed"})


@app.tool(
    name="ontoleap_map_site_topology",
    description="Crawls a site's XML sitemap or URL list, calculates NetworkX PageRank authority hubs, maps topic cluster silos, and generates in-context internal link insertion opportunities with suggested anchor texts."
)
async def ontoleap_map_site_topology(
    sitemap_url: Optional[str] = None,
    urls: Optional[List[str]] = None,
    max_pages: int = 10
) -> str:
    """
    Constructs a site-wide knowledge graph and identifies internal linking opportunities.

    Args:
        sitemap_url: Target XML sitemap URL (e.g. 'https://www.ordwaylabs.com/sitemap_index.xml').
        urls: Optional explicit list of target URLs to analyze.
        max_pages: Maximum pages to crawl and analyze (default: 10).
    """
    try:
        engine = get_graph_engine()
        result = await engine.analyze_site_topology(
            sitemap_url=sitemap_url,
            urls=urls,
            max_pages=max_pages
        )
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error("ontoleap_map_site_topology failed: %s", e, exc_info=True)
        return json.dumps({"error": type(e).__name__, "status": "failed"})


@app.tool(
    name="ontoleap_track_competitor_changes",
    description="Tracks historical claim movement and grounding changes for a competitor brand using the immutable Product Truth ledger. Identifies newly added claims, dropped capabilities, and grounding drift."
)
async def ontoleap_track_competitor_changes(
    brand_name: str
) -> str:
    """
    Retrieves the competitor change history timeline for a brand.

    Args:
        brand_name: Competitor brand name (e.g. 'Chargebee', 'Ordway').
    """
    try:
        timeline = ledger_history.brand_timeline(brand_name)
        return json.dumps(timeline, indent=2)
    except Exception as e:
        logger.error("ontoleap_track_competitor_changes failed: %s", e, exc_info=True)
        return json.dumps({"error": type(e).__name__, "status": "failed"})


@app.tool(
    name="ontoleap_export_w3c_ontology",
    description="Exports an extracted knowledge graph into formal W3C RDF Turtle (.ttl) or OWL 2 DL RDF/XML (.owl) format with Dublin Core, DCAT cataloging, SKOS taxonomies, and owl:inverseOf axioms."
)
async def ontoleap_export_w3c_ontology(
    source: str,
    domain: Optional[str] = None,
    export_format: str = "turtle",
    vertical_id: str = "b2b_saas_fintech"
) -> str:
    """
    Exports a domain's knowledge graph to standard W3C RDF or OWL.

    Args:
        source: Web URL or raw text to extract from.
        domain: Domain name (e.g. 'ordwaylabs.com'). If omitted, extracted from source URL or defaults to 'platform.local'.
        export_format: 'turtle' (.ttl) or 'owl_xml' (.owl).
        vertical_id: Domain vertical ID.
    """
    try:
        engine = get_graph_engine()
        kg = await engine.extract_knowledge_graph(source=source, vertical_id=vertical_id)
        triples = [
            SemanticTriple(
                subject=t["subject"],
                predicate=t["predicate"],
                object=t["object"],
                confidence=t.get("confidence", 0.9),
                evidence_sentence=t.get("evidence", "")
            )
            for t in kg.get("triples", [])
        ]
        resolved_domain = domain or (source.split("://")[1].split("/")[0] if source.startswith("http") else "platform.local")
        entities = [e["text"] for e in kg.get("entities", [])]

        if export_format.lower() == "owl_xml":
            output = export_to_owl_xml(resolved_domain, triples, hubs={}, entities=entities)
        else:
            output = export_to_rdf_turtle(resolved_domain, triples, hubs={}, entities=entities, vertical_id=vertical_id)

        return json.dumps({
            "domain": resolved_domain,
            "format": export_format,
            "triples_count": len(triples),
            "serialized_ontology": output
        }, indent=2)
    except Exception as e:
        logger.error("ontoleap_export_w3c_ontology failed: %s", e, exc_info=True)
        return json.dumps({"error": type(e).__name__, "status": "failed"})


if __name__ == "__main__":
    # Start the server using stdio transport (compatible with Claude Desktop, Cursor, etc.)
    logger.info("Starting OntoLeap MCP Server on stdio transport...")
    app.run(transport="stdio")
