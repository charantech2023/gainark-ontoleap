"""
GainARK OntoLeap — Smart Anti-Bot Scraper Engine
Combines:
1. Level 1 (Fast & Free): curl_cffi with Chrome 124 TLS (JA3/JA4) fingerprint impersonation
   and realistic browser navigation headers.
2. Anti-Bot Challenge Detection: Automatically identifies Cloudflare Turnstile, WAF 403/503,
   and captcha challenge pages.
3. Level 2 (Managed Fallback): Automated fallback to Firecrawl API if FIRECRAWL_API_KEY is configured.
4. Level 3 (Standard Fallback): Resilient standard HTTP request fallback with stealth headers.
5. Strict SSRF Protection: Blocks private IPs, localhost, and cloud metadata endpoints.
"""

import os
import re
import socket
import logging
from typing import Optional, Dict, Any, Tuple
from urllib.parse import urlparse
import requests
import httpx

try:
    from curl_cffi import requests as curl_requests
    from curl_cffi.requests import AsyncSession as CurlAsyncSession
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False

from constants import BLOCKED_HOSTNAMES, BLOCKED_IP_PREFIXES

logger = logging.getLogger("gainark.scraper")

# Signatures indicating an anti-bot challenge page rather than real website content
CHALLENGE_SIGNATURES = [
    "cf-browser-verification",
    "cf-turnstile",
    "challenges.cloudflare.com",
    "Just a moment...",
    "Attention Required! | Cloudflare",
    "Checking your browser before accessing",
    "Verifying you are human",
    "Please wait while we verify your browser",
    "ddos-guard",
    "<title>Access Denied</title>",
    "<title>Security Challenge</title>",
]


def validate_url_for_fetch(url: str) -> None:
    """
    Validates that a URL is safe to fetch.
    Raises ValueError if the URL points to a private IP, localhost,
    cloud metadata service, or uses a non-HTTP scheme (SSRF protection).
    """
    try:
        parsed = urlparse(url)
    except Exception:
        raise ValueError(f"Malformed URL: {url!r}")

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsafe URL scheme '{parsed.scheme}'. Only http and https are allowed.")

    hostname = (parsed.hostname or "").lower().strip(".")
    if not hostname:
        raise ValueError("URL must contain a valid hostname.")

    # Block reserved/private hostnames
    if hostname in BLOCKED_HOSTNAMES:
        raise ValueError(f"Blocked hostname: {hostname!r}")

    # Resolve to IP and check against blocked private ranges
    try:
        resolved_ip = socket.gethostbyname(hostname)
        if any(resolved_ip.startswith(prefix) for prefix in BLOCKED_IP_PREFIXES):
            raise ValueError(f"URL resolves to a private/reserved IP address: {resolved_ip}")
    except socket.gaierror:
        # If DNS cannot resolve, still allow (could be internal Docker/container network resolution)
        pass


def is_challenge_page(status_code: int, html_text: str) -> bool:
    """Checks whether a response indicates an anti-bot challenge or block."""
    if status_code in (403, 429, 503):
        return True
    
    if not html_text:
        return False

    # Check for challenge signatures
    lower_text = html_text.lower()
    for sig in CHALLENGE_SIGNATURES:
        if sig.lower() in lower_text:
            return True

    return False


class SmartScraper:
    """
    Enterprise-grade scraper with Chrome TLS fingerprinting and anti-bot mitigation.
    """

    def __init__(self, firecrawl_api_key: Optional[str] = None):
        self.firecrawl_api_key = firecrawl_api_key or os.environ.get("FIRECRAWL_API_KEY")
        self.browser_headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="124", "Not-A.Brand";v="24", "Google Chrome";v="124"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        }

    def _fetch_firecrawl(self, url: str, timeout: int = 25) -> Optional[str]:
        """Routes scrape through Firecrawl API to bypass advanced bot protection / JS."""
        if not self.firecrawl_api_key:
            return None

        logger.info("Routing URL %s through Firecrawl anti-bot API...", url)
        try:
            fc_headers = {
                "Authorization": f"Bearer {self.firecrawl_api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "url": url,
                "formats": ["html"]
            }
            resp = requests.post(
                "https://api.firecrawl.dev/v1/scrape",
                json=payload,
                headers=fc_headers,
                timeout=timeout
            )
            if resp.status_code == 200:
                data = resp.json()
                html = data.get("data", {}).get("html") or data.get("html")
                if html:
                    logger.info("Firecrawl successfully bypassed anti-bot for %s (%d bytes)", url, len(html))
                    return html
            else:
                logger.warning("Firecrawl returned HTTP %d for %s: %s", resp.status_code, url, resp.text[:200])
        except Exception as e:
            logger.error("Firecrawl fallback failed for %s: %s", url, e)

        return None

    async def _fetch_firecrawl_async(self, url: str, timeout: int = 25) -> Optional[str]:
        """Asynchronous Firecrawl API caller."""
        if not self.firecrawl_api_key:
            return None

        logger.info("Routing URL %s through Firecrawl anti-bot API (async)...", url)
        try:
            fc_headers = {
                "Authorization": f"Bearer {self.firecrawl_api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "url": url,
                "formats": ["html"]
            }
            async with httpx.AsyncClient(timeout=float(timeout)) as client:
                resp = await client.post(
                    "https://api.firecrawl.dev/v1/scrape",
                    json=payload,
                    headers=fc_headers
                )
                if resp.status_code == 200:
                    data = resp.json()
                    html = data.get("data", {}).get("html") or data.get("html")
                    if html:
                        logger.info("Firecrawl successfully bypassed anti-bot for %s (async)", url)
                        return html
                else:
                    logger.warning("Firecrawl returned HTTP %d for %s: %s", resp.status_code, url, resp.text[:200])
        except Exception as e:
            logger.error("Firecrawl async fallback failed for %s: %s", url, e)

        return None

    def fetch_html(self, url: str, timeout: int = 15) -> str:
        """
        Synchronously fetches a URL's HTML content.
        Uses Chrome TLS impersonation, checks for anti-bot blocks,
        and falls back to Firecrawl or standard requests as needed.
        """
        validate_url_for_fetch(url)

        # Level 1: curl_cffi with Chrome 124 TLS impersonation
        if CURL_CFFI_AVAILABLE:
            try:
                resp = curl_requests.get(
                    url,
                    impersonate="chrome124",
                    headers=self.browser_headers,
                    timeout=timeout,
                    verify=True
                )
                
                # If valid page and not a challenge
                if resp.status_code == 200 and not is_challenge_page(resp.status_code, resp.text):
                    return resp.text

                logger.warning(
                    "Level 1 curl_cffi got status %d or challenge for %s. Checking Level 2 fallback...",
                    resp.status_code, url
                )
            except Exception as e:
                logger.warning("Level 1 curl_cffi failed for %s: %s", url, e)

        # Level 2: Firecrawl fallback (if key is set)
        if self.firecrawl_api_key:
            fc_html = self._fetch_firecrawl(url, timeout=timeout + 10)
            if fc_html:
                return fc_html

        # Level 3: Resilient standard requests with browser headers
        logger.info("Attempting Level 3 standard requests for %s...", url)
        session = requests.Session()
        resp = session.get(url, headers=self.browser_headers, timeout=timeout, verify=True)
        resp.raise_for_status()
        return resp.text

    async def fetch_html_async(self, url: str, timeout: int = 15) -> str:
        """
        Asynchronously fetches a URL's HTML content.
        Ideal for multi-page concurrent crawlers and sitemap ingestion.
        """
        validate_url_for_fetch(url)

        # Level 1: curl_cffi AsyncSession with Chrome 124 TLS impersonation
        if CURL_CFFI_AVAILABLE:
            try:
                async with CurlAsyncSession(impersonate="chrome124") as session:
                    resp = await session.get(
                        url,
                        headers=self.browser_headers,
                        timeout=timeout,
                        verify=True
                    )
                    if resp.status_code == 200 and not is_challenge_page(resp.status_code, resp.text):
                        return resp.text

                    logger.warning(
                        "Async Level 1 got status %d or challenge for %s", resp.status_code, url
                    )
            except Exception as e:
                logger.warning("Async Level 1 curl_cffi failed for %s: %s", url, e)

        # Level 2: Firecrawl async fallback
        if self.firecrawl_api_key:
            fc_html = await self._fetch_firecrawl_async(url, timeout=timeout + 10)
            if fc_html:
                return fc_html

        # Level 3: httpx async fallback
        logger.info("Attempting Level 3 async httpx for %s...", url)
        async with httpx.AsyncClient(timeout=float(timeout), follow_redirects=True, headers=self.browser_headers) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.text


# Global default scraper instance
_default_scraper: Optional[SmartScraper] = None

def get_smart_scraper() -> SmartScraper:
    """Returns the singleton instance of SmartScraper."""
    global _default_scraper
    if _default_scraper is None:
        _default_scraper = SmartScraper()
    return _default_scraper

def smart_fetch(url: str, timeout: int = 15) -> str:
    """Synchronous helper function to fetch a URL using the smart scraper."""
    return get_smart_scraper().fetch_html(url, timeout=timeout)

async def smart_fetch_async(url: str, timeout: int = 15) -> str:
    """Asynchronous helper function to fetch a URL using the smart scraper."""
    return await get_smart_scraper().fetch_html_async(url, timeout=timeout)
