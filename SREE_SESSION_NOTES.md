# OntoLeap — Session Notes (Sree + Claude, 7 Sep 2026)

These notes document a full review session with Sree. Read this before picking up
new work. It covers what was found, what is already done, and what is genuinely missing.

---

## Context

- Test client used throughout: **Ordway Labs** (ordwaylabs.com)
- Sree works at Ordway so can immediately tell when output is wrong or right
- OntoLeap is sector-agnostic — Ordway is just the validation client, not the only target

---

## What we found by looking at the live output

### Problem 1 — False drift alerts (e.g. "ASC 606 Revenue Recognition")
The tool flagged ASC 606 as a drift gap. It is wrong for two reasons:
1. ASC 606 compliance lives on trust/security/compliance pages, not in the API spec
2. The hardcoded demo fallback text (see below) was injecting "ASC 606" as a fake claim

**Current status:** The demo fallback is already gated behind `ONTOLEAP_DEMO_MODE` env var
(TODO.md T2 — done). But the fallback TEXT itself is still wrong if demo mode is on.
The compliance drift logic still flags standards even when compliance pages were never crawled.

**What still needs fixing:**
- In drift detection, do NOT flag a compliance standard (ASC 606, GDPR, HIPAA etc.)
  unless a trust/security/compliance page was actually crawled and checked.
  If those pages were not in scope, skip the flag entirely.
- Replace the hardcoded fallback text in the demo fallback with something generic
  (e.g. empty string) so accidental demo mode activation doesn't inject fake claims.

---

### Problem 2 — Tool only processes one page
When given `https://www.ordwaylabs.com`, it only processes the homepage.
Features, pricing, integrations, compliance pages are all missed.
This causes most false drift alerts — capabilities that exist on /product/revenue-recognition
are flagged as absent because only the homepage was checked.

**Current status:** `fetch_sitemap_urls()` exists in the codebase but is NOT wired into
the main audit pipeline in `product_truth.py`. The Zendesk API path exists for support
subdomains but only for Zendesk — no detection for other platforms.

**Proof:** Running the audit on `ordwaylabs.com/product/revenue-recognition` directly
produced a score of 88/100 vs 43/100 for the homepage alone. The page-level engine works.
The problem is crawl scope.

---

### Problem 3 — Support subdomain (support.ordwaylabs.com) gets blocked
Zendesk help centers sit behind Cloudflare + Zendesk's own bot protection.
The scraper cannot get through even with Chrome TLS impersonation.

**Current status:** The Zendesk API path is partially implemented for Ordway but
no other platforms (Intercom, Freshdesk, GitBook, Readme.io) are handled.

---

## What is confirmed ALREADY BUILT (don't rebuild)

From ANTIGRAVITY_TASKS.md and TODO.md review:
- Zendesk API detection — partially done in product_truth.py
- fetch_sitemap_urls() — exists but not wired into main audit
- Security hardening (SSRF, rate limiting, redirect validation)
- Competitor change tracking (truth_ledger/history.jsonl)
- PyKEEN misrepresentation fixed
- Readiness score renamed to "Structured Data Readiness"
- Graph completeness score fixed (non-circular)
- GitHub as proof-discovery source

---

## What is GENUINELY MISSING and needs building

### Priority 1 — Fix compliance drift logic
**File:** `product_truth.py` — drift_alerts generation section
**Change:** Before flagging a compliance standard as drift, check whether
a trust/security/compliance page was actually crawled. If not, skip the flag.
Pattern to detect compliance-type pages: URLs containing /trust, /security,
/compliance, /legal, /privacy.

### Priority 2 — Wire sitemap crawl into main audit pipeline
**File:** `product_truth.py` — `analyze_product_truth()` function
**Change:** Before running the single-page analysis, call `fetch_sitemap_urls()`
on the root domain. Filter URLs for product-relevant pages:
  - Keep: /product, /features, /platform, /pricing, /integrations,
           /compliance, /trust, /security, /solutions, /how-it-works
  - Skip: /blog, /news, /press, /careers, /legal, /author, /tag
Cap at 25 pages. Run each through the existing pipeline. Merge all findings
before drift detection. This alone eliminates most false positives.

### Priority 3 — Multi-platform help center detection
**New file:** `platform_detector.py`

Detection order for any subdomain (support.*, help.*, docs.*):

1. **Zendesk** — try `https://{domain}/api/v2/help_center/en-us/articles.json`
   If returns JSON with "articles" key → fetch all articles via API (already partially done)

2. **Intercom** — HTML source contains `intercom.io` or `intercomcdn.com`
   API: `https://api.intercom.io/articles` (requires API key — skip for now, use scraper)

3. **Freshdesk** — HTML source contains `freshdesk.com` or `freshworks.com`
   API: `https://{domain}/api/v2/solutions/articles` (requires API key — skip, use scraper)

4. **GitBook** — HTML contains `gitbook.com` or CNAME points to gitbook.io
   Approach: sitemap-based (GitBook is scraper-friendly, no Cloudflare)

5. **Readme.io** — HTML contains `readme.io` or `readmecdn.com`
   API: `https://dash.readme.com/api/v1/docs` (requires API key — skip, use scraper)

6. **Default fallback** — use sitemap approach

Detection method priority:
  a. Try API endpoint directly
  b. If fails, fetch HTML and check for platform signatures in script tags
  c. DNS CNAME lookup as last resort

### Priority 4 — Apify for G2/Capterra review data (future)
Not urgent for build phase. Use Apify marketplace G2 actor once the
core crawl issues above are fixed. Free tier ($5/month credit) sufficient for testing.
Reviews give third-party verified capability evidence — weight higher in confidence score.

---

## Paid tools recommended (free tiers sufficient during build phase)

| Tool | Purpose | Free tier |
|------|---------|-----------|
| Firecrawl | Bypass Cloudflare on marketing sites | 500 pages/month |
| Apify | G2/Capterra review scraping | $5 credit/month |
| SerpAPI | Review platform search | 100 searches/month |

Zendesk API, sitemap crawling — completely free, no quota.

---

## Validated approaches (Sree tested these himself)

1. **Zendesk public API** — `https://support.ordwaylabs.com/api/v2/help_center/articles.json`
   Works, returns full article content including categories, labels, dates.
   An older separate script (`download_docs.py`) already does this and works fast.

2. **Sitemap-based crawl** — `download_sitemap.py` script (separate from OntoLeap)
   Works for sites with sitemap.xml or llms.txt. Filters, saves as Markdown.
   This approach is proven — wire equivalent logic into OntoLeap's pipeline.

---

## What NOT to do (already evaluated and rejected — see TODO.md)

- Scrapy, spaCy, Neo4j, Common Crawl, SEC EDGAR, Companies House — all rejected
- Do not re-litigate these without a strong new reason

---

## Notes on the codebase health

The scraper itself (scraper.py) is well-built:
- Chrome TLS impersonation via curl_cffi
- Firecrawl fallback (needs API key configured)
- Standard requests fallback
- SSRF protection on all redirect hops
- Response size ceiling

The engine works correctly at the page level. The problems are all about
WHAT pages are fed into it, not HOW individual pages are processed.

---

_Written by Claude (Sonnet 4.6) based on session with Sree, 7 Sep 2026_
_Do not deploy — review and integrate changes carefully_

---
## Session: Sep 8, 2026 — Architecture Decisions

### Crawl Strategy (decided)
- **Onboarding:** Full site crawl once (marketing + docs + competitors). 30-60 min, background job.
- **Monitoring:** Weekly delta crawl using sitemap Last-Modified timestamp. 2-5 min.
- **On-demand:** Uses indexed data, returns in seconds. What MCP server exposes.
- **Now (prototype):** On-demand crawl with 24hr disk cache. Sufficient for demos.

### Vertical Strategy (decided)
- Start narrow: **Billing & RevOps** (Ordway, Chargebee, Zuora, Maxio, Recurly, Paddle)
- Not "all of Finance" — that's a 12-month project
- Expansion path: Customer Success → Sales Engagement → HR Tech
- Each vertical = same engine + different ontology vocabulary layer

### Ontology Build Order (decided)
1. **SKOS synonym layer** — marketing language ↔ API language mappings
   - Fixes: "Order-to-Revenue Cycle" → ["order management", "revenue workflow"]
   - Fixes: "Deferred Revenue Schedules" → ["revenue schedule", "revenue_schedule"]
2. **Compliance ontology** — standard → required product features
   - ASC 606 → 5 steps → specific capabilities
   - SOC 2 → 5 trust criteria → technical controls
3. **Feature taxonomy** — canonical feature tree for Billing/RevOps category

### No ready-made ontology exists for B2B SaaS Billing/RevOps
- FIBO covers capital markets, not SaaS
- Build from: ASC 606 FASB standard, SOC 2 AICPA criteria, G2 feature categories
- This ontology IS the IP — competitors must rebuild from scratch

### Alert Progress
- Started: 26 alerts
- After Sentence Transformers + known customers fix: 16
- After Crawl4AI + KEEP_PATTERNS fix: 16 (cache issue)
- After cache clear + fresh crawl: 14
- After generic phrases fix + SOC2 dedup: 11
- Current run: full 68-page crawl + 50 support articles (target: <8)
