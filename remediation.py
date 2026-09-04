import json
import re
import asyncio
import concurrent.futures
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, quote
import httpx

# In-memory cache for resolved Wikidata entities
_wikidata_cache: Dict[str, Optional[dict]] = {}

EXCLUDED_DESCRIPTIONS = [
    "scholarly article",
    "academic paper",
    "town in",
    "census-designated place",
    "railway station",
]

PREFERRED_KEYWORDS = [
    "software",
    "business",
    "economics",
    "economic",
    "finance",
    "financial",
    "accounting",
    "pricing",
    "price",
]


async def resolve_wikidata(query: str) -> Optional[dict]:
    """
    Asynchronously queries the public Wikidata API with in-memory caching to resolve
    an entity or concept to its canonical Wikidata ID and URI.
    
    Prevents noisy or irrelevant entity resolutions:
    1. Retrieves limit=3 candidates from wbsearchentities.
    2. Filters out entities whose description contains excluded noisy patterns
       ('scholarly article', 'academic paper', 'town in', 'census-designated place', 'railway station').
    3. Prefers entities whose description references 'software', 'business', 'economics', 'finance', 'accounting', or 'pricing'.
    4. If no candidate matches or if the match is ambiguous, skips adding that specific sameAs link.
    """
    if not query or len(query.strip()) < 2:
        return None

    cleaned = query.strip()
    cache_key = cleaned.lower()
    if cache_key in _wikidata_cache:
        return _wikidata_cache[cache_key]

    encoded = quote(cleaned)
    url = f"https://www.wikidata.org/w/api.php?action=wbsearchentities&format=json&language=en&limit=3&search={encoded}"
    headers = {
        "User-Agent": "OntologyIntelligenceBot/1.0 (https://github.com/gainARK/ontology; contact@gainark.com)"
    }

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                search = data.get("search", [])

                # 1. Filter out excluded descriptions and candidates without an ID
                valid_candidates = []
                for item in search:
                    qid = item.get("id")
                    if not qid:
                        continue
                    desc = (item.get("description") or "").lower()
                    if any(ex in desc for ex in EXCLUDED_DESCRIPTIONS):
                        continue
                    valid_candidates.append(item)

                if not valid_candidates:
                    _wikidata_cache[cache_key] = None
                    return None

                # 2. Identify candidates whose description references preferred domains
                preferred_candidates = []
                for item in valid_candidates:
                    desc = (item.get("description") or "").lower()
                    if any(pk in desc for pk in PREFERRED_KEYWORDS):
                        preferred_candidates.append(item)

                # 3. Resolve preferred candidate or handle ambiguity
                chosen = None
                q_lower = cleaned.lower()

                if preferred_candidates:
                    if len(preferred_candidates) == 1:
                        chosen = preferred_candidates[0]
                    else:
                        # Multiple preferred candidates: check for exact label match
                        exact = [c for c in preferred_candidates if (c.get("label") or "").strip().lower() == q_lower]
                        if len(exact) == 1:
                            chosen = exact[0]
                        elif len(exact) > 1:
                            # Primary concept ranked highest by Wikidata
                            chosen = exact[0]
                        else:
                            # Ambiguous: multiple candidates in domain but none have exact label
                            _wikidata_cache[cache_key] = None
                            return None
                else:
                    # No candidate matches preferred domain descriptions -> skip to avoid incorrect QIDs
                    _wikidata_cache[cache_key] = None
                    return None

                if chosen:
                    qid = chosen.get("id")
                    res = {
                        "id": qid,
                        "name": chosen.get("label") or cleaned,
                        "sameAs": f"https://www.wikidata.org/wiki/{qid}",
                        "description": chosen.get("description", ""),
                        "concepturi": chosen.get("concepturi") or f"http://www.wikidata.org/entity/{qid}"
                    }
                    _wikidata_cache[cache_key] = res
                    return res
    except Exception:
        pass

    _wikidata_cache[cache_key] = None
    return None


# Backward-compatible alias
resolve_wikidata_entity = resolve_wikidata


def resolve_wikidata_batch(entity_names: List[str]) -> List[Dict[str, Any]]:
    """
    Batch resolves multiple entity names concurrently using resolve_wikidata.
    Safely executes whether called in a synchronous thread or within an existing event loop.
    """
    async def _runner():
        tasks = [resolve_wikidata(name) for name in entity_names]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        resolved = []
        seen_ids = set()
        for r in results:
            if isinstance(r, dict) and r.get("id") and r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                resolved.append(r)
        return resolved

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            future = executor.submit(lambda: asyncio.run(_runner()))
            return future.result()
    else:
        return asyncio.run(_runner())


def classify_domain_context(
    text: str = "",
    title: str = "",
    meta_desc: str = "",
    headings: Optional[List[str]] = None,
    org_name: str = "Platform"
) -> Dict[str, Any]:
    """
    Intelligently analyzes page text, headings, title, and metadata to classify the
    domain's true product vertical and tailor Schema.org application subcategory,
    target audience, and offer details with zero hardcoded bias.
    """
    combined = f"{title} {meta_desc} {' '.join(headings or [])} {text[:4000]}".lower()

    categories = [
        {
            "id": "fintech_billing",
            "keywords": ["billing", "invoice", "invoicing", "revenue recognition", "asc 606", "accounts receivable", "subscription management", "usage-based pricing", "metered billing", "dunning", "payment gateway", "fintech"],
            "app_category": "BusinessApplication",
            "sub_category": "Subscription Billing & Revenue Management Platform",
            "audience": "Finance leaders, CFOs, RevOps, and billing operations teams",
            "offer_category": "Subscription Billing & Financial Software",
            "feature_defaults": [
                "Automated Invoicing & Recurring Billing",
                "Real-Time Revenue Recognition & Compliance",
                "Usage-Based & Metered Pricing Rating",
                "Automated Accounts Receivable & Dunning",
                "Multi-Currency Payment Processing"
            ],
            "synonym_templates": [
                f"{org_name} Billing Platform",
                "Subscription Management & Invoicing Software",
                "Usage-Based Billing Automation Engine",
                "SaaS Recurring Revenue & AR Platform",
                "Enterprise Revenue Operations Suite"
            ]
        },
        {
            "id": "developer_tools",
            "keywords": ["api", "developer", "sdk", "devops", "observability", "monitoring", "kubernetes", "cloud infrastructure", "ci/cd", "deployment", "telemetry", "git", "code", "latency", "runtime"],
            "app_category": "DeveloperApplication",
            "sub_category": "Developer Tools & Cloud Infrastructure Platform",
            "audience": "Software engineers, DevOps engineers, and technical infrastructure leaders",
            "offer_category": "Developer Tools & Cloud Infrastructure Subscription",
            "feature_defaults": [
                f"{org_name} Developer APIs & Client SDKs",
                "Automated Cloud Deployment & CI/CD Pipelines",
                "Real-Time Telemetry, Metrics & Observability",
                "High-Throughput Scalable Cloud Infrastructure",
                "Enterprise Access Control & Secret Management"
            ],
            "synonym_templates": [
                f"{org_name} Developer Platform",
                f"{org_name} Cloud Infrastructure",
                "Cloud Observability & DevOps Platform",
                "Developer API & Runtime Infrastructure",
                f"{org_name} Engineering Suite"
            ]
        },
        {
            "id": "crm_sales",
            "keywords": ["crm", "sales pipeline", "lead generation", "inbound marketing", "sales engagement", "email campaigns", "deal tracking", "prospecting", "customer relationship", "outreach"],
            "app_category": "BusinessApplication",
            "sub_category": "CRM & Sales Engagement Platform",
            "audience": "Sales leaders, account executives, and Revenue Operations teams",
            "offer_category": "SaaS CRM & Sales Platform",
            "feature_defaults": [
                "Lead & Pipeline Management Automation",
                "Automated Multi-Channel Sales Sequences",
                "Real-Time Deal & Revenue Forecasting",
                "Customer Interaction History & Tracking",
                "Seamless Email & Calendar Integrations"
            ],
            "synonym_templates": [
                f"{org_name} CRM",
                f"{org_name} Sales Platform",
                "Customer Relationship & Pipeline Software",
                "Enterprise Sales Engagement Suite",
                f"{org_name} Sales Cloud"
            ]
        },
        {
            "id": "security_compliance",
            "keywords": ["security", "cybersecurity", "soc 2", "hipaa", "gdpr", "compliance", "zero trust", "identity", "authentication", "sso", "vulnerability", "threat detection", "pentest", "iam"],
            "app_category": "SecurityApplication",
            "sub_category": "Cybersecurity & Continuous Compliance Platform",
            "audience": "Chief Information Security Officers (CISOs), security engineers, and compliance officers",
            "offer_category": "Enterprise Security & Compliance Software",
            "feature_defaults": [
                "Continuous Automated Security Auditing",
                "Automated SOC 2 & ISO 27001 Compliance Tracking",
                "Real-Time Vulnerability Scanning & Alerting",
                "Enterprise Role-Based Access Control (RBAC)",
                "Cryptographic Audit Logs & Evidence Collection"
            ],
            "synonym_templates": [
                f"{org_name} Security Platform",
                f"{org_name} Compliance Automation",
                "Cloud Security & Compliance Suite",
                "Continuous Trust & Compliance Engine",
                f"{org_name} Cybersecurity Suite"
            ]
        },
        {
            "id": "productivity_collaboration",
            "keywords": ["project management", "workspace", "collaboration", "agile", "sprint", "kanban", "task management", "team communication", "wiki", "docs", "workflow", "ticketing"],
            "app_category": "BusinessApplication",
            "sub_category": "Project Management & Team Collaboration Platform",
            "audience": "Product managers, engineering teams, and agile project leaders",
            "offer_category": "Team Collaboration & Workspace Subscription",
            "feature_defaults": [
                "Interactive Sprint & Kanban Board Management",
                "Real-Time Collaborative Docs & Specifications",
                "Custom Automated Task & Workflow Triggers",
                "Team Capacity & Roadmap Planning",
                "Unified Search & Workspace Integrations"
            ],
            "synonym_templates": [
                f"{org_name} Workspace",
                f"{org_name} Project Management",
                "Team Collaboration & Agile Workspace",
                "Workflow Automation & Planning Suite",
                f"{org_name} Productivity Platform"
            ]
        },
        {
            "id": "data_analytics",
            "keywords": ["analytics", "data warehouse", "business intelligence", "sql", "data pipeline", "visualization", "bi dashboard", "data lake", "metrics", "reporting", "etl"],
            "app_category": "BusinessApplication",
            "sub_category": "Data Intelligence & Business Analytics Platform",
            "audience": "Data analysts, BI leaders, and executive decision-makers",
            "offer_category": "Data Analytics & Business Intelligence Software",
            "feature_defaults": [
                "Automated Data Pipeline Ingestion & ETL",
                "Real-Time Interactive BI Dashboards",
                "Custom SQL Querying & Metric Exploration",
                "Automated Scheduled Executive Reports",
                "Role-Based Data Governance & Security"
            ],
            "synonym_templates": [
                f"{org_name} Analytics",
                f"{org_name} Data Platform",
                "Business Intelligence & Reporting Software",
                "Self-Service Data Analytics Engine",
                f"{org_name} Intelligence Suite"
            ]
        },
        {
            "id": "hr_recruiting",
            "keywords": ["hr", "payroll", "recruiting", "hiring", "applicant tracking", "ats", "benefits", "employee onboarding", "workforce management", "people operations"],
            "app_category": "BusinessApplication",
            "sub_category": "HR & Workforce Operations Platform",
            "audience": "People Operations leaders, HR executives, and talent acquisition teams",
            "offer_category": "Enterprise HR & Payroll Software",
            "feature_defaults": [
                "Automated Global Payroll & Tax Compliance",
                "Applicant Tracking & Structured Recruiting Pipeline",
                "Self-Service Employee Onboarding & Directory",
                "Benefits Administration & Time-Off Tracking",
                "Workforce Analytics & Retention Reporting"
            ],
            "synonym_templates": [
                f"{org_name} HR Platform",
                f"{org_name} People Operations",
                "Workforce Management & Payroll Software",
                "Enterprise Talent & HR Automation",
                f"{org_name} HR Cloud"
            ]
        },
        {
            "id": "ecommerce",
            "keywords": ["ecommerce", "e-commerce", "storefront", "checkout", "shopping cart", "merchant", "inventory", "pos", "shopify", "orders", "catalog"],
            "app_category": "BusinessApplication",
            "sub_category": "E-Commerce & Merchant Infrastructure Platform",
            "audience": "Online merchants, retail founders, and digital commerce managers",
            "offer_category": "E-Commerce Platform & Merchant Software",
            "feature_defaults": [
                "High-Converting Checkout & Payment Processing",
                "Multi-Channel Inventory & Order Management",
                "Customizable Storefront Design & Catalog Engine",
                "Automated Abandoned Cart Recovery & Marketing",
                "Real-Time Sales & Merchandise Analytics"
            ],
            "synonym_templates": [
                f"{org_name} Commerce Platform",
                f"{org_name} Storefront",
                "Digital Commerce & Merchant Software",
                "Enterprise E-Commerce Engine",
                f"{org_name} Commerce Suite"
            ]
        }
    ]

    best_cat = None
    best_score = 0
    for cat in categories:
        score = sum(combined.count(kw) for kw in cat["keywords"])
        if score > best_score:
            best_score = score
            best_cat = cat

    if best_cat and best_score >= 2:
        return best_cat

    # Fallback to dynamic general B2B SaaS platform tailored to the audited entity
    return {
        "id": "general_b2b_saas",
        "keywords": [],
        "app_category": "BusinessApplication",
        "sub_category": f"{org_name} Cloud Software Platform",
        "audience": f"Enterprise teams and business leaders utilizing {org_name}",
        "offer_category": f"{org_name} SaaS Subscription",
        "feature_defaults": [
            f"{org_name} Core Platform Access",
            "Automated Cloud Workflows & Operations",
            "Role-Based Access Control & User Permissions",
            "Enterprise Security & Data Protection",
            "REST API & Cloud Service Integrations"
        ],
        "synonym_templates": [
            f"{org_name} Platform",
            f"{org_name} Cloud Software",
            f"{org_name} Enterprise Application",
            f"{org_name} Software Solution",
            f"{org_name} Cloud System"
        ]
    }


def synthesize_alternate_names(category_info: Dict[str, Any], org_name: str, features: List[str]) -> List[str]:
    """
    Synthesizes 4-6 high-intent category synonyms derived from the domain's classified vertical
    and detected capabilities to expand search engine ontology comprehension.
    """
    templates = category_info.get("synonym_templates", [])
    candidates = list(templates)

    # Add feature-specific synonyms if available
    for f in features[:3]:
        clean_f = f.strip()
        if len(clean_f) > 3 and not any(clean_f.lower() in c.lower() for c in candidates):
            candidates.append(f"{org_name} {clean_f}")

    seen = set()
    result = []
    for s in candidates:
        if s.lower() not in seen:
            seen.add(s.lower())
            result.append(s)
        if len(result) >= 6:
            break
    return result[:6]


def generate_schema_patch(extraction_result: Any) -> Dict[str, Any]:
    """
    Dynamically generates an advanced Schema.org JSON-LD patch addressing missing
    mandatory schemas (SoftwareApplication, Organization, Offer), mapping detected features to featureList,
    synthesizing alternateName synonyms, applicationSubCategory, and target Audience,
    resolving high-confidence entities to canonical Wikidata URIs (injected into 'about'),
    and connecting provider to verified Organization metadata.
    """
    if hasattr(extraction_result, "model_dump"):
        res = extraction_result.model_dump()
    elif isinstance(extraction_result, dict):
        res = extraction_result
    else:
        res = getattr(extraction_result, "__dict__", {})

    url = res.get("url") or "https://example.com"
    parsed_url = urlparse(url)
    domain = parsed_url.netloc.replace("www.", "") if parsed_url.netloc else "example.com"
    domain_brand = domain.split(".")[0].capitalize()
    scheme = parsed_url.scheme or "https"
    base_url = f"{scheme}://{parsed_url.netloc or 'example.com'}".rstrip("/")

    title = res.get("title") or ""
    meta_desc = res.get("meta_description") or ""
    site_name = res.get("site_name") or ""
    headings = res.get("headings") or []
    extracted_text = res.get("extracted_text_snippet") or ""

    # 1. Connect provider to verified Organization
    verified_org_name = None

    if site_name and len(site_name.strip()) > 1:
        verified_org_name = site_name.strip()

    if not verified_org_name:
        for s in res.get("schema_org", []):
            s_data = s.get("data", {}) if isinstance(s, dict) else getattr(s, "data", {})
            s_type = s.get("schema_type", "") if isinstance(s, dict) else getattr(s, "schema_type", "")
            if "Organization" in s_type and isinstance(s_data, dict):
                verified_org_name = s_data.get("name") or s_data.get("legalName")
                if verified_org_name and len(verified_org_name.strip()) > 1:
                    break

    if not verified_org_name:
        for ent in res.get("entities", []):
            ent_label = ent.get("label") if isinstance(ent, dict) else getattr(ent, "label", "")
            ent_text = ent.get("text") if isinstance(ent, dict) else getattr(ent, "text", "")
            if ent_label == "Software Platform" and len(ent_text.strip()) > 1:
                verified_org_name = ent_text.strip()
                break

    if not verified_org_name and title:
        # Match segment that matches domain brand or pick clean title prefix
        parts = [p.strip() for p in re.split(r"[-|:•—–]", title) if p.strip()]
        for p in parts:
            if domain_brand and p.lower() == domain_brand.lower():
                verified_org_name = p
                break
        if not verified_org_name:
            for p in parts:
                if len(p) > 1 and len(p.split()) <= 3 and not any(w in p.lower() for w in ["home", "welcome", "official", "login", "sign in"]):
                    verified_org_name = p
                    break

    if not verified_org_name:
        verified_org_name = domain_brand or "Platform"

    org_id = f"{base_url}/#organization"
    software_id = f"{base_url}/#software"
    offer_id = f"{base_url}/#offer"

    # 2. Dynamic Category Classification based on page content
    category_info = classify_domain_context(
        text=extracted_text,
        title=title,
        meta_desc=meta_desc,
        headings=headings,
        org_name=verified_org_name
    )

    # 3. Dynamically map detected features to featureList
    feature_list: List[str] = []
    seen_features = set()

    for ent in res.get("entities", []):
        label = ent.get("label") if isinstance(ent, dict) else getattr(ent, "label", "")
        text = ent.get("text") if isinstance(ent, dict) else getattr(ent, "text", "")
        if label in ["Billing Feature", "Pricing Model", "Accounting Standard", "Security Standard"]:
            cleaned = text.strip()
            if cleaned.islower():
                cleaned = cleaned.title()
            if cleaned.lower() not in seen_features and len(cleaned) > 2:
                seen_features.add(cleaned.lower())
                feature_list.append(cleaned)

    for sc in res.get("seed_concepts", []):
        concept = sc.get("concept") if isinstance(sc, dict) else getattr(sc, "concept", "")
        count = sc.get("count", 0) if isinstance(sc, dict) else getattr(sc, "count", 0)
        if count > 0 and concept.lower() not in seen_features:
            seen_features.add(concept.lower())
            feature_list.append(concept)

    # Extract capability phrases from headings
    for h in headings:
        cleaned_h = h.strip()
        if 5 < len(cleaned_h) < 45 and not any(w in cleaned_h.lower() for w in ["pricing", "about us", "contact", "login", "terms"]):
            if cleaned_h.lower() not in seen_features:
                seen_features.add(cleaned_h.lower())
                feature_list.append(cleaned_h)
            if len(feature_list) >= 6:
                break

    # If still fewer than 4 features, use category-tailored defaults
    for default_feat in category_info.get("feature_defaults", []):
        if len(feature_list) >= 6:
            break
        if default_feat.lower() not in seen_features:
            seen_features.add(default_feat.lower())
            feature_list.append(default_feat)

    # 4. Resolve High-Confidence Entities to Wikidata URIs ('about' array)
    candidate_entities = []
    for ent in res.get("entities", []):
        text_val = ent.get("text") if isinstance(ent, dict) else getattr(ent, "text", "")
        score_val = ent.get("score") if isinstance(ent, dict) else getattr(ent, "score", 0.0)
        if score_val >= 0.6 and len(text_val.strip()) > 2:
            candidate_entities.append(text_val.strip())

    for sc in res.get("seed_concepts", []):
        concept = sc.get("concept") if isinstance(sc, dict) else getattr(sc, "concept", "")
        count = sc.get("count", 0) if isinstance(sc, dict) else getattr(sc, "count", 0)
        if count > 0:
            candidate_entities.append(concept)

    unique_candidates = []
    seen_cand = set()
    for c in candidate_entities:
        if c.lower() not in seen_cand:
            seen_cand.add(c.lower())
            unique_candidates.append(c)

    wikidata_matches = resolve_wikidata_batch(unique_candidates[:8])
    about_nodes = [
        {
            "@type": "Thing",
            "name": item["name"],
            "sameAs": item["sameAs"]
        }
        for item in wikidata_matches
    ]

    # 5. Alternate Names & Description
    alternate_names = synthesize_alternate_names(category_info, verified_org_name, feature_list)

    if meta_desc and len(meta_desc) > 25:
        software_description = meta_desc
    else:
        software_description = f"{verified_org_name} is an enterprise cloud-based {category_info['sub_category'].lower()} built for {category_info['audience'].lower()}."

    # 6. Build Schema Nodes
    provider = {
        "@type": "Organization",
        "@id": org_id,
        "name": verified_org_name,
        "url": base_url,
        "description": f"{verified_org_name} — official corporate provider of {category_info['sub_category']}."
    }

    software_node = {
        "@type": "SoftwareApplication",
        "@id": software_id,
        "name": verified_org_name,
        "alternateName": alternate_names,
        "description": software_description,
        "applicationCategory": category_info["app_category"],
        "applicationSubCategory": category_info["sub_category"],
        "operatingSystem": "Cloud-based / Web Browser / REST API",
        "audience": {
            "@type": "Audience",
            "audienceType": category_info["audience"]
        },
        "about": about_nodes,
        "featureList": feature_list,
        "provider": {
            "@id": org_id
        },
        "offers": {
            "@id": offer_id
        }
    }

    # Map extracted relational triples
    triples = res.get("triples", [])
    integrates_with = []
    complies_with = []

    for t in triples:
        pred = t.get("predicate") if isinstance(t, dict) else getattr(t, "predicate", "")
        obj = t.get("object") if isinstance(t, dict) else getattr(t, "object", "")
        if pred == "integratesWith" and obj and obj not in integrates_with:
            integrates_with.append(obj)
        elif pred == "compliesWith" and obj and obj not in complies_with:
            complies_with.append(obj)

    if integrates_with:
        software_node["softwareRequirements"] = [f"{obj} Integration" for obj in integrates_with]
        software_node["isRelatedTo"] = [
            {"@type": "SoftwareApplication", "name": obj}
            for obj in integrates_with
        ]

    if complies_with:
        software_node["knowsAbout"] = complies_with
        for c in complies_with:
            if not any(a.get("name", "").lower() == c.lower() for a in about_nodes):
                about_nodes.append({
                    "@type": "DefinedTerm",
                    "name": c,
                    "termCode": c
                })

    offer_node = {
        "@type": "Offer",
        "@id": offer_id,
        "name": f"{verified_org_name} Commercial Plans",
        "price": "Custom",
        "priceCurrency": "USD",
        "category": category_info["offer_category"],
        "description": f"Commercial subscription tiers and enterprise pricing options for {verified_org_name}.",
        "seller": {
            "@id": org_id
        }
    }

    patch_dict = {
        "@context": "https://schema.org",
        "@graph": [
            provider,
            software_node,
            offer_node
        ]
    }

    json_ld_str = json.dumps(patch_dict, indent=2)

    return {
        "patch_dict": patch_dict,
        "json_ld": json_ld_str,
        "schemas_added": ["SoftwareApplication", "Organization", "Offer"],
        "features_mapped": feature_list,
        "resolved_provider": provider,
        "about_entities": about_nodes,
        "alternate_names": alternate_names,
        "audience": category_info["audience"],
        "sub_category": category_info["sub_category"],
        "integrates_with": integrates_with,
        "complies_with": complies_with
    }
