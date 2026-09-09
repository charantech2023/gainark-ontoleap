"""
OntoLeap — Autonomous Technical Proof Discovery Engine

Resolves the "No API Documentation" gap. When a company or competitor provides
no OpenAPI specification, gates their developer portal behind authentication, or
blocks scrapers with Cloudflare Turnstile, this module autonomously discovers and
ingests technical evidence from open public registries:

1. Public Trust Centers & Compliance Registries:
   - Probes trust.<domain>, <domain>/trust, <domain>/security, <domain>/compliance
   - Extracts certified compliance standards (SOC 2, ISO 27001, HIPAA, PCI-DSS, GDPR)
   - Extracts enterprise security controls (AES-256, TLS 1.3, SAML/SSO, RBAC, MFA)

2. Open Package Registries (npm & PyPI):
   - Queries public JSON endpoints on registry.npmjs.org and pypi.org
   - Identifies official client SDKs (Python, Node.js, Ruby, Go, Java)
   - Extracts exposed methods, module parameters, and integration capabilities

3. Public Changelogs & Product Release Notes:
   - Probes <domain>/changelog, updates.<domain>, <domain>/releases
   - Extracts shipped functional capabilities and recent enterprise features

4. Fast OpenAPI / Swagger Spec Probing:
   - Tests common standard spec paths (/openapi.json, /v1/openapi.json, /api-docs)

All extracted capabilities are returned as canonical SemanticTriples with W3C PROV-O
attribution to their exact public proof origins.
"""

import concurrent.futures
import json
import logging
import os
import re
import socket
import time
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from models import SemanticTriple
from scraper import smart_fetch, validate_url_for_fetch

logger = logging.getLogger(__name__)

# Standard request headers for public registry APIs
_REGISTRY_HEADERS = {
    "User-Agent": "GainARK-OntoLeap-ProofDiscovery/2.2 (+https://gainark.com/ontoleap)",
    "Accept": "application/json, text/plain, */*",
}

# Recognized enterprise compliance standards
_COMPLIANCE_STANDARDS_MAP = [
    (r"\bSOC\s*2\s*Type\s*(?:II|2)\b", "SOC 2 Type II"),
    (r"\bSOC\s*2\s*Type\s*(?:I|1)\b", "SOC 2 Type I"),
    (r"\bSOC\s*2\b", "SOC 2"),
    (r"\bSOC\s*1\b", "SOC 1"),
    (r"\bSOC\s*3\b", "SOC 3"),
    (r"\bISO\s*/?\s*IEC\s*27001(?::\d{4})?\b", "ISO 27001"),
    (r"\bISO\s*27001\b", "ISO 27001"),
    (r"\bISO\s*27701\b", "ISO 27701"),
    (r"\bHIPAA\b|\bHITECH\b", "HIPAA"),
    (r"\bPCI\s*-?\s*DSS\s*Level\s*1\b", "PCI-DSS Level 1"),
    (r"\bPCI\s*-?\s*DSS\b", "PCI-DSS"),
    (r"\bGDPR\b", "GDPR"),
    (r"\bCCPA\b|\bCPRA\b", "CCPA"),
    (r"\bFedRAMP\b", "FedRAMP"),
    (r"\bStateRAMP\b", "StateRAMP"),
    (r"\bCSA\s*STAR\b", "CSA STAR"),
]

# Recognized security practices
_SECURITY_PRACTICES_MAP = [
    (r"\bAES\s*-?\s*256\b", "AES-256 Encryption"),
    (r"\bTLS\s*1\.[23]\b", "TLS 1.3 Transport Security"),
    (r"\bSAML\s*2\.0\b|\bSSO\b|\bSingle\s*Sign\s*-?\s*On\b", "SAML 2.0 / SSO"),
    (r"\bMFA\b|\bMulti\s*-?\s*Factor\s*Auth\w*\b|\b2FA\b", "Multi-Factor Authentication"),
    (r"\bRBAC\b|\bRole\s*-?\s*Based\s*Access\b", "Role-Based Access Control"),
    (r"\bPenetration\s*Test\w*\b|\bPentest\w*\b", "Annual Penetration Testing"),
    (r"\bVulnerability\s*Disclosure\b|\bBug\s*Bounty\b", "Vulnerability Disclosure Program"),
]

_COMMON_SPEC_PATHS = [
    "/openapi.json",
    "/v1/openapi.json",
    "/v2/openapi.json",
    "/swagger.json",
    "/v1/swagger.json",
    "/api-docs/openapi.json",
    "/api/openapi.json",
]


def _host_resolves(url: str) -> bool:
    """Quick DNS check to prevent socket timeouts on non-existent hosts."""
    try:
        host = urlparse(url).netloc.split(":")[0]
        if not host:
            return False
        socket.getaddrinfo(host, None, socket.AF_INET)
        return True
    except (socket.gaierror, socket.herror, Exception):
        return False


def discover_trust_center(domain: str, brand: str, timeout: int = 4) -> Tuple[List[SemanticTriple], Optional[Dict[str, Any]]]:
    """
    Probes public Trust Centers and compliance surfaces:
    - https://trust.{domain}
    - https://{domain}/trust
    - https://{domain}/security
    - https://{domain}/compliance
    """
    clean_domain = domain.replace("https://", "").replace("http://", "").strip("/").split("/")[0]
    if clean_domain.startswith("www."):
        clean_domain = clean_domain[4:]

    candidate_urls = [
        f"https://trust.{clean_domain}",
        f"https://{clean_domain}/trust",
        f"https://{clean_domain}/security",
        f"https://{clean_domain}/compliance",
    ]

    triples: List[SemanticTriple] = []
    matched_standards: Set[str] = set()
    matched_practices: Set[str] = set()
    found_url: Optional[str] = None

    for url in candidate_urls:
        if not _host_resolves(url):
            continue
        try:
            validate_url_for_fetch(url)
            # Quick HEAD check to skip 404/redirect pages before full fetch
            try:
                import requests as _req
                head_resp = _req.head(url, timeout=timeout, allow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
                if head_resp.status_code in (404, 410, 403):
                    logger.debug("Trust Center probe: %s returned %d, skipping", url, head_resp.status_code)
                    continue
            except Exception:
                pass  # If HEAD fails, still try full fetch
            html = smart_fetch(url, timeout=timeout)
            if not html or len(html) < 200:
                continue

            # Strip non-content elements
            soup = BeautifulSoup(html, "html.parser")
            for el in soup(["script", "style", "nav", "footer"]):
                el.decompose()
            text = soup.get_text(separator=" ", strip=True)

            # Check for compliance standards
            for pattern, std_name in _COMPLIANCE_STANDARDS_MAP:
                if re.search(pattern, text, re.IGNORECASE) and std_name not in matched_standards:
                    matched_standards.add(std_name)
                    triples.append(
                        SemanticTriple(
                            subject=brand,
                            predicate="compliesWith",
                            object=std_name,
                            provenance=f"trust_center:{url}",
                        )
                    )

            # Check for security practices
            for pattern, practice in _SECURITY_PRACTICES_MAP:
                if re.search(pattern, text, re.IGNORECASE) and practice not in matched_practices:
                    matched_practices.add(practice)
                    triples.append(
                        SemanticTriple(
                            subject=brand,
                            predicate="supportsSecurityPractice",
                            object=practice,
                            provenance=f"trust_center:{url}",
                        )
                    )

            if matched_standards or matched_practices:
                found_url = url
                logger.info(
                    "Trust Center discovery: found %d standards, %d practices at %s",
                    len(matched_standards),
                    len(matched_practices),
                    url,
                )
                break
        except Exception as e:
            logger.debug("Trust Center probe error on %s: %s", url, e)

    proof_info = None
    if found_url:
        proof_info = {
            "source_type": "trust_center",
            "url": found_url,
            "title": f"{brand} Trust & Compliance Center",
            "status": "verified",
            "capabilities_count": len(triples),
            "standards": sorted(list(matched_standards)),
            "practices": sorted(list(matched_practices)),
        }

    return triples, proof_info


def discover_public_sdks(brand: str, domain: str, timeout: int = 4) -> Tuple[List[SemanticTriple], List[Dict[str, Any]]]:
    """
    Probes open package registries (npm & PyPI) for official client SDKs.
    Extracts supported languages, client methods, and integration capabilities.
    """
    brand_slug = re.sub(r"[^a-zA-Z0-9-]", "", brand.lower())
    clean_domain = domain.replace("https://", "").replace("http://", "").strip("/").split("/")[0]
    if clean_domain.startswith("www."):
        clean_domain = clean_domain[4:]

    triples: List[SemanticTriple] = []
    proof_sources: List[Dict[str, Any]] = []

    # 1. Probe PyPI (Python Package Index)
    pypi_candidates = [brand_slug, f"{brand_slug}-python", f"{brand_slug}-sdk", f"{brand_slug}-client"]
    for pkg in pypi_candidates:
        pypi_url = f"https://pypi.org/pypi/{pkg}/json"
        try:
            resp = requests.get(pypi_url, headers=_REGISTRY_HEADERS, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                info = data.get("info", {})
                home = (info.get("home_page") or "") + " " + (info.get("project_url") or "")
                summary = info.get("summary") or ""
                # Verify affiliation with brand or domain
                if (
                    clean_domain in home.lower()
                    or brand.lower() in summary.lower()
                    or brand.lower() in (info.get("author") or "").lower()
                ):
                    triples.append(
                        SemanticTriple(
                            subject=brand,
                            predicate="providesSdk",
                            object="Python SDK",
                            provenance=f"pypi:{pkg}",
                        )
                    )
                    # Check for integrations or capabilities in keywords/summary
                    combined_txt = f"{summary} {' '.join(info.get('keywords') or [])}"
                    if any(k in combined_txt.lower() for k in ["async", "asyncio"]):
                        triples.append(
                            SemanticTriple(
                                subject=brand,
                                predicate="supportsProtocol",
                                object="Async / AsyncIO",
                                provenance=f"pypi:{pkg}",
                            )
                        )
                    if any(k in combined_txt.lower() for k in ["webhook", "events"]):
                        triples.append(
                            SemanticTriple(
                                subject=brand,
                                predicate="supportsProtocol",
                                object="Webhooks API",
                                provenance=f"pypi:{pkg}",
                            )
                        )

                    proof_sources.append(
                        {
                            "source_type": "public_sdk",
                            "registry": "PyPI",
                            "package_name": pkg,
                            "url": f"https://pypi.org/project/{pkg}/",
                            "version": info.get("version", ""),
                            "description": summary,
                            "status": "verified",
                        }
                    )
                    break
        except Exception as e:
            logger.debug("PyPI probe error for %s: %s", pkg, e)

    # 2. Probe npm (Node.js / TypeScript Package Registry)
    npm_candidates = [brand_slug, f"{brand_slug}-node", f"{brand_slug}-sdk", f"@{brand_slug}/sdk"]
    for pkg in npm_candidates:
        npm_url = f"https://registry.npmjs.org/{pkg}"
        try:
            resp = requests.get(npm_url, headers=_REGISTRY_HEADERS, timeout=timeout)
            if resp.status_code == 200:
                data = resp.json()
                desc = data.get("description") or ""
                homepage = data.get("homepage") or ""
                author = str(data.get("author") or "")

                if (
                    clean_domain in homepage.lower()
                    or brand.lower() in desc.lower()
                    or brand.lower() in author.lower()
                ):
                    triples.append(
                        SemanticTriple(
                            subject=brand,
                            predicate="providesSdk",
                            object="Node.js / TypeScript SDK",
                            provenance=f"npm:{pkg}",
                        )
                    )
                    latest_tag = (data.get("dist-tags") or {}).get("latest", "")
                    proof_sources.append(
                        {
                            "source_type": "public_sdk",
                            "registry": "npm",
                            "package_name": pkg,
                            "url": f"https://www.npmjs.com/package/{pkg}",
                            "version": latest_tag,
                            "description": desc,
                            "status": "verified",
                        }
                    )
                    break
        except Exception as e:
            logger.debug("npm probe error for %s: %s", pkg, e)

    return triples, proof_sources


def discover_github_evidence(
    brand: str,
    domain: str,
    timeout: int = 4
) -> Tuple[List[SemanticTriple], Optional[Dict[str, Any]]]:
    """
    Probes public GitHub organization repositories for official SDKs, OpenAPI specs,
    and ecosystem connectors.
    Unauthenticated up to 60 req/hr; uses GITHUB_TOKEN (up to 5,000 req/hr) when set.
    """
    brand_slug = re.sub(r"[^a-zA-Z0-9-]", "", brand.lower())
    clean_domain = domain.replace("https://", "").replace("http://", "").strip("/").split("/")[0]
    if clean_domain.startswith("www."):
        clean_domain = clean_domain[4:]
    domain_slug = clean_domain.split(".")[0].lower()

    candidates: List[str] = []
    for c in [brand_slug, domain_slug]:
        if c and c not in candidates:
            candidates.append(c)

    triples: List[SemanticTriple] = []
    proof_source: Optional[Dict[str, Any]] = None

    headers = dict(_REGISTRY_HEADERS)
    headers["Accept"] = "application/vnd.github+json"
    gh_token = os.environ.get("GITHUB_TOKEN", "").strip()
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    for org in candidates:
        api_url = f"https://api.github.com/orgs/{org}/repos?per_page=15&sort=pushed"
        try:
            validate_url_for_fetch(api_url)
            resp = requests.get(api_url, headers=headers, timeout=timeout)
            if resp.status_code != 200:
                if resp.status_code == 404:
                    user_api_url = f"https://api.github.com/users/{org}/repos?per_page=15&sort=pushed"
                    validate_url_for_fetch(user_api_url)
                    resp = requests.get(user_api_url, headers=headers, timeout=timeout)
                    if resp.status_code != 200:
                        continue
                else:
                    continue

            repos = resp.json()
            if not isinstance(repos, list) or not repos:
                continue

            extracted_count = 0
            repo_names = []
            languages = set()

            for r in repos:
                if not isinstance(r, dict):
                    continue
                r_name = r.get("name", "")
                r_desc = r.get("description") or ""
                r_lang = r.get("language") or ""
                repo_names.append(r_name)
                if r_lang:
                    languages.add(r_lang)

                lower_name = r_name.lower()
                lower_desc = r_desc.lower()

                # 1. Official SDKs
                if any(x in lower_name for x in ["-sdk", "-python", "-node", "-go", "-java", "-ruby", "-php", "client", "sdk"]):
                    sdk_name = f"{r_lang} SDK" if r_lang else f"{r_name} SDK"
                    triples.append(
                        SemanticTriple(
                            subject=brand,
                            predicate="providesSdk",
                            object=sdk_name,
                            confidence=0.85,
                            source_type="technical_truth",
                            provenance=f"github:{org}/{r_name}",
                            evidence_sentence=f"GitHub repository {org}/{r_name}: {r_desc}" if r_desc else f"GitHub repository {org}/{r_name}"
                        )
                    )
                    extracted_count += 1

                # 2. OpenAPI / API Specification
                if any(x in lower_name for x in ["openapi", "swagger", "api-spec", "api-docs"]):
                    triples.append(
                        SemanticTriple(
                            subject=brand,
                            predicate="providesApi",
                            object="OpenAPI Specification",
                            confidence=0.90,
                            source_type="technical_truth",
                            provenance=f"github:{org}/{r_name}",
                            evidence_sentence=f"API specification repository {org}/{r_name}"
                        )
                    )
                    extracted_count += 1

                # 3. Connectors & Integrations
                for partner in ["Salesforce", "NetSuite", "HubSpot", "Slack", "Jira", "Stripe", "Kubernetes", "Terraform", "AWS", "Google Cloud", "Azure"]:
                    if partner.lower() in lower_name or partner.lower() in lower_desc:
                        triples.append(
                            SemanticTriple(
                                subject=brand,
                                predicate="integratesWith",
                                object=partner,
                                confidence=0.85,
                                source_type="technical_truth",
                                provenance=f"github:{org}/{r_name}",
                                evidence_sentence=f"Integration repository {org}/{r_name}: {r_desc}" if r_desc else f"Integration repository {org}/{r_name}"
                            )
                        )
                        extracted_count += 1

            proof_source = {
                "source_type": "github_org",
                "org": org,
                "url": f"https://github.com/{org}",
                "public_repos_evaluated": len(repos),
                "languages": sorted(list(languages)),
                "capabilities_count": extracted_count,
                "status": "verified" if extracted_count > 0 else "unverified"
            }
            break  # Found matching org
        except Exception as ex:
            logger.debug("GitHub proof discovery error for %s: %s", org, ex)

    return triples, proof_source


def discover_public_changelog(domain: str, brand: str, pipeline=None, timeout: int = 4) -> Tuple[List[SemanticTriple], Optional[Dict[str, Any]]]:
    """
    Probes public changelogs and release notes:
    - https://{domain}/changelog
    - https://updates.{domain}
    - https://{domain}/releases
    """
    clean_domain = domain.replace("https://", "").replace("http://", "").strip("/").split("/")[0]
    if clean_domain.startswith("www."):
        clean_domain = clean_domain[4:]

    candidate_urls = [
        f"https://{clean_domain}/changelog",
        f"https://updates.{clean_domain}",
        f"https://{clean_domain}/releases",
    ]

    triples: List[SemanticTriple] = []
    found_url: Optional[str] = None
    features_found: List[str] = []

    for url in candidate_urls:
        if not _host_resolves(url):
            continue
        try:
            validate_url_for_fetch(url)
            html = smart_fetch(url, timeout=timeout)
            if not html or len(html) < 250:
                continue

            soup = BeautifulSoup(html, "html.parser")
            headings = [h.get_text(strip=True) for h in soup.find_all(["h1", "h2", "h3"]) if len(h.get_text(strip=True)) > 5]
            if not headings:
                continue

            found_url = url
            known_integrations = []
            if pipeline and hasattr(pipeline, "config") and pipeline.config:
                known_integrations = getattr(pipeline.config, "known_integrations", [])

            for h_text in headings[:15]:
                for integ in known_integrations:
                    if re.search(r"\b" + re.escape(integ) + r"\b", h_text, re.IGNORECASE):
                        triples.append(
                            SemanticTriple(
                                subject=brand,
                                predicate="integratesWith",
                                object=integ,
                                provenance=f"changelog:{url}",
                            )
                        )
                        features_found.append(f"Integration with {integ}")

            logger.info("Changelog discovery: found %d capabilities at %s", len(triples), url)
            break
        except Exception as e:
            logger.debug("Changelog probe error on %s: %s", url, e)

    proof_info = None
    if found_url:
        proof_info = {
            "source_type": "changelog",
            "url": found_url,
            "title": f"{brand} Public Product Changelog",
            "status": "verified",
            "capabilities_count": len(triples),
            "features_sampled": features_found[:5],
        }

    return triples, proof_info


def probe_common_specs(origin: str, brand: str, config=None, timeout: int = 3) -> Tuple[List[SemanticTriple], Optional[Dict[str, Any]]]:
    """
    Rapidly probes common OpenAPI / Swagger endpoints on the brand's primary host.
    """
    from product_truth import parse_openapi_spec

    parsed = urlparse(origin)
    base = f"{parsed.scheme}://{parsed.netloc}"

    for path in _COMMON_SPEC_PATHS:
        spec_url = f"{base}{path}"
        try:
            validate_url_for_fetch(spec_url)
            resp = requests.get(spec_url, headers=_REGISTRY_HEADERS, timeout=timeout)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                    if isinstance(data, dict) and ("paths" in data or "swagger" in data or "openapi" in data):
                        logger.info("Spec probe: discovered live OpenAPI spec at %s", spec_url)
                        triples = parse_openapi_spec(data, brand_name=brand, source_origin=spec_url, config=config)
                        proof_info = {
                            "source_type": "spec_probe",
                            "url": spec_url,
                            "title": f"{brand} OpenAPI Specification",
                            "status": "verified",
                            "capabilities_count": len(triples),
                        }
                        return triples, proof_info
                except (ValueError, json.JSONDecodeError):
                    pass
        except Exception:
            pass

    return [], None


def orchestrate_autonomous_proof_discovery(
    marketing_url: str,
    brand_name: str,
    pipeline=None,
    time_budget: float = 6.0,
) -> Tuple[List[SemanticTriple], List[Dict[str, Any]]]:
    """
    Concurrently triggers all autonomous technical proof discovery channels.
    Bounded by time_budget to ensure low-latency audit response.
    Returns:
      (discovered_triples, proof_sources_metadata)
    """
    logger.info(
        "Initiating Autonomous Technical Proof Discovery for '%s' (url=%s, budget=%.1fs)...",
        brand_name,
        marketing_url,
        time_budget,
    )
    start_time = time.monotonic()
    config = getattr(pipeline, "config", None) if pipeline else None

    all_triples: List[SemanticTriple] = []
    proof_sources: List[Dict[str, Any]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        f_trust = executor.submit(discover_trust_center, marketing_url, brand_name, 4)
        f_sdk = executor.submit(discover_public_sdks, brand_name, marketing_url, 4)
        f_change = executor.submit(discover_public_changelog, marketing_url, brand_name, pipeline, 4)
        f_spec = executor.submit(probe_common_specs, marketing_url, brand_name, config, 3)
        f_gh = executor.submit(discover_github_evidence, brand_name, marketing_url, 4)

        futures = [f_trust, f_sdk, f_change, f_spec, f_gh]
        done, _ = concurrent.futures.wait(futures, timeout=time_budget)

        for f in done:
            try:
                res = f.result()
                if f in (f_trust, f_change, f_spec, f_gh):
                    t_list, meta = res
                    all_triples.extend(t_list)
                    if meta:
                        proof_sources.append(meta)
                elif f is f_sdk:
                    t_list, metas = res
                    all_triples.extend(t_list)
                    proof_sources.extend(metas)
            except Exception as e:
                logger.debug("Proof discovery future error: %s", e)

    # Deduplicate triples by (predicate, object)
    deduped: List[SemanticTriple] = []
    seen: Set[Tuple[str, str]] = set()
    for t in all_triples:
        key = (t.predicate.lower(), t.object.lower())
        if key not in seen:
            seen.add(key)
            deduped.append(t)

    elapsed = time.monotonic() - start_time
    logger.info(
        "Autonomous Proof Discovery finished in %.2fs: %d capabilities from %d sources.",
        elapsed,
        len(deduped),
        len(proof_sources),
    )

    return deduped, proof_sources
