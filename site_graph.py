"""
site_graph.py — Pure Site-Wide Knowledge Graph & Domain Ontology Synthesizer

Aggregates page-level graphs across a website to construct a unified domain Knowledge Graph:
1. Crawls breadth-first from a start URL, reading high-value architectural paths
   (/features, /pricing, /integrations, /solutions) ahead of everything else.
2. Extracts page knowledge graphs in batch.
3. Performs Entity Coreference & Canonicalization (merging aliases and surface forms).
4. Induces the Domain Class Hierarchy (e.g. SoftwarePlatform -> integratesWith -> IntegrationPartner).
5. Computes PageRank authority hubs and semantic topic clusters.
6. Serializes site-wide W3C JSON-LD and Turtle graphs.
"""

import re
import json
import time
import logging
from collections import deque
from typing import List, Dict, Tuple, Any, Optional
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import networkx as nx

from models import (
    SiteKnowledgeGraph, KGNode, KGEdge,
    InducedClassRelation, TopicCluster, PageFailure
)
from scraper import smart_fetch, validate_url_for_fetch
from entity_grounding import wikidata_uri
from industry_ontology import classify_vertical
from page_graph import build_page_kg, order_nodes_for_export
from constants import DEEP_CRAWL_PATHS, WIKIDATA_KB

logger = logging.getLogger("gainark.site_graph")

# A crawl aggregates many pages, so the guard sits higher than the page-level one.
_MAX_EXPORT_NODES = 1000


# A crawl follows links from every page it reads, so the frontier has to be bounded
# independently of max_pages: a large site can offer far more URLs than we will ever read.
_MAX_FRONTIER = 800

_ASSET_SUFFIXES = re.compile(r'\.(pdf|png|jpg|jpeg|svg|css|js|webp|gif|zip|xml|ico|mp4|woff2?)$')


def _page_key(url: str) -> str:
    """Identity of a page for crawl purposes: host and path, nothing else.

    Query strings and fragments are dropped deliberately. Tracking parameters and
    pagination would otherwise present one page as many and let a crawl spend its whole
    budget going nowhere.
    """
    parsed = urlparse(url)
    netloc = parsed.netloc.replace("www.", "").lower()
    path = parsed.path.rstrip("/").lower() or "/"
    return netloc + path


def _extract_internal_links(page_url: str, html: str) -> Tuple[List[str], List[str]]:
    """Internal links on one page, split into high-value paths and the rest.

    Resolved against `page_url` rather than the site origin: a relative href means
    something different on a deep page than it does on the homepage.
    """
    parsed = urlparse(page_url)
    base_netloc = parsed.netloc.replace("www.", "").lower()

    soup = BeautifulSoup(html, "html.parser")
    priority_links: List[str] = []
    regular_links: List[str] = []
    seen_here = set()

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue

        full_url = urljoin(page_url, href)
        parsed_full = urlparse(full_url)
        if parsed_full.scheme not in ("http", "https"):
            continue
        if parsed_full.netloc.replace("www.", "").lower() != base_netloc:
            continue

        path = parsed_full.path.rstrip("/").lower()
        if not path or _ASSET_SUFFIXES.search(path):
            continue

        key = _page_key(full_url)
        if key in seen_here:
            continue
        seen_here.add(key)

        # Drop the fragment; it addresses a position on a page, not another page.
        clean = parsed_full._replace(fragment="").geturl()
        if any(target in path for target in DEEP_CRAWL_PATHS):
            priority_links.append(clean)
        else:
            regular_links.append(clean)

    return priority_links, regular_links


def _discover_site_urls(start_url: str, html: str) -> List[str]:
    """Pages linked from `html`, most semantically valuable first, start page included.

    One page's links only. Routing uses this to widen thin evidence cheaply; the crawl
    itself uses `crawl_slice`, which recurses.
    """
    priority_links, regular_links = _extract_internal_links(start_url, html)
    return [start_url] + priority_links + regular_links


def new_crawl_state(start_url: str) -> Dict[str, Any]:
    """The frontier of a crawl that has not started, in a form JSON can carry.

    Everything the crawl needs to continue lives here rather than in local variables, so
    a crawl can stop after any page and be resumed later, in a different process, from
    storage. Sets and deques become lists for the same reason.
    """
    return {
        "start_url": start_url,
        "priority": [start_url],
        "regular": [],
        "seen": [_page_key(start_url)],
        "crawled": [],
        "failed": [],
        "nodes": [],
        "edges": [],
        "attempts": 0,
    }


def crawl_is_complete(state: Dict[str, Any], max_pages: int) -> bool:
    """True when the budget is spent or there is nothing left linked to read."""
    return state["attempts"] >= max_pages or not (state["priority"] or state["regular"])


def crawl_slice(
    state: Dict[str, Any],
    vertical_id: str,
    max_pages: int,
    budget_pages: Optional[int] = None,
    budget_seconds: Optional[float] = None,
) -> Dict[str, Any]:
    """Advance a crawl by at most one slice, updating `state` in place.

    Slices exist because a long crawl has to be many short requests rather than one long
    one. Cloud Run throttles CPU outside request handling, so a background thread stops
    making progress the moment a response is returned; work has to happen inside a
    request. A budget keeps each request well under the request timeout, and gives a
    caller something truthful to show instead of a spinner.

    Passing no budget crawls to completion, which is what the synchronous path wants.
    """
    priority_q: deque = deque(state["priority"])
    regular_q: deque = deque(state["regular"])
    seen = set(state["seen"])

    def enqueue(urls: List[str], queue: deque) -> None:
        for url in urls:
            key = _page_key(url)
            if key in seen or len(seen) >= _MAX_FRONTIER:
                continue
            seen.add(key)
            queue.append(url)

    started = time.monotonic()
    pages_this_slice = 0

    # max_pages budgets attempts, not successes. The binding constraint is the request
    # timeout, and a site that fails half its fetches would otherwise silently fetch twice
    # as many pages as asked for.
    while state["attempts"] < max_pages and (priority_q or regular_q):
        # Both budgets are ignored until one page has been attempted. A slice that can
        # return having done nothing is not a slow slice, it is a job that never finishes:
        # the caller polls forever and the crawl stays where it was.
        if pages_this_slice:
            if budget_pages is not None and pages_this_slice >= budget_pages:
                break
            # Checked before starting a page rather than after finishing one: a page costs
            # roughly sixteen seconds, so a budget tested afterwards would overshoot by
            # that much every time.
            if budget_seconds is not None and (time.monotonic() - started) >= budget_seconds:
                break

        page_url = priority_q.popleft() if priority_q else regular_q.popleft()
        state["attempts"] += 1
        pages_this_slice += 1

        try:
            validate_url_for_fetch(page_url)
            html = smart_fetch(page_url)
        except Exception as err:
            # Separated from extraction below: "we never got the page" and "we got it and
            # could not read it" call for different responses from whoever investigates.
            logger.warning("[SiteKG] Could not fetch %s: %s", page_url, err)
            state["failed"].append({"url": page_url, "error": "fetch failed: %s" % str(err)[:250]})
            continue

        try:
            # Hand over the HTML we already hold. Passing the URL would make build_page_kg
            # fetch it a second time, doubling every crawl.
            payload = html
            if payload.lstrip()[:8].lower().startswith(("http://", "https://")):
                payload = "<html><body>%s</body></html>" % html
            pkg = build_page_kg(payload, url=page_url, vertical_id=vertical_id)
        except Exception as err:
            logger.warning("[SiteKG] Could not extract %s: %s", page_url, err)
            state["failed"].append({"url": page_url, "error": "extraction failed: %s" % str(err)[:250]})
            continue

        state["crawled"].append(page_url)
        # Stored as plain dicts so the state can be written out and read back between
        # requests without the models having to survive the round trip.
        state["nodes"].extend(n.model_dump() for n in pkg.nodes)
        state["edges"].extend(e.model_dump() for e in pkg.edges)

        found_priority, found_regular = _extract_internal_links(page_url, html)
        enqueue(found_priority, priority_q)
        enqueue(found_regular, regular_q)

    state["priority"] = list(priority_q)
    state["regular"] = list(regular_q)
    state["seen"] = list(seen)
    return state


def assemble_site_kg(
    state: Dict[str, Any],
    vertical_id: str,
    max_pages: int,
    persist: bool = True
) -> SiteKnowledgeGraph:
    """Canonicalize a finished crawl into the site graph and its exports.

    Split from the crawl itself so a resumed job can assemble a result from state it did
    not gather, and so the expensive part runs once at the end rather than per slice.
    """
    start_url = state["start_url"]
    domain = urlparse(start_url).netloc.replace("www.", "").lower()

    crawled_urls = state["crawled"]
    if not crawled_urls:
        # Refuse rather than return an empty graph. Coverage is scored against whatever
        # the graph holds, so nothing read scores as nothing covered: an unreachable site
        # would be handed back a confident 0% and the entire ontology as unwritten
        # content. That is a finding about the crawl, not about the site.
        first = state["failed"][0]["error"] if state["failed"] else "no readable pages were found"
        raise ValueError("Could not read any page of %s (%s)." % (start_url, first))

    all_raw_nodes = [KGNode(**n) for n in state["nodes"]]
    all_raw_edges = [KGEdge(**e) for e in state["edges"]]
    failed_pages = [PageFailure(**f) for f in state["failed"]]

    logger.info("[SiteKG] %s: read %d of %d pages found (limit %d), %d failed",
                domain, len(crawled_urls), len(state["seen"]), max_pages, len(failed_pages))

    canonical_nodes = _canonicalize_nodes(all_raw_nodes)
    canonical_edges = _canonicalize_edges(all_raw_edges, canonical_nodes)
    induced_schema = _induce_domain_ontology(canonical_edges)
    top_hubs, clusters = _compute_topology_and_clusters(canonical_nodes, canonical_edges)
    jsonld_graph = _build_site_jsonld(domain, canonical_nodes, canonical_edges)
    turtle_graph = _build_site_turtle(domain, canonical_nodes, canonical_edges)

    site_kg = SiteKnowledgeGraph(
        domain=domain,
        pages_crawled=len(crawled_urls),
        page_urls=crawled_urls,
        pages_discovered=len(state["seen"]),
        pages_requested=max_pages,
        pages_failed=len(failed_pages),
        failed_pages=failed_pages[:25],
        nodes=canonical_nodes,
        edges=canonical_edges,
        induced_class_hierarchy=induced_schema,
        topic_clusters=clusters,
        top_authority_hubs=top_hubs,
        # The vocabulary this crawl actually ran with, set here so it cannot drift from
        # the labels that produced the nodes above.
        vertical_id=vertical_id,
        export_jsonld=jsonld_graph,
        export_turtle=turtle_graph
    )

    if persist:
        persist_site_kg(site_kg, vertical_id)

    return site_kg


def _canonicalize_nodes(raw_nodes: List[KGNode]) -> List[KGNode]:
    """Merge coreferent entity nodes across multiple pages into canonical nodes."""
    canonical_map: Dict[str, KGNode] = {}

    for node in raw_nodes:
        # Normalize key for canonical clustering
        clean_key = re.sub(r'\s+', ' ', node.canonical_name.strip().lower())

        # Strip common trailing suffixes for canonical grouping (e.g. "Stripe API" -> "Stripe")
        base_key = clean_key
        for suffix in [" api", " integration", " connector", " sync", " platform", " software", " solution"]:
            if base_key.endswith(suffix) and len(base_key) > len(suffix) + 3:
                base_key = base_key[:-len(suffix)].strip()
                break

        if base_key in canonical_map:
            existing = canonical_map[base_key]
            existing.mentions_count += node.mentions_count
            for url in node.source_urls:
                if url not in existing.source_urls:
                    existing.source_urls.append(url)
            for alias in node.aliases:
                if alias not in existing.aliases:
                    existing.aliases.append(alias)
            if node.wikidata_id and not existing.wikidata_id:
                existing.wikidata_id = node.wikidata_id
        else:
            # Check Wikidata lookup
            qid = node.wikidata_id or WIKIDATA_KB.get(base_key)
            node_id = f"entity:{re.sub(r'[^a-zA-Z0-9_-]', '_', base_key)}"
            canonical_map[base_key] = KGNode(
                id=node_id,
                canonical_name=node.canonical_name,
                entity_type=node.entity_type,
                aliases=list(set(node.aliases + [node.canonical_name])),
                wikidata_id=qid,
                mentions_count=node.mentions_count,
                source_urls=list(node.source_urls)
            )

    return list(canonical_map.values())


def _canonicalize_edges(raw_edges: List[KGEdge], canonical_nodes: List[KGNode]) -> List[KGEdge]:
    """Deduplicate and link edges to canonical entity names."""
    # Build name lookup to canonical name
    alias_to_canonical = {}
    for node in canonical_nodes:
        alias_to_canonical[node.canonical_name.lower()] = node.canonical_name
        for alias in node.aliases:
            alias_to_canonical[alias.lower()] = node.canonical_name

    unique_edges: Dict[Tuple[str, str, str], KGEdge] = {}

    for edge in raw_edges:
        can_source = alias_to_canonical.get(edge.source.lower(), edge.source)
        can_target = alias_to_canonical.get(edge.target.lower(), edge.target)

        # Skip self loops
        if can_source.lower() == can_target.lower():
            continue

        edge_key = (can_source.lower(), edge.predicate.lower(), can_target.lower())

        if edge_key in unique_edges:
            existing = unique_edges[edge_key]
            # Keep higher confidence or longer provenance sentence
            if edge.confidence > existing.confidence:
                existing.confidence = edge.confidence
            if edge.provenance_sentence and (not existing.provenance_sentence or len(edge.provenance_sentence) > len(existing.provenance_sentence)):
                existing.provenance_sentence = edge.provenance_sentence
        else:
            sub_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', can_source.lower())
            tgt_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', can_target.lower())
            edge_id = f"edge:{sub_slug}-{edge.predicate.lower()}-{tgt_slug}"

            unique_edges[edge_key] = KGEdge(
                id=edge_id,
                source=can_source,
                target=can_target,
                predicate=edge.predicate,
                source_type=edge.source_type,
                target_type=edge.target_type,
                confidence=edge.confidence,
                provenance_sentence=edge.provenance_sentence,
                source_url=edge.source_url
            )

    return list(unique_edges.values())


def _induce_domain_ontology(edges: List[KGEdge]) -> List[InducedClassRelation]:
    """Induce the structural class-level ontology from instance-level edges."""
    class_pair_counts: Dict[Tuple[str, str, str], int] = {}

    for edge in edges:
        s_type = edge.source_type or "Entity"
        p = edge.predicate
        t_type = edge.target_type or "Entity"

        key = (s_type, p, t_type)
        class_pair_counts[key] = class_pair_counts.get(key, 0) + 1

    induced = []
    for (s_type, pred, t_type), count in sorted(class_pair_counts.items(), key=lambda x: x[1], reverse=True):
        induced.append(InducedClassRelation(
            source_class=s_type,
            predicate=pred,
            target_class=t_type,
            count=count
        ))

    return induced


def _compute_topology_and_clusters(nodes: List[KGNode], edges: List[KGEdge]) -> Tuple[List[str], List[TopicCluster]]:
    """Compute PageRank authority hubs and semantic topic clusters using NetworkX."""
    G = nx.DiGraph()

    for node in nodes:
        G.add_node(node.canonical_name, entity_type=node.entity_type)

    for edge in edges:
        G.add_edge(edge.source, edge.target, predicate=edge.predicate, weight=edge.confidence)

    if len(G) == 0:
        return [], []

    # 1. PageRank Authority Hubs
    try:
        pagerank_scores = nx.pagerank(G, alpha=0.85, max_iter=100)
        sorted_hubs = sorted(pagerank_scores.items(), key=lambda x: x[1], reverse=True)
        top_hubs = [name for name, score in sorted_hubs[:8]]
    except Exception as e:
        logger.debug("PageRank calculation skipped: %s", e)
        top_hubs = [n.canonical_name for n in sorted(nodes, key=lambda x: x.mentions_count, reverse=True)[:8]]

    # 2. Topic Clusters grouped by entity type and co-occurrence
    cluster_map: Dict[str, List[str]] = {}
    for node in nodes:
        cluster_map.setdefault(node.entity_type, []).append(node.canonical_name)

    clusters = []
    for idx, (etype, members) in enumerate(cluster_map.items(), 1):
        clusters.append(TopicCluster(
            cluster_id=f"cluster_{idx}",
            cluster_label=f"{etype} Concepts",
            representative_entities=members[:10],
            page_urls=[]
        ))

    return top_hubs, clusters


def _build_site_jsonld(domain: str, nodes: List[KGNode], edges: List[KGEdge]) -> Dict[str, Any]:
    """Generates site-wide W3C JSON-LD @graph representation."""
    graph_items = []

    # Main organization / platform
    brand_name = domain.split(".")[0].capitalize()
    graph_items.append({
        "@type": "SoftwareApplication",
        "@id": f"https://{domain}/#{brand_name.lower()}",
        "name": brand_name,
        "url": f"https://{domain}",
        "featureList": [e.target for e in edges if e.predicate in ("hasFeature", "automates")][:25],
        "availableOnDevice": [e.target for e in edges if e.predicate == "integratesWith"][:25]
    })

    for node in order_nodes_for_export(nodes, _MAX_EXPORT_NODES):
        item: Dict[str, Any] = {
            "@type": "Thing",
            "@id": node.id,
            "name": node.canonical_name,
            "additionalType": node.entity_type
        }
        same_as = wikidata_uri(node.wikidata_id)
        if same_as:
            item["sameAs"] = same_as
        graph_items.append(item)

    return {
        "@context": "https://schema.org",
        "@graph": graph_items
    }


def _build_site_turtle(domain: str, nodes: List[KGNode], edges: List[KGEdge]) -> str:
    """Generates site-wide W3C RDF Turtle representation."""
    brand_name = domain.split(".")[0].capitalize()
    sub_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', brand_name.lower())

    lines = [
        "@prefix schema: <http://schema.org/> .",
        "@prefix ex: <https://gainark.com/kg/> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
        f"ex:{sub_slug} a schema:SoftwareApplication ;",
        f'    schema:name "{brand_name}" ;',
        f'    schema:url <https://{domain}> .'
    ]

    for e in edges:
        tgt_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', e.target.lower())
        lines.append(f'ex:{sub_slug} ex:{e.predicate} ex:{tgt_slug} .')
        lines.append(f'ex:{tgt_slug} schema:name "{e.target}" .')

    return "\n".join(lines)


def _page_text(html: str) -> str:
    """Readable prose from a page, falling back to the raw markup."""
    try:
        import trafilatura
        return trafilatura.extract(html) or html or ""
    except Exception:
        return html or ""


def route_domain_to_vertical(start_url: str, max_extra_pages: int = 3) -> Dict[str, Any]:
    """Choose an existing vertical for a domain, reading more pages if the first is thin.

    A subscriber gives a domain, not a taxonomy id, so the vertical has to be inferred
    before extraction can start - the vertical supplies the entity labels the extractor
    looks for, so it cannot be decided afterwards.

    Only fetches are used here, never the extractor: a fetch costs about a second against
    roughly sixteen for a GLiNER pass, so widening the evidence is cheap in a way that
    crawling is not.
    """
    validate_url_for_fetch(start_url)
    try:
        html = smart_fetch(start_url)
    except Exception as err:
        # Routing runs before the crawl and outside its error handling, so an unreachable
        # domain surfaced here as a bare 500. It is an unreachable site, which the caller
        # can act on, not an internal fault.
        raise ValueError("Could not fetch %s: %s" % (start_url, err))
    text = _page_text(html)
    result = classify_vertical(text)
    result["pages_read"] = 1

    if result.get("vertical_id") or max_extra_pages <= 0:
        return result

    # Thin or ambiguous. Widen the evidence before giving up.
    extra = [u for u in _discover_site_urls(start_url, html)
             if u != start_url][:max_extra_pages]
    read = 1
    for url in extra:
        try:
            text += "\n" + _page_text(smart_fetch(url))
            read += 1
        except Exception as err:
            logger.warning("[Routing] Could not read %s: %s", url, err)

    if read == 1:
        return result

    widened = classify_vertical(text)
    widened["pages_read"] = read
    if not widened.get("vertical_id"):
        widened["reason"] = "%s (after reading %d pages)" % (widened["reason"], read)
    return widened


def persist_site_kg(site_kg: SiteKnowledgeGraph, vertical_id: str) -> Optional[Dict[str, Any]]:
    """Record one crawl as a run graph in the durable store. Never raises.

    A run is the unit of "when", so storing each crawl under its own graph id is what
    makes change-over-time answerable at all: two runs can be diffed without any triple
    carrying a timestamp of its own.

    persist_graph, not persist_audit: persist_audit splits on the ontology namespace and
    calls replace_graph() on the ontology half, and replace_graph DELETEs before writing.
    A site graph is minted under a different namespace from the ontology, so that half is
    always empty and the vertical's stored concepts would be wiped on every crawl.

    Persistence is a side benefit of a crawl, never its purpose: a store that cannot be
    written is a degraded service, not a failed request, so every failure here is logged
    and swallowed.
    """
    try:
        import graph_store
        from rdflib import Graph as RdfGraph

        graph = RdfGraph()
        graph.parse(data=site_kg.export_turtle, format="turtle")
        if len(graph) == 0:
            logger.info("Site graph for %s holds no triples; nothing to persist.", site_kg.domain)
            return None

        graph_id = graph_store.run_graph_id(site_kg.domain)
        quads = graph_store.persist_graph(
            graph, graph_id, kind="run",
            domain=site_kg.domain, vertical_id=vertical_id,
            metadata=json.dumps({
                "pages_crawled": site_kg.pages_crawled,
                "nodes": len(site_kg.nodes),
                "edges": len(site_kg.edges),
            }),
        )
        logger.info("Persisted run graph %s (%d quads).", graph_id, quads)
        return {"graph_id": graph_id, "quads": quads}
    except Exception as err:
        logger.error("Could not persist the site graph for %s: %s", site_kg.domain, err)
        return None


def build_site_kg(
    start_url: str,
    max_pages: int = 10,
    vertical_id: str = "b2b_saas_fintech",
    persist: bool = True
) -> SiteKnowledgeGraph:
    """
    Crawl, aggregate, and synthesize an entire website into a canonical Knowledge Graph.

    Runs the whole crawl in one call. The same machinery can be driven a slice at a time
    through crawl_slice() when the crawl is too long to fit inside one request.
    """
    validate_url_for_fetch(start_url)

    state = new_crawl_state(start_url)
    crawl_slice(state, vertical_id, max_pages)
    return assemble_site_kg(state, vertical_id, max_pages, persist=persist)
