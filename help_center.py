"""
GainARK OntoLeap - Help Centres Read Through Their Own API
==========================================================
A help centre is where a product's own vocabulary is written, and it is large: the
ordwaylabs.com sitemap lists 962 help URLs, and the 17 Sep 2026 crawl read 12 of them
before setting the kind aside. Reading it page by page costs a fetch per article.

Zendesk Guide, which hosts most SaaS help centres ("/hc/<locale>/articles/<id>-<slug>"),
publishes every public article, body included, at

    /api/v2/help_center/<locale>/articles.json?per_page=100

with no key. On 17 Sep 2026 support.ordwaylabs.com returned 830 articles in 9 requests.

So when a crawl meets a help host, this reads the whole centre in a few requests and
caches each article's body as a small page (scraper.source_cache_key). The crawl still
decides which articles to read; reading one converts that page instead of fetching it.
Converting all 830 up front took 21s for the 18 the crawl read. Where the cache is cold -
another instance picked the job up - the article is simply fetched as a page.
"""

import html as html_lib
import json
import logging
import re
from collections import Counter
from typing import Callable, Dict, Iterable, List, Optional
from urllib.parse import urlparse

from cache import get_content_cache
from scraper import smart_fetch, source_cache_key, validate_url_for_fetch

logger = logging.getLogger("gainark.help_center")

# 100 articles a request; 50 requests is the planner's whole candidate ceiling.
ZENDESK_PAGE_SIZE = 100
ZENDESK_MAX_REQUESTS = 50
ZENDESK_TIMEOUT = 20

_HC_LOCALE = re.compile(r"/hc/([a-z]{2}(?:-[a-z]{2})?)/", re.I)


def zendesk_locale(urls: Iterable[str], host: str) -> str:
    """The locale a host's help URLs use most, e.g. "en-us" from /hc/en-us/articles/..."""
    host = host.lower()
    seen = Counter()
    for url in urls:
        if urlparse(url).netloc.lower() == host:
            match = _HC_LOCALE.search(url)
            if match:
                seen[match.group(1).lower()] += 1
    return seen.most_common(1)[0][0] if seen else "en-us"


def article_source(article: Dict) -> str:
    """One API article as a page: its title and body, nothing around them."""
    title = html_lib.escape(article.get("title") or article.get("name") or "")
    body = article.get("body") or ""
    return "<html><head><title>%s</title></head><body><h1>%s</h1>%s</body></html>" % (title, title, body)


def read_zendesk(host: str, locale: str = "en-us",
                 fetch: Optional[Callable[..., str]] = None) -> List[Dict[str, str]]:
    """Every public article of a Zendesk help centre, cached for reading as lite pages.

    Returns [{url, title, section, labels}] in the API's order; empty when the host is not
    a Zendesk help centre or the API cannot be read. Never raises. `fetch` defaults to
    smart_fetch; the site crawl passes its own, as it does for sitemaps.
    """
    fetch = fetch or smart_fetch
    host = host.lower()
    url = "https://%s/api/v2/help_center/%s/articles.json?per_page=%d" % (host, locale, ZENDESK_PAGE_SIZE)
    cache = get_content_cache()
    articles: List[Dict[str, str]] = []
    for _ in range(ZENDESK_MAX_REQUESTS):
        try:
            validate_url_for_fetch(url)
            data = json.loads(fetch(url, timeout=ZENDESK_TIMEOUT) or "")
        except Exception as err:
            logger.debug("[HelpCenter] No Zendesk API at %s: %s", url, err)
            break
        if not isinstance(data, dict) or not isinstance(data.get("articles"), list):
            break
        for article in data["articles"]:
            if not isinstance(article, dict):
                continue
            page_url = article.get("html_url") or ""
            # Drafts and articles for signed-in segments are not what the public reads.
            if (article.get("draft") or article.get("user_segment_id")
                    or urlparse(page_url).netloc.lower() != host):
                continue
            cache.set(source_cache_key(page_url), article_source(article))
            articles.append({
                "url": page_url,
                "title": article.get("title") or "",
                "section": str(article.get("section_id") or ""),
                "labels": ", ".join(article.get("label_names") or []),
            })
        # Followed only on the same host: the next link is the API's to give, but where
        # it points is still ours to check.
        url = data.get("next_page") or ""
        if not url or urlparse(url).netloc.lower() != host:
            break
    if articles:
        logger.info("[HelpCenter] %s: %d articles read through the Zendesk API", host, len(articles))
    return articles
