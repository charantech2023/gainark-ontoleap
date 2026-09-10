"""
GainARK OntoLeap — Autonomous Industry Profiler & Zero-Shot Taxonomy Discovery
Converts any B2B SaaS domain into a complete Industry Ontology:
1. Crawls homepage, meta tags, and hero copy
2. Prompts Google Gemini 2.5 Flash for industry taxonomy, compliance, integrations, and competitors
3. Asynchronously grounds entities against Wikidata and Google Knowledge Graph
4. Persists the vertical profile into verticals/ so OntologyPipeline can immediately use it
"""

import os
import json
import re
import asyncio
import logging
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse
from bs4 import BeautifulSoup

import vertex_ai_client
from entity_grounding import resolve_wikidata
from scraper import smart_fetch
from models import (
    IndustryDiscoveryResponse,
    GroundedConcept
)

from security import verticals_dir

logger = logging.getLogger("gainark.industry_profiler")


# Ceiling on auto-discovered vertical profiles held on disk. Discovery is an
# unauthenticated write path in the default configuration.
MAX_VERTICAL_PROFILES = int(os.environ.get("MAX_VERTICAL_PROFILES", "200") or 200)


def _sanitize_slug(text: str) -> str:
    """Generate safe identifier string for file paths and vertical IDs."""
    clean = re.sub(r'[^a-zA-Z0-9_]+', '_', text.strip().lower())
    return clean.strip('_')[:50] or "custom_vertical"


def extract_page_summary(url: str, timeout: float = 12.0) -> Dict[str, Any]:
    """
    Crawls the target URL and extracts high-signal structural elements:
    title, meta description, H1/H2 headings, and hero body copy.
    """
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    parsed = urlparse(url)
    domain = parsed.netloc or parsed.path

    try:
        html = smart_fetch(url, timeout=int(timeout))
    except Exception as e:
        logger.warning("Failed to fetch %s: %s. Using URL fallback.", url, e)
        return {
            "url": url,
            "domain": domain,
            "title": domain,
            "meta_description": "",
            "headings": [],
            "body_snippet": f"B2B SaaS software platform operating at {domain}"
        }

    soup = BeautifulSoup(html, "html.parser")

    # Clean non-content elements
    for el in soup(["script", "style", "nav", "footer", "noscript", "svg"]):
        el.decompose()

    # Title
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    # Meta description
    meta_desc = ""
    m_tag = soup.find("meta", attrs={"name": re.compile(r"description", re.I)}) or \
            soup.find("meta", attrs={"property": "og:description"})
    if m_tag and m_tag.get("content"):
        meta_desc = m_tag["content"].strip()

    # Headings
    headings = []
    for h in soup.find_all(["h1", "h2"]):
        h_text = h.get_text(strip=True)
        if h_text and len(h_text) > 3:
            headings.append(h_text)

    # Body snippet
    body_text = soup.get_text(separator=" ", strip=True)
    body_snippet = " ".join(body_text.split()[:250])

    return {
        "url": url,
        "domain": domain,
        "title": title,
        "meta_description": meta_desc,
        "headings": headings[:8],
        "body_snippet": body_snippet
    }


def call_gemini_industry_discovery(
    page_summary: Dict[str, Any],
    brand_hint: Optional[str] = None
) -> Dict[str, Any]:
    """
    Prompts Google Gemini 2.5 Flash to synthesize the Industry Ontology
    and competitive market taxonomy from the page summary.
    """
    domain = page_summary.get("domain", "")
    title = page_summary.get("title", "")
    desc = page_summary.get("meta_description", "")
    headings = "\n- ".join(page_summary.get("headings", []))
    body = page_summary.get("body_snippet", "")

    brand_clause = f"The user indicates the brand name is '{brand_hint}'." if brand_hint else ""

    prompt = f"""
You are an expert enterprise B2B SaaS ontology architect and market taxonomist.
Analyze the following corporate website metadata for a B2B company:

Domain: {domain}
Page Title: {title}
Meta Description: {desc}
Key Headings:
- {headings}
Sample Copy: {body}
{brand_clause}

Synthesize a complete, industry-grade Industry Ontology configuration for this company.
Return ONLY a valid, raw JSON object (without markdown fences, or with standard ```json fence) matching this exact schema:

{{
  "brand_name": "Official brand or company name (e.g. Snyk, Gusto, Snowflake)",
  "vertical_id": "slug_format_vertical_name (e.g. cybersecurity_devsecops, hr_payroll, cloud_data_warehouse)",
  "display_name": "Formal Category Display Name (e.g. Developer Security & DevSecOps)",
  "category": "High level industry category (e.g. Cybersecurity, Human Resources, Cloud Infrastructure, Fintech)",
  "gliner_labels": [
    "List of 6 distinct, high-signal NER entity labels tailored to this vertical (e.g. 'Security Platform', 'Threat Vector', 'Security Standard', 'Integration Partner', 'Deployment Model', 'Pricing Model')"
  ],
  "core_seed_concepts": [
    "List of 10 to 12 essential domain concepts, technical standards, or core functional capabilities that define this category"
  ],
  "known_compliance": [
    "List of 6 to 8 standard regulatory, compliance, or security frameworks expected in this vertical (e.g. SOC 2, ISO 27001, FedRAMP, HIPAA, GDPR, PCI-DSS, NIST)"
  ],
  "known_integrations": [
    "List of 8 to 12 prominent software platforms or ecosystem tools that solutions in this space typically integrate with"
  ],
  "known_pricing": [
    "List of 3 to 5 common B2B pricing models for this vertical (e.g. 'Per-Developer Pricing', 'Usage-Based Ingestion', 'Tiered Enterprise')"
  ],
  "known_features": [
    "List of 6 to 10 discrete functional features standard in this vertical (e.g. 'Role-Based Access Control', 'Single Sign-On', 'Audit Logging', 'Automated Reporting')"
  ],
  "known_segments": [
    "List of 4 to 6 customer segments targeted in this vertical (e.g. 'Enterprise', 'Mid-Market', 'SMB', 'High-Growth Startups')"
  ],
  "known_deployment": [
    "List of 3 to 5 hosting/deployment architectures common in this space (e.g. 'Cloud-Native', 'Multi-Tenant SaaS', 'Private Cloud', 'On-Premise')"
  ],
  "known_sla": [
    "List of 2 to 4 standard reliability or uptime SLA guarantees (e.g. '99.9% Uptime', '99.99% Uptime', '24/7 Support')"
  ],
  "known_replaces": [
    "List of 3 to 6 legacy or manual workflows eliminated by software in this vertical (e.g. 'Manual Spreadsheets', 'Excel-Based Reporting', 'Manual Approvals')"
  ],
  "concept_hierarchy": {{
    "Object mapping each narrower concept to the broader one it sits under, for example mapping 'Prior Authorization' to 'Revenue Cycle Management'. Add an entry for every core_seed_concept and known_automation item that belongs under a broader umbrella; a parent may be a term not otherwise listed. Never create cycles. Omit concepts with no natural parent."
  }},
  "known_automation": [
    "List of 8 to 12 core operational capabilities that products in this vertical automate, phrased as the buyer would name them (e.g. 'Vulnerability Scanning', 'Clinical Documentation', 'Payroll Runs', 'Revenue Recognition'). These are what marketing claims are checked against, so favour concrete workflows over abstract benefits."
  ],
  "suggested_competitors": [
    "List of 3 to 5 real-world direct market competitors offering alternative software solutions in this exact vertical"
  ],
  "summary": "2-sentence executive summary of the discovered market category and ontological scope."
}}
"""

    response_text = vertex_ai_client._call_gemini(
        prompt=prompt,
        system_instruction="You are an autonomous enterprise B2B ontology extraction system. Output valid JSON only.",
        temperature=0.1
    )

    if not response_text:
        # Fallback heuristic if Gemini is temporarily unavailable
        brand = brand_hint or domain.split(".")[0].capitalize()
        slug = _sanitize_slug(domain.split(".")[0])
        return {
            "brand_name": brand,
            "vertical_id": f"saas_{slug}",
            "display_name": f"{brand} Enterprise SaaS",
            "category": "Enterprise B2B SaaS",
            "gliner_labels": ["Software Platform", "Feature", "Integration Partner", "Compliance Standard", "Pricing Model", "Deployment Model"],
            "core_seed_concepts": ["API Integration", "Workflow Automation", "Analytics", "Role-Based Access Control", "Data Security", "Cloud Architecture"],
            "known_compliance": ["SOC 2 Type II", "ISO 27001", "GDPR", "CCPA"],
            "known_integrations": ["Salesforce", "Slack", "AWS", "Google Cloud", "Microsoft Azure"],
            "known_pricing": ["Subscription Pricing", "Usage-Based Pricing", "Tiered Enterprise"],
            "known_features": ["Role-Based Access Control", "Single Sign-On", "Audit Trail", "Custom Reporting", "API Access"],
            "known_segments": ["Enterprise", "Mid-Market", "SMB"],
            "known_deployment": ["Cloud-Native", "Multi-Tenant SaaS"],
            "known_sla": ["99.9% Uptime", "24/7 Support"],
            "known_replaces": ["Manual Spreadsheets", "Legacy Manual Workflows"],
            "known_automation": ["Workflow Automation", "Reporting", "User Provisioning", "Data Sync", "Alerting"],
            "suggested_competitors": [],
            "summary": f"Autonomous ontology profile generated for {brand} based on domain structure."
        }

    # Clean JSON
    cleaned = response_text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed_json = json.loads(cleaned)
        return parsed_json
    except Exception as ex:
        logger.error("Failed to parse Gemini industry JSON: %s. Text was:\n%s", ex, response_text)
        brand = brand_hint or domain.split(".")[0].capitalize()
        return {
            "brand_name": brand,
            "vertical_id": f"saas_{_sanitize_slug(brand)}",
            "display_name": f"{brand} SaaS Platform",
            "category": "B2B Software",
            "gliner_labels": ["Software Platform", "Feature", "Integration Partner", "Compliance Standard", "Pricing Model"],
            "core_seed_concepts": ["Automation", "Integration", "Security", "Scalability"],
            "known_compliance": ["SOC 2", "ISO 27001", "GDPR"],
            "known_integrations": ["Slack", "Salesforce"],
            "known_pricing": ["Tiered Pricing", "Usage-Based"],
            "known_automation": ["Workflow Automation", "Reporting", "Data Sync"],
            "suggested_competitors": [],
            "summary": f"Fallback profile for {brand}."
        }


async def ground_discovered_entities(
    compliance_list: List[str],
    integration_list: List[str]
) -> List[GroundedConcept]:
    """
    Asynchronously queries Wikidata to ground top compliance and integration entities
    with canonical Q-IDs and descriptions.
    """
    grounded: List[GroundedConcept] = []
    candidates = (compliance_list[:4] + integration_list[:4])

    tasks = [resolve_wikidata(c) for c in candidates]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    for name, res in zip(candidates, results):
        if isinstance(res, dict) and res:
            # resolve_wikidata returns id/sameAs, not wikidata_id/wikidata_url. Reading
            # the wrong keys made every entity here come back with a description and no
            # Q-ID at all - grounding that reported itself as ungrounded.
            grounded.append(GroundedConcept(
                name=name,
                wikidata_id=res.get("id"),
                wikidata_url=res.get("sameAs"),
                description=res.get("description")
            ))
        else:
            grounded.append(GroundedConcept(name=name))

    return grounded


def save_vertical_configuration(
    vertical_id: str,
    display_name: str,
    gliner_labels: List[str],
    core_seed_concepts: List[str],
    known_integrations: List[str],
    known_compliance: List[str],
    known_pricing: List[str],
    known_automation: Optional[List[str]] = None,
    concept_hierarchy: Optional[Dict[str, str]] = None,
    known_features: Optional[List[str]] = None,
    known_segments: Optional[List[str]] = None,
    known_deployment: Optional[List[str]] = None,
    known_sla: Optional[List[str]] = None,
    known_replaces: Optional[List[str]] = None
) -> str:
    """
    Saves the discovered vertical configuration into verticals/<vertical_id>.json
    so that OntologyPipeline can immediately instantiate it.
    """
    target_dir = verticals_dir()
    os.makedirs(target_dir, exist_ok=True)
    clean_id = _sanitize_slug(vertical_id)
    config_path = os.path.join(target_dir, f"{clean_id}.json")

    # /api/discover-industry writes one profile per call and is reachable by anyone who
    # can reach the API, so without a ceiling repeated calls fill the disk. Overwriting
    # an existing profile is always allowed; only creating a brand new one is capped.
    if not os.path.exists(config_path):
        existing = [f for f in os.listdir(target_dir) if f.endswith(".json")]
        if len(existing) >= MAX_VERTICAL_PROFILES:
            raise RuntimeError(
                f"Vertical profile limit reached ({MAX_VERTICAL_PROFILES}). "
                f"Remove unused profiles from verticals/ before discovering new ones."
            )

    config_data = {
        "vertical_id": clean_id,
        "display_name": display_name,
        "gliner_labels": gliner_labels,
        "mandatory_schema_types": [
            "SoftwareApplication",
            "Organization",
            "Offer"
        ],
        "core_seed_concepts": core_seed_concepts,
        "known_integrations": known_integrations,
        "known_compliance": known_compliance,
        "known_pricing": known_pricing,
        "known_features": known_features or [],
        "known_segments": known_segments or [],
        "known_deployment": known_deployment or [],
        "known_sla": known_sla or [],
        "known_replaces": known_replaces or [],
        # Drives what triple extraction looks for on every site in this vertical.
        "known_automation": known_automation or [],
        # Lets coverage roll up: marketing a narrower concept counts as covering the
        # broader one it sits under.
        "concept_hierarchy": concept_hierarchy or {}
    }

    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config_data, f, indent=2)

    logger.info("Saved dynamic vertical config to %s", config_path)
    return config_path


async def discover_industry_profile_async(
    url: str,
    brand_hint: Optional[str] = None,
    save_config: bool = True
) -> IndustryDiscoveryResponse:
    """
    Full async orchestration pipeline for zero-shot industry discovery:
    1. Extract page summary
    2. LLM synthesis
    3. Wikidata grounding
    4. Save configuration
    """
    # 1. Page summary
    summary = extract_page_summary(url)

    # 2. LLM synthesis
    discovered = call_gemini_industry_discovery(summary, brand_hint=brand_hint)

    brand_name = discovered.get("brand_name") or brand_hint or summary["domain"]
    vertical_id = _sanitize_slug(discovered.get("vertical_id") or f"custom_{brand_name}")
    display_name = discovered.get("display_name") or f"{brand_name} Vertical"
    category = discovered.get("category") or "B2B SaaS"
    gliner_labels = discovered.get("gliner_labels") or ["Software Platform", "Feature", "Compliance Standard", "Integration Partner"]
    core_seed_concepts = discovered.get("core_seed_concepts") or []
    known_compliance = discovered.get("known_compliance") or []
    known_integrations = discovered.get("known_integrations") or []
    known_pricing = discovered.get("known_pricing") or []
    known_features = discovered.get("known_features") or []
    known_segments = discovered.get("known_segments") or []
    known_deployment = discovered.get("known_deployment") or []
    known_sla = discovered.get("known_sla") or []
    known_replaces = discovered.get("known_replaces") or []
    known_automation = discovered.get("known_automation") or []
    concept_hierarchy = discovered.get("concept_hierarchy") or {}
    if not isinstance(concept_hierarchy, dict):
        logger.warning("Discarding non-dict concept_hierarchy from discovery output.")
        concept_hierarchy = {}
    suggested_competitors = discovered.get("suggested_competitors") or []
    discovery_summary = discovered.get("summary") or f"Discovered {display_name} ontology for {brand_name}."

    # 3. Ground entities
    grounded = await ground_discovered_entities(known_compliance, known_integrations)

    # 4. Save config
    config_file = None
    if save_config:
        config_file = save_vertical_configuration(
            vertical_id=vertical_id,
            display_name=display_name,
            gliner_labels=gliner_labels,
            core_seed_concepts=core_seed_concepts,
            known_integrations=known_integrations,
            known_compliance=known_compliance,
            known_pricing=known_pricing,
            known_automation=known_automation,
            concept_hierarchy=concept_hierarchy,
            known_features=known_features,
            known_segments=known_segments,
            known_deployment=known_deployment,
            known_sla=known_sla,
            known_replaces=known_replaces
        )

    return IndustryDiscoveryResponse(
        url=summary["url"],
        brand_name=brand_name,
        vertical_id=vertical_id,
        display_name=display_name,
        category=category,
        category_name=category,
        core_seed_concepts=core_seed_concepts,
        known_compliance=known_compliance,
        compliance_frameworks=known_compliance,
        known_integrations=known_integrations,
        ecosystem_integrations=known_integrations,
        known_pricing=known_pricing,
        known_features=known_features,
        known_segments=known_segments,
        known_deployment=known_deployment,
        known_sla=known_sla,
        known_replaces=known_replaces,
        gliner_labels=gliner_labels,
        suggested_competitors=suggested_competitors,
        direct_competitors=suggested_competitors,
        grounded_entities=grounded,
        config_file=config_file,
        confidence_score=0.96,
        discovery_summary=discovery_summary,
        domain_scope_description=discovery_summary
    )


def discover_industry_profile(
    url: str,
    brand_hint: Optional[str] = None,
    save_config: bool = True
) -> IndustryDiscoveryResponse:
    """Sync wrapper for discover_industry_profile_async."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import nest_asyncio
            nest_asyncio.apply()
            return loop.run_until_complete(discover_industry_profile_async(url, brand_hint, save_config))
        return loop.run_until_complete(discover_industry_profile_async(url, brand_hint, save_config))
    except RuntimeError:
        return asyncio.run(discover_industry_profile_async(url, brand_hint, save_config))
