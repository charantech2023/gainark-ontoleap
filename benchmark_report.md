# Ontology Pipeline Benchmark: Competitive Structured Data Readiness Analysis

> **Executive Note on Brand Authority vs. Single-Page Schema Readiness:**
> This audit measures **Single-Page Structured Data & Schema Implementation** (Mandatory Schema 40%, Seed Concept Coverage 30%, Entity Richness 30%) on specific landing pages. High brand citation authority across AI answer engines (such as Stripe) can coexist with low single-page structured data scores when individual product landing pages omit static `SoftwareApplication`, `Organization`, or `Offer` JSON-LD nodes. For comprehensive domain-level generative search authority, evaluate using the **Site-Wide AI Citation Readiness Index**. This single-page score is deliberately not calibrated against AI citation frequency; `test_calibration.py` records the evidence behind that decision.

| Target URL | Structured Data Readiness (Single-Page) | Mandatory Schema Compliance | Number of Detected Entities | Top Missing Concepts |
| :--- | :---: | :---: | :---: | :--- |
| `https://www.chargebee.com` | **59.15 / 100** | 3/3 | 1 | ERP Integration, Accounts Receivable, SOC 1 (+2 more) |
| `https://www.maxio.com` | **40.75 / 100** | 1/3 | 7 | ASC 606, Billing Automation, ERP Integration (+4 more) |
| `https://stripe.com/billing` | **40.42 / 100** | 0/3 | 8 | Subscription Management, ERP Integration, Payment Gateway |

## Detailed Competitor Profiles

### https://www.chargebee.com
- **Page Title**: Chargebee: Billing & Monetization for SaaS and AI Companies
- **Structured Data Readiness Score**: 59.15 / 100.0
- **Mandatory Schemas Detected**: SoftwareApplication, Organization, Offer (3/3)
- **Missing Schemas**: None
- **Total Entities Extracted**: 1
- **Detected Seed Concepts**: Revenue Recognition, ASC 606, Subscription Management, Billing Automation, Payment Gateway
- **Missing Seed Concepts**: ERP Integration, Accounts Receivable, SOC 1, SOC 2, REST API

**Top Extracted Entities:**
- `[Software Platform]` AI-powered retention engine (conf: 0.5484)

### https://www.maxio.com
- **Page Title**: Billing and Financial Reporting for B2B SaaS & AI | Maxio
- **Structured Data Readiness Score**: 40.75 / 100.0
- **Mandatory Schemas Detected**: Organization (1/3)
- **Missing Schemas**: SoftwareApplication, Offer
- **Total Entities Extracted**: 7
- **Detected Seed Concepts**: Revenue Recognition, Subscription Management, Payment Gateway
- **Missing Seed Concepts**: ASC 606, Billing Automation, ERP Integration, Accounts Receivable, SOC 1, SOC 2, REST API

**Top Extracted Entities:**
- `[Software Platform]` Maxio (conf: 0.9589)
- `[Software Platform]` Braintree (conf: 0.7942)
- `[Software Platform]` Stripe (conf: 0.7614)
- `[Accounting Standard]` IFRS (conf: 0.7523)
- `[Software Platform]` Authorize.net (conf: 0.6887)
- `[Accounting Standard]` GAAP (conf: 0.6859)
- `[Billing Feature]` Usage-based billing (conf: 0.4557)

### https://stripe.com/billing
- **Page Title**: Stripe Billing | Recurring Payments & Subscription Solutions
- **Structured Data Readiness Score**: 40.42 / 100.0
- **Mandatory Schemas Detected**: None (0/3)
- **Missing Schemas**: SoftwareApplication, Organization, Offer
- **Total Entities Extracted**: 8
- **Detected Seed Concepts**: Revenue Recognition, ASC 606, Billing Automation, Accounts Receivable, SOC 1, SOC 2, REST API
- **Missing Seed Concepts**: Subscription Management, ERP Integration, Payment Gateway

**Top Extracted Entities:**
- `[Software Platform]` Stripe (conf: 0.8058)
- `[Pricing Model]` Usage-based billing (conf: 0.7898)
- `[Integration Partner]` Metronome (conf: 0.7790)
- `[Pricing Model]` Consumption-based billing (conf: 0.7152)
- `[Pricing Model]` hybrid models (conf: 0.6396)
- `[Accounting Standard]` accounts receivable (conf: 0.4497)
- `[Pricing Model]` usage-based pricing models (conf: 0.4241)
- `[Software Platform]` Airwallet (conf: 0.4209)

