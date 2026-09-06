"""
GainARK OntoLeap — Company Product Truth & Dual Ingestion Engine
Part of the Tri-Ontology Marketing Governance Platform (Step 2).

Cross-examines Marketing Claims against Technical Reality:
1. Ingests Marketing Websites (claims, promises, positioning)
2. Ingests Technical Docs / OpenAPI Specs (actual endpoints, schemas, integrations)
3. Computes the Product Truth Matrix:
   - Verified Claims (proven in both)
   - Marketing Drift & Fluff (unbacked marketing claims)
   - Hidden Capabilities (unmarketed engineering gold)
4. Calculates the Marketing Grounding Index (MGI 0-100) and governance alerts.
"""

import os
import re
import json
import logging
from urllib.parse import urlparse
from typing import Dict, Any, List, Optional, Set, Tuple
import requests
from bs4 import BeautifulSoup

from models import (
    SemanticTriple,
    ProductTruthRequest,
    ProductTruthMatrixResponse
)
from pipeline import OntologyPipeline, validate_url_for_fetch
from scraper import smart_fetch
from constants import KNOWN_AUTOMATION, KNOWN_COMPLIANCE, KNOWN_PRICING, KNOWN_INTEGRATIONS

logger = logging.getLogger("gainark.product_truth")


def _normalize_concept(text: str) -> str:
    """Normalize concept string for semantic alignment."""
    clean = re.sub(r'[^a-zA-Z0-9\s]', '', text.lower())
    return " ".join(clean.split())


def _concepts_match(c1: str, c2: str) -> bool:
    """Check if two concepts match either exactly or via significant substring/stem."""
    n1 = _normalize_concept(c1)
    n2 = _normalize_concept(c2)
    if not n1 or not n2:
        return False
    if n1 == n2:
        return True
    if n1 in n2 or n2 in n1:
        return True
    tokens1 = set(n1.split())
    tokens2 = set(n2.split())
    common = tokens1.intersection(tokens2)
    return len(common) >= 1 and any(len(t) > 3 for t in common)


def parse_openapi_spec(
    spec: Dict[str, Any],
    brand_name: str = "The Platform",
    source_origin: str = "openapi.json"
) -> List[SemanticTriple]:
    """
    Parses an OpenAPI 3.x or Swagger 2.0 JSON specification into technical ground-truth triples.
    Extracts:
    - automates: derived from paths, tags, operationId, and summaries
    - integratesWith: derived from tags and endpoint parameters referencing third-party platforms
    - compliesWith: derived from securitySchemes (OAuth2, OpenID, APIKey) and compliance tags
    """
    triples: List[SemanticTriple] = []
    seen: Set[Tuple[str, str, str]] = set()

    def _add_triple(pred: str, obj: str, confidence: float, evidence: str, prov: str):
        key = (brand_name.lower(), pred.lower(), obj.lower())
        if key not in seen and len(obj.strip()) > 1:
            seen.add(key)
            triples.append(SemanticTriple(
                subject=brand_name,
                predicate=pred,
                object=obj.strip(),
                confidence=confidence,
                evidence_sentence=evidence,
                source_type="technical_truth",
                provenance=prov
            ))

    # 1. Inspect Security Schemes (compliesWith)
    components = spec.get("components", {})
    sec_schemes = components.get("securitySchemes", {}) or spec.get("securityDefinitions", {})
    for s_name, s_def in sec_schemes.items():
        s_type = s_def.get("type", "").lower()
        if s_type == "oauth2":
            _add_triple("compliesWith", "OAuth 2.0", 0.98, f"Configured OAuth2 security scheme '{s_name}'", f"{source_origin}#/security/{s_name}")
        elif s_type == "http" and s_def.get("scheme", "").lower() == "bearer":
            _add_triple("compliesWith", "Bearer Token Authentication", 0.95, f"Bearer token scheme '{s_name}'", f"{source_origin}#/security/{s_name}")
        elif s_type == "apikey":
            _add_triple("compliesWith", "API Key Authentication", 0.95, f"API Key in {s_def.get('in', 'header')}", f"{source_origin}#/security/{s_name}")
        elif s_type == "openidconnect":
            _add_triple("compliesWith", "OpenID Connect", 0.98, f"OpenID Connect scheme '{s_name}'", f"{source_origin}#/security/{s_name}")

    # 2. Inspect Paths & Operations (automates & integratesWith)
    paths = spec.get("paths", {})
    known_integrations_detect = [
        "salesforce", "netsuite", "stripe", "quickbooks", "workday", "hubspot",
        "slack", "github", "gitlab", "jira", "aws", "azure", "google cloud",
        "datadog", "splunk", "snowflake", "xero", "twilio", "zendesk"
    ]

    for path_str, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue

        for method in ["get", "post", "put", "patch", "delete"]:
            op = path_item.get(method)
            if not isinstance(op, dict):
                continue

            summary = op.get("summary", "")
            desc = op.get("description", "")
            tags = op.get("tags", [])
            op_id = op.get("operationId", "")

            combined_text = f"{path_str} {summary} {desc} {' '.join(tags)} {op_id}".lower()

            # Capability extraction from tags
            for tag in tags:
                clean_tag = tag.replace("-", " ").replace("_", " ").title()
                if len(clean_tag) > 3 and clean_tag.lower() not in ["api", "default", "v1", "v2"]:
                    _add_triple(
                        "automates",
                        clean_tag,
                        0.92,
                        f"Endpoint {method.upper()} {path_str} implements capability: {summary or clean_tag}",
                        f"{source_origin}#{method.upper()}{path_str}"
                    )

            # Detect ecosystem integrations
            for ki in known_integrations_detect:
                if ki in combined_text:
                    _add_triple(
                        "integratesWith",
                        ki.title(),
                        0.94,
                        f"Endpoint {method.upper()} {path_str} references integration with {ki.title()}",
                        f"{source_origin}#{method.upper()}{path_str}"
                    )

            # Scan operation summary & description for capabilities, compliance, and pricing models
            text_to_scan = f"{summary} {desc}".strip()
            if text_to_scan:
                for item in KNOWN_AUTOMATION:
                    if item.lower() in text_to_scan.lower():
                        _add_triple("automates", item, 0.92, f"Endpoint {method.upper()} {path_str}: {summary or item}", f"{source_origin}#{method.upper()}{path_str}")
                for std in KNOWN_COMPLIANCE:
                    if re.search(rf'\b{re.escape(std.lower())}\b', text_to_scan.lower()):
                        _add_triple("compliesWith", std, 0.96, f"Endpoint {method.upper()} {path_str}: {summary or std}", f"{source_origin}#{method.upper()}{path_str}")
                for pm in KNOWN_PRICING:
                    pm_simple = pm.lower().replace(" pricing", "").replace(" billing", "")
                    if pm.lower() in text_to_scan.lower() or pm_simple in text_to_scan.lower():
                        _add_triple("supportsPricingModel", pm, 0.90, f"Endpoint {method.upper()} {path_str}: {summary or pm}", f"{source_origin}#{method.upper()}{path_str}")

    # 3. Inspect global tags
    for tag_obj in spec.get("tags", []):
        if isinstance(tag_obj, dict):
            t_name = tag_obj.get("name", "")
            t_desc = tag_obj.get("description", "")
            if t_name and len(t_name) > 3:
                _add_triple("automates", t_name.title(), 0.90, f"API Tag: {t_name} - {t_desc}", f"{source_origin}#/tags/{t_name}")

    return triples


def fetch_docs_content(url: str, timeout: float = 15.0) -> Tuple[str, str]:
    """
    Fetches documentation HTML or JSON using the Smart Scraper and returns (raw_content, clean_text).
    """
    raw = smart_fetch(url, timeout=int(timeout))

    # Check if raw is JSON (e.g. openapi.json)
    try:
        json.loads(raw)
        return raw, raw
    except Exception:
        pass

    soup = BeautifulSoup(raw, "html.parser")
    for el in soup(["script", "style", "nav", "footer", "noscript"]):
        el.decompose()

    clean_text = soup.get_text(separator=" ", strip=True)
    return raw, clean_text


def extract_technical_triples_from_text(
    text: str,
    pipeline: OntologyPipeline,
    brand_name: str,
    provenance: str = "technical_docs"
) -> List[SemanticTriple]:
    """
    Extracts semantic triples from raw technical text or documentation using the OntologyPipeline,
    tagging each triple as technical_truth.
    """
    res = pipeline.process(text=text)
    raw_triples = res.triples
    tech_triples: List[SemanticTriple] = []

    for t in raw_triples:
        tech_triples.append(SemanticTriple(
            subject=brand_name,
            predicate=t.predicate,
            object=t.object,
            confidence=round(min(1.0, t.confidence * 1.05), 2),
            evidence_sentence=t.evidence_sentence,
            source_type="technical_truth",
            provenance=provenance
        ))

    return tech_triples


def build_product_truth_matrix(
    marketing_triples: List[SemanticTriple],
    technical_triples: List[SemanticTriple],
    brand_name: str,
    marketing_url: str,
    tech_docs_url: Optional[str] = None
) -> ProductTruthMatrixResponse:
    """
    Computes the differential truth matrix comparing Marketing Claims against Technical Truth.
    Partitions into:
    1. Verified Claims (marketing backed by technical documentation)
    2. Unbacked Marketing Claims (marketing fluff, hallucination risk, product drift)
    3. Hidden Capabilities (real capabilities in docs/API omitted from marketing copy)
    """
    verified: List[SemanticTriple] = []
    unbacked: List[SemanticTriple] = []
    hidden: List[SemanticTriple] = []

    matched_tech_indices: Set[int] = set()

    # Step 1: Evaluate each marketing claim against technical triples
    for m_triple in marketing_triples:
        match_found = False
        for idx, t_triple in enumerate(technical_triples):
            # Check predicate match and object concept match
            if m_triple.predicate.lower() == t_triple.predicate.lower():
                if _concepts_match(m_triple.object, t_triple.object):
                    match_found = True
                    matched_tech_indices.add(idx)
                    # Verified triple combines marketing claim with technical evidence
                    verified.append(SemanticTriple(
                        subject=brand_name,
                        predicate=m_triple.predicate,
                        object=m_triple.object,
                        confidence=max(m_triple.confidence, t_triple.confidence),
                        evidence_sentence=f"[Marketing]: {m_triple.evidence_sentence or m_triple.object} | [Tech Reality]: {t_triple.evidence_sentence or t_triple.object}",
                        source_type="verified_truth",
                        provenance=f"{m_triple.provenance or 'marketing'} ⟷ {t_triple.provenance or 'tech_docs'}"
                    ))
                    break

        if not match_found:
            unbacked.append(SemanticTriple(
                subject=brand_name,
                predicate=m_triple.predicate,
                object=m_triple.object,
                confidence=m_triple.confidence,
                evidence_sentence=m_triple.evidence_sentence,
                source_type="unbacked_marketing_claim",
                provenance=m_triple.provenance or marketing_url
            ))

    # Step 2: Any technical triple not matched is a hidden capability
    for idx, t_triple in enumerate(technical_triples):
        if idx not in matched_tech_indices:
            hidden.append(t_triple)

    # Step 3: Compute Marketing Grounding Index (MGI)
    total_marketing = len(marketing_triples)
    total_technical = len(technical_triples)
    verified_count = len(verified)

    if total_marketing > 0:
        mgi = round((verified_count / total_marketing) * 100.0, 1)
    else:
        mgi = 100.0 if total_technical > 0 else 0.0

    # Step 4: Generate Actionable Governance Alerts
    drift_alerts: List[str] = []
    for u in unbacked:
        if u.predicate == "compliesWith":
            drift_alerts.append(f"Regulatory Drift: Marketing claims compliance with '{u.object}', but no corresponding compliance standard or security scheme was verified in technical documentation.")
        elif u.predicate == "integratesWith":
            drift_alerts.append(f"Integration Drift: Marketing claims integration with '{u.object}', but no connector, endpoint, or SDK parameter was detected in the technical documentation.")
        elif u.predicate == "automates":
            drift_alerts.append(f"Capability Drift: Marketing advertises automated '{u.object}', which is absent from verified API methods and documentation.")

    growth_recs: List[str] = []
    for h in hidden[:5]:
        growth_recs.append(f"Unmarketed Feature: Technical surface proves production support for {h.predicate} '{h.object}' ({h.provenance or 'API'}). Create dedicated marketing landing page copy to capture buyer search intent.")

    # Executive Verdict Summary
    if mgi >= 80.0:
        status_text = "High Grounding (Marketing tightly aligned with technical reality)"
    elif mgi >= 50.0:
        status_text = "Moderate Product Drift (Noticeable gap between marketing promises and technical documentation)"
    else:
        status_text = "Severe Marketing Drift (High hallucination risk; critical claims lack technical verification)"

    summary = (
        f"{brand_name} Marketing Grounding Index: {mgi:.1f}/100 ({status_text}). "
        f"Verified {verified_count}/{total_marketing} marketing claims against {total_technical} technical capabilities. "
        f"Flagged {len(unbacked)} unbacked marketing claims and identified {len(hidden)} unmarketed engineering capabilities."
    )

    return ProductTruthMatrixResponse(
        brand_name=brand_name,
        marketing_url=marketing_url,
        tech_docs_url=tech_docs_url,
        marketing_grounding_index=mgi,
        total_marketing_claims=total_marketing,
        total_technical_capabilities=total_technical,
        verified_claims_count=verified_count,
        unbacked_claims_count=len(unbacked),
        hidden_capabilities_count=len(hidden),
        verified_triples=verified,
        unbacked_claims=unbacked,
        hidden_capabilities=hidden,
        drift_alerts=drift_alerts,
        growth_recommendations=growth_recs,
        executive_summary=summary
    )


def execute_product_truth_audit(
    req: ProductTruthRequest,
    pipeline: OntologyPipeline
) -> ProductTruthMatrixResponse:
    """
    Orchestrates the complete dual-ingestion product truth audit:
    1. Crawls and extracts marketing claims from marketing_url
    2. Ingests technical documentation (OpenAPI spec, tech docs URL, or text)
    3. Builds differential matrix and returns governance report
    """
    brand = req.brand_name or req.company_name or (urlparse(req.marketing_url).netloc.replace("www.", "").split(".")[0].capitalize() if req.marketing_url else "Unknown")

    # 1. Marketing Claims Ingestion
    logger.info("Extracting marketing claims from %s for %s...", req.marketing_url, brand)
    marketing_result = pipeline.process(url=req.marketing_url, deep_crawl=True)
    marketing_triples = marketing_result.triples

    # Tag provenance
    for mt in marketing_triples:
        mt.source_type = "marketing_claim"
        mt.provenance = req.marketing_url

    # 2. Technical Reality Ingestion
    technical_triples: List[SemanticTriple] = []

    # Priority A: Raw OpenAPI spec dict provided
    if req.openapi_spec:
        logger.info("Parsing provided OpenAPI specification for %s...", brand)
        parsed_spec_triples = parse_openapi_spec(req.openapi_spec, brand_name=brand, source_origin="uploaded_spec.json")
        technical_triples.extend(parsed_spec_triples)

    # Priority B: Tech Docs URL provided
    if req.tech_docs_url:
        logger.info("Fetching and analyzing technical documentation at %s...", req.tech_docs_url)
        try:
            raw_docs, clean_docs = fetch_docs_content(req.tech_docs_url)
            # Check if it was an OpenAPI / Swagger JSON endpoint
            try:
                json_data = json.loads(raw_docs)
                if isinstance(json_data, dict) and ("paths" in json_data or "swagger" in json_data):
                    logger.info("Detected OpenAPI/Swagger JSON at %s", req.tech_docs_url)
                    parsed_spec_triples = parse_openapi_spec(json_data, brand_name=brand, source_origin=req.tech_docs_url)
                    technical_triples.extend(parsed_spec_triples)
                else:
                    t_from_text = extract_technical_triples_from_text(clean_docs, pipeline, brand, req.tech_docs_url)
                    technical_triples.extend(t_from_text)
            except (json.JSONDecodeError, ValueError):
                # HTML technical documentation or support portal page
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
                doc_texts = [clean_docs]
                try:
                    soup = BeautifulSoup(raw_docs, "html.parser")
                    sub_links = []
                    parsed_base = urlparse(req.tech_docs_url)
                    base_origin = f"{parsed_base.scheme}://{parsed_base.netloc}"
                    for a in soup.find_all("a", href=True):
                        href = a["href"].strip()
                        if any(pattern in href.lower() for pattern in ["/article", "/hc/", "/guide", "/docs/", "/api/", "/integration", "/billing", "/revenue", "/invoic"]):
                            if href.startswith("/"):
                                full_url = base_origin + href
                            elif href.startswith("http"):
                                full_url = href
                            else:
                                continue
                            if urlparse(full_url).netloc == parsed_base.netloc and full_url != req.tech_docs_url:
                                if full_url not in sub_links and not any(skip in full_url.lower() for skip in ["signin", "signup", "login", "auth", "search"]):
                                    sub_links.append(full_url)
                    # Fetch top documentation sub-articles
                    for sub_url in sub_links[:6]:
                        try:
                            s_text = smart_fetch(sub_url, timeout=8)
                            s_soup = BeautifulSoup(s_text, "html.parser")
                            for el in s_soup(["script", "style", "nav", "footer", "noscript"]):
                                el.decompose()
                            sub_txt = s_soup.get_text(separator=" ", strip=True)
                            if len(sub_txt) > 50:
                                doc_texts.append(sub_txt)
                        except Exception:
                            pass
                except Exception as sub_e:
                    logger.debug("Subpage discovery error on docs: %s", sub_e)

                combined_doc_text = " \n".join(doc_texts)
                t_from_text = extract_technical_triples_from_text(combined_doc_text, pipeline, brand, req.tech_docs_url)
                technical_triples.extend(t_from_text)
        except Exception as e:
            logger.warning("Failed to ingest tech_docs_url %s: %s", req.tech_docs_url, e)
            doc_warning = f"Documentation Fetch Notice: Could not access tech docs at '{req.tech_docs_url}' ({e}). If the portal is behind Cloudflare or authentication, please paste the OpenAPI specification or documentation text into the Technical Truth Surface."
        else:
            doc_warning = None
    else:
        doc_warning = None

    # Priority C: Raw markdown / documentation text provided
    if req.tech_docs_text:
        logger.info("Extracting technical triples from raw documentation text...")
        t_from_text = extract_technical_triples_from_text(req.tech_docs_text, pipeline, brand, "uploaded_docs_text")
        technical_triples.extend(t_from_text)

    # Deduplicate technical triples by (predicate, normalized object)
    dedup_tech: List[SemanticTriple] = []
    seen_tech = set()
    for tt in technical_triples:
        k = (tt.predicate.lower(), _normalize_concept(tt.object))
        if k not in seen_tech:
            seen_tech.add(k)
            dedup_tech.append(tt)
    technical_triples = dedup_tech

    # 3. Build Truth Matrix
    matrix = build_product_truth_matrix(
        marketing_triples=marketing_triples,
        technical_triples=technical_triples,
        brand_name=brand,
        marketing_url=req.marketing_url,
        tech_docs_url=req.tech_docs_url
    )
    if doc_warning:
        matrix.drift_alerts.insert(0, doc_warning)
    return matrix
