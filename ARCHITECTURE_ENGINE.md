# OntoLeap Engine Architecture & Co-Worker Reference Guide

> **For Co-Workers (Claude, Antigravity, and Human Engineers)**: This document defines the architectural contracts, data structures, and tool bindings of the GainARK OntoLeap platform. Follow these standards when modifying or extending this codebase.

---

## 1. What the Engine Mechanically Is

OntoLeap is **not** a generic LLM wrapper or a vector-similarity search engine. It is a:
> **Deterministic Relational Knowledge Graph Extraction, Topology & Graph-Diff Engine.**

It executes discrete, repeatable graph mathematics across digital text, code specifications, and live AI search engines.

```
                    ┌──────────────────────────────┐
                    │ Raw Input (URL, Spec, Text)  │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │    1. Fact Extractor         │
                    │ (GLiNER Transformer + Rules) │
                    └──────────────┬───────────────┘
                                   │
                                   ▼
                    ┌──────────────────────────────┐
                    │  Semantic Triples ⟨S, P, O⟩  │
                    │   + Schema.org + Entities    │
                    └──────┬───────────────┬───────┘
                           │               │
            ┌──────────────┴───────┐       └──────────────┬───────────────┐
            │                      │                      │               │
            ▼                      ▼                      ▼               ▼
┌──────────────────────┐ ┌───────────────────┐ ┌─────────────────┐ ┌─────────────┐
│ 2. Graph Diff        │ │ 3. Network Graph  │ │ 4. AI Search    │ │ 5. Export   │
│ A ∩ B (Grounded)     │ │ PageRank          │ │ Prober          │ │ W3C RDF     │
│ A \ B (Hallucination)│ │ Betweenness       │ │ Gemini / Web    │ │ PROV-O .ttl │
│ B \ A (Omission)     │ │ Topic Silos       │ │ Share of Voice  │ │ JSON-LD     │
└──────────────────────┘ └───────────────────┘ └─────────────────┘ └─────────────┘
```

---

## 2. Codebase Map & File Responsibilities

| File | Primary Responsibility | Key Functions / Classes |
| :--- | :--- | :--- |
| `graph_engine.py` | **Core Engine Primitive**. Decoupled extraction, graph diff, topology, and GEO probing. | `GraphEngine`, `extract_knowledge_graph`, `diff_knowledge_graphs`, `analyze_site_topology`, `probe_ai_search_sov` |
| `mcp_server.py` | **Official MCP Server** on stdio transport for Claude Desktop, Cursor, and AI agents. | `ontoleap_extract_facts`, `ontoleap_cross_examine_diff`, `ontoleap_probe_ai_sov`, `ontoleap_map_site_topology` |
| `pipeline.py` | Core crawler, GLiNER NER model loader, Schema.org extractor (`extruct`), and triple miner. | `OntologyPipeline`, `fetch_sitemap_urls`, `discover_subpages` |
| `product_truth.py` | OpenAPI 3.0 spec parser, proof discovery aggregator, and grounding evaluator. | `extract_capabilities_from_openapi`, `evaluate_grounding` |
| `proof_discovery.py`| Autonomous discovery of proof sources (Trust Centers, PyPI/npm packages, changelogs). | `discover_technical_proof` |
| `geo_engine.py` | Live AI search query synthesizer, multi-engine prober, and Share of Voice (SOV) computer. | `run_geo_probing`, `generate_buyer_queries`, `probe_ai_citation` |
| `semantic_seo.py` | Multi-page crawler, topic authority hub discovery, and in-context internal linking engine. | `audit_internal_links`, `SemanticLinkingEngine` |
| `graph_analytics.py`| NetworkX graph algorithms: PageRank, betweenness centrality, cluster topology. | `compute_graph_pagerank`, `build_cluster_topology` |
| `models.py` | Pydantic data contracts and models. Single source of truth for schemas. | `SemanticTriple`, `EntityMatch`, `SiteAuditAndLinkResult`, `GeoProbingResult` |
| `scraper.py` | Headless scraping with Chrome TLS impersonation, rate-limiting, and SSRF security protection. | `smart_fetch`, `smart_fetch_async`, `validate_url_for_fetch` |
| `constants.py` | Grounding taxonomies, Wikidata knowledge base, and high-value path patterns. | `WIKIDATA_KB`, `DEEP_CRAWL_PATHS`, `BLOCKED_IP_PREFIXES` |

---

## 3. The 4 MCP Tools (Signatures & JSON Schemas)

AI assistants call these tools via the Model Context Protocol:

### Tool 1: `ontoleap_extract_facts`
* **Description**: Extracts verified semantic triples $\langle S, P, O \rangle$, named entities, and Schema.org types from a web URL or raw text.
* **Arguments**:
  * `source` (str, required): Web URL or raw markdown/text.
  * `vertical_id` (str, optional, default: `"b2b_saas_fintech"`): Domain vertical.
* **Returns**:
  ```json
  {
    "source": "https://www.ordwaylabs.com",
    "subject_entity": "Ordway",
    "readiness_score": 78.5,
    "entities_count": 14,
    "triples_count": 9,
    "entities": [{"text": "ASC 606", "label": "Accounting Standard", "score": 0.94}],
    "triples": [
      {
        "subject": "Ordway",
        "predicate": "automates",
        "object": "Revenue Recognition",
        "confidence": 0.9,
        "evidence": "Ordway automates revenue recognition compliant with ASC 606."
      }
    ],
    "detected_schemas": ["SoftwareApplication", "Organization"]
  }
  ```

### Tool 2: `ontoleap_cross_examine_diff`
* **Description**: Computes the discrete mathematical set difference between Source A (claims/copy) and Source B (proof/code/competitor).
* **Arguments**:
  * `source_a` (str, required): Marketing copy URL, blog draft, or landing page.
  * `source_b` (str, required): OpenAPI spec JSON, technical documentation URL, codebase docs, or competitor URL.
  * `vertical_id` (str, optional): Domain vertical ID.
* **Set Diff Math**:
  * `grounded_facts` $= A \cap B$ (Verified overlap)
  * `unbacked_claims` $= A \setminus B$ (Hallucinations / unbacked marketing drift)
  * `omitted_capabilities` $= B \setminus A$ (Shipped features omitted from marketing)
* **Returns**:
  ```json
  {
    "grounding_score": 66.7,
    "verdict": "Moderate Drift",
    "summary": {
      "total_claims_in_a": 6,
      "total_proof_in_b": 8,
      "grounded_count": 4,
      "unbacked_count": 2,
      "omitted_count": 4
    },
    "grounded_facts": [...],
    "unbacked_claims": [...],
    "omitted_capabilities": [...]
  }
  ```

### Tool 3: `ontoleap_probe_ai_sov`
* **Description**: Probes live AI answer engines with buyer queries, computes Share of Voice (SOV %), and audits AI hallucinations.
* **Arguments**:
  * `brand_name` (str, required): Primary brand.
  * `domain` (str, required): Brand domain.
  * `competitor_names` (list[str], optional): Competitor brands (e.g. `["Chargebee", "Stripe"]`).
  * `vertical_id` (str, optional): Industry vertical.
  * `custom_queries` (list[str], optional): Custom buyer queries.
* **Returns**:
  ```json
  {
    "brand_name": "Ordway",
    "share_of_voice_pct": 60.0,
    "weighted_sov_pct": 52.0,
    "ontology_grounding_score_pct": 100.0,
    "competitor_breakdown": {
      "Chargebee": 80.0,
      "Stripe": 60.0
    },
    "citation_gaps_count": 2,
    "queries_audited": [...]
  }
  ```

### Tool 4: `ontoleap_map_site_topology`
* **Description**: Crawls a sitemap or URL list, calculates NetworkX PageRank authority hubs, and generates in-context internal linking opportunities.
* **Arguments**:
  * `sitemap_url` (str, optional): XML sitemap URL.
  * `urls` (list[str], optional): List of explicit URLs.
  * `max_pages` (int, default: 10): Crawl page budget.
* **Returns**:
  ```json
  {
    "root_domain": "ordwaylabs.com",
    "pages_analyzed": 10,
    "topic_hubs": [
      {
        "concept": "Revenue Recognition",
        "canonical_url": "https://ordwaylabs.com/products/revenue-recognition-software-asc-606-ifrs-15/",
        "role": "Authority Anchor",
        "pagerank": 0.2415,
        "inbound_links": 6
      }
    ],
    "internal_link_opportunities": [...]
  }
  ```

---

## 4. Key Rules for Co-Workers

1. **Preserve Determinism**: Never replace graph diff math ($A \cap B$) with fuzzy LLM approximations. The engine must remain verifiable and reproducible.
2. **Strict Evidence Gating**: A claim is only verified if supported by an OpenAPI route, an SDK registry package (PyPI/npm), a Trust Center certificate, or verified technical documentation.
3. **No Unbounded Crawling**: Keep `max_pages` capped (default 10-15) and recursion guarded (`_depth <= 2`) to ensure fast response times under Cloud Run timeouts (60s).
4. **Clean Stdio Logging in MCP**: All logging in `mcp_server.py` must stream to `sys.stderr` so that `sys.stdout` remains dedicated exclusively to the JSON-RPC Model Context Protocol messages.
