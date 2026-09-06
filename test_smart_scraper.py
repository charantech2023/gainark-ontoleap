"""
Test suite for SmartScraper and Competitor Ontology Engine
"""

import sys
import logging
from scraper import smart_fetch, validate_url_for_fetch, is_challenge_page, SmartScraper
from models import CompetitorOntologyRequest
from competitive_alignment import extract_competitor_ontology
from pipeline import get_default_pipeline

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("test_smart_scraper")


def test_ssrf_protection():
    logger.info("Testing SSRF protection...")
    blocked_urls = [
        "http://127.0.0.1/admin",
        "http://localhost:8000/metrics",
        "http://169.254.169.254/computeMetadata/v1/",
        "ftp://example.com/file",
        "http://metadata.google.internal/"
    ]
    for u in blocked_urls:
        try:
            validate_url_for_fetch(u)
            assert False, f"Expected ValueError for SSRF URL: {u}"
        except ValueError:
            logger.info("  Correctly blocked SSRF URL: %s", u)

    # Valid URL should pass
    validate_url_for_fetch("https://example.com")
    logger.info("  SSRF validation passed for safe URL: https://example.com")


def test_challenge_detection():
    logger.info("Testing challenge detection...")
    assert is_challenge_page(403, "Forbidden")
    assert is_challenge_page(503, "Service Unavailable")
    assert is_challenge_page(200, "<html><head><title>Just a moment...</title></head><body>cf-turnstile-wrapper</body></html>")
    assert not is_challenge_page(200, "<html><head><title>Ordway Labs | Billing Automation</title></head><body>Enterprise billing platform</body></html>")
    logger.info("  Challenge detection tests passed!")


def test_smart_fetch_tls():
    logger.info("Testing smart_fetch with Chrome TLS impersonation...")
    html = smart_fetch("https://httpbin.org/html", timeout=15)
    assert len(html) > 100
    assert "<html" in html.lower()
    logger.info("  smart_fetch successfully retrieved %d bytes via Chrome TLS impersonation!", len(html))


def test_competitor_ontology_extraction():
    logger.info("Testing Competitor Ontology extraction...")
    pipeline = get_default_pipeline()
    req = CompetitorOntologyRequest(
        url="https://example.com",
        brand_name="ExampleCo",
        crawl_subpages=False
    )
    res = extract_competitor_ontology(req, pipeline)
    assert res.brand_name == "ExampleCo"
    assert res.url == "https://example.com"
    assert res.pages_analyzed >= 1
    logger.info("  Competitor ontology extraction passed! Summary: %s", res.ontology_summary)


if __name__ == "__main__":
    test_ssrf_protection()
    test_challenge_detection()
    test_smart_fetch_tls()
    test_competitor_ontology_extraction()
    logger.info("ALL TESTS PASSED SUCCESSFULLY!")
