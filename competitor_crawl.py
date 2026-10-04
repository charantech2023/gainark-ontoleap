"""
competitor_crawl.py - crawl a customer's confirmed competitors.

Phase 2 of COMPETITOR_PROFILES_DESIGN.md, and the competitor half of
BUYER_SITUATIONS_DESIGN.md. For each competitor competitor_set lists as crawlable:

1. Claims. The customer's own crawl, unchanged - site_graph's planner, extraction and
   resolution, persisted as a run graph under the competitor's domain - because any
   difference between the two code paths would measure the pipeline, not the companies
   (design §3). The planner already reads product, pricing, integrations and security
   first and gives editorial 6% of the budget.
2. Situations. The planner gives customer stories 8% of the budget (two pages of 25),
   too few to learn from. So up to STORY_PAGES of the customer stories the crawl
   discovered are read separately, by buyer_stories.read_story with the competitor as the
   vendor, and stored as the competitor's situations: the buyers a customer lost.

A competitor whose pages cannot be read - a bot check, a refusal - is reported with what
was attempted, never worked around (recurly.com, 4 Oct 2026).
"""

import logging
import re
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger("gainark.competitor_crawl")

CLAIM_PAGES = 25      # design §11.2
STORY_PAGES = 10      # BUYER_SITUATIONS_DESIGN.md §12.4

# The index page of a customer-story section, which lists stories and tells none.
_HUB_SEGMENTS = {"customers", "customer", "customer-stories", "case-studies", "case-study",
                 "success-stories", "stories", "testimonials"}


def story_urls(plan: Dict[str, Any], limit: int = STORY_PAGES) -> List[str]:
    """Customer stories the crawl discovered, in the order the planner found them, hub
    pages left out."""
    out = []
    for cand in sorted(plan.get("candidates", {}).values(), key=lambda c: c.get("order", 0)):
        if cand.get("kind") != "customers":
            continue
        segments = [s for s in urlparse(cand["url"]).path.split("/") if s]
        if not segments or segments[-1].lower() in _HUB_SEGMENTS:
            continue
        out.append(cand["url"])
        if len(out) >= limit:
            break
    return out


def _brand(entry: Dict[str, Any]) -> str:
    """The competitor's current name: the form its domain carries ("Maxio", not
    "SaaSOptics (Maxio)")."""
    from competitor_set import domain_names
    forms = entry.get("forms") or [entry["name"]]
    return next((f for f in forms if domain_names(entry["domain"], [f])), forms[0])


def crawl_competitor(entry: Dict[str, Any], vertical_id: str, claim_pages: int = CLAIM_PAGES,
                     story_pages: int = STORY_PAGES, read: Optional[Callable] = None,
                     page_text: Optional[Callable[[str], str]] = None,
                     db_path: Optional[str] = None) -> Dict[str, Any]:
    """Crawl one competitor for claims and situations. Never raises for a site that cannot
    be read; the result says what happened."""
    import buyer_situations
    import buyer_stories
    from site_graph import assemble_site_kg, crawl_slice, new_crawl_state

    domain, brand = entry["domain"], _brand(entry)
    result: Dict[str, Any] = {"competitor": entry["name"], "brand": brand, "domain": domain}

    state = new_crawl_state("https://%s" % domain)
    try:
        crawl_slice(state, vertical_id, claim_pages)
        kg = assemble_site_kg(state, vertical_id, claim_pages, persist=True)
    except Exception as err:
        result.update(status="unreadable", reason=str(err)[:300],
                      pages_attempted=len(state.get("crawled", [])) + len(state.get("failed", [])),
                      failed=[f if isinstance(f, str) else f.get("url") for f in state.get("failed", [])][:10])
        logger.warning("[Competitors] %s could not be crawled: %s", domain, err)
        return result

    proven = [e for e in kg.edges if e.provenance_sentence and e.source_url]
    concept_claims = {e.target_id for e in proven if e.target_id and "/concept/" in e.target_id}
    result.update(
        status="crawled", pages_read=kg.pages_crawled, pages_failed=kg.pages_failed,
        failed_reasons=sorted({(f.error or "")[:120] for f in kg.failed_pages})[:5],
        claims_with_proof=len(proven), concepts_claimed=len(concept_claims),
        by_kind={k: v.get("read", 0) for k, v in (kg.crawl_summary or {}).items()
                 if isinstance(v, dict) and v.get("read")},
    )

    urls = story_urls(state["plan"], story_pages)
    read = read or buyer_stories.read_story
    page_text = page_text or buyer_stories.page_text
    stories = []
    for url in urls:
        try:
            story = read(url, page_text(url), [], brand)
        except Exception as err:
            logger.info("[Competitors] Could not read story %s: %s", url, err)
            continue
        stories.append({"source_url": url, "voice": story.voice,
                        "parts": {k: {"text": p.text, "quote": p.quote} for k, p in story.parts.items()},
                        "dropped_parts": story.dropped})
    situations = buyer_situations.read_site(domain, vertical_id, stories=stories, path=db_path)
    result.update(story_pages_found=len(urls), stories_with_parts=sum(1 for s in stories if s["parts"]),
                  situations=situations["report"], situations_graph=situations["graph_id"])
    return result


def crawl_set(site: str, vertical_id: str, **kwargs) -> List[Dict[str, Any]]:
    """Every crawlable competitor of a customer site."""
    import competitor_set
    return [crawl_competitor(entry, vertical_id, **kwargs) for entry in competitor_set.crawlable(site)]


if __name__ == "__main__":
    import json
    import sys
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(crawl_set(sys.argv[1], sys.argv[2]), indent=1, ensure_ascii=False, default=str))
