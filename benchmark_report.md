# Ontology Pipeline Benchmark: Competitive Structured Data Readiness Analysis

> **Executive Note on Brand Authority vs. Single-Page Schema Readiness:**
> This audit measures **Single-Page Structured Data & Schema Implementation** (Mandatory Schema 40%, Seed Concept Coverage 30%, Entity Richness 30%) on specific landing pages. High brand citation authority across AI answer engines (such as Stripe) can coexist with low single-page structured data scores when individual product landing pages omit static `SoftwareApplication`, `Organization`, or `Offer` JSON-LD nodes. For comprehensive domain-level generative search authority, evaluate using the **Site-Wide AI Citation Readiness Index**. This single-page score is deliberately not calibrated against AI citation frequency; `test_calibration.py` records the evidence behind that decision.

| Target URL | Structured Data Readiness (Single-Page) | Mandatory Schema Compliance | Number of Detected Entities | Top Missing Concepts |
| :--- | :---: | :---: | :---: | :--- |
| `https://www.chargebee.com` | **76.03 / 100** | 3/3 | 6 | ERP Integration, Accounts Receivable, SOC 1 (+2 more) |
| `https://www.maxio.com` | **42.09 / 100** | 1/3 | 11 | ASC 606, Billing Automation, ERP Integration (+4 more) |
| `https://stripe.com/billing` | **45.92 / 100** | 0/3 | 26 | Subscription Management, ERP Integration, Payment Gateway |

## Detailed Competitor Profiles

### https://www.chargebee.com
- **Page Title**: Chargebee: Billing & Monetization for SaaS and AI Companies
- **Structured Data Readiness Score**: 76.03 / 100.0
- **Mandatory Schemas Detected**: SoftwareApplication, Organization, Offer (3/3)
- **Missing Schemas**: None
- **Total Entities Extracted**: 6
- **Detected Seed Concepts**: Revenue Recognition, ASC 606, Subscription Management, Billing Automation, Payment Gateway
- **Missing Seed Concepts**: ERP Integration, Accounts Receivable, SOC 1, SOC 2, REST API

**Top Extracted Entities:**
- `[Accounting Standard]` ASC 606 (conf: 0.9755)
- `[Accounting Standard]` IFRS 15 (conf: 0.9632)
- `[Software Platform]` Chargebee (conf: 0.6917)
- `[Software Platform]` AI-powered retention engine (conf: 0.6663)
- `[Billing Feature]` invoicing (conf: 0.6144)
- `[Integration Partner]` developer-friendly APIs (conf: 0.5009)

### https://www.maxio.com
- **Page Title**: Billing and Financial Reporting for B2B SaaS & AI | Maxio
- **Structured Data Readiness Score**: 42.09 / 100.0
- **Mandatory Schemas Detected**: Organization (1/3)
- **Missing Schemas**: SoftwareApplication, Offer
- **Total Entities Extracted**: 11
- **Detected Seed Concepts**: Revenue Recognition, Subscription Management, Payment Gateway
- **Missing Seed Concepts**: ASC 606, Billing Automation, ERP Integration, Accounts Receivable, SOC 1, SOC 2, REST API

**Top Extracted Entities:**
- `[Software Platform]` Maxio (conf: 0.9657)
- `[Software Platform]` Braintree (conf: 0.7905)
- `[Accounting Standard]` IFRS (conf: 0.7821)
- `[Software Platform]` Stripe (conf: 0.7686)
- `[Accounting Standard]` GAAP (conf: 0.7109)
- `[Software Platform]` Authorize.net (conf: 0.6657)
- `[Integration Partner]` Zapier (conf: 0.6536)
- `[Billing Feature]` personalized pricing (conf: 0.5120)

### https://stripe.com/billing
- **Page Title**: Stripe Billing | Recurring Payments & Subscription Solutions
- **Structured Data Readiness Score**: 45.92 / 100.0
- **Mandatory Schemas Detected**: None (0/3)
- **Missing Schemas**: SoftwareApplication, Organization, Offer
- **Total Entities Extracted**: 26
- **Detected Seed Concepts**: Revenue Recognition, ASC 606, Billing Automation, Accounts Receivable, SOC 1, SOC 2, REST API
- **Missing Seed Concepts**: Subscription Management, ERP Integration, Payment Gateway

**Top Extracted Entities:**
- `[Software Platform]` Stripe (conf: 0.9656)
- `[Accounting Standard]` ASC 606 (conf: 0.9303)
- `[Accounting Standard]` IFRS 15 (conf: 0.9229)
- `[Security Standard]` ISO 27001 (conf: 0.9196)
- `[Accounting Standard]` PCI DSS (conf: 0.9125)
- `[Accounting Standard]` SOC 2 (conf: 0.8509)
- `[Pricing Model]` Usage-based billing (conf: 0.8128)
- `[Integration Partner]` Metronome (conf: 0.8033)

