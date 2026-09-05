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
# Sub-paths that are high-value for product ontology signals (deep crawl)
# ---------------------------------------------------------------------------

DEEP_CRAWL_PATHS: List[str] = [
    "/pricing", "/features", "/product", "/integrations", "/solutions", "/platform",
]

DEEP_CRAWL_MAX: int = 3


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
