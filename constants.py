"""
GainARK OntoLeap — Shared Constants & Knowledge Base

Single source of truth for all static knowledge bases, entity lists, and
canonical Wikidata Q-ID mappings used across the entire platform.

Importing from here (instead of defining locally) ensures that updates
propagate automatically to all modules with no risk of drift.
"""

from typing import Dict, List

# ---------------------------------------------------------------------------
# Canonical Wikidata Q-ID Knowledge Base
# ---------------------------------------------------------------------------
# Maps lowercase entity/concept names to their canonical Wikidata URIs.
# Used for schema:sameAs grounding across Schema.org patches, RDF exports,
# OWL ontologies, and Knowledge Graph Link Prediction.
#
# Verification source: https://www.wikidata.org/wiki/<QID>
# ---------------------------------------------------------------------------

WIKIDATA_KB: Dict[str, str] = {
    # -------------------------------------------------------------------------
    # Compliance, Accounting & Regulatory Standards
    # -------------------------------------------------------------------------
    "asc 606":          "https://www.wikidata.org/wiki/Q2819869",
    "ifrs 15":          "https://www.wikidata.org/wiki/Q16996614",
    # SOC 1 and SOC 2 are distinct audit frameworks
    "soc 1":            "https://www.wikidata.org/wiki/Q7548567",   # SSAE 18 / SOC 1
    "soc 2":            "https://www.wikidata.org/wiki/Q105822363",  # SOC 2 Trust Services
    "soc 2 type ii":    "https://www.wikidata.org/wiki/Q105822363",
    "soc 1 type ii":    "https://www.wikidata.org/wiki/Q7548567",
    "gaap":             "https://www.wikidata.org/wiki/Q478440",
    "us gaap":          "https://www.wikidata.org/wiki/Q478440",
    "gdpr":             "https://www.wikidata.org/wiki/Q11723205",
    "pci-dss":          "https://www.wikidata.org/wiki/Q1051515",
    "iso 27001":        "https://www.wikidata.org/wiki/Q1135272",
    "hipaa":            "https://www.wikidata.org/wiki/Q1586524",
    "ccpa":             "https://www.wikidata.org/wiki/Q55606411",

    # -------------------------------------------------------------------------
    # Software Integrations & Enterprise Ecosystem Partners
    # -------------------------------------------------------------------------
    "salesforce":           "https://www.wikidata.org/wiki/Q760814",
    "netsuite":             "https://www.wikidata.org/wiki/Q1978731",
    "quickbooks":           "https://www.wikidata.org/wiki/Q7271981",
    "stripe":               "https://www.wikidata.org/wiki/Q7624119",
    "workday":              "https://www.wikidata.org/wiki/Q2592881",
    "hubspot":              "https://www.wikidata.org/wiki/Q17055745",
    "sage intacct":         "https://www.wikidata.org/wiki/Q28956947",
    "sage":                 "https://www.wikidata.org/wiki/Q1197415",
    "xero":                 "https://www.wikidata.org/wiki/Q8043818",
    "avalara":              "https://www.wikidata.org/wiki/Q16836798",
    "taxjar":               "https://www.wikidata.org/wiki/Q106726884",
    "sap":                  "https://www.wikidata.org/wiki/Q5528",
    "oracle":               "https://www.wikidata.org/wiki/Q19900",
    "zendesk":              "https://www.wikidata.org/wiki/Q8069151",
    "slack":                "https://www.wikidata.org/wiki/Q16202723",
    "plaid":                "https://www.wikidata.org/wiki/Q65069792",
    "snowflake":            "https://www.wikidata.org/wiki/Q104862415",
    "microsoft dynamics":   "https://www.wikidata.org/wiki/Q1050212",

    # -------------------------------------------------------------------------
    # Core B2B SaaS Concepts & Capabilities
    # -------------------------------------------------------------------------
    "revenue recognition":          "https://www.wikidata.org/wiki/Q7318047",
    "accounts receivable":          "https://www.wikidata.org/wiki/Q190766",
    "invoicing":                    "https://www.wikidata.org/wiki/Q185521",
    "usage-based pricing":          "https://www.wikidata.org/wiki/Q1134591",
    "subscription business model":  "https://www.wikidata.org/wiki/Q381373",
    "saas":                         "https://www.wikidata.org/wiki/Q211246",
    "cloud computing":              "https://www.wikidata.org/wiki/Q483639",
    "enterprise resource planning": "https://www.wikidata.org/wiki/Q14620",
    "erp":                          "https://www.wikidata.org/wiki/Q14620",
}


# ---------------------------------------------------------------------------
# B2B SaaS Integration Partners (for rule-based triple extraction)
# ---------------------------------------------------------------------------

KNOWN_INTEGRATIONS: List[str] = [
    "Salesforce", "NetSuite", "QuickBooks", "Stripe", "Workday", "HubSpot",
    "Sage Intacct", "Sage", "Xero", "Avalara", "TaxJar", "SAP", "Oracle",
    "Zendesk", "Slack", "Plaid", "Datadog", "Snowflake", "Microsoft Dynamics",
]


# ---------------------------------------------------------------------------
# Compliance & Regulatory Standards (for rule-based triple extraction)
# ---------------------------------------------------------------------------

KNOWN_COMPLIANCE: List[str] = [
    "ASC 606", "IFRS 15", "SOC 1", "SOC 2", "SOC 2 Type II", "SOC 1 Type II",
    "GAAP", "US GAAP", "GDPR", "PCI-DSS", "ISO 27001", "HIPAA", "CCPA",
]


# ---------------------------------------------------------------------------
# Pricing & Monetization Models (for rule-based triple extraction)
# ---------------------------------------------------------------------------

KNOWN_PRICING: List[str] = [
    "Usage-Based Pricing", "Subscription Pricing", "Consumption-Based Pricing",
    "Transaction Pricing", "Hybrid Pricing", "Tiered Pricing",
    "Per-Seat Pricing", "Recurring Billing", "Dynamic Pricing",
    "Flat-Fee Pricing", "Overage Pricing", "Milestone-Based Billing",
]


# ---------------------------------------------------------------------------
# Automated Workflows & Capabilities (for rule-based triple extraction)
# ---------------------------------------------------------------------------

KNOWN_AUTOMATION: List[str] = [
    "Billing Automation", "Revenue Recognition", "Accounts Receivable",
    "Invoicing", "Payment Collection", "Dunning Automation", "Contract Modifications",
    "Order-to-Revenue Cycle", "Revenue Operations", "Deferred Revenue Schedules",
    "Invoice Calculations", "Subscription Billing",
]



# ---------------------------------------------------------------------------
# Product Features & Capabilities (hasFeature predicate)
# ---------------------------------------------------------------------------

KNOWN_FEATURES: List[str] = [
    "Automated Invoicing", "Revenue Recognition Automation", "Dunning Management",
    "Subscription Management", "Contract Management", "Quote-to-Cash",
    "Real-Time Analytics", "Revenue Forecasting", "Multi-Currency Support",
    "Audit Trail", "Role-Based Access Control", "Single Sign-On",
    "Custom Reporting", "API Access", "Webhook Support",
    "Self-Serve Portal", "Bulk Operations", "Data Export",
]


# ---------------------------------------------------------------------------
# Target Customer Segments (targetsSegment predicate)
# ---------------------------------------------------------------------------

KNOWN_SEGMENTS: List[str] = [
    "Enterprise", "Mid-Market", "SMB", "Startups", "Scale-ups",
    "Public Companies", "Private Companies", "PE-Backed Companies",
    "High-Growth SaaS", "B2B SaaS", "B2C Companies",
    "Finance Teams", "Revenue Operations Teams", "Accounting Teams",
]


# ---------------------------------------------------------------------------
# Industries Served (servesIndustry predicate)
# ---------------------------------------------------------------------------

KNOWN_INDUSTRIES: List[str] = [
    "SaaS", "FinTech", "HealthTech", "EdTech", "MarTech",
    "Insurance", "Financial Services", "Healthcare", "Media",
    "Telecommunications", "E-Commerce", "Professional Services",
    "Manufacturing", "Real Estate", "Energy",
]


# ---------------------------------------------------------------------------
# Deployment Models (deployedAs predicate)
# ---------------------------------------------------------------------------

KNOWN_DEPLOYMENT: List[str] = [
    "Cloud-Native", "SaaS", "On-Premise", "Hybrid Cloud",
    "Multi-Tenant", "Single-Tenant", "Private Cloud",
    "Serverless", "Containerized", "Docker", "Kubernetes",
]


# ---------------------------------------------------------------------------
# Security & Trust Certifications (certifiedBy predicate)
# ---------------------------------------------------------------------------

KNOWN_CERTIFICATIONS: List[str] = [
    "SOC 2 Type II", "SOC 1 Type II", "ISO 27001", "PCI-DSS",
    "HIPAA", "GDPR", "CCPA", "FedRAMP", "CSA STAR",
    "NIST", "SSAE 18", "ISAE 3402",
]


# ---------------------------------------------------------------------------
# API & Integration Standards (hasAPI predicate)
# ---------------------------------------------------------------------------

KNOWN_API_TYPES: List[str] = [
    "REST API", "GraphQL API", "SOAP API", "Webhooks",
    "OpenAPI", "Swagger", "OAuth 2.0", "SAML", "SCIM",
    "EDI", "CSV Import", "SDK",
]


# ---------------------------------------------------------------------------
# Geographic & Language Support (supportsLocale predicate)
# ---------------------------------------------------------------------------

KNOWN_LOCALES: List[str] = [
    "United States", "United Kingdom", "European Union", "Canada",
    "Australia", "India", "Singapore", "Japan", "Germany",
    "France", "Global", "Multi-Language", "Multi-Currency",
]


# ---------------------------------------------------------------------------
# SLA & Reliability Guarantees (guarantees predicate)
# ---------------------------------------------------------------------------

KNOWN_SLA: List[str] = [
    "99.9% Uptime", "99.99% Uptime", "24/7 Support", "99.5% Uptime",
    "SLA-Backed Uptime", "Zero Downtime Deployments",
    "Disaster Recovery", "Data Redundancy", "Real-Time Backup",
]


# ---------------------------------------------------------------------------
# Manual Workflows Replaced (replacesWorkflow predicate)
# ---------------------------------------------------------------------------

KNOWN_REPLACES: List[str] = [
    "Manual Spreadsheets", "Excel-Based Reporting", "Manual Journal Entries",
    "Manual Revenue Calculations", "Manual Invoice Generation",
    "Email-Based Approvals", "Manual Reconciliation",
    "Legacy ERP Workarounds", "Manual Dunning", "Manual Contract Amendments",
]


# ---------------------------------------------------------------------------
# Named Competitors (competesAgainst predicate)
# ---------------------------------------------------------------------------

KNOWN_COMPETITORS: List[str] = [
    "Zuora", "Chargebee", "Recurly", "Maxio", "Stripe Billing",
    "Salesforce Revenue Cloud", "NetSuite Billing", "Sage Intacct",
    "Paddle", "FastSpring", "Chargify", "Aria Systems",
    "SAP Billing", "Oracle Subscription Management",
]

# Named customers are specific to whoever is being audited, so there is no sensible
# vertical-wide default and this stays empty on purpose - the entity extractor supplies
# them from the page. It exists so the schema HAS somewhere to put a customer name.
#
# Without it, a company named in a testimonial had no correct bucket and zero-shot NER
# put it in the nearest one: Ordway's marketing quotes a customer, Paubox, next to a
# sentence about Salesforce, and Paubox was extracted as an integration partner and then
# reported as integration drift because no connector for it exists in the docs. The same
# failure put Chargebee under integrations and SSO under pricing, and the fix each time
# was to give the model the right label rather than to tune a threshold.
KNOWN_CUSTOMERS: List[str] = []

# ---------------------------------------------------------------------------
# Sub-paths that are high-value for product ontology signals (deep crawl)
# ---------------------------------------------------------------------------

DEEP_CRAWL_PATHS: List[str] = [
    "/pricing", "/features", "/feature", "/product", "/products",
    "/integrations", "/integration", "/solutions", "/solution",
    "/platform", "/resources", "/customers", "/use-cases", "/services",
]

DEEP_CRAWL_MAX: int = 5


# Paths whose pages carry evidence about *who buys*, as opposed to what the product does.
# Industry discovery ranks links by these before reading, because the ICP fields
# (segments, industries, competitors, displaced practices) are almost never stated on a
# homepage: they live in case studies, customer stories and comparison pages. Ordered
# most-to-least direct; matching is a substring test against the URL path.
ICP_EVIDENCE_PATHS: List[str] = [
    "/case-stud", "/customer-stor", "/customers", "/success-stor", "/testimonial",
    "/vs-", "/vs/", "/compare", "/comparison", "/alternative", "/competitors",
    "/migrate", "/switch",
    "/industries", "/industry", "/who-we-serve", "/solutions", "/use-cases",
    "/pricing", "/security", "/compliance", "/about",
]


# ---------------------------------------------------------------------------
# Security: Private/reserved IP ranges blocked for SSRF protection
# ---------------------------------------------------------------------------

BLOCKED_IP_PREFIXES: tuple = (
    "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.",
    "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.",
    "172.29.", "172.30.", "172.31.", "192.168.", "127.", "0.", "169.254.",
    "::1", "fc00:", "fd",
)

BLOCKED_HOSTNAMES: tuple = (
    "localhost", "metadata.google.internal", "metadata", "169.254.169.254",
)


# ---------------------------------------------------------------------------
# Vocabulary resolution
# ---------------------------------------------------------------------------
# The lists above are the generic B2B baseline, and they lean towards billing and
# revenue because that is the vertical this platform started in. A vertical config
# that carries its own vocabulary must win, or every site is read through a billing
# lens - a healthcare product being scanned for "Dunning Automation" finds nothing
# and is reported as having no capabilities.

def resolve_vocabulary(config, field: str, default: list) -> list:
    """Return the vertical's vocabulary for `field`, falling back to the global default.

    `config` is a VerticalConfig (or None). Verticals authored before these fields
    existed simply have empty lists and transparently keep the previous behaviour.
    """
    if config is None:
        return default
    values = getattr(config, field, None)
    if values:
        return list(values)
    return default


def resolve_surface_forms(config, field: str, default: list) -> list:
    """Return [(canonical, [surface forms])] for a vocabulary field.

    A concept is written differently depending on who is writing. Marketing pages say
    "Renewal Management"; the product's own documentation says "renewal". Matching only
    the drafted label meant a claim raised from marketing copy could never be verified
    against the docs, so the audit reported drift on capabilities that plainly exist.

    Surface forms come from the vertical's alt_labels map. Every form maps back to one
    canonical label, and callers emit the canonical, so both sides of the audit converge
    on the same string regardless of which register the page was written in.

    Forms are returned longest-first: when "Deferred Revenue Schedules" and "Revenue
    Schedules" both match a sentence, the longer is the more specific reading and the
    caller should stop at the first hit.
    """
    terms = resolve_vocabulary(config, field, default)
    alt_map = (getattr(config, "alt_labels", None) or {}) if config is not None else {}
    resolved = []
    for term in terms:
        forms = {term}
        forms.update(alt_map.get(term, []) or [])
        resolved.append((term, sorted(forms, key=lambda s: (-len(s), s))))
    return resolved


def concept_ancestors(concept: str, hierarchy: dict, _max_depth: int = 12) -> list:
    """Walk a concept up its parent chain and return the broader concepts it belongs under.

    `hierarchy` maps specific -> broader, e.g. {"Prior Authorization": "Revenue Cycle
    Management"}. Returns ancestors nearest-first, excluding the concept itself.

    Depth-capped and cycle-guarded: these hierarchies are generated by an LLM, so a
    loop ("A under B, B under A") is a realistic input and must not hang the request.
    """
    if not hierarchy or not concept:
        return []
    # Case-insensitive lookup without mutating the caller's mapping.
    lookup = {str(k).strip().lower(): v for k, v in hierarchy.items()}
    ancestors = []
    seen = {str(concept).strip().lower()}
    current = str(concept).strip().lower()
    for _ in range(_max_depth):
        parent = lookup.get(current)
        if not parent:
            break
        key = str(parent).strip().lower()
        if key in seen:
            break  # cycle
        seen.add(key)
        ancestors.append(parent)
        current = key
    return ancestors


def covers_concept(target: str, marketed_terms, hierarchy: dict, normalize=None) -> bool:
    """True when `target` is covered, directly or by something narrower that rolls up to it.

    A competitor writing only about Prior Authorization does cover Revenue Cycle
    Management; treating those as unrelated is what makes whitespace report owned
    territory as unclaimed.
    """
    norm = normalize or (lambda s: " ".join(str(s).lower().split()))
    target_n = norm(target)
    if not target_n:
        return True  # nothing to assert about an empty concept
    for term in marketed_terms:
        term_n = norm(term)
        if not term_n:
            continue
        if target_n == term_n or target_n in term_n or term_n in target_n:
            return True
        # The marketed term may be a narrower concept sitting under the target.
        for ancestor in concept_ancestors(term, hierarchy):
            if norm(ancestor) == target_n:
                return True
    return False
