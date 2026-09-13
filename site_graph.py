"""
site_graph.py — Pure Site-Wide Knowledge Graph & Domain Ontology Synthesizer

Aggregates page-level graphs across a website to construct a unified domain Knowledge Graph:
1. Crawls breadth-first from a start URL, reading high-value architectural paths
   (/features, /pricing, /integrations, /solutions) ahead of everything else.
2. Extracts page knowledge graphs in batch.
3. Resolves every extracted name to an identity through entity_resolver: a registry
   entity, a vertical concept, the site's own brand, or an unresolved name.
4. Induces the Domain Class Hierarchy (e.g. SoftwarePlatform -> integratesWith -> IntegrationPartner).
5. Computes PageRank authority hubs and semantic topic clusters.
6. Serializes site-wide W3C JSON-LD and Turtle graphs.
"""

import os
import re
import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import List, Dict, Tuple, Any, Optional
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import networkx as nx

from models import (
    SiteKnowledgeGraph, KGNode, KGEdge,
    InducedClassRelation, TopicCluster, PageFailure
)
from rdflib import Graph as RdfGraph, Literal, Namespace, RDF, URIRef
from rdflib.namespace import SKOS

from scraper import smart_fetch, validate_url_for_fetch
from entity_grounding import wikidata_uri
import crawl_planner
from entity_registry import Registry, default_store
from entity_resolver import normalise_key, resolve_site
from industry_ontology import _alias_index, _label_matches, classify_vertical, load_industry_ontology
from industry_profiler import confirm_vertical_by_category, fetch_sitemap_urls
from page_graph import build_page_kg, order_nodes_for_export
from constants import DEEP_CRAWL_PATHS

logger = logging.getLogger("gainark.site_graph")

# A crawl aggregates many pages, so the guard sits higher than the page-level one.
_MAX_EXPORT_NODES = 1000


# Pages fetched at once. Fetching is waiting, so this is where a crawl gets faster; the
# scraper's Jina rate limit keeps it within what the reader allows.
FETCH_CONCURRENCY = int(os.environ.get("ONTOLEAP_FETCH_CONCURRENCY", "6") or 6)

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
    storage. The plan - candidates, per-kind yield, what the graph already holds - is part
    of it for the same reason.
    """
    plan = crawl_planner.new_plan(start_url)
    crawl_planner.add_candidates(plan, [start_url], "start", set())
    return {
        "start_url": start_url,
        "plan": plan,
        "seen": [_page_key(start_url)],
        "crawled": [],
        "failed": [],
        "nodes": [],
        "edges": [],
        "attempts": 0,
    }


def _plan_for(state: Dict[str, Any]) -> Dict[str, Any]:
    """The state's plan, built from an older queue-based state if it predates planning."""
    plan = state.get("plan")
    if plan is None:
        plan = crawl_planner.new_plan(state["start_url"])
        done = {_page_key(u) for u in state.get("crawled", [])} | {
            _page_key(f["url"]) for f in state.get("failed", [])}
        queued = list(state.pop("priority", []) or []) + list(state.pop("regular", []) or [])
        crawl_planner.add_candidates(plan, queued, "link", done)
        state["plan"] = plan
    return plan


def crawl_is_complete(state: Dict[str, Any], max_pages: int) -> bool:
    """True when the budget is spent, or no page left to read belongs to a kind that is
    still adding anything to the graph."""
    if state["attempts"] >= max_pages:
        return True
    return crawl_planner.exhausted(_plan_for(state))


def _page_links(page_url: str, html: str) -> List[str]:
    """Every link on a page, absolute. Which of them are worth reading is the planner's call."""
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        links.append(urljoin(page_url, href))
    return links


def _read_sitemap(origin: str) -> List[str]:
    """A host's sitemap, read through this module's fetch. Never raises."""
    return fetch_sitemap_urls(origin, fetch=lambda url, **kw: smart_fetch(url, **kw))


def _fetch_page(url: str, lite: str) -> Tuple[Optional[str], Optional[str]]:
    """(html, None) or (None, error text). Runs on a worker thread."""
    try:
        validate_url_for_fetch(url)
        return smart_fetch(url, lite=lite), None
    except Exception as err:
        return None, str(err)


def _fetch_batch(batch: List[Dict[str, Any]], start_url: str) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
    """Fetch a batch of pages concurrently.

    Fetching is waiting - on the site, or on Jina rendering it - so it runs in parallel;
    extraction is CPU and stays serial. The start page is read with its navigation kept,
    because its menu and footer are the crawl's map of the site; every other page is read
    trimmed. The rate limit lives in the scraper, so concurrency here cannot push Jina past it.
    """
    start_key = _page_key(start_url)
    jobs = [(c["url"], "full" if c["key"] == start_key else "trimmed") for c in batch]
    if len(jobs) == 1:
        return {jobs[0][0]: _fetch_page(*jobs[0])}
    with ThreadPoolExecutor(max_workers=min(FETCH_CONCURRENCY, len(jobs))) as pool:
        futures = {url: pool.submit(_fetch_page, url, lite) for url, lite in jobs}
        return {url: fut.result() for url, fut in futures.items()}


def page_identities(pkg, registry: Optional[Registry], industry) -> List[str]:
    """What one page tells the graph, as identity strings, for scoring a crawl's yield.

    Three kinds count: a registry entity the page names, a vertical concept it covers -
    matched exactly as coverage scoring matches, so yield and coverage agree - and a claim
    (predicate and object). An unresolved name does not count: every customer story names
    a new company, and counting those would keep a crawl reading customer stories forever.
    """
    out: List[str] = []
    terms = set()
    for node in pkg.nodes:
        for form in [node.canonical_name] + list(node.aliases or []):
            terms.add(form.strip().lower())
            if registry is not None:
                hits = registry.lookup(normalise_key(form))
                if len(hits) == 1:
                    out.append("entity:" + hits[0].id)
    for edge in pkg.edges:
        terms.add(edge.target.strip().lower())
        out.append("claim:%s|%s" % (edge.predicate, normalise_key(edge.target)))
    if industry is not None and industry.concepts and terms:
        index = _alias_index(industry)
        for concept in industry.concepts:
            if _label_matches(concept.pref_label, index, terms):
                out.append("concept:" + concept.id)
    return out


def _resolution_context(vertical_id: str):
    try:
        registry = default_store().registry()
    except Exception as err:
        logger.warning("[SiteKG] Registry unavailable for yield scoring: %s", err)
        registry = None
    try:
        industry = load_industry_ontology(vertical_id)
    except Exception:
        industry = None
    return registry, industry


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

    Which pages, in what order, and when to stop is crawl_planner's. This reads the
    site's sitemap once, fetches the planner's batches in parallel, extracts each page,
    scores what it added, and feeds its links back to the planner.

    Passing no budget crawls to completion, which is what the synchronous path wants.
    """
    plan = _plan_for(state)
    done = {_page_key(u) for u in state["crawled"]} | {_page_key(f["url"]) for f in state["failed"]}
    registry, industry = _resolution_context(vertical_id)
    started = time.monotonic()
    pages_this_slice = 0

    def add(urls: List[str], source: str) -> None:
        for host in crawl_planner.add_candidates(plan, urls, source, done):
            # A docs or help host the site links to: its sitemap is where its articles are
            # listed, and it is read once.
            plan["sitemaps"].append(host)
            add(_read_sitemap("https://%s" % host), "sitemap")

    if plan["site_host"] not in plan["sitemaps"]:
        plan["sitemaps"].append(plan["site_host"])
        parsed = urlparse(state["start_url"])
        add(_read_sitemap("%s://%s" % (parsed.scheme, parsed.netloc)), "sitemap")

    # max_pages budgets attempts, not successes. The binding constraint is the request
    # timeout, and a site that fails half its fetches would otherwise silently fetch twice
    # as many pages as asked for.
    while state["attempts"] < max_pages:
        # Both budgets are ignored until one page has been attempted. A slice that can
        # return having done nothing is not a slow slice, it is a job that never finishes:
        # the caller polls forever and the crawl stays where it was.
        size = FETCH_CONCURRENCY
        if pages_this_slice:
            if budget_pages is not None and pages_this_slice >= budget_pages:
                break
            # Checked before starting a batch rather than after finishing one, so a slice
            # overshoots its budget by at most one batch.
            if budget_seconds is not None and (time.monotonic() - started) >= budget_seconds:
                break
        if budget_pages is not None:
            size = min(size, budget_pages - pages_this_slice)

        batch = crawl_planner.next_batch(plan, max_pages, state["attempts"], size)
        if not batch:
            break
        # Marked done before any of the batch is read: a link on its first page to its
        # last would otherwise make the last a candidate again, and it would be read twice.
        # The first local 150-page crawl of ordwaylabs.com read four pages twice this way.
        done.update(cand["key"] for cand in batch)
        state["attempts"] += len(batch)
        fetched = _fetch_batch(batch, state["start_url"])

        for cand in batch:
            page_url, kind = cand["url"], cand["kind"]
            pages_this_slice += 1
            html, fetch_error = fetched[page_url]
            if fetch_error is not None:
                # Separated from extraction below: "we never got the page" and "we got it
                # and could not read it" call for different responses from whoever
                # investigates.
                logger.warning("[SiteKG] Could not fetch %s: %s", page_url, fetch_error)
                state["failed"].append({"url": page_url, "error": "fetch failed: %s" % fetch_error[:250]})
                crawl_planner.record_failure(plan, kind)
                continue

            try:
                # Hand over the HTML we already hold. Passing the URL would make
                # build_page_kg fetch it a second time, doubling every crawl.
                payload = html
                if payload.lstrip()[:8].lower().startswith(("http://", "https://")):
                    payload = "<html><body>%s</body></html>" % html
                pkg = build_page_kg(payload, url=page_url, vertical_id=vertical_id)
            except Exception as err:
                logger.warning("[SiteKG] Could not extract %s: %s", page_url, err)
                state["failed"].append({"url": page_url, "error": "extraction failed: %s" % str(err)[:250]})
                crawl_planner.record_failure(plan, kind)
                continue

            state["crawled"].append(page_url)
            # Stored as plain dicts so the state can be written out and read back between
            # requests without the models having to survive the round trip.
            state["nodes"].extend(n.model_dump() for n in pkg.nodes)
            state["edges"].extend(e.model_dump() for e in pkg.edges)

            added = crawl_planner.record_yield(plan, page_url, kind, page_identities(pkg, registry, industry))
            logger.debug("[SiteKG] %s (%s) added %d", page_url, kind, added)
            add(_page_links(page_url, html), "link")

    state["seen"] = sorted(done | set(plan["candidates"]) | set(state.get("seen") or []))
    return state


def assemble_site_kg(
    state: Dict[str, Any],
    vertical_id: str,
    max_pages: int,
    persist: bool = True,
    registry: Optional[Registry] = None,
) -> SiteKnowledgeGraph:
    """Resolve a finished crawl into the site graph and its exports.

    Split from the crawl itself so a resumed job can assemble a result from state it did
    not gather, and so the expensive part runs once at the end rather than per slice.
    Deterministic given `state` and the registry, which is what lets eval/registry_replay
    measure an identity change on stored crawls.
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

    store = None
    if registry is None:
        store = default_store()
        registry = store.registry()
    try:
        industry = load_industry_ontology(vertical_id)
    except Exception as err:
        # Without the vertical, concepts cannot be identified; registry entities and the
        # brand still can, so this degrades rather than fails.
        logger.warning("[SiteKG] Vertical %s unavailable for resolution: %s", vertical_id, err)
        industry = None

    resolved = resolve_site(all_raw_nodes, all_raw_edges, domain, vertical_id, registry, industry)
    logger.info("[SiteKG] %s resolution: %s", domain, resolved.summary["groups_by_method"])

    induced_schema = _induce_domain_ontology(resolved.edges)
    top_hubs, clusters = _compute_topology_and_clusters(resolved.nodes, resolved.edges)
    jsonld_graph = _build_site_jsonld(domain, resolved.brand, resolved.nodes, resolved.edges)
    turtle_graph = _build_site_turtle(domain, resolved.brand, resolved.nodes, resolved.edges)

    site_kg = SiteKnowledgeGraph(
        domain=domain,
        pages_crawled=len(crawled_urls),
        page_urls=crawled_urls,
        pages_discovered=len(state["seen"]),
        pages_requested=max_pages,
        pages_failed=len(failed_pages),
        failed_pages=failed_pages[:25],
        nodes=resolved.nodes,
        mentions=resolved.mentions,
        resolution_summary=resolved.summary,
        crawl_summary=crawl_planner.summary(state["plan"]) if state.get("plan") else {},
        edges=resolved.edges,
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
        if store is not None:
            # What each page added travels with how each name resolved: together they are
            # the evidence for where on a site of this kind the graph's content lives.
            store.record_observation(dict(resolved.observation,
                                          pages_crawled=len(crawled_urls),
                                          summary=resolved.summary,
                                          crawl=site_kg.crawl_summary,
                                          yield_log=(state.get("plan") or {}).get("yield_log", [])))

    return site_kg


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


def _build_site_jsonld(domain: str, brand: KGNode, nodes: List[KGNode], edges: List[KGEdge]) -> Dict[str, Any]:
    """Site-wide JSON-LD @graph: the brand, then every identified node.

    Mentions are not here. A common-noun phrase ("new customers") names nothing a consumer
    of the export could link to, and publishing it as a Thing is noise that buries the
    entities that do have identities.
    """
    subject: Dict[str, Any] = {
        "@type": "SoftwareApplication",
        "@id": brand.id,
        "name": brand.canonical_name,
        "url": f"https://{domain}",
        "featureList": [e.target for e in edges if e.predicate in ("hasFeature", "automates")][:25],
        "availableOnDevice": [e.target for e in edges if e.predicate == "integratesWith"][:25]
    }
    brand_same_as = wikidata_uri(brand.wikidata_id)
    if brand_same_as:
        subject["sameAs"] = brand_same_as
    graph_items = [subject]

    for node in order_nodes_for_export([n for n in nodes if n.id != brand.id], _MAX_EXPORT_NODES):
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


_EX = Namespace("https://gainark.com/kg/")
_SCHEMA = Namespace("http://schema.org/")


def _build_site_turtle(domain: str, brand: KGNode, nodes: List[KGNode], edges: List[KGEdge]) -> str:
    """Site-wide Turtle: the claims, between resolved identities.

    Subjects and objects are node ids, not slugs of whatever spelling a page used, so the
    stored run graph - and graph_store.diff_runs over two of them - compares identities.
    A site that writes "Salesforce.com" one month and "Salesforce" the next no longer
    reads as one claim dropped and another added.

    Built through rdflib rather than by string formatting, which broke on any name
    containing a quote.
    """
    g = RdfGraph()
    g.bind("schema", _SCHEMA)
    g.bind("ex", _EX)
    g.bind("skos", SKOS)

    by_id = {n.id: n for n in nodes}
    subject = URIRef(brand.id)
    g.add((subject, RDF.type, _SCHEMA.SoftwareApplication))
    g.add((subject, _SCHEMA.name, Literal(brand.canonical_name)))
    g.add((subject, _SCHEMA.url, URIRef(f"https://{domain}")))

    described = set()
    for e in edges:
        s, o = URIRef(e.source_id), URIRef(e.target_id)
        g.add((s, _EX[e.predicate], o))
        for node_id, name in ((e.source_id, e.source), (e.target_id, e.target)):
            if node_id in described:
                continue
            described.add(node_id)
            g.add((URIRef(node_id), _SCHEMA.name, Literal(name)))
            node = by_id.get(node_id)
            if node is None:
                continue
            same_as = wikidata_uri(node.wikidata_id)
            if same_as:
                g.add((URIRef(node_id), _SCHEMA.sameAs, URIRef(same_as)))
            if node.concept_uri:
                g.add((URIRef(node_id), SKOS.exactMatch, URIRef(node.concept_uri)))

    return g.serialize(format="turtle")


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
    pages = [(start_url, html)]
    text = _page_text(html)
    result = classify_vertical(text, distinctive=True)
    result["pages_read"] = 1

    if result.get("confident") or max_extra_pages <= 0:
        return _confirm_weak_route(result, pages)

    # Thin, ambiguous, or routed on weak evidence. Widen before deciding anything.
    extra = [u for u in _discover_site_urls(start_url, html)
             if u != start_url][:max_extra_pages]
    for url in extra:
        try:
            page_html = smart_fetch(url)
        except Exception as err:
            logger.warning("[Routing] Could not read %s: %s", url, err)
            continue
        pages.append((url, page_html))
        text += "\n" + _page_text(page_html)

    if len(pages) == 1:
        return _confirm_weak_route(result, pages)

    widened = classify_vertical(text, distinctive=True)
    widened["pages_read"] = len(pages)
    if not widened.get("vertical_id"):
        widened["reason"] = "%s (after reading %d pages)" % (widened["reason"], len(pages))
    return _confirm_weak_route(widened, pages)


def route_text_to_vertical(text: str, url: Optional[str] = None) -> Dict[str, Any]:
    """Route content the caller already supplied, under the same rules as a domain."""
    result = classify_vertical(text, distinctive=True)
    result["pages_read"] = 0
    return _confirm_weak_route(result, [(url or "", text or "")])


def _confirm_weak_route(result: Dict[str, Any], pages: List[Tuple[str, str]]) -> Dict[str, Any]:
    """Keep a route only if its evidence is strong, or the site's own category agrees.

    Refusing beats defaulting here as everywhere in routing: a crawl measured against the
    wrong vertical returns a complete, plausible report about the wrong industry. So a weak
    route the category check cannot confirm - the model is unavailable, or the answer does
    not parse - is refused, not trusted.
    """
    vertical_id = result.get("vertical_id")
    if not vertical_id or result.get("confident"):
        return result

    check = confirm_vertical_by_category(pages, vertical_id)
    result["category_check"] = check
    if check.get("confirmed"):
        result["reason"] = "%s Evidence was thin, so the category was checked: %s" % (
            result["reason"], check["reason"])
        return result

    refused = {k: v for k, v in result.items() if k not in ("display_name", "matched", "evidence")}
    refused["vertical_id"] = None
    refused["reason"] = "Page text pointed to %s on %d terms, which is too thin to trust, and %s" % (
        vertical_id, result.get("matched", 0),
        check["reason"][0].lower() + check["reason"][1:] if check.get("confirmed") is False
        else "the category could not be checked: " + check["reason"])
    logger.info("[Routing] Refused %s: %s", vertical_id, refused["reason"])
    return refused


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
