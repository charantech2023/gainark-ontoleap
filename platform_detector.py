"""
GainARK OntoLeap — Help Center Platform Detector
=================================================
Detects which documentation/help platform a subdomain runs on,
then fetches content via the most reliable method for that platform.

Detection order for any support/help/docs subdomain:
  1. Try the platform's public API directly (fastest, cleanest)
  2. Check HTML source for platform-specific script signatures
  3. Fallback to sitemap-based crawl

Supported platforms:
  - Zendesk    → public REST API, no key needed for public help centers
  - GitBook    → sitemap-friendly, no Cloudflare
  - Intercom   → API requires key; falls back to scraper
  - Freshdesk  → API requires key; falls back to scraper
  - Readme.io  → API requires key; falls back to scraper
  - Unknown    → sitemap fallback
"""

import logging
import time
import re
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests

logger = logging.getLogger("gainark.platform_detector")

TIMEOUT = 15
PAUSE = 0.3  # polite delay between requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; OntoLeap/2.0; +https://gainark.com)",
    "Accept": "application/json, text/html, */*",
}

# HTML signatures that identify each platform
PLATFORM_SIGNATURES = {
    "zendesk": ["zendesk.com", "zdassets.com", "ekr.zdassets.com"],
    "intercom": ["intercom.io", "intercomcdn.com", "widget.intercom.io"],
    "freshdesk": ["freshdesk.com", "freshworks.com", "freshdesk-assets.com"],
    "gitbook": ["gitbook.com", "gitbook.io", "gitbook-cdn.com"],
    "readme": ["readme.io", "readmecdn.com", "readme-oss.com"],
}

# Paths that indicate compliance/trust content was in scope
COMPLIANCE_PATH_PATTERNS = ["/trust", "/security", "/compliance", "/legal", "/privacy", "/certifications"]


def detect_platform(domain: str) -> str:
    """
    Detect which help center platform a domain runs on.
    Returns one of: 'zendesk', 'intercom', 'freshdesk', 'gitbook', 'readme', 'unknown'
    """
    # Step 1: Try Zendesk API directly — fastest check
    try:
        url = f"https://{domain}/api/v2/help_center/en-us/articles.json?per_page=1"
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            if "articles" in data or "count" in data:
                logger.info("Platform detected: Zendesk (API confirmed) for %s", domain)
                return "zendesk"
    except Exception as e:
        logger.debug("Zendesk API check failed for %s: %s", domain, e)

    # Step 2: Check HTML source for platform signatures
    try:
        resp = requests.get(f"https://{domain}", headers=HEADERS, timeout=TIMEOUT)
        if resp.status_code == 200:
            html_lower = resp.text.lower()
            for platform, signatures in PLATFORM_SIGNATURES.items():
                for sig in signatures:
                    if sig in html_lower:
                        logger.info("Platform detected: %s (HTML signature '%s') for %s", platform, sig, domain)
                        return platform
    except Exception as e:
        logger.debug("HTML signature check failed for %s: %s", domain, e)

    logger.info("Platform not detected for %s — will use sitemap fallback", domain)
    return "unknown"


def fetch_zendesk_articles(domain: str, max_articles: int = 200) -> str:
    """
    Fetch all articles from a public Zendesk help center via API.
    Returns combined plain text of all article content.
    """
    articles = []
    url = f"https://{domain}/api/v2/help_center/en-us/articles.json?per_page=100"
    page = 1

    while url and len(articles) < max_articles:
        try:
            logger.info("Zendesk API page %d for %s", page, domain)
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            if resp.status_code != 200:
                logger.warning("Zendesk API returned %d for %s", resp.status_code, domain)
                break
            data = resp.json()
            batch = data.get("articles", [])
            articles.extend(batch)
            url = data.get("next_page")
            page += 1
            if url:
                time.sleep(PAUSE)
        except Exception as e:
            logger.error("Zendesk fetch error for %s: %s", domain, e)
            break

    if not articles:
        return ""

    # Convert articles to plain text, grouped by title
    chunks = []
    for article in articles[:max_articles]:
        title = article.get("title", "").strip()
        body_html = article.get("body") or ""
        # Strip HTML tags simply
        body_text = re.sub(r"<[^>]+>", " ", body_html)
        body_text = re.sub(r"\s+", " ", body_text).strip()
        if body_text and len(body_text) > 50:
            chunks.append(f"Help Article: {title}\n{body_text}")

    combined = "\n\n".join(chunks)
    logger.info("Zendesk: extracted %d articles (%d chars) from %s", len(articles), len(combined), domain)
    return combined


def fetch_sitemap_urls_sync(domain: str, must_contain: str = "", max_pages: int = 25):
    """
    Synchronous sitemap fetcher.
    Tries sitemap.xml and sitemap_index.xml, returns filtered list of URLs.
    """
    import xml.etree.ElementTree as ET

    candidate_sitemaps = [
        f"https://{domain}/sitemap.xml",
        f"https://www.{domain}/sitemap.xml",
        f"https://{domain}/sitemap_index.xml",
        f"https://www.{domain}/sitemap_index.xml",
    ]

    all_urls = []
    seen = set()

    def parse_sitemap(sitemap_url, depth=0):
        if depth > 3 or sitemap_url in seen:
            return
        seen.add(sitemap_url)
        try:
            resp = requests.get(sitemap_url, headers=HEADERS, timeout=TIMEOUT)
            if resp.status_code != 200:
                return
            root = ET.fromstring(resp.content)
            ns = root.tag.split("}")[0].strip("{") if "}" in root.tag else ""
            prefix = f"{{{ns}}}" if ns else ""

            is_index = root.tag.endswith("sitemapindex")
            for block in root:
                loc = block.find(f"{prefix}loc")
                if loc is None or not loc.text:
                    continue
                loc_url = loc.text.strip()
                if is_index:
                    time.sleep(0.2)
                    parse_sitemap(loc_url, depth + 1)
                else:
                    if loc_url not in seen:
                        all_urls.append(loc_url)
        except Exception as e:
            logger.debug("Sitemap parse error for %s: %s", sitemap_url, e)

    for sm in candidate_sitemaps:
        parse_sitemap(sm)
        if all_urls:
            break

    # Filter for product-relevant pages
    KEEP_PATTERNS = [
        "/product", "/features", "/platform", "/capabilities",
        "/integrations", "/pricing", "/solutions", "/how-it-works",
        "/about", "/revenue", "/billing", "/api", "/docs", "/resources/glossary",
    ]
    SKIP_PATTERNS = [
        "/blog", "/news", "/press", "/careers", "/legal", "/author",
        "/tag", "/category", "/search", "/signin", "/signup", "/login",
        "/changelog", "/releases",  # these rarely exist; remove to avoid timeouts
        "/compliance", "/trust", "/security",  # 404 on many SaaS sites incl. ordwaylabs.com
    ]

    if must_contain:
        filtered = [u for u in all_urls if must_contain in u]
    else:
        # Skip only clearly irrelevant pages; keep everything else for full coverage
        filtered = [
            u for u in all_urls
            if not any(p in u.lower() for p in SKIP_PATTERNS)
        ]

    # Secondary preference: product-relevant pages first, others appended after
    priority = [u for u in filtered if any(p in u.lower() for p in KEEP_PATTERNS)]
    remainder = [u for u in filtered if u not in priority]
    filtered = priority + remainder

    logger.info("Sitemap: %d total URLs found, %d after filtering for %s", len(all_urls), len(filtered), domain)
    return filtered[:max_pages]


def fetch_help_center_content(url: str) -> Tuple[str, str]:
    """
    Main entry point. Given any help center or docs URL, detect the platform
    and return (platform_name, combined_text_content).

    Usage:
        platform, text = fetch_help_center_content("https://support.ordwaylabs.com")
    """
    parsed = urlparse(url)
    domain = parsed.netloc.lower().lstrip("www.")

    platform = detect_platform(domain)

    if platform == "zendesk":
        text = fetch_zendesk_articles(domain)
        return "zendesk", text

    # For other platforms, fall back to sitemap-based crawl
    # (API keys required for Intercom/Freshdesk/Readme — not implemented yet)
    logger.info("Using sitemap fallback for platform '%s' at %s", platform, domain)
    urls = fetch_sitemap_urls_sync(domain)

    if not urls:
        logger.warning("No sitemap URLs found for %s", domain)
        return platform, ""

    from scraper import smart_fetch
    from bs4 import BeautifulSoup

    chunks = []
    for page_url in urls:
        try:
            html = smart_fetch(page_url, timeout=12)
            soup = BeautifulSoup(html, "html.parser")
            for el in soup(["script", "style", "nav", "footer", "noscript", "header"]):
                el.decompose()
            text = soup.get_text(separator=" ", strip=True)
            if len(text) > 100:
                chunks.append(text)
            time.sleep(PAUSE)
        except Exception as e:
            logger.debug("Failed to fetch %s: %s", page_url, e)

    return platform, "\n\n".join(chunks)


def compliance_pages_in_scope(crawled_urls) -> bool:
    """
    Returns True if any of the crawled URLs look like trust/security/compliance pages.
    Used to gate regulatory drift alerts — only flag compliance gaps if we actually
    checked the pages where compliance claims would live.
    """
    if not crawled_urls:
        return False
    for u in crawled_urls:
        u_lower = u.lower()
        if any(p in u_lower for p in COMPLIANCE_PATH_PATTERNS):
            return True
    return False
