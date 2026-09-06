# Master Statement of Truth (Product Governance Ledger)
> Inspired by Karpathy's Compounding Truth Ledger & llm-iso27001 Statement of Applicability.
> A version-controlled, auditable index of ground truth across B2B SaaS domains.

---

## 🏛️ Capability & Governance Index

| Entity / Capability | Predicate | Vertical | Grounding Status | Evidence Source | Last Verified |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **ASC 606 Revenue Recognition** | compliesWith | B2B SaaS Fintech | VERIFIED_TRUTH | API: /v1/revenue-schedules, /v1/contracts | 2026-09-06 |
| **SOC 2 Type II** | compliesWith | B2B SaaS Fintech | VERIFIED_TRUTH | Security Scheme: Bearer JWT + TLS 1.3 | 2026-09-06 |
| **Usage-Based / Consumption Metering** | supportsPricingModel | B2B SaaS Fintech | VERIFIED_TRUTH | API: /v1/metered-events, /v1/usages | 2026-09-06 |
| **Subscription Billing Automation** | utomates | B2B SaaS Fintech | VERIFIED_TRUTH | API: /v1/subscriptions, /v1/plans | 2026-09-06 |
| **NetSuite ERP Two-Way Sync** | integratesWith | B2B SaaS Fintech | VERIFIED_TRUTH | Docs: Zendesk Article NetSuite External ID Mapping | 2026-09-06 |
| **Salesforce CRM Integration** | integratesWith | B2B SaaS Fintech | VERIFIED_TRUTH | API: /v1/connectors/salesforce | 2026-09-06 |
| **QuickBooks Online Sync** | integratesWith | B2B SaaS Fintech | VERIFIED_TRUTH | API: /v1/connectors/quickbooks | 2026-09-06 |
| **Stripe Payment Gateway** | integratesWith | B2B SaaS Fintech | VERIFIED_TRUTH | API: /v1/payment-gateways/stripe | 2026-09-06 |
| **Sub-Second Real-Time Rating** | utomates | B2B SaaS Fintech | MARKETING_DRIFT_FLUFF | Marketing claim; OpenAPI ingestion is asynchronous queue | 2026-09-06 |
| **HIPAA Compliance** | compliesWith | B2B SaaS Fintech | UNBACKED_MARKETING_CLAIM | No BAA or ePHI retention policy in technical surface | 2026-09-06 |
| **Multi-Currency FX Revaluation** | utomates | B2B SaaS Fintech | UNMARKETED_CAPABILITY | API endpoint /v1/fx-revaluations present but missing from copy | 2026-09-06 |

---

## 🏷️ Grounding Status Definitions

- **VERIFIED_TRUTH**: Marketing claim is rigorously backed by production OpenAPI endpoints and/or technical documentation.
- **MARKETING_DRIFT_FLUFF**: Marketing claims capabilities or speed SLAs that conflict with actual API architecture.
- **UNBACKED_MARKETING_CLAIM**: Marketing claims a regulatory framework, partner connector, or pricing model with zero technical evidence.
- **UNMARKETED_CAPABILITY**: Production engineering endpoints exist in OpenAPI/code but are omitted from marketing landing pages.
