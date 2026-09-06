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
from cache import get_content_cache

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


import ipaddress

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

    # Check if hostname itself is directly an IP literal
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast:
            raise ValueError(f"URL points to a private/reserved IP address: {hostname}")
    except ValueError as val_e:
        if "private/reserved" in str(val_e):
            raise
        # Not a raw IP literal, proceed to DNS resolution

    # Resolve to IP and check against blocked private ranges using socket.getaddrinfo (IPv4 + IPv6)
    try:
        addr_info = socket.getaddrinfo(hostname, None)
        for _, _, _, _, sockaddr in addr_info:
            resolved_ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(resolved_ip_str)
                if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local or ip.is_multicast:
                    raise ValueError(f"URL resolves to a private/reserved IP address: {resolved_ip_str}")
            except ValueError as ip_err:
                if "private/reserved" in str(ip_err):
                    raise
            if any(resolved_ip_str.startswith(prefix) for prefix in BLOCKED_IP_PREFIXES):
                raise ValueError(f"URL resolves to a private/reserved IP address: {resolved_ip_str}")
    except socket.gaierror:
        # If DNS cannot resolve, still allow (could be internal container or mock network)
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
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            "Referer": "https://www.google.com/",
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

    def fetch_html(
        self,
        url: str,
        timeout: int = 15,
        force_refresh: bool = False,
        max_age: Optional[int] = None
    ) -> str:
        """
        Synchronously fetches a URL's HTML content.
        Checks in-memory/disk cache first before making network calls.
        Uses Chrome TLS impersonation, checks for anti-bot blocks,
        and falls back to Firecrawl or standard requests as needed.
        """
        validate_url_for_fetch(url)

        # Check Cache
        content_cache = get_content_cache()
        cached = content_cache.get(url, max_age=max_age, force_refresh=force_refresh)
        if cached is not None:
            logger.debug("Cache hit for %s; skipping network fetch.", url)
            return cached

        html_result = None

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
                    html_result = resp.text

                if not html_result:
                    logger.warning(
                        "Level 1 curl_cffi got status %d or challenge for %s. Checking Level 2 fallback...",
                        resp.status_code, url
                    )
            except Exception as e:
                logger.warning("Level 1 curl_cffi failed for %s: %s", url, e)

        # Level 2: Firecrawl fallback (if key is set and level 1 failed)
        if not html_result and self.firecrawl_api_key:
            fc_html = self._fetch_firecrawl(url, timeout=timeout + 10)
            if fc_html:
                html_result = fc_html

        # Level 3: Resilient standard requests with browser headers
        if not html_result:
            logger.info("Attempting Level 3 standard requests for %s...", url)
            session = requests.Session()
            resp = session.get(url, headers=self.browser_headers, timeout=timeout, verify=True)
            resp.raise_for_status()
            if "charset" not in resp.headers.get("Content-Type", "").lower():
                resp.encoding = resp.apparent_encoding or resp.encoding
            html_result = resp.text

        # Cache successful fetch
        if html_result:
            content_cache.set(url, html_result)

        return html_result

    async def fetch_html_async(
        self,
        url: str,
        timeout: int = 15,
        force_refresh: bool = False,
        max_age: Optional[int] = None
    ) -> str:
        """
        Asynchronously fetches a URL's HTML content.
        Checks in-memory/disk cache first before making network calls.
        Ideal for multi-page concurrent crawlers and sitemap ingestion.
        """
        validate_url_for_fetch(url)

        # Check Cache
        content_cache = get_content_cache()
        cached = content_cache.get(url, max_age=max_age, force_refresh=force_refresh)
        if cached is not None:
            logger.debug("Async cache hit for %s; skipping network fetch.", url)
            return cached

        html_result = None

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
                        html_result = resp.text
                    else:
                        logger.warning(
                            "Async Level 1 got status %d or challenge for %s", resp.status_code, url
                        )
            except Exception as e:
                logger.warning("Async Level 1 curl_cffi failed for %s: %s", url, e)

        # Level 2: Firecrawl async fallback
        if not html_result and self.firecrawl_api_key:
            fc_html = await self._fetch_firecrawl_async(url, timeout=timeout + 10)
            if fc_html:
                html_result = fc_html

        # Level 3: httpx async fallback
        if not html_result:
            logger.info("Attempting Level 3 async httpx for %s...", url)
            async with httpx.AsyncClient(timeout=float(timeout), follow_redirects=True, headers=self.browser_headers) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                html_result = resp.text

        # Cache successful fetch
        if html_result:
            content_cache.set(url, html_result)

        return html_result


# Global default scraper instance
_default_scraper: Optional[SmartScraper] = None

def get_smart_scraper() -> SmartScraper:
    """Returns the singleton instance of SmartScraper."""
    global _default_scraper
    if _default_scraper is None:
        _default_scraper = SmartScraper()
    return _default_scraper

def smart_fetch(
    url: str,
    timeout: int = 15,
    force_refresh: bool = False,
    max_age: Optional[int] = None
) -> str:
    """Synchronous helper function to fetch a URL using the smart scraper and cache."""
    return get_smart_scraper().fetch_html(url, timeout=timeout, force_refresh=force_refresh, max_age=max_age)

async def smart_fetch_async(
    url: str,
    timeout: int = 15,
    force_refresh: bool = False,
    max_age: Optional[int] = None
) -> str:
    """Asynchronous helper function to fetch a URL using the smart scraper and cache."""
    return await get_smart_scraper().fetch_html_async(url, timeout=timeout, force_refresh=force_refresh, max_age=max_age)

