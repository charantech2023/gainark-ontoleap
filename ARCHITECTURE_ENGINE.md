# OntoLeap Engine Architecture & Co-Worker Reference Guide (v2.2)

> **For Co-Workers (Claude, Antigravity, and Human Engineers)**: This document defines the architectural contracts, data structures, and tool bindings of the GainARK OntoLeap platform following the knowledge-graph core refactor (commit `b7a9174` and subsequent releases). Follow these standards when modifying or extending this codebase.

---

## 1. What the Engine Mechanically Is

OntoLeap is **not** a generic LLM wrapper or a vector-similarity search engine. It is a:
> **Deterministic Relational Knowledge Graph Extraction, Topology & Industry Alignment Engine.**

It executes discrete, reproducible graph mathematics across digital text, code specifications, and structured semantic ontologies.

```
                    ┌──────────────────────────────┐
                    │ Raw Input (URL, HTML, Text)  │
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
                    │    + Grounded Wikidata Q-IDs │
                    │       + Schema.org JSON-LD   │
                    └──────┬───────────────┬───────┘
                           │               │
            ┌──────────────┴───────┐       └──────────────┬───────────────┐
            │                      │                      │               │
            ▼                      ▼                      ▼               ▼
┌──────────────────────┐ ┌───────────────────┐ ┌─────────────────┐ ┌─────────────┐
│ 2. Page & Site KG    │ │ 3. Network Graph  │ │ 4. Taxonomy     │ │ 5. Standards│
│ Entity nodes,        │ │ PageRank Centrality│ │ Alignment       │ │ Export      │
│ relation edges,      │ │ Topic Authority   │ │ Covered Concepts│ │ W3C RDF     │
│ evidence quotes      │ │ Entity Canonical. │ │ Whitespace Gaps │ │ OWL 2 DL    │
│ (page/site_graph.py) │ │ (crawl_jobs.py)   │ │ (industry_ont.) │ │ SPARQL 1.1  │
└──────────────────────┘ └───────────────────┘ └─────────────────┘ └─────────────┘
```

---

## 2. Codebase Map & Module Responsibilities

| File | Primary Responsibility | Key Functions / Classes |
| :--- | :--- | :--- |
| `page_graph.py` | **Page-Level KG Engine**. Single-page extraction, entity canonicalization, GLiNER NER, and RDF Turtle generation. | `extract_page_knowledge_graph`, `PageKnowledgeGraph` |
| `site_graph.py` | **Site-Level KG Engine**. Multi-page crawl aggregation, cross-page entity resolution, and domain ontology schema induction. | `build_site_knowledge_graph`, `SiteKnowledgeGraph` |
| `crawl_jobs.py` | **Resumable Async Crawl Coordinator**. Discrete multi-step crawl execution preventing Cloud Run timeouts. | `CrawlJobManager`, `advance_job`, `CrawlJobStatus` |
| `industry_ontology.py` | **Taxonomy & SKOS Engine**. Curated reference ontologies, concept hierarchies, altLabel matching, and whitespace analysis. | `IndustryOntologyEngine`, `align_graph_with_industry`, `resolve_concept_by_label` |
| `industry_profiler.py` | **Autonomous Vertical Discovery**. Zero-shot category discovery from candidate pages (reading buyer personas, evidence, competitors). | `discover_industry_vertical`, `extract_evidence_candidates` |
| `vertical_store.py` | **Vertical Persistence**. Filesystem persistence of dynamically discovered industry taxonomies across server lifecycles. | `VerticalStore`, `save_vertical`, `get_vertical` |
| `graph_engine.py` | **Unified Graph Query & Reasoning**. Decoupled engine facade for SPARQL 1.1 querying, OWL 2 DL exports, and link prediction. | `GraphEngine`, `get_graph_engine`, `execute_sparql_query` |
| `entity_grounding.py` | **External Entity Authority**. Resolves phrases to authoritative Wikidata Q-IDs via curated KB and bounded live resolution. | `ground_url`, `ground_id`, `prefetch`, `wikidata_uri` |
| `compliance_ontology.py`| **Compliance Frameworks**. Multi-step evaluation for regulatory standards (SOC 2, HIPAA, GDPR, ISO 27001, ASC 606). | `COMPLIANCE_FRAMEWORKS`, `evaluate_compliance_readiness` |
| `ontology_schema.py` | **Schema & Relations**. Single source of truth for semantic predicates, relation specs, and domain/range definitions. | `CORE_RELATIONS`, `RelationSpec`, `get_relation_spec` |
| `link_prediction.py` | **Graph Inferences**. Predicts missing edges in extracted graphs using ontological priors and transitivity rules. | `predict_missing_links`, `LinkPredictionResponse` |
| `pipeline.py` | **NLP Pipeline & Workers**. Shared GLiNER model loading, Schema.org extractor (`extruct`), and relational triple mining. | `OntologyPipeline`, `_load_shared_gliner` |
| `scraper.py` | **Headless HTTP/TLS Scraper**. Optional Jina Reader first level (`ONTOLEAP_JINA_READER`), Chrome TLS impersonation (curl_cffi), anti-bot header rotation, rate limiting, and SSRF security. | `smart_fetch`, `smart_fetch_async`, `validate_url_for_fetch` |
| `mcp_server.py` | **Official MCP Server**. Standard I/O Model Context Protocol server exposing KG extraction tools to Claude, Cursor, and agents. | `ontoleap_build_page_kg`, `ontoleap_build_site_kg`, `ontoleap_align_industry_ontology` |
| `models.py` | **Pydantic Data Contracts**. Pydantic v2 data models for knowledge graphs, alignments, and crawl jobs. | `KGNode`, `KGEdge`, `PageKnowledgeGraph`, `SiteKnowledgeGraph`, `GraphAlignmentResult` |
| `constants.py` | **Taxonomies & Network Rules**. Curated Wikidata KB, high-value crawl path heuristics, and SSRF blocked IP prefixes. | `WIKIDATA_KB`, `DEEP_CRAWL_PATHS`, `BLOCKED_IP_PREFIXES` |

---

## 3. The 4 Official MCP Tools (Signatures & JSON Schemas)

AI assistants (Claude Desktop, Cursor, Antigravity) call these tools via the Model Context Protocol (`mcp_server.py`):

### Tool 1: `ontoleap_build_page_kg`
* **Description**: Extracts a rich Knowledge Graph from a URL or raw HTML: Named Entities with Wikidata Q-IDs, semantic triples with exact sentence evidence, Schema.org types, and W3C JSON-LD / Turtle serialization.
* **Arguments**:
  * `source` (str, required): Web page URL (e.g. `'https://www.ordwaylabs.com'`) or raw HTML content.
  * `url` (str, optional): Canonical URL if `source` contains raw HTML.
  * `vertical_id` (str, optional): Vertical ontology domain ID. Omit to infer it dynamically from content.
* **Returns**:
  ```json
  {
    "url": "https://www.ordwaylabs.com",
    "domain": "ordwaylabs.com",
    "vertical_id": "b2b_saas_fintech",
    "nodes_count": 16,
    "edges_count": 12,
    "nodes": [
      {
        "id": "ordwaylabs.com:Ordway",
        "label": "Ordway",
        "entity_type": "Organization",
        "wikidata_id": "https://www.wikidata.org/wiki/Q113645856",
        "mentions_count": 8
      },
      {
        "id": "ordwaylabs.com:ASC 606",
        "label": "ASC 606",
        "entity_type": "Standard",
        "wikidata_id": "https://www.wikidata.org/wiki/Q28195748"
      }
    ],
    "edges": [
      {
        "subject_id": "ordwaylabs.com:Ordway",
        "predicate": "compliesWith",
        "object_id": "ordwaylabs.com:ASC 606",
        "confidence": 0.95,
        "evidence_quote": "Ordway billing and revenue recognition software is fully compliant with ASC 606 and IFRS 15."
      }
    ],
    "turtle_serialization": "@prefix schema: <https://schema.org/> .\n..."
  }
  ```

### Tool 2: `ontoleap_build_site_kg`
* **Description**: Crawls a website across multiple pages, canonicalizes entities across aliases, induces the domain ontology schema, and computes PageRank authority hubs.
* **Arguments**:
  * `start_url` (str, required): Target domain homepage URL (e.g. `'https://www.ordwaylabs.com'`).
  * `max_pages` (int, default: 40): Maximum pages to crawl.
  * `vertical_id` (str, optional): Vertical ontology domain ID. Omit to infer from the site.
* **Returns**:
  ```json
  {
    "domain": "ordwaylabs.com",
    "vertical_id": "b2b_saas_fintech",
    "pages_crawled": 12,
    "nodes_count": 48,
    "edges_count": 37,
    "topic_authority_hubs": [
      {
        "concept": "Revenue Recognition",
        "canonical_url": "https://ordwaylabs.com/products/revenue-recognition-software-asc-606-ifrs-15/",
        "pagerank_score": 0.284,
        "inbound_connections": 9
      }
    ]
  }
  ```

### Tool 3: `ontoleap_align_industry_ontology`
* **Description**: Aligns an extracted Page Knowledge Graph or Site Knowledge Graph against an industry reference taxonomy to identify Covered Concepts, Category Whitespace, and Standards Compliance.
* **Arguments**:
  * `source_url` (str, required): Target web page URL to extract and align.
  * `vertical_id` (str, optional): Industry vertical ID (e.g. `'b2b_saas_fintech'`, `'cybersecurity'`). Omit to infer.
* **Returns**:
  ```json
  {
    "vertical_id": "b2b_saas_fintech",
    "coverage_score_pct": 74.2,
    "covered_concepts": [
      {
        "concept_id": "revenue_recognition",
        "pref_label": "Revenue Recognition",
        "matched_term": "ASC 606 Revenue Schedule",
        "match_type": "altLabel"
      }
    ],
    "whitespace_concepts": [
      {
        "concept_id": "dunning_management",
        "pref_label": "Dunning Management",
        "category": "Billing Operations"
      }
    ],
    "proprietary_concepts": [
      "Smart Revenue Schedule Engine"
    ]
  }
  ```

### Tool 4: `ontoleap_list_industry_ontologies`
* **Description**: Lists all registered and discovered industry reference ontologies with display names, category hierarchies, and IDs.
* **Arguments**: None.
* **Returns**:
  ```json
  {
    "industries": [
      {"id": "b2b_saas_fintech", "name": "B2B SaaS Fintech & Billing", "concepts_count": 34},
      {"id": "cybersecurity", "name": "Enterprise Cybersecurity", "concepts_count": 81}
    ]
  }
  ```

---

## 4. Key Rules for Co-Workers

1. **Deterministic Extraction & Graph Math**: Do not substitute LLM-generated approximations for discrete graph facts. Entity grounding, triple matching, and taxonomy coverage must remain reproducible and mathematically verifiable.
2. **Robust Synonym Matching (`prefLabel` + `altLabel`)**: When comparing extracted text to vertical taxonomies, always test both `prefLabel` and curated `altLabel` dictionaries (e.g. "Order-to-Revenue Cycle" must match "Quote-to-Cash"). Respect word boundaries so short forms (e.g. "IR", "EDR") do not match inside unrelated words.
3. **Budgeted Resumable Crawls**: Large site-wide graphs must be executed via `crawl_jobs.py` as resumable batches rather than single synchronous HTTP requests to prevent Cloud Run 60s/300s timeout crashes.
4. **Clean Stdio Logging in MCP**: All logging in `mcp_server.py` must stream exclusively to `sys.stderr` (`stream=sys.stderr`). Never write raw strings to `sys.stdout`, as stdout is reserved strictly for JSON-RPC MCP messages.
5. **Safe Entity Grounding**: All Wikidata lookups must route through `entity_grounding.py` using `prefetch()` for concurrency, caching negative misses, and respecting the `ONTOLEAP_WIKIDATA_BUDGET` ceiling.
