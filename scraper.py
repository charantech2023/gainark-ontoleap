"""
GainARK OntoLeap — Smart Anti-Bot Scraper Engine
Combines:
1. Level 1 (Fast & Free): curl_cffi with Chrome 124 TLS (JA3/JA4) fingerprint impersonation
   and realistic browser navigation headers.
2. Anti-Bot Challenge Detection: Automatically identifies Cloudflare Turnstile, WAF 403/503,
   and captcha challenge pages.
3. Fallbacks, tried in order and skipped when unavailable. The sync and async cascades
   differ, so the level numbers are not interchangeable:
     sync   0 Jina Reader -> 1 curl_cffi -> 2 Crawl4AI -> 3 Firecrawl -> 4 standard requests
     async  0 Jina Reader -> 1 curl_cffi -> 2 Firecrawl -> 3 httpx
   Level 0 runs only when ONTOLEAP_JINA_READER is set. It returns the rendered DOM, so
   callers keep parsing HTML, and it is never used for robots.txt, sitemaps or specs.
   Crawl4AI is an optional import and is NOT in requirements.txt, so it is absent from
   the deployed image; Firecrawl runs only when FIRECRAWL_API_KEY is set. A deployment
   with neither goes straight from level 1 to plain HTTP.
5. Strict SSRF Protection: Blocks private IPs, localhost, and cloud metadata endpoints —
   on the initial URL *and on every redirect hop*, since a public host may redirect inward.
6. Response size ceiling: a single fetch cannot exhaust process memory.
"""

import os
import json
import time
import socket
import logging
import ipaddress
import threading
from typing import Optional, Dict, Any, Tuple
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


JINA_READER_ENDPOINT = "https://r.jina.ai/"

# Without a key Jina allows 20 requests a minute. Once it says no, every page of a crawl
# would ask again and be refused again, so the reader is skipped for this long instead.
JINA_COOLDOWN_SECONDS = _env_int("JINA_COOLDOWN_SECONDS", 60)

# Requests a minute, kept under Jina's ceilings: 20 without a key, 500 with a free or paid
# one. Exceeding them earns a 429 and a cooldown, so a crawl fetching pages in parallel
# waits its turn instead.
JINA_RPM_KEYED = _env_int("JINA_RPM", 450)
JINA_RPM_KEYLESS = _env_int("JINA_RPM_KEYLESS", 18)
# How long a fetch waits for its turn before reading the page directly instead.
JINA_RATE_WAIT_SECONDS = _env_int("JINA_RATE_WAIT_SECONDS", 20)

# A key Jina refuses (401 bad key, 402 no balance) is set aside for this long and reads
# continue without it, at the keyless rate. On 13 Sep 2026 the configured key had run out
# of balance; pausing the whole reader for that would have sent every page back to a
# direct fetch while Jina itself was still available.
JINA_KEY_RETRY_SECONDS = _env_int("JINA_KEY_RETRY_SECONDS", 900)

# What a "lite" read strips before Jina counts tokens. Measured on 13 Sep 2026 against the
# same pages: full HTML cost 120,000-190,000 tokens a page, markdown 5,000-8,500, and
# markdown with the page chrome and images removed 1,200-4,100. A 150-page crawl in HTML
# would spend roughly 25M tokens; the chrome is also the part extraction discards.
_JINA_LITE_REMOVE = "header, nav, footer, [role=navigation], [role=banner], [role=contentinfo]"

# Upstream statuses that Jina reports but a direct fetch might still get past: the reader's
# addresses can be blocked, or rate limited, where ours are not.
_JINA_RETRY_LOCALLY = {403, 429, 503}

# Jina renders these as HTML - a sitemap through its XSL, robots.txt inside a <pre> - which
# destroys the <loc> tags and directives the callers are reading them for.
_JINA_SKIP_SUFFIXES = (".txt", ".xml", ".json", ".yaml", ".yml", ".gz", ".pdf", ".csv", ".rss", ".atom")


# Docs platforms that build the page in the browser, keyed by host suffix: the element that
# holds the rendered article, which Jina is told to wait for before reading.
#
# Stoplight serves a loading shell; a direct fetch of ordwaylabs.stoplight.io reads 206
# characters. Jina without a wait returned the full article in 7 of 9 renders across three
# pages, took up to 71s, and read 19 and 220 characters on the other two. Waiting for the
# markdown viewer returned it in 9 of 9, in 9-15s. Waiting for any "h1" was no better than
# not waiting: the shell has a heading before the article arrives.
#
# ReadMe, GitBook and Mintlify serve their text in the HTML itself, so a direct fetch
# already reads them in full and they are not listed. A Stoplight site on a custom domain
# is not caught by its host.
#
# Each entry is (selector to wait for, class that proves it rendered). When the wait runs
# out Jina returns the shell anyway, with a 200.
_JINA_WAIT_FOR_SELECTOR = {
    "stoplight.io": (".sl-markdown-viewer", "sl-markdown-viewer"),
}

# How long Jina may wait for that element. Callers pass fetch timeouts of 6-15s, shorter
# than a render; renders measured 9-15s in one hour and 13-34s in the next, and at 30 one
# in six came back as the shell.
JINA_RENDER_TIMEOUT = 45


def _jina_wait_selector(url: str) -> Optional[Tuple[str, str]]:
    host = (urlparse(url).hostname or "").lower()
    for suffix, wait in _JINA_WAIT_FOR_SELECTOR.items():
        if host == suffix or host.endswith("." + suffix):
            return wait
    return None


def _jina_should_skip(url: str) -> bool:
    """True for documents that must be read as served, not as a browser renders them."""
    path = urlparse(url).path.lower()
    last = path.rstrip("/").rsplit("/", 1)[-1]
    return path.endswith(_JINA_SKIP_SUFFIXES) or "sitemap" in last


class _JinaKeyRefused(Exception):
    """Jina refused the API key (bad or out of balance); the read may be retried without it."""


class _RateLimiter:
    """At most `rpm` acquisitions in any sixty seconds, shared by every thread."""

    def __init__(self, rpm: int):
        self.rpm = max(1, rpm)
        self._stamps: list = []
        self._lock = threading.Lock()

    def acquire(self, max_wait: float) -> bool:
        deadline = time.monotonic() + max_wait
        while True:
            with self._lock:
                now = time.monotonic()
                self._stamps = [t for t in self._stamps if now - t < 60.0]
                if len(self._stamps) < self.rpm:
                    self._stamps.append(now)
                    return True
                wake = self._stamps[0] + 60.0
            if wake > deadline:
                return False
            time.sleep(max(0.05, min(wake - time.monotonic(), 1.0)))


def _markdown_page(data: Dict[str, Any]) -> str:
    """A Jina markdown read, as the minimal HTML every caller already parses.

    The page's own prose goes in <main>, rendered by markdown-it with raw HTML disabled so
    nothing in a page can inject markup. The links summary goes in a <nav>: link
    extraction reads every <a>, while trafilatura discards navigation, so the links reach
    the crawl frontier without reaching the text extraction reads.
    """
    import html as html_lib
    from markdown_it import MarkdownIt

    title = html_lib.escape(data.get("title") or "")
    description = html_lib.escape(data.get("description") or "", quote=True)
    body = MarkdownIt("commonmark", {"html": False}).render(data.get("content") or "")
    anchors = []
    for link in data.get("links") or []:
        if isinstance(link, (list, tuple)) and len(link) == 2 and isinstance(link[1], str):
            anchors.append('<a href="%s">%s</a>' % (html_lib.escape(link[1], quote=True),
                                                    html_lib.escape(link[0] or "")))
    return ('<html><head><title>%s</title><meta name="description" content="%s"></head>'
            '<body><main>%s</main><nav>%s</nav></body></html>'
            % (title, description, body, "".join(anchors)))


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

    def __init__(self, firecrawl_api_key: Optional[str] = None, jina_reader: Optional[bool] = None):
        self.firecrawl_api_key = firecrawl_api_key or os.environ.get("FIRECRAWL_API_KEY")
        if jina_reader is None:
            jina_reader = os.environ.get("ONTOLEAP_JINA_READER", "").strip().lower() in ("1", "true", "yes")
        self.jina_reader = jina_reader
        self.jina_api_key = os.environ.get("JINA_API_KEY") or None
        self._jina_paused_until = 0.0
        self._jina_key_suspended_until = 0.0
        self._jina_limits = {True: _RateLimiter(JINA_RPM_KEYED), False: _RateLimiter(JINA_RPM_KEYLESS)}
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


    def _jina_usable(self, url: str) -> bool:
        return (
            self.jina_reader
            and not _jina_should_skip(url)
            and time.monotonic() >= self._jina_paused_until
        )

    def _jina_key(self) -> Optional[str]:
        """The key to send, unless Jina has recently refused it."""
        if self.jina_api_key and time.monotonic() >= self._jina_key_suspended_until:
            return self.jina_api_key
        return None

    def _jina_request(self, url: str, timeout: int, force_refresh: bool,
                      lite: Optional[str] = None) -> Tuple[Dict[str, str], int]:
        """Headers for one Jina read, and how long to wait for the response.

        `lite` asks for markdown instead of rendered HTML: "full" keeps the page chrome
        (its navigation links are a crawl's map of the site), "trimmed" removes it.
        """
        # JSON rather than the plain body, because only the JSON carries the target's own
        # status: a missing page otherwise comes back as 200 with the site's 404 template.
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Return-Format": "markdown" if lite else "html",
        }
        if lite:
            headers["X-With-Links-Summary"] = "all"
            headers["X-Retain-Images"] = "none"
            if lite == "trimmed":
                headers["X-Remove-Selector"] = _JINA_LITE_REMOVE
        wait_for = _jina_wait_selector(url)
        if wait_for:
            headers["X-Wait-For-Selector"] = wait_for[0]
            timeout = max(timeout, JINA_RENDER_TIMEOUT)
        headers["X-Timeout"] = str(timeout)
        key = self._jina_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        if force_refresh:
            headers["X-No-Cache"] = "true"
        return headers, timeout + 15

    def _jina_turn(self, headers: Dict[str, str]) -> bool:
        """Wait for a request slot at the rate this read's key allows."""
        if self._jina_limits["Authorization" in headers].acquire(JINA_RATE_WAIT_SECONDS):
            return True
        logger.info("Jina Reader rate limit reached; reading directly instead.")
        return False

    def _jina_result(self, url: str, status_code: int, body: bytes,
                     lite: Optional[str] = None, keyed: bool = False) -> Optional[str]:
        """Turn a Jina response into page HTML, None to fall back, or an HTTPError.

        Raises only when the target itself answered with a definitive error, so a missing
        page fails the way it does on a direct fetch instead of being read as content.
        A refused key raises _JinaKeyRefused, so the caller can retry without it.
        """
        if status_code != 200:
            if keyed and status_code in (401, 402):
                # The key is bad or out of balance; Jina itself is fine. Read on without
                # the key rather than sending every page back to a direct fetch.
                self._jina_key_suspended_until = time.monotonic() + JINA_KEY_RETRY_SECONDS
                logger.warning(
                    "Jina Reader refused the API key (HTTP %d); reading without it at %d "
                    "requests a minute for %ds.", status_code, JINA_RPM_KEYLESS, JINA_KEY_RETRY_SECONDS)
                raise _JinaKeyRefused()
            # Rate limited, or refused without a key: every later page would be refused
            # the same way, so stop asking for a while.
            if status_code in (401, 402, 429):
                self._jina_paused_until = time.monotonic() + JINA_COOLDOWN_SECONDS
                logger.warning(
                    "Jina Reader returned HTTP %d; pausing it for %ds and fetching directly.",
                    status_code, JINA_COOLDOWN_SECONDS
                )
            else:
                logger.warning("Jina Reader returned HTTP %d for %s; fetching directly.", status_code, url)
            return None

        # JSON escaping inflates the page, hence the allowance above the page ceiling.
        if len(body) > 2 * MAX_RESPONSE_BYTES:
            logger.warning("Jina Reader response for %s exceeds the size ceiling; fetching directly.", url)
            return None
        try:
            data = json.loads(body).get("data") or {}
        except (ValueError, AttributeError):
            logger.warning("Jina Reader sent an unreadable response for %s; fetching directly.", url)
            return None

        upstream = data.get("httpStatus")
        if isinstance(upstream, int) and upstream >= 400:
            if upstream in _JINA_RETRY_LOCALLY:
                logger.info("Target answered Jina Reader with %d for %s; fetching directly.", upstream, url)
                return None
            raise requests.HTTPError(f"{upstream} error for url: {url} (reported by Jina Reader)")

        if lite:
            if not (data.get("content") or "").strip():
                logger.info("Jina Reader got no usable text for %s; fetching directly.", url)
                return None
            html = _markdown_page(data)
        else:
            html = data.get("html") or ""
        if not html.strip() or is_challenge_page(200, html):
            logger.info("Jina Reader got no usable page for %s; fetching directly.", url)
            return None
        logger.debug("Level 0 Jina Reader succeeded for %s (%d chars, %s tokens)",
                     url, len(html), (data.get("usage") or {}).get("tokens"))
        return html[:MAX_RESPONSE_BYTES]

    def _fetch_jina(self, url: str, timeout: int, force_refresh: bool,
                    lite: Optional[str] = None) -> Optional[str]:
        """Level 0: read the page through Jina Reader, which fetches and renders it remotely."""
        attempt = 1
        key_retried = False
        while attempt <= 2:
            headers, wait = self._jina_request(url, timeout, force_refresh, lite)
            if not self._jina_turn(headers):
                return None
            try:
                # POST, so a query string in the target cannot be confused with the reader's own.
                resp = requests.post(JINA_READER_ENDPOINT, json={"url": url}, headers=headers, timeout=wait)
            except requests.RequestException as e:
                logger.warning("Jina Reader failed for %s: %s", url, type(e).__name__)
                return None
            try:
                html = self._jina_result(url, resp.status_code, resp.content, lite,
                                         keyed="Authorization" in headers)
            except _JinaKeyRefused:
                if key_retried:
                    return None
                key_retried = True
                continue  # same attempt, now without the key
            if not self._render_unfinished(url, html, attempt):
                return html
            attempt += 1
        return None

    async def _fetch_jina_async(self, url: str, timeout: int, force_refresh: bool) -> Optional[str]:
        headers, wait = self._jina_request(url, timeout, force_refresh)
        for attempt in (1, 2):
            try:
                async with httpx.AsyncClient(timeout=float(wait)) as client:
                    resp = await client.post(JINA_READER_ENDPOINT, json={"url": url}, headers=headers)
            except httpx.HTTPError as e:
                logger.warning("Jina Reader failed for %s (async): %s", url, type(e).__name__)
                return None
            try:
                html = self._jina_result(url, resp.status_code, resp.content,
                                         keyed="Authorization" in headers)
            except _JinaKeyRefused:
                # The key is now set aside; this read falls back, the next goes keyless.
                return None
            if not self._render_unfinished(url, html, attempt):
                return html
        return None

    @staticmethod
    def _render_unfinished(url: str, html: Optional[str], attempt: int) -> bool:
        """True when a docs page came back as its loading shell, so it must not be kept.

        A shell kept here is cached as the page, and every later read gets the shell too.
        One retry, because the renders that missed were slow rather than broken.
        """
        wait_for = _jina_wait_selector(url)
        if not html or not wait_for or wait_for[1] in html:
            return False
        if attempt == 1:
            logger.info("Jina Reader returned %s before %s rendered; retrying once.", url, wait_for[0])
        else:
            logger.warning("Jina Reader could not render %s (no %s); fetching directly.", url, wait_for[0])
        return True

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
        max_age: Optional[int] = None,
        lite: Optional[str] = None,
    ) -> str:
        """
        Synchronously fetches a URL's HTML content.
        Checks in-memory/disk cache first before making network calls.
        Uses Chrome TLS impersonation, checks for anti-bot blocks,
        and falls back to Firecrawl or standard requests as needed.

        `lite` ("full" or "trimmed") is for callers that need a page's text and links but
        not its markup - the site crawl. Through Jina it reads markdown rendered back into
        minimal HTML, at a small fraction of the tokens; without Jina it changes nothing,
        because a direct fetch costs the same either way. A lite read is cached apart from
        the full page, so a caller that needs meta tags or embedded schema never gets one.
        A page whose docs viewer must render first is always read in full.

        Every redirect hop is re-validated against the SSRF policy before it is followed.
        """
        validate_url_for_fetch(url)

        # Check cache, keyed on the URL the caller asked for, before redirect resolution
        content_cache = get_content_cache()
        if lite and _jina_wait_selector(url):
            lite = None
        if lite:
            lite_key = "%s#jina-%s" % (url, lite)
            cached = content_cache.get(lite_key, max_age=max_age, force_refresh=force_refresh)
            if cached is not None:
                return cached
        cached = content_cache.get(url, max_age=max_age, force_refresh=force_refresh)
        if cached is not None:
            logger.debug("Cache hit for %s; skipping network fetch.", url)
            return cached

        # Level 0: Jina Reader. Ahead of redirect resolution, because when it succeeds the
        # site is never contacted from here at all - redirects included, which Jina follows.
        if self._jina_usable(url):
            jina_html = self._fetch_jina(url, timeout, force_refresh, lite)
            if jina_html:
                content_cache.set(lite_key if lite else url, jina_html)
                return jina_html

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

        if self._jina_usable(url):
            jina_html = await self._fetch_jina_async(url, timeout, force_refresh)
            if jina_html:
                content_cache.set(url, jina_html)
                return jina_html

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
    max_age: Optional[int] = None,
    lite: Optional[str] = None,
) -> str:
    """Synchronous helper function to fetch a URL using the smart scraper and cache."""
    return get_smart_scraper().fetch_html(url, timeout=timeout, force_refresh=force_refresh,
                                          max_age=max_age, lite=lite)


async def smart_fetch_async(
    url: str,
    timeout: int = 15,
    force_refresh: bool = False,
    max_age: Optional[int] = None
) -> str:
    """Asynchronous helper function to fetch a URL using the smart scraper and cache."""
    return await get_smart_scraper().fetch_html_async(url, timeout=timeout, force_refresh=force_refresh, max_age=max_age)
