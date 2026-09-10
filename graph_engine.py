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
from site_graph import build_site_kg
from industry_ontology import (
    load_industry_ontology,
    list_available_industries,
    align_graph_with_industry
)

logger = logging.getLogger("gainark.graph_engine")


class GraphEngine:
    """
    Decoupled engine for Knowledge Graph Extraction, Site Synthesis, and Industry Alignment.
    """

    async def extract_page_knowledge_graph(
        self,
        source: str,
        url: Optional[str] = None,
        vertical_id: str = "b2b_saas_fintech"
    ) -> PageKnowledgeGraph:
        """
        Extracts entities, Wikidata linking, Schema.org nodes, and relational semantic triples
        from a URL or raw HTML string.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: build_page_kg(url_or_html=source, url=url, vertical_id=vertical_id)
        )

    async def build_site_knowledge_graph(
        self,
        start_url: str,
        max_pages: int = 15,
        vertical_id: str = "b2b_saas_fintech"
    ) -> SiteKnowledgeGraph:
        """
        Synthesizes a site-wide Knowledge Graph and induces the domain ontology schema.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: build_site_kg(start_url=start_url, max_pages=max_pages, vertical_id=vertical_id)
        )

    async def align_with_industry(
        self,
        kg: Any,
        vertical_id: str = "b2b_saas_fintech"
    ) -> GraphAlignmentResult:
        """
        Aligns a PageKnowledgeGraph or SiteKnowledgeGraph against an industry reference taxonomy.
        """
        loop = asyncio.get_running_loop()
        industry = load_industry_ontology(vertical_id)
        return await loop.run_in_executor(
            None,
            lambda: align_graph_with_industry(kg=kg, industry=industry)
        )

    def list_industries(self) -> List[Dict[str, str]]:
        """List all available industry ontologies."""
        return list_available_industries()


_GLOBAL_GRAPH_ENGINE: Optional[GraphEngine] = None


def get_graph_engine() -> GraphEngine:
    """Returns the singleton instance of the GraphEngine."""
    global _GLOBAL_GRAPH_ENGINE
    if _GLOBAL_GRAPH_ENGINE is None:
        _GLOBAL_GRAPH_ENGINE = GraphEngine()
    return _GLOBAL_GRAPH_ENGINE
