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
import os
import re
import logging
import xml.etree.ElementTree as ET
import asyncio
import threading
from typing import Optional, List, Dict, Any, Set
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import extruct
import trafilatura

from models import (
    VerticalConfig, ExtractionResult, EntityMatch, SchemaOrgData,
    SeedConceptMatch, ReadinessBreakdown, KeywordGapItem, CompetitiveGapAnalysis,
    SemanticTriple, PageCrawlSummary, UnifiedSiteGraph
)
from scraper import smart_fetch, smart_fetch_async, validate_url_for_fetch
from entity_grounding import ground_url, prefetch as prefetch_grounding
from constants import (
    WIKIDATA_KB, KNOWN_INTEGRATIONS, KNOWN_COMPLIANCE,
    KNOWN_PRICING, KNOWN_AUTOMATION, KNOWN_FEATURES, KNOWN_SEGMENTS,
    KNOWN_INDUSTRIES, KNOWN_DEPLOYMENT, KNOWN_CERTIFICATIONS, KNOWN_API_TYPES,
    KNOWN_LOCALES, KNOWN_SLA, KNOWN_REPLACES, KNOWN_COMPETITORS, KNOWN_CUSTOMERS,
    DEEP_CRAWL_PATHS, DEEP_CRAWL_MAX,
      resolve_vocabulary, resolve_surface_forms
)

# ---------------------------------------------------------------------------
# Structured logger — writes JSON-compatible records for Google Cloud Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)


# validate_url_for_fetch is imported from scraper.py (single source of truth for SSRF protection)


# ---------------------------------------------------------------------------
# Shared GLiNER model registry
# ---------------------------------------------------------------------------
# The model is a stateless read-only forward pass, but it is several hundred MB of
# weights. Held per-pipeline it was loaded once per vertical, so serving N verticals
# cost N copies of the same weights. Keyed by model name and shared process-wide.
_MODEL_REGISTRY: Dict[str, Any] = {}
_MODEL_REGISTRY_LOCK = threading.Lock()


def _load_shared_gliner(model_name: str) -> Any:
    """Return the process-wide GLiNER instance for `model_name`, loading it once."""
    with _MODEL_REGISTRY_LOCK:
        if model_name in _MODEL_REGISTRY:
            return _MODEL_REGISTRY[model_name]

    logger.info("Loading GLiNER model on-demand: %s", model_name)
    from gliner import GLiNER
    model = GLiNER.from_pretrained(model_name)

    with _MODEL_REGISTRY_LOCK:
        # Another thread may have finished first; keep whichever instance won.
        return _MODEL_REGISTRY.setdefault(model_name, model)


def _resolve_overlapping_matches(triples: List[Any]) -> List[Any]:
    """Longest-match-wins for vocabulary terms that contain one another.

    The vertical vocabularies overlap by design: "Invoicing" is a real process and
    "Automated Invoicing" is a real feature, and both legitimately belong in the
    ontology. But a single phrase then matched both, so one marketing sentence
    produced two claims:

        "The platform provides Automated Invoicing"
            -> hasFeature  Automated Invoicing
            -> automates    Invoicing

    The truth matrix then hunts for evidence of two claims where the page made one,
    and a phrase with no docs backing generates two drift alerts instead of one -
    inflating the alert count and depressing the grounding score.

    Gazetteer matching has a standard answer: when two vocabulary terms match the
    same span, the longer one is the more specific reading and wins. Applied per
    evidence sentence, so the same short term still stands on its own elsewhere in
    the document.

    Triples sharing an identical object are untouched - SOC 2 Type II is correctly
    both compliesWith and certifiedBy, and neither contains the other.

    Scope differs by where a triple came from:

      * Rule-derived triples carry an evidence sentence, so they are compared only
        against others from that same sentence. A short term still stands on its own
        wherever the document uses it alone.
      * Entity- and seed-derived triples have no evidence sentence - the seed path is
        how "Revenue Recognition Automation" also yielded `automates Revenue
        Recognition` - so they are compared against every object in the document. An
        unevidenced triple that merely restates a more specific evidenced one adds a
        claim without adding information.
    """
    by_sentence: Dict[str, List[Any]] = {}
    unevidenced: List[Any] = []
    for t in triples:
        if t.evidence_sentence:
            by_sentence.setdefault(t.evidence_sentence, []).append(t)
        else:
            unevidenced.append(t)

    def subsumed_by(t: Any, candidates: List[Any]) -> Optional[Any]:
        t_obj = t.object.strip().lower()
        for other in candidates:
            if other is t:
                continue
            o_obj = other.object.strip().lower()
            if t_obj == o_obj or len(t_obj) >= len(o_obj):
                continue
            # Word-bounded containment: "Invoicing" inside "Automated Invoicing" is a
            # subsumed reading; "Sage" inside "Message" is not.
            if re.search(rf'\b{re.escape(t_obj)}\b', o_obj):
                return other
        return None

    dropped: Set[int] = set()
    for group in by_sentence.values():
        for t in group:
            winner = subsumed_by(t, group)
            if winner is not None:
                dropped.add(id(t))
                logger.debug("Overlap: %s '%s' subsumed by '%s' in the same sentence.",
                             t.predicate, t.object, winner.object)

    for t in unevidenced:
        winner = subsumed_by(t, triples)
        if winner is not None:
            dropped.add(id(t))
            logger.debug("Overlap: unevidenced %s '%s' subsumed by '%s'.",
                         t.predicate, t.object, winner.object)

    if dropped:
        logger.info("Overlap resolution dropped %d subsumed triple(s).", len(dropped))

    return [t for t in triples if id(t) not in dropped]


# Canonical default vertical profile. verticals/ is what the API serves
# (routers/deps.py resolves every vertical_id there), so benchmarks and demos must
# read the same file or they measure a configuration nobody runs. Resolved against
# this module's directory rather than the working directory, so a caller's cwd
# cannot silently select a different profile.
DEFAULT_VERTICAL_PROFILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "verticals", "b2b_saas_fintech.json"
)


class OntologyPipeline:
    def __init__(
        self,
        config: Optional[VerticalConfig] = None,
        config_path: Optional[str] = None,
        gliner_model_name: str = "urchade/gliner_small-v2.1"
    ):
        if config:
            self.config = config
        else:
            with open(config_path or DEFAULT_VERTICAL_PROFILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.config = VerticalConfig(**data)

        self.gliner_model_name = gliner_model_name
        self._model: Optional[Any] = None

    @property
    def model(self) -> Any:
        if self._model is None:
            self._model = _load_shared_gliner(self.gliner_model_name)
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
        
        # Process in chunks if text is long.
        # GLiNER truncates input at 384 tokens, so a chunk larger than that is
        # silently cut and the remainder of the page is never analysed. At roughly
        # 1.17 tokens per word for this corpus, 250 words (~293 tokens) leaves
        # headroom for token-dense pages. The previous 1500 put most pages in a
        # single oversized chunk: stripe.com/billing sent 1097 words as one chunk
        # and had ~70% of it discarded.
        chunk_size = 250
        # Chunk boundaries can split an entity ("ASC 606" landed on one). Overlap
        # so a term cut at a boundary still appears whole in the neighbouring chunk;
        # the dedupe below collapses the repeats, keeping the highest score.
        chunk_overlap = 25
        words = text.split()
        chunks = []
        step = chunk_size - chunk_overlap
        for i in range(0, len(words), step):
            chunk_words = words[i:i + chunk_size]
            if not chunk_words:
                break
            chunks.append(" ".join(chunk_words))
            if i + chunk_size >= len(words):
                break

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
            # Must be same domain (ignoring www prefix differences) and match a high-value path prefix
            if parsed_href.netloc.replace("www.", "").lower() != parsed.netloc.replace("www.", "").lower():
                continue
            path = parsed_href.path.rstrip("/").lower()
            for target in DEEP_CRAWL_PATHS:
                if path == target or path.startswith(target + "/") or path.startswith(target + "-"):
                    canonical = f"{parsed_href.scheme}://{parsed_href.netloc}{parsed_href.path}"
                    if canonical not in seen and path != "":
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

        # Vocabulary comes from the active vertical when it defines one, so a
        # healthcare site is read for HIPAA and Epic rather than for ASC 606 and
        # NetSuite. Verticals without their own lists fall back to the generic
        # B2B defaults, preserving previous behaviour exactly.
        # Features and automation carry alt_labels: marketing writes "Renewal
        # Management", the product's docs write "renewal". Both resolve to one canonical
        # label so a claim raised from marketing copy can be verified against the docs.
        forms_automation = resolve_surface_forms(self.config, 'known_automation', KNOWN_AUTOMATION)
        forms_features = resolve_surface_forms(self.config, 'known_features', KNOWN_FEATURES)

        vocab_automation = resolve_vocabulary(self.config, 'known_automation', KNOWN_AUTOMATION)
        vocab_integrations = resolve_vocabulary(self.config, 'known_integrations', KNOWN_INTEGRATIONS)
        vocab_compliance = resolve_vocabulary(self.config, 'known_compliance', KNOWN_COMPLIANCE)
        vocab_pricing = resolve_vocabulary(self.config, 'known_pricing', KNOWN_PRICING)
        vocab_features = resolve_vocabulary(self.config, 'known_features', KNOWN_FEATURES)
        vocab_segments = resolve_vocabulary(self.config, 'known_segments', KNOWN_SEGMENTS)
        vocab_industries = resolve_vocabulary(self.config, 'known_industries', KNOWN_INDUSTRIES)
        vocab_deployment = resolve_vocabulary(self.config, 'known_deployment', KNOWN_DEPLOYMENT)
        vocab_certifications = resolve_vocabulary(self.config, 'known_certifications', KNOWN_CERTIFICATIONS)
        vocab_api_types = resolve_vocabulary(self.config, 'known_api_types', KNOWN_API_TYPES)
        vocab_locales = resolve_vocabulary(self.config, 'known_locales', KNOWN_LOCALES)
        vocab_sla = resolve_vocabulary(self.config, 'known_sla', KNOWN_SLA)
        vocab_replaces = resolve_vocabulary(self.config, 'known_replaces', KNOWN_REPLACES)
        vocab_competitors = resolve_vocabulary(self.config, 'known_competitors', KNOWN_COMPETITORS)
        vocab_customers = resolve_vocabulary(self.config, 'known_customers', KNOWN_CUSTOMERS)

        # 1. automates
        #
        # As with hasFeature, the cue word is a confidence signal rather than a gate -
        # but here the asymmetry it created was worse. Marketing copy says "automates
        # dunning"; documentation says "dunning rules are configured under Setup" and
        # never uses an automation cue at all. So a claim raised from marketing could
        # essentially never be verified against the docs, and `automates` became a
        # structural drift generator rather than a measurement.
        #
        # The vocabulary is curated and corpus-grounded, so a bounded match on
        # "Revenue Allocation" is evidence whether or not the sentence advertises it.
        # Uncued matches sit above the 0.75 floor in semantic_seo.py but below the 0.88
        # threshold that marks a high-signal capability there.
        auto_cues = ["automate", "automates", "automating", "automated", "streamline", "streamlines", "effortless", "liberate", "replaces spreadsheet"]
        for sent in sentences:
            s_lower = sent.lower()
            cued = any(cue in s_lower for cue in auto_cues)
            for canonical, surface_forms in forms_automation:
                # Longest form first, so the specific reading wins and we stop.
                for form in surface_forms:
                    if re.search(rf'\b{re.escape(form.lower())}\b', s_lower):
                        add_triple("automates", canonical, conf=0.90 if cued else 0.80, ev=sent)
                        break

        # 2. integratesWith
        int_cues = ["integrate", "integrates", "integration", "integrations", "connect", "connects", "sync", "syncs", "built for", "works with", "native"]
        for sent in sentences:
            s_lower = sent.lower()
            if any(cue in s_lower for cue in int_cues):
                for partner in vocab_integrations:
                    if re.search(rf'\b{re.escape(partner.lower())}\b', s_lower):
                        add_triple("integratesWith", partner, conf=0.90, ev=sent)
            else:
                for partner in vocab_integrations[:5]:
                    if re.search(rf'\b{re.escape(partner.lower())}\b', s_lower) and any(w in s_lower for w in ["partner", "connect", "sync", "api", "ecosystem"]):
                        add_triple("integratesWith", partner, conf=0.85, ev=sent)

        # 3. compliesWith
        for sent in sentences:
            s_lower = sent.lower()
            for std in vocab_compliance:
                if re.search(rf'\b{re.escape(std.lower())}\b', s_lower):
                    add_triple("compliesWith", std, conf=0.95, ev=sent)

        # 4. supportsPricingModel
        pricing_cues = ["pricing", "bill", "billing", "model", "monetiz", "monetize", "plans"]
        for sent in sentences:
            s_lower = sent.lower()
            for pm in vocab_pricing:
                pm_simple = pm.lower().replace(" pricing", "").replace(" billing", "")
                if pm.lower() in s_lower or (pm_simple in s_lower and any(cue in s_lower for cue in pricing_cues)):
                    add_triple("supportsPricingModel", pm, conf=0.90, ev=sent)

        # 5. hasFeature
        #
        # The cue word is a confidence signal, not a gate. It earned its keep when
        # known_features held 18 generic strings ("Custom Reporting", "Data Export")
        # where a bare match said little. Against a curated vertical taxonomy a match
        # on "Standalone Selling Price Allocation" is strong evidence on its own, and
        # requiring a cue in the same sentence mostly cost recall: real product copy
        # writes "Ordway generates revenue schedules and issues credit notes", which
        # names two features and contains no cue word at all.
        #
        # Uncued matches stay above the 0.75 floor in semantic_seo.py so they survive,
        # but rank below cued ones wherever evidence is weighed.
        #
        # Matching is word-bounded: the vocabulary is long and contains short entries,
        # and substring matching would read "Auto-Pay" out of "auto-payment" and
        # "VAT Support" out of unrelated prose.
        feature_cues = ["feature", "features", "capability", "capabilities", "includes", "offers", "provides", "built-in", "native"]
        for sent in sentences:
            s_lower = sent.lower()
            cued = any(cue in s_lower for cue in feature_cues)
            for canonical, surface_forms in forms_features:
                for form in surface_forms:
                    if re.search(rf'\b{re.escape(form.lower())}\b', s_lower):
                        add_triple("hasFeature", canonical, conf=0.85 if cued else 0.78, ev=sent)
                        break

        # 6. replacesWorkflow
        replace_cues = ["replace", "replaces", "eliminate", "eliminates", "no more", "ditch", "stop using", "get rid of", "instead of", "without"]
        for sent in sentences:
            s_lower = sent.lower()
            if any(cue in s_lower for cue in replace_cues):
                for wf in vocab_replaces:
                    if wf.lower() in s_lower or any(w in s_lower for w in ["spreadsheet", "excel", "manual", "journal entr"]):
                        add_triple("replacesWorkflow", wf, conf=0.85, ev=sent)
            # Catch implicit manual workflow elimination
            if any(c in s_lower for c in ["manual", "spreadsheet", "excel"]) and any(c in s_lower for c in ["eliminate", "automate", "replace", "no longer"]):
                add_triple("replacesWorkflow", "Manual Processes", conf=0.80, ev=sent)

        # 7. targetsSegment
        segment_cues = ["for", "built for", "designed for", "serving", "ideal for", "tailored for", "made for", "focused on"]
        for sent in sentences:
            s_lower = sent.lower()
            for seg in vocab_segments:
                if seg.lower() in s_lower and any(cue in s_lower for cue in segment_cues):
                    add_triple("targetsSegment", seg, conf=0.85, ev=sent)

        # 8. servesIndustry
        industry_cues = ["industry", "sector", "vertical", "market", "serving", "for companies", "for businesses"]
        for sent in sentences:
            s_lower = sent.lower()
            for ind in vocab_industries:
                if ind.lower() in s_lower:
                    add_triple("servesIndustry", ind, conf=0.85, ev=sent)

        # 9. deployedAs
        deploy_cues = ["deployed", "deployment", "hosted", "available as", "delivered as", "runs on", "infrastructure"]
        for sent in sentences:
            s_lower = sent.lower()
            if any(cue in s_lower for cue in deploy_cues):
                for dep in vocab_deployment:
                    if dep.lower() in s_lower:
                        add_triple("deployedAs", dep, conf=0.85, ev=sent)
            # Cloud-native often appears without explicit deploy cues
            for dep in ["Cloud-Native", "SaaS", "Multi-Tenant"]:
                if dep.lower() in s_lower:
                    add_triple("deployedAs", dep, conf=0.80, ev=sent)

        # 10. certifiedBy
        cert_cues = ["certified", "certification", "compliant", "compliance", "audited", "attested", "accredited"]
        for sent in sentences:
            s_lower = sent.lower()
            for cert in vocab_certifications:
                if re.search(rf'\b{re.escape(cert.lower())}\b', s_lower):
                    add_triple("certifiedBy", cert, conf=0.92, ev=sent)

        # 11. hasAPI
        api_cues = ["api", "integration", "connect", "developer", "sdk", "webhook", "endpoint"]
        for sent in sentences:
            s_lower = sent.lower()
            if any(cue in s_lower for cue in api_cues):
                for api in vocab_api_types:
                    if api.lower() in s_lower:
                        add_triple("hasAPI", api, conf=0.88, ev=sent)

        # 12. supportsLocale
        locale_cues = ["available in", "supports", "operates in", "headquartered", "global", "international", "localized"]
        for sent in sentences:
            s_lower = sent.lower()
            if any(cue in s_lower for cue in locale_cues):
                for loc in vocab_locales:
                    if loc.lower() in s_lower:
                        add_triple("supportsLocale", loc, conf=0.82, ev=sent)

        # 13. guarantees
        sla_cues = ["uptime", "sla", "guarantee", "reliability", "availability", "support", "downtime"]
        for sent in sentences:
            s_lower = sent.lower()
            if any(cue in s_lower for cue in sla_cues):
                for sla in vocab_sla:
                    if sla.lower() in s_lower:
                        add_triple("guarantees", sla, conf=0.88, ev=sent)
            # Catch percentage uptime patterns
            import re as _re
            uptime_match = _re.search(r'(\d{2,3}\.?\d*\s*%\s*uptime)', s_lower)
            if uptime_match:
                add_triple("guarantees", uptime_match.group(1).title(), conf=0.90, ev=sent)

        # 14. competesAgainst
        compete_cues = ["versus", "vs", "compared to", "unlike", "alternative to", "switch from", "migrate from", "better than", "competitor"]
        for sent in sentences:
            s_lower = sent.lower()
            if any(cue in s_lower for cue in compete_cues):
                for comp in vocab_competitors:
                    if re.search(rf'\b{re.escape(comp.lower())}\b', s_lower):
                        add_triple("competesAgainst", comp, conf=0.80, ev=sent)

        # 15. hasCustomer
        #
        # Customer names are vendor-specific, so vocab_customers is normally empty and
        # this rule contributes nothing; the entity branch below does the work. The rule
        # exists so a vertical CAN pin known customers, and so the predicate has a
        # declared home in ontology_schema.
        customer_cues = ["customer", "customers", "case study", "success story",
                         "testimonial", "trusted by", "used by", "director of", "cfo of"]
        for sent in sentences:
            s_lower = sent.lower()
            if any(cue in s_lower for cue in customer_cues):
                for cust in vocab_customers:
                    if re.search(rf'\b{re.escape(cust.lower())}\b', s_lower):
                        add_triple("hasCustomer", cust, conf=0.85, ev=sent)

        # Being a customer is a ROLE, not a kind of thing, and zero-shot NER only
        # classifies kinds. Asked to label "Paubox" it answers Software Platform,
        # because that is what Paubox is - it is a customer only by virtue of the
        # sentence it appears in. So the role is read from the sentence shape instead:
        # a job title attached to a company, or a named case study. Without this the
        # company falls to whichever entity label sits nearest, and Ordway's own
        # customer was reported as an unverifiable integration partner.
        # The title words are matched case-insensitively via a scoped flag, but the
        # captured company name must stay case-SENSITIVE: capitalisation is the only
        # signal separating a company from ordinary prose after "at".
        _TITLE_AT_COMPANY = re.compile(
            r'(?i:\b(?:director|vp|vice president|head|chief|cfo|ceo|coo|cto|controller|'
            r'manager|founder|owner)\b[^,.\n]{0,44}?\bat\s+)'
            r'([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,3})')
        _NAMED_CASE_STUDY = re.compile(
            r'\b([A-Z][\w&.\-]*(?:\s+[A-Z][\w&.\-]*){0,2})\s+(?i:(?:video\s+)?case study)\b')
        for sent in sentences:
            for pattern, conf in ((_TITLE_AT_COMPANY, 0.86), (_NAMED_CASE_STUDY, 0.82)):
                for match in pattern.finditer(sent):
                    name = match.group(1).strip(" ,.")
                    # The vendor naming itself is not its own customer.
                    if name and name.lower() != subject.strip().lower():
                        add_triple("hasCustomer", name, conf=conf, ev=sent)

        # Augment with extracted entities and seed concepts.
        #
        # GLiNER is zero-shot: it assigns every entity to the closest label it was
        # given, so a term that belongs to one relation is regularly also emitted
        # under a neighbouring one. "mid-market" arrives as a Geographic Market and
        # becomes supportsLocale, while the rule pass has already - correctly - read
        # "Mid-Market" as targetsSegment from an explicit vocabulary match.
        #
        # The rule passes above ran first and matched against curated vocabulary, so
        # where the two disagree the rule is the better answer. An entity-derived
        # triple is therefore dropped when the rule pass already claimed that exact
        # text for a different predicate.
        #
        # Scoped deliberately to entity-derived triples: a term legitimately holding
        # two relations gets both from the rule passes and is untouched here. SOC 2
        # is the case to protect - it is correctly compliesWith AND certifiedBy, and
        # both come from vocabulary rules.
        rule_claimed = {t.object.strip().lower(): t.predicate for t in triples}

        # NER returns whatever the page wrote, so an entity arrives in the page's own
        # register: "Automated Invoicing" and "Role-Based Access Control" rather than
        # the canonical "Invoicing" and "Permissions". Emitting the raw span would put
        # the marketing spelling and the documentation spelling into the graph as two
        # unrelated concepts - exactly the split alt_labels exists to close - so an
        # alternate is mapped back to its canonical before the triple is built.
        # Normalising the label alone is not enough: the canonical may belong to a
        # different bucket than the NER label implied. "Automated Invoicing" reads as a
        # Product Feature, but its canonical "Invoicing" is a process the product runs,
        # so the triple has to become `automates Invoicing` rather than `hasFeature
        # Invoicing`. The predicate travels with the canonical, not with the span.
        _alt_to_canonical = {}
        for _canon, _alts in forms_automation:
            for _alt in _alts:
                _alt_to_canonical[_alt.strip().lower()] = (_canon, "automates")
        for _canon, _alts in forms_features:
            for _alt in _alts:
                _alt_to_canonical[_alt.strip().lower()] = (_canon, "hasFeature")

        def _find_enclosing_sentence(target_text: str) -> str:
            t_low = target_text.strip().lower()
            if not t_low:
                return ""
            for s in sentences:
                if t_low in s.lower():
                    return s
            return ""

        has_custom_int = bool(getattr(self.config, 'known_integrations', None))
        has_custom_compliance = bool(getattr(self.config, 'known_compliance', None))

        def add_entity_triple(pred: str, obj: str, conf: float = 0.85, ev: str = ""):
            canonical = _alt_to_canonical.get(obj.strip().lower())
            if canonical is not None:
                obj, pred = canonical
            claimed_by = rule_claimed.get(obj.strip().lower())
            if claimed_by is not None and claimed_by != pred:
                logger.debug(
                    "Dropping entity triple %s '%s': rule pass already read it as %s.",
                    pred, obj, claimed_by
                )
                return

            # If the vertical explicitly defines a closed vocabulary for integrations or
            # compliance, do not let uncurated zero-shot entities leak cross-domain.
            if pred == "integratesWith" and has_custom_int:
                obj_low = obj.strip().lower()
                if not any(p.lower() in obj_low or obj_low in p.lower() for p in vocab_integrations):
                    return

            if pred == "compliesWith" and has_custom_compliance:
                obj_low = obj.strip().lower()
                if not any(s.lower() in obj_low or obj_low in s.lower() for s in vocab_compliance):
                    return

            add_triple(pred, obj, conf=conf, ev=ev)

        # Open capability & automation induction for unconstrained/domain-agnostic text
        auto_open_re = re.compile(
            r'(?i)\b(?:automates?|automating|streamlines?)\s+([a-zA-Z0-9\-\s]{4,40}?)(?:\s+(?:for|across|in|with|to|and\s+eliminates|\.|\,|$))'
        )
        for sent in sentences:
            s_lower = sent.lower()
            if any(cue in s_lower for cue in ["automate", "automates", "automating", "streamline", "streamlines"]):
                for match in auto_open_re.finditer(sent):
                    cand = match.group(1).strip()
                    words = [w for w in cand.split() if w.lower() not in ["the", "all", "your", "our", "their", "and", "or", "a", "an"]]
                    if 1 <= len(words) <= 4:
                        clean_cap = " ".join(words).title()
                        if len(clean_cap) >= 4 and clean_cap.lower() != subject.lower() and not any(c in clean_cap.lower() for c in ["http", "www", "cookie"]):
                            add_triple("automates", clean_cap, conf=0.80, ev=sent)

        # Open integration partner induction when vertical does not constrain integrations
        has_custom_int = bool(getattr(self.config, 'known_integrations', None))
        if not has_custom_int:
            int_open_re = re.compile(
                r'(?i)\b(?:integrates?\s+with|native\s+integration\s+with|connects?\s+to)\s+([A-Z][\w\.\-]+(?:\s+[A-Z][\w\.\-]+)?(?:\s*,\s*[A-Z][\w\.\-]+(?:\s+[A-Z][\w\.\-]+)?)*(?:\s+and\s+[A-Z][\w\.\-]+(?:\s+[A-Z][\w\.\-]+)?)?)'
            )
            for sent in sentences:
                for match in int_open_re.finditer(sent):
                    cand_span = match.group(1)
                    items = re.split(r',\s*|\s+and\s+', cand_span)
                    for item in items:
                        item_clean = item.strip(" ,.")
                        if len(item_clean) >= 2 and item_clean.lower() not in ["the", "all", "your", "our", "seamlessly", "easily"] and item_clean.lower() != subject.lower():
                            add_triple("integratesWith", item_clean, conf=0.82, ev=sent)

        if entities:
            for ent in entities:
                txt = ent.text.strip()
                ev_sent = _find_enclosing_sentence(txt)
                if ent.label in ["Integration Partner", "Ecosystem Integration", "Healthcare Integration", "HR Integration"]:
                    add_entity_triple("integratesWith", txt, conf=0.88, ev=ev_sent)
                elif ent.label in ["Accounting Standard", "Security Standard", "Compliance Regulation", "Financial Standard", "Medical Standard", "HR Compliance"]:
                    add_entity_triple("compliesWith", txt, conf=0.92, ev=ev_sent)
                elif ent.label in ["Pricing Model", "Pricing Structure", "Billing Model"]:
                    add_entity_triple("supportsPricingModel", txt, conf=0.88, ev=ev_sent)
                elif ent.label in ["Billing Feature", "Automation Workflow"] and any(w in txt.lower() for w in ["automate", "recognition", "invoicing", "dunning", "reconciliation", "workflow", "process", "management", "provisioning", "orchestration", "detection", "monitoring"]):
                    add_entity_triple("automates", txt, conf=0.88, ev=ev_sent)
                elif ent.label == "Automation Workflow":
                    add_entity_triple("automates", txt, conf=0.88, ev=ev_sent)
                elif ent.label in ["Product Feature", "Benefits Administration", "Employee Benefit", "Payroll Service", "Tax Filing", "Tax Filing Service", "Time Tracking Feature", "Time Tracking Solution", "Vulnerability Management", "Threat Intelligence"]:
                    add_entity_triple("hasFeature", txt, conf=0.88, ev=ev_sent)
                elif ent.label == "Legacy Workflow":
                    add_entity_triple("replacesWorkflow", txt, conf=0.85, ev=ev_sent)
                elif ent.label == "Customer Segment":
                    add_entity_triple("targetsSegment", txt, conf=0.88, ev=ev_sent)
                elif ent.label == "Industry Vertical":
                    add_entity_triple("servesIndustry", txt, conf=0.88, ev=ev_sent)
                elif ent.label == "Deployment Model":
                    add_entity_triple("deployedAs", txt, conf=0.88, ev=ev_sent)
                elif ent.label == "Trust Certification":
                    add_entity_triple("certifiedBy", txt, conf=0.92, ev=ev_sent)
                elif ent.label == "API Standard":
                    add_entity_triple("hasAPI", txt, conf=0.88, ev=ev_sent)
                elif ent.label == "Geographic Market":
                    add_entity_triple("supportsLocale", txt, conf=0.82, ev=ev_sent)
                elif ent.label == "SLA Commitment":
                    add_entity_triple("guarantees", txt, conf=0.88, ev=ev_sent)
                elif ent.label == "Competitor":
                    add_entity_triple("competesAgainst", txt, conf=0.80, ev=ev_sent)
                elif ent.label == "Customer":
                    add_entity_triple("hasCustomer", txt, conf=0.85, ev=ev_sent)
                else:
                    from ontology_schema import relation_for_gliner_label
                    rel_spec = relation_for_gliner_label(ent.label)
                    if rel_spec:
                        add_entity_triple(rel_spec.predicate, txt, conf=0.85, ev=ev_sent)

        if seed_concepts:
            for sc in seed_concepts:
                if sc.count > 0:
                    c_name = sc.concept
                    ev_seed = _find_enclosing_sentence(c_name)
                    if c_name in ["ASC 606", "SOC 1", "SOC 2"]:
                        if not has_custom_compliance or any(s.lower() in c_name.lower() or c_name.lower() in s.lower() for s in vocab_compliance):
                            add_triple("compliesWith", c_name, conf=0.90, ev=ev_seed)
                    elif c_name == "ERP Integration":
                        add_triple("integratesWith", "ERP Systems", conf=0.85, ev=ev_seed)
                    elif c_name == "Payment Gateway":
                        add_triple("integratesWith", "Payment Gateways", conf=0.85, ev=ev_seed)
                    elif c_name in ["Revenue Recognition", "Billing Automation", "Accounts Receivable"]:
                        add_triple("automates", c_name, conf=0.88, ev=ev_seed)

        return _resolve_overlapping_matches(triples)

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
            action_plan=action_plan
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
        logger.warning("Could not parse sitemap %s: %s", sitemap_url, e)

    # If top-level sitemap fetch returned no URLs, probe common alternative sitemap paths
    if not urls and _depth == 0:
        parsed = urlparse(sitemap_url)
        if parsed.scheme and parsed.netloc:
            origin = f"{parsed.scheme}://{parsed.netloc}"
            candidate_paths = [
                "/sitemap_index.xml",
                "/wp-sitemap.xml",
                "/page-sitemap.xml",
                "/sitemap.xml",
                "/sitemap-index.xml"
            ]
            for path in candidate_paths:
                candidate_url = f"{origin}{path}"
                if candidate_url.lower() == sitemap_url.lower():
                    continue
                try:
                    alt_urls = await fetch_sitemap_urls(candidate_url, max_urls=max_urls, _depth=1)
                    if alt_urls:
                        logger.info("Discovered active alternative sitemap: %s (%d URLs)", candidate_url, len(alt_urls))
                        return alt_urls[:max_urls]
                except Exception:
                    continue

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
# Grounding below goes through entity_grounding instead, which keeps this dict as its
# fast path and adds live resolution behind it - so the Schema.org output a crawler sees
# is grounded on the same terms as the RDF graph, rather than on 40 entries alone.
WIKIDATA_MAP = WIKIDATA_KB



def build_site_wide_schema_graph(domain: str, triples: List[SemanticTriple], entities: List[str]) -> Dict:
    """
    Builds a unified @graph JSON-LD node representing the organization and software ecosystem,
    enriched with canonical Wikidata sameAs entity reconciliation for search crawlers and LLMs.
    """
    clean_domain = domain.replace("www.", "") if domain else "example.com"
    brand_name = clean_domain.split('.')[0].capitalize()

    # One concurrent batch before the loops start asking; ground_url() itself is pure.
    prefetch_grounding([brand_name] + [t.object for t in triples])

    integrations = []
    for t in triples:
        if t.predicate == "integratesWith":
            item = {"@type": "SoftwareApplication", "name": t.object}
            w_url = ground_url(t.object)
            if w_url:
                item["sameAs"] = w_url
            integrations.append(item)

    capabilities = [t.object for t in triples if t.predicate == "automates"]
    pricing_models = [t.object for t in triples if t.predicate == "supportsPricingModel"]

    compliance = []
    for t in triples:
        if t.predicate == "compliesWith":
            item = {"@type": "DefinedTerm", "name": t.object, "termCode": t.object}
            w_url = ground_url(t.object)
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
    brand_url = ground_url(brand_name)
    if brand_url:
        org_node["sameAs"] = [brand_url]

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
                # The full exception goes to the server log; the response carries only
                # the class name, so internal paths and library internals are not echoed
                # back to an unauthenticated caller.
                logger.warning("Crawl worker failed for %s: %s", url, e, exc_info=True)
                summaries.append(PageCrawlSummary(
                    url=url,
                    status="failed",
                    triples_found=0,
                    entities_found=0,
                    error=type(e).__name__
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


