"""
GainARK OntoLeap — Official Model Context Protocol (MCP) Server

Exposes the pure Knowledge Graph & Domain Ontology Engine directly to AI assistants
including Claude Desktop, Cursor, Antigravity, and autonomous agents.

Tools provided:
1. ontoleap_build_page_kg: Extracts entities, Wikidata QIDs, semantic triples, and JSON-LD/Turtle from a page.
2. ontoleap_build_site_kg: Crawls a domain, canonicalizes entities, and induces domain ontology schema.
3. ontoleap_align_industry_ontology: Ground page or site KG against industry reference taxonomy.
4. ontoleap_list_industry_ontologies: Enumerates all supported industry reference models.
"""

import sys
import json
import logging
from typing import Optional

from mcp.server.mcpserver import MCPServer
from graph_engine import get_graph_engine

# Set up logging to stderr so stdout remains clean for MCP stdio protocol
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger("gainark.mcp")

# Initialize MCP Server
app = MCPServer(
    name="OntoLeap Knowledge Graph & Domain Ontology Engine",
    version="2.0.0",
    description="Deterministic Knowledge Graph Extraction, Multi-Page Domain Synthesis, and Industry Ontology Grounding Engine."
)


@app.tool(
    name="ontoleap_build_page_kg",
    description="Extracts a rich Knowledge Graph from a URL or HTML: Named Entities with Wikidata QIDs, semantic triples with exact sentence quotes, Schema.org nodes, and W3C JSON-LD / Turtle formats."
)
async def ontoleap_build_page_kg(
    source: str,
    url: Optional[str] = None,
    vertical_id: str = "b2b_saas_fintech"
) -> str:
    """
    Extracts a page-level Knowledge Graph.

    Args:
        source: Web page URL (e.g. 'https://www.ordwaylabs.com') or raw HTML content.
        url: Canonical page URL if source contains raw HTML.
        vertical_id: Vertical ontology domain ID (default: 'b2b_saas_fintech').
    """
    try:
        engine = get_graph_engine()
        result = await engine.extract_page_knowledge_graph(source=source, url=url, vertical_id=vertical_id)
        return json.dumps(result.model_dump(), indent=2)
    except Exception as e:
        logger.error("ontoleap_build_page_kg failed: %s", e, exc_info=True)
        return json.dumps({"error": type(e).__name__, "message": str(e), "status": "failed"})


@app.tool(
    name="ontoleap_build_site_kg",
    description="Crawls a website, aggregates pages, canonicalizes entities across aliases, induces the domain ontology schema, and computes PageRank authority hubs."
)
async def ontoleap_build_site_kg(
    start_url: str,
    max_pages: int = 10,
    vertical_id: str = "b2b_saas_fintech"
) -> str:
    """
    Synthesizes a site-wide Knowledge Graph and induces domain ontology schema.

    Args:
        start_url: Target domain homepage URL (e.g. 'https://www.ordwaylabs.com').
        max_pages: Maximum pages to crawl (1-30, default: 10).
        vertical_id: Vertical ontology domain ID (default: 'b2b_saas_fintech').
    """
    try:
        engine = get_graph_engine()
        result = await engine.build_site_knowledge_graph(start_url=start_url, max_pages=max_pages, vertical_id=vertical_id)
        return json.dumps(result.model_dump(), indent=2)
    except Exception as e:
        logger.error("ontoleap_build_site_kg failed: %s", e, exc_info=True)
        return json.dumps({"error": type(e).__name__, "message": str(e), "status": "failed"})


@app.tool(
    name="ontoleap_align_industry_ontology",
    description="Aligns a Page Knowledge Graph or Site Knowledge Graph against an industry reference taxonomy to identify Covered Concepts, Category Whitespace, and Standards Compliance."
)
async def ontoleap_align_industry_ontology(
    source_url: str,
    vertical_id: str = "b2b_saas_fintech"
) -> str:
    """
    Extracts page graph and aligns it against the specified industry reference taxonomy.

    Args:
        source_url: Target web page URL to extract and align.
        vertical_id: Industry vertical ID (e.g. 'b2b_saas_fintech', 'cybersecurity', 'healthtech').
    """
    try:
        engine = get_graph_engine()
        kg = await engine.extract_page_knowledge_graph(source=source_url, vertical_id=vertical_id)
        alignment = await engine.align_with_industry(kg=kg, vertical_id=vertical_id)
        return json.dumps(alignment.model_dump(), indent=2)
    except Exception as e:
        logger.error("ontoleap_align_industry_ontology failed: %s", e, exc_info=True)
        return json.dumps({"error": type(e).__name__, "message": str(e), "status": "failed"})


@app.tool(
    name="ontoleap_list_industry_ontologies",
    description="Lists all available industry reference ontologies with their display names and IDs."
)
async def ontoleap_list_industry_ontologies() -> str:
    """Lists all available industry reference ontologies."""
    try:
        engine = get_graph_engine()
        industries = engine.list_industries()
        return json.dumps({"industries": industries}, indent=2)
    except Exception as e:
        logger.error("ontoleap_list_industry_ontologies failed: %s", e, exc_info=True)
        return json.dumps({"error": type(e).__name__, "message": str(e), "status": "failed"})


if __name__ == "__main__":
    logger.info("Starting OntoLeap MCP Server on stdio transport...")
    app.run(transport="stdio")
