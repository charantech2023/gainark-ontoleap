"""
GainARK OntoLeap — Smart Anti-Bot Scraper Engine
Combines:
1. Level 1 (Fast & Free): curl_cffi with Chrome 124 TLS (JA3/JA4) fingerprint impersonation
   and realistic browser navigation headers.
2. Anti-Bot Challenge Detection: Automatically identifies Cloudflare Turnstile, WAF 403/503,
   and captcha challenge pages.
3. Fallbacks, tried in order and skipped when unavailable. The sync and async cascades
   differ, so the level numbers are not interchangeable:
     sync   1 curl_cffi -> 2 Crawl4AI -> 3 Firecrawl -> 4 standard requests
     async  1 curl_cffi -> 2 Firecrawl -> 3 httpx
   Crawl4AI is an optional import and is NOT in requirements.txt, so it is absent from
   the deployed image; Firecrawl runs only when FIRECRAWL_API_KEY is set. A deployment
   with neither goes straight from level 1 to plain HTTP.
5. Strict SSRF Protection: Blocks private IPs, localhost, and cloud metadata endpoints —
   on the initial URL *and on every redirect hop*, since a public host may redirect inward.
6. Response size ceiling: a single fetch cannot exhaust process memory.
"""

import os
import socket
import logging
import ipaddress
from typing import Optional, Dict, Any
from urllib.parse import urlparse, urljoin
import requests
import httpx
from cache import get_content_cache

try:
    from curl_cffi import requests as curl_requests
    from curl_cffi.requests import AsyncSession as CurlAsyncSession
    CURL_CFFI_AVAILABLE = True
except ImportError:
    CURL_CFFI_AVAILABLE = False

try:
    from crawl4ai import AsyncWebCrawler
    from crawl4ai.async_configs import BrowserConfig, CrawlerRunConfig, CacheMode
    CRAWL4AI_AVAILABLE = True
except ImportError:
    CRAWL4AI_AVAILABLE = False

from constants import BLOCKED_HOSTNAMES, BLOCKED_IP_PREFIXES


def _next_sync_fallback(has_firecrawl_key: bool) -> str:
    """Name the fallback the sync cascade will actually reach next.

    Crawl4AI is an optional import absent from requirements.txt, and Firecrawl needs a
    key, so which level comes next depends on the deployment rather than on the code.
    Announcing a fixed "Level 2" sends a reader looking for a browser fetch that never
    ran.
    """
    if CRAWL4AI_AVAILABLE:
        return "level 2 Crawl4AI"
    if has_firecrawl_key:
        return "level 3 Firecrawl (Crawl4AI not installed)"
    return "level 4 standard requests (Crawl4AI not installed, no Firecrawl key)"

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


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """Read a positive integer from the environment, falling back on any bad value."""
    try:
        return max(minimum, int(os.environ.get(name, "").strip() or default))
    except (TypeError, ValueError):
        return default


# A single page must not be able to exhaust the container's memory. 5 MB is far above
# any real marketing page or OpenAPI spec this platform ingests.
MAX_RESPONSE_BYTES = _env_int("MAX_RESPONSE_BYTES", 5 * 1024 * 1024, minimum=64 * 1024)

# Redirects are followed manually so each hop can be re-validated (see _resolve_redirects).
MAX_REDIRECTS = _env_int("MAX_REDIRECTS", 5, minimum=0)

# When DNS cannot resolve a hostname we cannot prove the target is external, so the
# default is to refuse. Set ONTOLEAP_ALLOW_UNRESOLVABLE_HOSTS=1 only for offline test
# environments that fetch from mock hosts.
ALLOW_UNRESOLVABLE_HOSTS = os.environ.get(
    "ONTOLEAP_ALLOW_UNRESOLVABLE_HOSTS", ""
).strip().lower() in ("1", "true", "yes")


def _ip_is_forbidden(ip: Any) -> bool:
    """True when an address is anything other than a routable public address."""
    if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
        return True
    if ip.is_multicast or ip.is_unspecified:
        return True
    # IPv4-mapped and 6to4 IPv6 forms smuggle a v4 address past the v6 checks.
    mapped = getattr(ip, "ipv4_mapped", None) or getattr(ip, "sixtofour", None)
    if mapped is not None and _ip_is_forbidden(mapped):
        return True
    return False


def _check_resolved_address(raw_ip: str) -> None:
    """Raise ValueError if a resolved address is not safe to connect to."""
    # getaddrinfo can hand back a scoped IPv6 literal such as 'fe80::1%eth0'.
    candidate = raw_ip.split("%")[0]
    try:
        ip = ipaddress.ip_address(candidate)
    except ValueError:
        # Unparseable address: fall back to the textual prefix blocklist rather than
        # silently accepting it.
        if any(candidate.startswith(prefix) for prefix in BLOCKED_IP_PREFIXES):
            raise ValueError(f"URL resolves to a blocked address: {raw_ip}")
        return
    if _ip_is_forbidden(ip):
        raise ValueError(f"URL resolves to a private/reserved IP address: {candidate}")
    if any(candidate.startswith(prefix) for prefix in BLOCKED_IP_PREFIXES):
        raise ValueError(f"URL resolves to a blocked address: {candidate}")


def validate_url_for_fetch(url: str) -> None:
    """
    Validates that a URL is safe to fetch.
    Raises ValueError if the URL points to a private IP, localhost,
    cloud metadata service, or uses a non-HTTP scheme (SSRF protection).

    This validates one URL. Redirects are validated separately, per hop, by
    _resolve_redirects — a public host is free to redirect to an internal one.
    """
    if not isinstance(url, str) or not url.strip():
        raise ValueError("URL must be a non-empty string.")

    try:
        parsed = urlparse(url.strip())
    except Exception:
        raise ValueError(f"Malformed URL: {url!r}")

    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Unsafe URL scheme '{parsed.scheme}'. Only http and https are allowed.")

    try:
        hostname = (parsed.hostname or "").lower().strip(".")
    except ValueError:
        # urlparse raises on malformed IPv6 bracket syntax.
        raise ValueError("URL contains a malformed host.")

    if not hostname:
        raise ValueError("URL must contain a valid hostname.")

    # Credentials in the authority are a parser-confusion vector and are never needed
    # for the public pages this platform reads.
    if parsed.username or parsed.password:
        raise ValueError("URLs containing embedded credentials are not permitted.")

    if hostname in BLOCKED_HOSTNAMES:
        raise ValueError(f"Blocked hostname: {hostname!r}")

    # Hostname may itself be an IP literal, in which case no DNS lookup is needed.
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    if literal_ip is not None:
        if _ip_is_forbidden(literal_ip):
            raise ValueError(f"URL points to a private/reserved IP address: {hostname}")
        return

    # Resolve and check every address the hostname maps to (IPv4 and IPv6).
    try:
        addr_info = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        if ALLOW_UNRESOLVABLE_HOSTS:
            logger.warning(
                "Allowing unresolvable host %r (ONTOLEAP_ALLOW_UNRESOLVABLE_HOSTS is set).", hostname
            )
            return
        # Fail closed: an unresolvable name cannot be shown to be external.
        raise ValueError(f"Could not resolve hostname {hostname!r}; refusing to fetch.")

    if not addr_info:
        raise ValueError(f"Hostname {hostname!r} resolved to no addresses; refusing to fetch.")

    for entry in addr_info:
        _check_resolved_address(str(entry[4][0]))


def _resolve_redirects(url: str, fetch_headers: Optional[Dict[str, str]] = None, timeout: int = 15) -> str:
    """
    Walk redirects manually, validating every hop, and return the final safe URL.

    This closes the redirect-based SSRF: validating only the URL a caller submitted is
    worthless when an attacker-controlled public host answers 302 with
    Location: http://169.254.169.254/.
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        validate_url_for_fetch(current)

        status, location = None, None
        try:
            resp = requests.head(
                current,
                headers=fetch_headers or {},
                timeout=timeout,
                allow_redirects=False,
            )
            status, location = resp.status_code, resp.headers.get("Location")
        except requests.RequestException:
            status = None

        # Some servers answer HEAD with 405/501 or refuse it outright while still
        # redirecting on GET. Falling back to a streamed GET reads the status line and
        # Location header without downloading a body, so those hosts keep working
        # instead of failing once redirects stopped being followed automatically.
        if status is None or status in (400, 403, 405, 501):
            try:
                with requests.get(
                    current,
                    headers=fetch_headers or {},
                    timeout=timeout,
                    allow_redirects=False,
                    stream=True,
                ) as probe:
                    status, location = probe.status_code, probe.headers.get("Location")
            except requests.RequestException:
                # Unreachable by our client. The URL is already validated and the real
                # fetch below runs with redirects disabled, so nothing unsafe follows.
                return current

        if status not in (301, 302, 303, 307, 308) or not location:
            return current

        current = urljoin(current, location)

    raise ValueError(f"Too many redirects (>{MAX_REDIRECTS}) while resolving {url!r}.")


def _capped_text(content: bytes, encoding: Optional[str] = None) -> str:
    """Decode at most MAX_RESPONSE_BYTES of a response body."""
    if len(content) > MAX_RESPONSE_BYTES:
        logger.warning("Response exceeded %d bytes; truncating.", MAX_RESPONSE_BYTES)
        content = content[:MAX_RESPONSE_BYTES]
    return content.decode(encoding or "utf-8", errors="replace")


def _content_length_exceeds_cap(headers: Any) -> bool:
    """True when a declared Content-Length is already over the ceiling."""
    try:
        declared = int(headers.get("Content-Length") or headers.get("content-length") or 0)
    except (TypeError, ValueError):
        return False
    return declared > MAX_RESPONSE_BYTES


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


    def _fetch_crawl4ai(self, url: str, timeout: int = 30) -> Optional[str]:
        """
        Level 2 fallback: Crawl4AI with a real headless Chrome browser.
        Bypasses Cloudflare, WAF, and JS-rendered pages that curl_cffi cannot handle.
        Runs the async crawler in a fresh event loop so it works from sync callers.
        """
        if not CRAWL4AI_AVAILABLE:
            return None
        try:
            import asyncio

            async def _crawl():
                browser_cfg = BrowserConfig(headless=True, verbose=False)
                run_cfg = CrawlerRunConfig(
                    cache_mode=CacheMode.BYPASS,
                    page_timeout=timeout * 1000,  # milliseconds
                    word_count_threshold=10,       # skip layout fragments & short boilerplate
                    exclude_external_links=True,   # stay in scope, reduce noise
                    process_iframes=False,         # skip embedded widgets
                )
                async with AsyncWebCrawler(config=browser_cfg) as crawler:
                    result = await crawler.arun(url=url, config=run_cfg)
                    if result.success:
                        # Prefer clean markdown; fall back to raw html if markdown empty
                        text = (result.markdown or "").strip()
                        if not text and result.html:
                            text = result.html
                        return text if text else None
                    return None

            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(asyncio.run, _crawl())
                        return future.result(timeout=timeout + 10)
                else:
                    return loop.run_until_complete(_crawl())
            except RuntimeError:
                return asyncio.run(_crawl())
        except Exception as e:
            logger.warning("Crawl4AI failed for %s: %s", url, e)
            return None

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
                    return html[:MAX_RESPONSE_BYTES]
            else:
                # Log the status only, never the upstream body: an error page can echo
                # request headers (including our Authorization header) back at us.
                logger.warning("Firecrawl returned HTTP %d for %s", resp.status_code, url)
        except Exception as e:
            logger.error("Firecrawl fallback failed for %s: %s", url, type(e).__name__)

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
                        return html[:MAX_RESPONSE_BYTES]
                else:
                    logger.warning("Firecrawl returned HTTP %d for %s (async)", resp.status_code, url)
        except Exception as e:
            logger.error("Firecrawl async fallback failed for %s: %s", url, type(e).__name__)

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

        Every redirect hop is re-validated against the SSRF policy before it is followed.
        """
        validate_url_for_fetch(url)

        # Check cache, keyed on the URL the caller asked for, before redirect resolution
        content_cache = get_content_cache()
        cached = content_cache.get(url, max_age=max_age, force_refresh=force_refresh)
        if cached is not None:
            logger.debug("Cache hit for %s; skipping network fetch.", url)
            return cached

        target = _resolve_redirects(url, self.browser_headers, timeout=timeout)
        html_result = None

        # Level 1: curl_cffi with Chrome 124 TLS impersonation
        if CURL_CFFI_AVAILABLE:
            try:
                resp = curl_requests.get(
                    target,
                    impersonate="chrome124",
                    headers=self.browser_headers,
                    timeout=timeout,
                    verify=True,
                    allow_redirects=False,
                )

                if _content_length_exceeds_cap(resp.headers):
                    logger.warning("Refusing oversized response from %s", target)
                else:
                    text = _capped_text(resp.content, getattr(resp, "encoding", None))
                    if resp.status_code == 200 and not is_challenge_page(resp.status_code, text):
                        html_result = text

                    if not html_result:
                        logger.warning(
                            "Level 1 curl_cffi got status %d or challenge for %s. Falling back to %s...",
                            resp.status_code, target,
                            _next_sync_fallback(bool(self.firecrawl_api_key))
                        )
            except Exception as e:
                logger.warning("Level 1 curl_cffi failed for %s: %s", target, e)

        # Level 2: Crawl4AI headless browser fallback (free, bypasses Cloudflare)
        # Returns clean markdown — significantly better signal-to-noise than raw HTML
        if not html_result and CRAWL4AI_AVAILABLE:
            logger.info("Attempting Level 2 Crawl4AI for %s...", target)
            c4a_text = self._fetch_crawl4ai(target, timeout=timeout)
            if c4a_text and not is_challenge_page(200, c4a_text):
                html_result = c4a_text
                logger.info("Level 2 Crawl4AI succeeded for %s (%d chars)", target, len(c4a_text))

        # Level 3: Firecrawl fallback (if key is set and levels 1+2 failed)
        if not html_result and self.firecrawl_api_key:
            logger.info("Attempting Level 3 Firecrawl for %s...", target)
            fc_html = self._fetch_firecrawl(target, timeout=timeout + 10)
            if fc_html:
                html_result = fc_html

        # Level 4: Resilient standard requests with browser headers, streamed under a cap
        if not html_result:
            logger.info("Attempting Level 4 standard requests for %s...", target)
            session = requests.Session()
            with session.get(
                target,
                headers=self.browser_headers,
                timeout=timeout,
                verify=True,
                allow_redirects=False,
                stream=True,
            ) as resp:
                resp.raise_for_status()
                if _content_length_exceeds_cap(resp.headers):
                    raise ValueError(
                        f"Response from {target} exceeds the {MAX_RESPONSE_BYTES} byte ceiling."
                    )
                body = bytearray()
                for chunk in resp.iter_content(chunk_size=65536):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        logger.warning("Truncating oversized response from %s", target)
                        break
                encoding = resp.encoding
                if "charset" not in (resp.headers.get("Content-Type", "") or "").lower():
                    encoding = resp.apparent_encoding or encoding
                html_result = _capped_text(bytes(body), encoding)

        # Cache successful fetch under the originally requested URL
        if html_result:
            content_cache.set(url, html_result)

        return html_result

    async def _resolve_redirects_async(self, url: str, timeout: int = 15) -> str:
        """Async twin of _resolve_redirects: validate each hop before following it."""
        current = url
        async with httpx.AsyncClient(
            timeout=float(timeout),
            follow_redirects=False,
            headers=self.browser_headers,
        ) as client:
            for _ in range(MAX_REDIRECTS + 1):
                validate_url_for_fetch(current)
                try:
                    resp = await client.head(current)
                except httpx.HTTPError:
                    # HEAD unsupported or refused; the GET runs with redirects disabled.
                    return current

                if resp.status_code not in (301, 302, 303, 307, 308):
                    return current
                location = resp.headers.get("Location")
                if not location:
                    return current
                current = urljoin(current, location)

        raise ValueError(f"Too many redirects (>{MAX_REDIRECTS}) while resolving {url!r}.")

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

        Every redirect hop is re-validated against the SSRF policy before it is followed.
        """
        validate_url_for_fetch(url)

        # Check Cache
        content_cache = get_content_cache()
        cached = content_cache.get(url, max_age=max_age, force_refresh=force_refresh)
        if cached is not None:
            logger.debug("Async cache hit for %s; skipping network fetch.", url)
            return cached

        target = await self._resolve_redirects_async(url, timeout=timeout)
        html_result = None

        # Level 1: curl_cffi AsyncSession with Chrome 124 TLS impersonation
        if CURL_CFFI_AVAILABLE:
            try:
                async with CurlAsyncSession(impersonate="chrome124") as session:
                    resp = await session.get(
                        target,
                        headers=self.browser_headers,
                        timeout=timeout,
                        verify=True,
                        allow_redirects=False,
                    )
                    if _content_length_exceeds_cap(resp.headers):
                        logger.warning("Refusing oversized response from %s", target)
                    else:
                        text = _capped_text(resp.content, getattr(resp, "encoding", None))
                        if resp.status_code == 200 and not is_challenge_page(resp.status_code, text):
                            html_result = text
                        else:
                            logger.warning(
                                "Async Level 1 got status %d or challenge for %s", resp.status_code, target
                            )
            except Exception as e:
                logger.warning("Async Level 1 curl_cffi failed for %s: %s", target, e)

        # Level 2: Firecrawl async fallback
        if not html_result and self.firecrawl_api_key:
            fc_html = await self._fetch_firecrawl_async(target, timeout=timeout + 10)
            if fc_html:
                html_result = fc_html

        # Level 3: httpx async fallback, streamed under a cap
        if not html_result:
            logger.info("Attempting Level 3 async httpx for %s...", target)
            async with httpx.AsyncClient(
                timeout=float(timeout),
                follow_redirects=False,
                headers=self.browser_headers,
            ) as client:
                async with client.stream("GET", target) as resp:
                    resp.raise_for_status()
                    if _content_length_exceeds_cap(resp.headers):
                        raise ValueError(
                            f"Response from {target} exceeds the {MAX_RESPONSE_BYTES} byte ceiling."
                        )
                    body = bytearray()
                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            logger.warning("Truncating oversized response from %s", target)
                            break
                    html_result = _capped_text(bytes(body), resp.encoding)

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
