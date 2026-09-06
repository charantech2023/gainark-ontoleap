"""
GainARK OntoLeap — Core Ontology Extraction & Semantic Ingestion Pipeline

This module executes the multi-stage ontology extraction and analysis pipeline:
1. Multi-Syntax Schema.org Extraction: Employs `extruct` and `BeautifulSoup` to parse JSON-LD,
   Microdata, RDFa, and OpenGraph structured data blocks.
2. Zero-Shot Named Entity Recognition: Leverages `GLiNER` (urchade/gliner_small-v2.1) bidirectional
   transformer to recognize custom vertical entities (features, integrations, standards, pricing).
3. Core Seed Concept Density: Evaluates domain-specific vocabulary presence and frequency.
4. Rule-Based Relational Semantic Triple Extraction: Mines structured subject-predicate-object
   statements (`automates`, `integratesWith`, `compliesWith`, `supportsPricingModel`).
5. Deep Sub-Page Crawling: Automatically discovers and parses high-signal subpages (/pricing,
   /features, /integrations, /solutions) to enrich entity coverage.
6. Multi-Page XML Sitemap Crawler: Crawls full sitemaps and indexes, consolidating deduplicated
   triples into a unified multi-page `@graph` JSON-LD schema with Wikidata entity grounding.

Changes:
- FIX #1:  SSRF protection — validate URLs before fetching to block localhost/internal IPs.
- FIX #5:  All knowledge bases consolidated into constants.py (no more triple-duplication).
- FIX #6:  run_audit_pipeline fixed to use keyword args so batch crawl actually works.
- FIX #12: Sitemap recursion depth guard to prevent infinite loops on circular sitemaps.
- FIX #21: All print() replaced with structured Python logging.
"""

import json
import re
import socket
import logging
import xml.etree.ElementTree as ET
import asyncio
from typing import Optional, List, Dict, Any, Set
from urllib.parse import urlparse, urljoin
import requests
import httpx
from bs4 import BeautifulSoup
import extruct
import trafilatura
from gliner import GLiNER

from models import (
    VerticalConfig, ExtractionResult, EntityMatch, SchemaOrgData,
    SeedConceptMatch, ReadinessBreakdown, KeywordGapItem, CompetitiveGapAnalysis,
    SemanticTriple, PageCrawlSummary, UnifiedSiteGraph
)
from scraper import smart_fetch, smart_fetch_async, validate_url_for_fetch
from constants import (
    WIKIDATA_KB, KNOWN_INTEGRATIONS, KNOWN_COMPLIANCE,
    KNOWN_PRICING, KNOWN_AUTOMATION, DEEP_CRAWL_PATHS, DEEP_CRAWL_MAX,
    BLOCKED_IP_PREFIXES, BLOCKED_HOSTNAMES,
)

# ---------------------------------------------------------------------------
# Structured logger — writes JSON-compatible records for Google Cloud Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# validate_url_for_fetch is imported from scraper.py (single source of truth for SSRF protection)


from remediation import generate_schema_patch


class OntologyPipeline:
    def __init__(
        self,
        config: Optional[VerticalConfig] = None,
        config_path: str = "vertical_config.json",
        gliner_model_name: str = "urchade/gliner_small-v2.1"
    ):
        if config:
            self.config = config
        else:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.config = VerticalConfig(**data)

        self.gliner_model_name = gliner_model_name
        self._model: Optional[GLiNER] = None

    @property
    def model(self) -> GLiNER:
        if self._model is None:
            logger.info("Loading GLiNER model: %s", self.gliner_model_name)
            self._model = GLiNER.from_pretrained(self.gliner_model_name)
        return self._model

    def fetch_url(self, url: str, timeout: int = 15) -> str:
        # Smart fetch provides Chrome TLS impersonation, SSRF protection, and anti-bot fallback
        return smart_fetch(url, timeout=timeout)

    def extract_schema_org(self, html: str) -> List[SchemaOrgData]:
        extracted_data = []
        try:
            raw_schemas = extruct.extract(html, uniform=True)
        except Exception as e:
            # FIX #21: use logger instead of print
            logger.warning("extruct failed to parse HTML: %s", e)
            raw_schemas = {}


        mandatory_set = {t.lower() for t in self.config.mandatory_schema_types}

        def clean_type(t: Any) -> List[str]:
            types = []
            if isinstance(t, str):
                types = [t]
            elif isinstance(t, list):
                types = [str(x) for x in t]
            return [x.rsplit("/", 1)[-1].rsplit(":", 1)[-1] for x in types]

        def collect_schema_nodes(obj: Any, syntax_type: str):
            if isinstance(obj, dict):
                type_val = obj.get("@type") or obj.get("type")
                if type_val:
                    type_names = clean_type(type_val)
                    type_name = ", ".join(type_names) if type_names else "Unknown"
                    is_mandatory = any(tn.lower() in mandatory_set for tn in type_names)
                    extracted_data.append(
                        SchemaOrgData(
                            schema_type=type_name,
                            syntax=syntax_type,
                            data=obj,
                            is_mandatory=is_mandatory
                        )
                    )
                for val in obj.values():
                    collect_schema_nodes(val, syntax_type)
            elif isinstance(obj, list):
                for item in obj:
                    collect_schema_nodes(item, syntax_type)

        for syntax in ["json-ld", "microdata", "rdfa", "opengraph"]:
            items = raw_schemas.get(syntax, [])
            collect_schema_nodes(items, syntax)

        return extracted_data

    def extract_seed_concepts(self, text: str, entities: Optional[List[EntityMatch]] = None) -> List[SeedConceptMatch]:
        matches = []
        text_lower = text.lower()
        entity_texts = [e.text.lower() for e in entities] if entities else []

        for concept in self.config.core_seed_concepts:
            pattern = re.compile(rf"\b{re.escape(concept)}\b", re.IGNORECASE)
            found = pattern.findall(text)

            # If exact regex did not match, try flexible multi-word matching & entity alignment
            if not found:
                concept_words = [w.lower() for w in re.split(r"[\s\-_]+", concept) if len(w) > 2]
                if concept_words:
                    # Check if all concept words appear in proximity or in extracted entities
                    matched_in_entity = [et for et in entity_texts if all(w in et for w in concept_words) or concept.lower() in et]
                    if matched_in_entity:
                        found = matched_in_entity
                    elif all(w in text_lower for w in concept_words):
                        found = [" ".join(concept_words)]

            if found:
                matches.append(
                    SeedConceptMatch(
                        concept=concept,
                        count=len(found),
                        matched_phrases=list(set(found))
                    )
                )
        return matches

    def extract_entities(self, text: str, threshold: float = 0.4) -> List[EntityMatch]:
        if not text.strip():
            return []
        
        # Process in chunks if text is long
        chunk_size = 1500
        words = text.split()
        chunks = []
        for i in range(0, len(words), chunk_size):
            chunks.append(" ".join(words[i:i + chunk_size]))

        all_matches = []
        for chunk in chunks:
            predictions = self.model.predict_entities(
                chunk,
                self.config.gliner_labels,
                threshold=threshold
            )
            for p in predictions:
                all_matches.append(
                    EntityMatch(
                        text=p["text"],
                        label=p["label"],
                        score=round(float(p["score"]), 4),
                        start=p["start"],
                        end=p["end"]
                    )
                )

        # Deduplicate identical text + label keeping highest score
        unique_matches = {}
        for m in all_matches:
            key = (m.text.lower(), m.label)
            if key not in unique_matches or m.score > unique_matches[key].score:
                unique_matches[key] = m

        return list(unique_matches.values())

    def discover_subpages(self, base_url: str, html: str) -> List[str]:
        """Discover high-value sub-paths from homepage internal links."""
        parsed = urlparse(base_url)
        base_origin = f"{parsed.scheme}://{parsed.netloc}"
        soup = BeautifulSoup(html, "html.parser")
        found: List[str] = []
        seen: Set[str] = set()
        # First pass: exact known paths
        for href_tag in soup.find_all("a", href=True):
            href = href_tag["href"].strip()
            # Resolve relative links
            if href.startswith("/"):
                full = base_origin + href
            elif href.startswith("http"):
                full = href
            else:
                continue
            parsed_href = urlparse(full)
            # Must be same domain and match a high-value path prefix
            if parsed_href.netloc != parsed.netloc:
                continue
            path = parsed_href.path.rstrip("/").lower()
            for target in DEEP_CRAWL_PATHS:
                if path == target or path.startswith(target + "/") or path.startswith(target + "-"):
                    canonical = f"{base_origin}{parsed_href.path}"
                    if canonical not in seen and canonical != base_url:
                        seen.add(canonical)
                        found.append(canonical)
                    break
            if len(found) >= DEEP_CRAWL_MAX:
                break
        return found[:DEEP_CRAWL_MAX]

    def resolve_target_subject(
        self,
        url: Optional[str] = None,
        title: Optional[str] = None,
        schema_data: Optional[List[SchemaOrgData]] = None,
        entities: Optional[List[EntityMatch]] = None,
        site_name: Optional[str] = None
    ) -> str:
        """Resolves canonical subject entity name for the target domain."""
        if site_name and len(site_name.strip()) > 1:
            return site_name.strip()
        if schema_data:
            for s in schema_data:
                if "Organization" in s.schema_type and isinstance(s.data, dict):
                    name = s.data.get("name") or s.data.get("legalName")
                    if name and len(name.strip()) > 1:
                        return name.strip()
        if entities:
            for ent in entities:
                if ent.label == "Software Platform" and len(ent.text.strip()) > 2:
                    return ent.text.strip()
        domain_brand = ""
        if url:
            netloc = urlparse(url).netloc.replace("www.", "")
            domain_brand = netloc.split(".")[0].capitalize()
        if title:
            # Check if any segment matches domain brand or is a clean brand name
            parts = [p.strip() for p in re.split(r"[-|:•—–]", title) if p.strip()]
            for p in parts:
                if domain_brand and p.lower() == domain_brand.lower():
                    return p
            for p in parts:
                if len(p) > 1 and len(p.split()) <= 3 and not any(w in p.lower() for w in ["home", "welcome", "official", "page", "login", "sign in"]):
                    return p
        if domain_brand:
            return domain_brand
        return "Platform"

    def extract_semantic_triples(
        self,
        text: str,
        subject: str,
        entities: Optional[List[EntityMatch]] = None,
        seed_concepts: Optional[List[SeedConceptMatch]] = None
    ) -> List[SemanticTriple]:
        """
        Rule-based predicate extraction engine for B2B SaaS targeting 4 key predicates:
        - automates (e.g. billing, revenue recognition, invoicing, accounts receivable)
        - integratesWith (e.g. NetSuite, Salesforce, QuickBooks, Stripe)
        - compliesWith (e.g. ASC 606, SOC 2, IFRS 15, GAAP)
        - supportsPricingModel (e.g. usage-based, subscription, hybrid, tiered)
        """
        triples: List[SemanticTriple] = []
        seen = set()

        def add_triple(pred: str, obj: str, conf: float = 0.85, ev: str = ""):
            obj_clean = obj.strip(" ,.-:\n\t\"'")
            if len(obj_clean) < 3:
                return
            key = (subject.lower(), pred, obj_clean.lower())
            if key not in seen:
                seen.add(key)
                triples.append(SemanticTriple(
                    subject=subject,
                    predicate=pred,
                    object=obj_clean,
                    confidence=round(conf, 2),
                    evidence_sentence=ev[:160].strip() if ev else None
                ))

        sentences = [s.strip() for s in re.split(r'(?<=[.!?\n])\s+', text) if len(s.strip()) > 12]

        # 1. automates
        auto_cues = ["automate", "automates", "automating", "automated", "streamline", "streamlines", "effortless", "liberate", "replaces spreadsheet"]
        for sent in sentences:
            s_lower = sent.lower()
            if any(cue in s_lower for cue in auto_cues):
                for item in KNOWN_AUTOMATION:
                    if item.lower() in s_lower:
                        add_triple("automates", item, conf=0.90, ev=sent)

        # 2. integratesWith
        int_cues = ["integrate", "integrates", "integration", "integrations", "connect", "connects", "sync", "syncs", "built for", "works with", "native"]
        for sent in sentences:
            s_lower = sent.lower()
            if any(cue in s_lower for cue in int_cues):
                for partner in KNOWN_INTEGRATIONS:
                    if re.search(rf'\b{re.escape(partner.lower())}\b', s_lower):
                        add_triple("integratesWith", partner, conf=0.90, ev=sent)
            else:
                for partner in ["Salesforce", "NetSuite", "QuickBooks", "Stripe", "Workday"]:
                    if re.search(rf'\b{re.escape(partner.lower())}\b', s_lower) and any(w in s_lower for w in ["partner", "connect", "sync", "api", "ecosystem"]):
                        add_triple("integratesWith", partner, conf=0.85, ev=sent)

        # 3. compliesWith
        for sent in sentences:
            s_lower = sent.lower()
            for std in KNOWN_COMPLIANCE:
                if re.search(rf'\b{re.escape(std.lower())}\b', s_lower):
                    add_triple("compliesWith", std, conf=0.95, ev=sent)

        # 4. supportsPricingModel
        pricing_cues = ["pricing", "bill", "billing", "model", "monetiz", "monetize", "plans"]
        for sent in sentences:
            s_lower = sent.lower()
            for pm in KNOWN_PRICING:
                pm_simple = pm.lower().replace(" pricing", "").replace(" billing", "")
                if pm.lower() in s_lower or (pm_simple in s_lower and any(cue in s_lower for cue in pricing_cues)):
                    add_triple("supportsPricingModel", pm, conf=0.90, ev=sent)

        # Augment with extracted entities and seed concepts
        if entities:
            for ent in entities:
                txt = ent.text.strip()
                if ent.label == "Integration Partner":
                    add_triple("integratesWith", txt, conf=0.88)
                elif ent.label in ["Accounting Standard", "Security Standard"]:
                    add_triple("compliesWith", txt, conf=0.92)
                elif ent.label == "Pricing Model":
                    add_triple("supportsPricingModel", txt, conf=0.88)
                elif ent.label == "Billing Feature" and any(w in txt.lower() for w in ["automate", "recognition", "invoicing", "dunning", "reconciliation"]):
                    add_triple("automates", txt, conf=0.88)

        if seed_concepts:
            for sc in seed_concepts:
                if sc.count > 0:
                    c_name = sc.concept
                    if c_name in ["ASC 606", "SOC 1", "SOC 2"]:
                        add_triple("compliesWith", c_name, conf=0.90)
                    elif c_name == "ERP Integration":
                        add_triple("integratesWith", "ERP Systems", conf=0.85)
                    elif c_name == "Payment Gateway":
                        add_triple("integratesWith", "Payment Gateways", conf=0.85)
                    elif c_name in ["Revenue Recognition", "Billing Automation", "Accounts Receivable"]:
                        add_triple("automates", c_name, conf=0.88)

        return triples

    def process(
        self,
        html: Optional[str] = None,
        text: Optional[str] = None,
        url: Optional[str] = None,
        deep_crawl: bool = False
    ) -> ExtractionResult:
        if url and not html:
            html = self.fetch_url(url)

        title = None
        meta_desc = None
        site_name = None
        headings: List[str] = []

        if html:
            soup = BeautifulSoup(html, "html.parser")
            if soup.title and soup.title.string:
                title = soup.title.string.strip()

            # Extract meta description
            desc_tag = (
                soup.find("meta", attrs={"name": "description"}) or
                soup.find("meta", attrs={"property": "og:description"}) or
                soup.find("meta", attrs={"name": "twitter:description"})
            )
            if desc_tag and desc_tag.get("content"):
                meta_desc = desc_tag["content"].strip()

            # Extract site name
            site_tag = (
                soup.find("meta", attrs={"property": "og:site_name"}) or
                soup.find("meta", attrs={"name": "application-name"})
            )
            if site_tag and site_tag.get("content"):
                site_name = site_tag["content"].strip()

            # Extract primary headings
            for h in soup.find_all(["h1", "h2"]):
                ht = h.get_text(separator=" ", strip=True)
                if ht and 3 < len(ht) < 120 and ht not in headings:
                    headings.append(ht)

            if not text:
                text = trafilatura.extract(html) or soup.get_text(separator=" ", strip=True)

        text = text or ""

        # 1. Schema.org data
        schema_data = self.extract_schema_org(html) if html else []
        found_types = set()
        for s in schema_data:
            for part in s.schema_type.split(", "):
                found_types.add(part.lower())

        mandatory_status = {
            m_type: (m_type.lower() in found_types)
            for m_type in self.config.mandatory_schema_types
        }

        # 2. GLiNER NER entities
        entities = self.extract_entities(text)

        # 3. Seed concept occurrences (grounded by extracted entities)
        seed_matches = self.extract_seed_concepts(text, entities=entities)

        # --- Deep Crawl: merge sub-page signals ---
        crawled_subpages: List[str] = []
        if deep_crawl and url and html:
            subpages = self.discover_subpages(url, html)
            for sub_url in subpages:
                try:
                    logger.info("[DeepCrawl] Fetching sub-page: %s", sub_url)
                    sub_html = self.fetch_url(sub_url)
                    sub_soup = BeautifulSoup(sub_html, "html.parser")
                    sub_text = trafilatura.extract(sub_html) or sub_soup.get_text(separator=" ", strip=True)

                    # Merge schema data
                    sub_schemas = self.extract_schema_org(sub_html)
                    for s in sub_schemas:
                        for part in s.schema_type.split(", "):
                            found_types.add(part.lower())
                    schema_data.extend(sub_schemas)

                    # Merge mandatory schema detection
                    for m_type in self.config.mandatory_schema_types:
                        if not mandatory_status[m_type]:
                            mandatory_status[m_type] = m_type.lower() in found_types

                    # Merge entities (deduplicated)
                    sub_entities = self.extract_entities(sub_text)
                    existing_keys = {(e.text.lower(), e.label) for e in entities}
                    for e in sub_entities:
                        key = (e.text.lower(), e.label)
                        if key not in existing_keys:
                            entities.append(e)
                            existing_keys.add(key)

                    # Merge seed concepts (grounded by sub_entities)
                    sub_seeds = self.extract_seed_concepts(sub_text, entities=sub_entities)
                    concept_map = {sc.concept: sc for sc in seed_matches}
                    for sc in sub_seeds:
                        if sc.concept in concept_map:
                            existing = concept_map[sc.concept]
                            concept_map[sc.concept] = SeedConceptMatch(
                                concept=sc.concept,
                                count=existing.count + sc.count,
                                matched_phrases=list(set(existing.matched_phrases + sc.matched_phrases))
                            )
                        else:
                            concept_map[sc.concept] = sc
                    seed_matches = list(concept_map.values())

                    crawled_subpages.append(sub_url)
                except Exception as ex:
                    logger.warning("[DeepCrawl] Failed to fetch %s: %s", sub_url, ex)

        snippet = text[:500] + "..." if len(text) > 500 else text
        readiness = self.calculate_readiness_score(mandatory_status, seed_matches, entities)

        # 4. Extract relational semantic triples (Subject, Predicate, Object)
        subject = self.resolve_target_subject(url=url, title=title, schema_data=schema_data, entities=entities, site_name=site_name)
        triples = self.extract_semantic_triples(text=text, subject=subject, entities=entities, seed_concepts=seed_matches)

        result = ExtractionResult(
            url=url,
            title=title,
            vertical_id=self.config.vertical_id,
            extracted_text_snippet=snippet,
            entities=entities,
            schema_org=schema_data,
            seed_concepts=seed_matches,
            mandatory_schema_status=mandatory_status,
            readiness_score=readiness.total_score,
            readiness_breakdown=readiness,
            crawled_subpages=crawled_subpages,
            triples=triples,
            meta_description=meta_desc,
            site_name=site_name,
            headings=headings
        )

        # 5. Google Knowledge Graph Presence Verification
        try:
            import google_kg_client
            brand_to_lookup = site_name or subject
            if not brand_to_lookup or brand_to_lookup == "The Platform":
                from urllib.parse import urlparse
                brand_to_lookup = urlparse(url).netloc.replace("www.", "").split(".")[0].capitalize()
            if brand_to_lookup and len(brand_to_lookup) > 1:
                result.google_kg_presence = google_kg_client.search_entity(brand_to_lookup)
        except Exception as kg_err:
            logger.warning("Google Knowledge Graph lookup error: %s", kg_err)

        result.recommended_patch = generate_schema_patch(result)
        return result

    def calculate_readiness_score(
        self,
        mandatory_status: Dict[str, bool],
        seed_matches: List[SeedConceptMatch],
        entities: List[EntityMatch]
    ) -> ReadinessBreakdown:
        # 1. Mandatory Schema Score (max 40 pts)
        num_mandatory_present = sum(1 for v in mandatory_status.values() if v)
        total_mandatory = len(self.config.mandatory_schema_types) or 1
        schema_score = round((num_mandatory_present / total_mandatory) * 40.0, 2)

        # 2. Seed Concept Coverage Score (max 30 pts)
        num_concepts_present = len(seed_matches)
        total_concepts = len(self.config.core_seed_concepts) or 1
        concept_score = round((num_concepts_present / total_concepts) * 30.0, 2)

        # 3. Entity Richness & Confidence (max 30 pts)
        found_labels = {e.label for e in entities}
        total_labels = len(self.config.gliner_labels) or 1
        diversity_ratio = len(found_labels) / total_labels
        avg_confidence = (sum(e.score for e in entities) / len(entities)) if entities else 0.0
        volume_factor = min(len(entities) / 5.0, 1.0)
        strength_score = avg_confidence * volume_factor
        entity_score = round((diversity_ratio * 15.0) + (strength_score * 15.0), 2)

        total_score = round(schema_score + concept_score + entity_score, 2)

        details = {
            "mandatory_schemas_detected": f"{num_mandatory_present}/{total_mandatory}",
            "seed_concepts_detected": f"{num_concepts_present}/{total_concepts}",
            "gliner_labels_covered": f"{len(found_labels)}/{total_labels}",
            "entities_extracted": len(entities),
            "average_entity_confidence": round(avg_confidence, 4) if entities else 0.0
        }

        return ReadinessBreakdown(
            schema_score=schema_score,
            concept_score=concept_score,
            entity_score=entity_score,
            total_score=total_score,
            details=details
        )

    def run_competitive_gap_analysis(
        self,
        primary_result: ExtractionResult,
        competitor_results: List[ExtractionResult]
    ) -> CompetitiveGapAnalysis:
        # 1. Score ranking & leader calculation
        all_results = [primary_result] + competitor_results
        sorted_results = sorted(all_results, key=lambda x: x.readiness_score, reverse=True)
        leader = sorted_results[0]
        score_gap = round(max(0.0, leader.readiness_score - primary_result.readiness_score), 2)

        # 2. Schema Structure Gaps
        schema_gaps = []
        primary_mand = primary_result.mandatory_schema_status
        for schema_name, passed in primary_mand.items():
            if not passed:
                competitors_with_schema = [
                    c.url for c in competitor_results
                    if c.mandatory_schema_status.get(schema_name, False)
                ]
                if competitors_with_schema:
                    comp_names = [urlparse(u).netloc.replace("www.", "") for u in competitors_with_schema]
                    schema_gaps.append(
                        f"Missing mandatory schema '{schema_name}' (implemented by {', '.join(comp_names)})"
                    )
                else:
                    schema_gaps.append(
                        f"Missing mandatory schema '{schema_name}' across the category"
                    )

        schema_gaps.append("Lacks canonical Wikidata entity grounding ('about' array) for AI search citations")
        schema_gaps.append("Missing high-intent category synonyms ('alternateName') and target 'Audience' nodes")

        # 3. Ontological Keyword Gaps
        primary_concepts = {c.concept for c in primary_result.seed_concepts if c.count > 0}
        keyword_gaps = []

        for concept in self.config.core_seed_concepts:
            if concept not in primary_concepts:
                competitors_mentioning = [
                    c.url for c in competitor_results
                    if any(sc.concept == concept and sc.count > 0 for sc in c.seed_concepts)
                ]
                comp_count = len(competitors_mentioning)
                if comp_count > 0:
                    priority = "High" if comp_count >= 2 else "Medium"
                    comp_names = [urlparse(u).netloc.replace("www.", "") for u in competitors_mentioning]
                    keyword_gaps.append(KeywordGapItem(
                        concept=concept,
                        competitor_count=comp_count,
                        competitors=comp_names,
                        priority=priority
                    ))

        keyword_gaps.sort(key=lambda x: (x.competitor_count, x.priority == "High"), reverse=True)

        # 4. Generate Leapfrog Remediation Patch specifically for primary
        leapfrog_patch = generate_schema_patch(primary_result)

        # 5. Strategic Action Plan
        top_keyword_names = [k.concept for k in keyword_gaps[:3]]
        keywords_phrase = f" ({', '.join(top_keyword_names)})" if top_keyword_names else ""

        action_plan = [
            "Deploy the Leapfrog Schema.org JSON-LD patch to instantly achieve 100% mandatory compliance.",
            f"Close Ontological Gaps by targeting competitor seed concepts{keywords_phrase} in homepage H2 tags and hero copy.",
            "Ground core software entities with Wikidata URIs to capture high-authority citations in AI engines like Perplexity and ChatGPT.",
            "Implement high-intent 'alternateName' category synonyms to redirect conversational query fan-outs currently captured by competitors."
        ]

        return CompetitiveGapAnalysis(
            primary_url=primary_result.url or "",
            primary_score=primary_result.readiness_score,
            leader_url=leader.url or "",
            leader_score=leader.readiness_score,
            score_gap=score_gap,
            schema_gaps=schema_gaps,
            keyword_gaps=keyword_gaps,
            action_plan=action_plan,
            leapfrog_patch=leapfrog_patch
        )


async def fetch_sitemap_urls(sitemap_url: str, max_urls: int = 15, _depth: int = 0) -> List[str]:
    """
    Parses standard sitemaps and sitemap indexes, returning up to max_urls.
    FIX #12: _depth parameter guards against infinite recursion on circular
    sitemap references. Maximum recursion depth is 2 (index → sub-sitemap → URLs).
    """
    # FIX #12: Never recurse deeper than 2 levels
    if _depth > 2:
        logger.warning("Sitemap recursion depth exceeded for %s — skipping", sitemap_url)
        return []

    urls: List[str] = []

    try:
        content = await smart_fetch_async(sitemap_url, timeout=15)
        if not content:
            return urls

        content_bytes = content.encode("utf-8") if isinstance(content, str) else content
        root = ET.fromstring(content_bytes)
        namespace = {'ns': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

        # Check for sitemap index
        sub_sitemaps = root.findall('ns:sitemap/ns:loc', namespace)
        if not sub_sitemaps:
            sub_sitemaps = root.findall('sitemap/loc')

        if sub_sitemaps:
            for sub in sub_sitemaps[:3]:
                if sub.text:
                    # FIX #12: pass incremented depth to prevent circular recursion
                    sub_urls = await fetch_sitemap_urls(
                        sub.text.strip(),
                        max_urls=max_urls - len(urls),
                        _depth=_depth + 1
                    )
                    urls.extend(sub_urls)
                    if len(urls) >= max_urls:
                        break
        else:
            loc_nodes = root.findall('ns:url/ns:loc', namespace)
            if not loc_nodes:
                loc_nodes = root.findall('url/loc')
            for loc in loc_nodes:
                if not loc.text:
                    continue
                u = loc.text.strip()
                # Filter out asset URLs or non-HTML targets
                if not any(u.endswith(ext) for ext in ['.xml', '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.svg']):
                    urls.append(u)
                if len(urls) >= max_urls:
                    break
    except Exception as e:
        # FIX #21: use logger instead of print
        logger.error("Error parsing sitemap %s: %s", sitemap_url, e)

    return urls[:max_urls]



def deduplicate_site_triples(all_triples: List[SemanticTriple]) -> List[SemanticTriple]:
    """
    Normalizes and deduplicates semantic triples across multiple pages.
    Preserves highest confidence and combines evidence where appropriate.
    """
    seen: Dict[tuple, SemanticTriple] = {}
    for t in all_triples:
        key = (t.subject.strip().lower(), t.predicate.strip().lower(), t.object.strip().lower())
        if key not in seen:
            seen[key] = t
        else:
            # Retain higher confidence score
            if t.confidence > seen[key].confidence:
                seen[key] = t
    return list(seen.values())



# FIX #5: WIKIDATA_MAP is now an alias of the canonical WIKIDATA_KB from constants.py.
# This preserves backward compatibility with any code that still references WIKIDATA_MAP.
WIKIDATA_MAP = WIKIDATA_KB



def build_site_wide_schema_graph(domain: str, triples: List[SemanticTriple], entities: List[str]) -> Dict:
    """
    Builds a unified @graph JSON-LD node representing the organization and software ecosystem,
    enriched with canonical Wikidata sameAs entity reconciliation for search crawlers and LLMs.
    """
    clean_domain = domain.replace("www.", "") if domain else "example.com"
    brand_name = clean_domain.split('.')[0].capitalize()

    integrations = []
    for t in triples:
        if t.predicate == "integratesWith":
            item = {"@type": "SoftwareApplication", "name": t.object}
            w_url = WIKIDATA_MAP.get(t.object.lower().strip())
            if w_url:
                item["sameAs"] = w_url
            integrations.append(item)

    capabilities = [t.object for t in triples if t.predicate == "automates"]
    pricing_models = [t.object for t in triples if t.predicate == "supportsPricingModel"]

    compliance = []
    for t in triples:
        if t.predicate == "compliesWith":
            item = {"@type": "DefinedTerm", "name": t.object, "termCode": t.object}
            w_url = WIKIDATA_MAP.get(t.object.lower().strip())
            if w_url:
                item["sameAs"] = w_url
            compliance.append(item)

    org_node: Dict[str, Any] = {
        "@type": "Organization",
        "@id": f"https://{domain}/#organization",
        "name": brand_name,
        "url": f"https://{domain}/",
        "knowsAbout": capabilities + [c["name"] for c in compliance]
    }
    if brand_name.lower() in WIKIDATA_MAP:
        org_node["sameAs"] = [WIKIDATA_MAP[brand_name.lower()]]

    return {
        "@context": "https://schema.org",
        "@graph": [
            org_node,
            {
                "@type": "SoftwareApplication",
                "@id": f"https://{domain}/#software",
                "name": brand_name,
                "applicationCategory": "BusinessApplication",
                "operatingSystem": "All",
                "isRelatedTo": integrations,
                "about": compliance,
                "featureList": capabilities,
                "offers": [{"@type": "Offer", "description": pm} for pm in pricing_models]
            }
        ]
    }


# Shared default pipeline instance for crawl_and_build_unified_graph
_default_pipeline: Optional[OntologyPipeline] = None

def get_default_pipeline() -> OntologyPipeline:
    global _default_pipeline
    if _default_pipeline is None:
        _default_pipeline = OntologyPipeline()
    return _default_pipeline


async def run_audit_pipeline(url: str, pipeline: Optional[OntologyPipeline] = None) -> ExtractionResult:
    """
    Asynchronously runs the ontology audit pipeline for a single URL using an executor thread.

    FIX #6: Previously called p.process(url, False) which passed url as the 'html'
    parameter and False as 'text', causing the pipeline to process the string "False"
    instead of actually fetching the URL. Now uses a lambda with keyword args.

    FIX #11: asyncio.get_event_loop() replaced with asyncio.get_running_loop()
    (deprecated/incorrect inside an already-running async context like FastAPI).
    """
    p = pipeline or get_default_pipeline()
    loop = asyncio.get_running_loop()
    # Use keyword arguments so url= is correctly interpreted as the URL to fetch
    return await loop.run_in_executor(
        None,
        lambda: p.process(url=url, deep_crawl=False)
    )



async def crawl_and_build_unified_graph(
    sitemap_url: str,
    max_pages: int = 10,
    concurrency: int = 3,
    pipeline: Optional[OntologyPipeline] = None
) -> UnifiedSiteGraph:
    """
    Crawls pages from sitemap and synthesizes a site-wide knowledge graph.
    """
    domain = urlparse(sitemap_url).netloc
    urls = await fetch_sitemap_urls(sitemap_url, max_urls=max_pages)
    
    semaphore = asyncio.Semaphore(concurrency)
    summaries: List[PageCrawlSummary] = []
    collected_triples: List[SemanticTriple] = []
    collected_entities: Set[str] = set()

    async def worker(url: str):
        async with semaphore:
            try:
                # Runs existing audit pipeline for a single URL
                audit_res = await run_audit_pipeline(url, pipeline=pipeline)
                
                triples = audit_res.extraction.triples or []
                entities = [e.name for e in audit_res.extraction.entities or []]
                
                collected_triples.extend(triples)
                collected_entities.update(entities)
                
                summaries.append(PageCrawlSummary(
                    url=url,
                    status="success",
                    triples_found=len(triples),
                    entities_found=len(entities)
                ))
            except Exception as e:
                summaries.append(PageCrawlSummary(
                    url=url,
                    status="failed",
                    triples_found=0,
                    entities_found=0,
                    error=str(e)
                ))

    await asyncio.gather(*(worker(u) for u in urls))
    
    # Unify and deduplicate
    unified_triples = deduplicate_site_triples(collected_triples)
    
    # Synthesize Site-Wide Schema.org Knowledge Graph
    site_graph_jsonld = build_site_wide_schema_graph(domain, unified_triples, list(collected_entities))

    return UnifiedSiteGraph(
        root_domain=domain,
        total_pages_crawled=len(summaries),
        unique_entities=sorted(list(collected_entities)),
        triples=unified_triples,
        page_summaries=summaries,
        schema_graph_jsonld=site_graph_jsonld
    )


