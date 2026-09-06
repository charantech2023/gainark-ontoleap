"""
GainARK OntoLeap — Network Topologies & Graph Analytics Engine

Computes directed graph metrics and topological clusters using NetworkX:
1. Internal PageRank (damping factor alpha=0.85) to score page authority.
2. Cluster Topology mapping hubs, spokes, entities, and recommended links.
"""

from typing import List, Dict, Any, Set, Optional
from urllib.parse import urlparse
import networkx as nx

from models import (
    GraphNode,
    GraphEdge,
    ClusterTopology,
    InternalLinkOpportunity,
    SemanticTriple
)


def compute_graph_pagerank(G: nx.DiGraph, alpha: float = 0.85) -> Dict[str, float]:
    """
    Computes internal PageRank on the directed graph using NetworkX.
    Uses nx.pagerank() directly with proper uniform fallback.
    """
    if not G or len(G) == 0:
        return {}
    try:
        return nx.pagerank(G, alpha=alpha)
    except Exception:
        n = len(G)
        return {node: round(1.0 / n, 4) for node in G.nodes()}


def build_cluster_topology(
    pages_data: List[Any],
    hubs: Dict[str, str],
    opportunities: List[InternalLinkOpportunity],
    triples: List[SemanticTriple]
) -> ClusterTopology:
    """
    Constructs a visual graph topology representation with hub, spoke, and entity nodes,
    and directed link opportunities.
    """
    nodes: List[GraphNode] = []
    edges: List[GraphEdge] = []
    seen_nodes: Set[str] = set()

    # Hub pages (purple/gold) vs Spoke pages (blue)
    hub_urls = set(hubs.values())
    for p in pages_data:
        is_hub = p.url in hub_urls
        group = "hub" if is_hub else "spoke"
        label = urlparse(p.url).path.rstrip("/").split("/")[-1] or p.title[:20]
        if not label:
            label = "Home"
        nodes.append(GraphNode(
            id=p.url,
            label=label,
            type=group,
            url=p.url,
            group=group,
            value=24 if is_hub else 16
        ))
        seen_nodes.add(p.url)

    # Key entity concepts (top 12)
    candidate_entities = list(hubs.keys())[:12]
    for ent in candidate_entities:
        ent_id = f"ent_{ent}"
        if ent_id not in seen_nodes:
            nodes.append(GraphNode(
                id=ent_id,
                label=ent,
                type="entity",
                group="entity",
                value=10
            ))
            seen_nodes.add(ent_id)
            hub_url = hubs.get(ent)
            if hub_url and hub_url in seen_nodes:
                edges.append(GraphEdge(
                    source=hub_url,
                    target=ent_id,
                    label="Topic Hub",
                    relation_type="hub_for"
                ))

    # Link opportunities as directed edges between pages
    for opp in opportunities:
        if opp.source_url in seen_nodes and opp.target_url in seen_nodes:
            edges.append(GraphEdge(
                source=opp.source_url,
                target=opp.target_url,
                label=opp.suggested_anchor,
                relation_type="recommends_link"
            ))

    return ClusterTopology(nodes=nodes, edges=edges)
