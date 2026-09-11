"""
GainARK OntoLeap — Core Knowledge Graph & Domain Ontology Engine

Clean computer-science primitives for Knowledge Graph extraction, site-wide synthesis,
and industry reference alignment:
1. Page-Level Knowledge Graph Extraction (Entities, Wikidata linking, Triples, Schema markup)
2. Site-Wide Knowledge Graph Synthesis (Crawling, Entity Coreference, Induced Domain Ontology)
3. Industry Reference Ontology Alignment (Covered Concepts, Category Whitespace, Compliance)
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional

from models import (
    PageKnowledgeGraph,
    SiteKnowledgeGraph,
    GraphAlignmentResult
)
from page_graph import build_page_kg
from site_graph import build_site_kg, route_domain_to_vertical
from industry_ontology import (
    load_industry_ontology,
    list_available_industries,
    align_graph_with_industry,
    classify_vertical
)

logger = logging.getLogger("gainark.graph_engine")


def _resolve_vertical(
    explicit: Optional[str],
    *,
    url: Optional[str] = None,
    text: Optional[str] = None
) -> str:
    """The vertical to work in: the caller's choice, or one inferred from the content.

    Refusing beats defaulting. A vertical decides which entity labels are looked for and
    which concepts count as covered, so guessing it wrong does not fail loudly - it
    returns a complete, plausible answer about the wrong industry.
    """
    if explicit:
        return explicit

    routed = classify_vertical(text) if text is not None else route_domain_to_vertical(url)
    vertical_id = routed.get("vertical_id")
    if not vertical_id:
        raise ValueError(
            "Could not identify an industry vertical (%s). Pass vertical_id explicitly; "
            "ontoleap_list_industry_ontologies lists the available ones."
            % routed.get("reason"))

    logger.info("Routed %s to %s (%s)", url or "supplied text", vertical_id, routed.get("reason"))
    return vertical_id


class GraphEngine:
    """
    Decoupled engine for Knowledge Graph Extraction, Site Synthesis, and Industry Alignment.
    """

    async def extract_page_knowledge_graph(
        self,
        source: str,
        url: Optional[str] = None,
        vertical_id: Optional[str] = None
    ) -> PageKnowledgeGraph:
        """
        Extracts entities, Wikidata linking, Schema.org nodes, and relational semantic triples
        from a URL or raw HTML string.

        `vertical_id` may be omitted, in which case it is inferred from the content.
        """
        is_url = source.startswith("http://") or source.startswith("https://")
        vertical_id = _resolve_vertical(
            vertical_id,
            url=(source if is_url else url),
            # Raw HTML is already in hand; classify it rather than fetch anything.
            text=(None if is_url else source),
        )

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: build_page_kg(url_or_html=source, url=url, vertical_id=vertical_id)
        )

    async def build_site_knowledge_graph(
        self,
        start_url: str,
        max_pages: int = 40,
        vertical_id: Optional[str] = None
    ) -> SiteKnowledgeGraph:
        """
        Synthesizes a site-wide Knowledge Graph and induces the domain ontology schema.

        `vertical_id` may be omitted, in which case it is inferred from the site.
        """
        vertical_id = _resolve_vertical(vertical_id, url=start_url)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: build_site_kg(start_url=start_url, max_pages=max_pages, vertical_id=vertical_id)
        )

    async def align_with_industry(
        self,
        kg: Any,
        vertical_id: Optional[str] = None
    ) -> GraphAlignmentResult:
        """
        Aligns a PageKnowledgeGraph or SiteKnowledgeGraph against an industry reference taxonomy.

        Omitting `vertical_id` uses the vertical the graph records having been built with,
        which is the only one whose coverage figure describes the same thing the graph does.
        """
        loop = asyncio.get_running_loop()
        # Passing a loaded ontology would override the graph's own vertical, so resolve
        # here only when the caller named one.
        industry = load_industry_ontology(vertical_id) if vertical_id else None
        return await loop.run_in_executor(
            None,
            lambda: align_graph_with_industry(kg=kg, industry=industry, vertical_id=vertical_id)
        )

    def list_industries(self) -> List[Dict[str, Any]]:
        """List all available industry ontologies."""
        return list_available_industries()


_GLOBAL_GRAPH_ENGINE: Optional[GraphEngine] = None


def get_graph_engine() -> GraphEngine:
    """Returns the singleton instance of the GraphEngine."""
    global _GLOBAL_GRAPH_ENGINE
    if _GLOBAL_GRAPH_ENGINE is None:
        _GLOBAL_GRAPH_ENGINE = GraphEngine()
    return _GLOBAL_GRAPH_ENGINE
