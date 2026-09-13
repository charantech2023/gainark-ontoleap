"""
document_graph.py — Universal Document Ingestion & Knowledge Graph Extractor

Powered by LlamaIndex document readers:
1. Ingests PDF files (whitepapers, SOC 2 reports, datasheets) via LlamaIndex PDFReader / pypdf.
2. Ingests Markdown documentation files (.md, .markdown) via LlamaIndex MarkdownReader.
3. Ingests plain text (.txt) and JSON/YAML specifications.
4. Auto-discovers downloadable PDFs and documents directly from target domains via sitemaps & resource hubs.
5. Mines named entities (grounded with Wikidata Q-IDs via GLiNER).
6. Mines relational semantic triples <Subject, Predicate, Object> with verbatim evidence quotes.
7. Serializes the document Knowledge Graph into W3C JSON-LD and RDF Turtle.
"""

import os
import re
import tempfile
import logging
from typing import Optional, List, Dict, Any, Union, Tuple
from pathlib import Path
from urllib.parse import urlparse, urljoin, unquote

import requests
from bs4 import BeautifulSoup

from models import PageKnowledgeGraph, KGNode, KGEdge
from page_graph import (
    _extract_entities_gliner,
    _extract_semantic_edges,
    _build_jsonld_graph,
    _build_turtle_graph
)
from scraper import smart_fetch, validate_url_for_fetch, _resolve_redirects

logger = logging.getLogger("gainark.document_graph")

SUPPORTED_EXTENSIONS = {".pdf", ".md", ".markdown", ".txt", ".json", ".yaml", ".yml"}

DOCUMENT_CATEGORIES = {
    "security": {
        "label": "🛡️ Security & Compliance",
        "hints": [
            "soc2", "soc-2", "soc_2", "iso27001", "iso-27001", "hipaa", "gdpr",
            "compliance", "security", "pci-dss", "csa-star", "privacy-shield",
            "trust", "audit", "pentest", "vulnerability"
        ]
    },
    "specs": {
        "label": "📐 Architecture & Specs",
        "hints": [
            "architecture", "datasheet", "data-sheet", "spec", "specification",
            "api", "integration", "developer", "technical", "schema", "solution-brief",
            "blueprint", "whitepaper"
        ]
    },
    "case_study": {
        "label": "🏆 Case Studies",
        "hints": [
            "case-study", "case_study", "casestudy", "case study",
            "customer-story", "customer_story", "customer story",
            "success-story", "success_story", "success story",
            "roi", "customer-study", "customer_study"
        ]
    },
    "market_report": {
        "label": "📊 Market & Analyst Reports",
        "hints": [
            "gartner", "forrester", "g2", "idc", "magic-quadrant", "wave",
            "market-guide", "analyst"
        ]
    }
}

CONVENTIONAL_DOC_HUBS = [
    "/resources",
    "/resource-library",
    "/whitepapers",
    "/datasheets",
    "/security",
    "/compliance",
    "/trust",
    "/trust-center",
    "/case-studies",
    "/documentation"
]

_DOC_EXT_PATTERN = re.compile(r'\.(pdf|docx?|pptx?|xlsx?)($|\?)', re.I)
_SITEMAP_DOC_LOC_RE = re.compile(r"<loc>\s*([^<\s]+\.(?:pdf|docx?|pptx?|xlsx?)[^<\s]*)\s*</loc>", re.I)
_SITEMAP_INDEX_RE = re.compile(r"<sitemapindex", re.I)
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
_ROBOTS_SITEMAP_RE = re.compile(r"^\s*sitemap:\s*(\S+)", re.I | re.M)


def classify_document(url: str, title: Optional[str] = None) -> str:
    """
    Categorizes a discovered document by analyzing keywords in its URL and title.
    Returns human-friendly category label.
    """
    combined = f"{url.lower()} {(title or '').lower()}"
    normalized = combined.replace('_', ' ').replace('-', ' ')
    for cat_id, cat_info in DOCUMENT_CATEGORIES.items():
        for hint in cat_info["hints"]:
            norm_hint = hint.replace('_', ' ').replace('-', ' ')
            if hint in combined or norm_hint in normalized:
                return cat_info["label"]
    return "📄 General Document"


def fetch_document_bytes(
    url: str,
    max_bytes: int = 20_000_000,
    timeout: int = 15
) -> Tuple[bytes, str]:
    """
    Safely fetches binary document bytes with SSRF validation,
    redirect security, and response size limits.
    Returns (bytes, filename).
    """
    validate_url_for_fetch(url)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/pdf,application/octet-stream,*/*"
    }

    final_url = _resolve_redirects(url, headers, timeout=timeout)
    resp = requests.get(final_url, headers=headers, stream=True, timeout=timeout)
    resp.raise_for_status()

    cl = resp.headers.get("Content-Length")
    if cl and cl.isdigit() and int(cl) > max_bytes:
        raise ValueError(f"Document size ({int(cl)} bytes) exceeds maximum allowable limit of {max_bytes} bytes.")

    body = bytearray()
    for chunk in resp.iter_content(chunk_size=65536):
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ValueError(f"Document exceeded maximum allowable limit of {max_bytes} bytes.")

    filename = "document.pdf"
    cd = resp.headers.get("Content-Disposition")
    if cd:
        fname_match = re.search(r'filename\*?=(?:UTF-8\'\')?["\']?([^"\';\r\n]+)', cd, re.I)
        if fname_match:
            filename = unquote(fname_match.group(1).strip())

    if filename == "document.pdf":
        parsed_path = urlparse(final_url).path
        if parsed_path:
            stem = unquote(parsed_path.rstrip("/").split("/")[-1])
            if stem and "." in stem:
                filename = stem

    return bytes(body), filename


def discover_domain_documents(base_url: str, max_docs: int = 10) -> List[Dict[str, Any]]:
    """
    Automatically discovers candidate PDFs and technical documents published by a domain.
    Inspects XML sitemaps, conventional resource hubs, and root navigation.
    """
    if not base_url.startswith("http://") and not base_url.startswith("https://"):
        base_url = "https://" + base_url

    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    candidates: Dict[str, Dict[str, Any]] = {}

    def add_candidate(url_cand: str, title: Optional[str] = None, source: str = "web"):
        url_clean = url_cand.split("#")[0].strip()
        if not url_clean or url_clean in candidates:
            return
        if not url_clean.startswith("http://") and not url_clean.startswith("https://"):
            url_clean = urljoin(origin, url_clean)

        parsed_cand = urlparse(url_clean)
        if parsed_cand.scheme not in ("http", "https"):
            return

        c_title = title.strip() if title else ""
        filename = unquote(parsed_cand.path.rstrip("/").split("/")[-1]) if parsed_cand.path else "document.pdf"
        if not c_title or len(c_title) < 3:
            stem = filename.split(".")[0].replace("_", " ").replace("-", " ").title()
            c_title = stem if stem else "Technical Document"

        category = classify_document(url_clean, c_title)
        candidates[url_clean] = {
            "url": url_clean,
            "filename": filename,
            "title": c_title,
            "category": category,
            "source": source
        }

    # 1. Sitemap Discovery
    sitemap_candidates = [f"{origin}/sitemap.xml", f"{origin}/sitemap_index.xml"]
    try:
        validate_url_for_fetch(f"{origin}/robots.txt")
        robots_txt = smart_fetch(f"{origin}/robots.txt", timeout=6)
        if robots_txt:
            for declared in _ROBOTS_SITEMAP_RE.findall(robots_txt):
                if declared not in sitemap_candidates:
                    sitemap_candidates.append(declared)
    except Exception as err:
        logger.debug("[DocDiscovery] No robots.txt for %s: %s", origin, err)

    seen_sitemaps = set()
    sitemaps_read = 0
    while sitemap_candidates and sitemaps_read < 4:
        sm_url = sitemap_candidates.pop(0)
        if sm_url in seen_sitemaps or sm_url.endswith(".gz"):
            continue
        seen_sitemaps.add(sm_url)
        sitemaps_read += 1

        try:
            validate_url_for_fetch(sm_url)
            xml_body = smart_fetch(sm_url, timeout=8)
        except Exception as sm_err:
            logger.debug("[DocDiscovery] Could not fetch sitemap %s: %s", sm_url, sm_err)
            continue

        if not xml_body:
            continue

        for doc_url in _SITEMAP_DOC_LOC_RE.findall(xml_body):
            add_candidate(doc_url, source="sitemap")

        if _SITEMAP_INDEX_RE.search(xml_body):
            for sub_loc in _LOC_RE.findall(xml_body):
                sub_loc_lower = sub_loc.lower()
                if any(k in sub_loc_lower for k in ["resource", "whitepaper", "doc", "page", "post"]):
                    if sub_loc not in seen_sitemaps:
                        sitemap_candidates.append(sub_loc)

    # 2. Conventional Resource Hub Probing
    for hub_path in CONVENTIONAL_DOC_HUBS:
        if len(candidates) >= max_docs * 2:
            break
        hub_url = f"{origin}{hub_path}"
        try:
            validate_url_for_fetch(hub_url)
            hub_html = smart_fetch(hub_url, timeout=8)
        except Exception:
            continue

        if not hub_html:
            continue

        soup = BeautifulSoup(hub_html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            full_url = urljoin(hub_url, href)
            if _DOC_EXT_PATTERN.search(full_url):
                anchor_text = a.get_text(" ", strip=True)
                add_candidate(full_url, title=anchor_text, source="resource_hub")

    # 3. Homepage Outbound Document Links
    try:
        validate_url_for_fetch(origin)
        home_html = smart_fetch(origin, timeout=8)
        if home_html:
            soup = BeautifulSoup(home_html, "html.parser")
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                if href and _DOC_EXT_PATTERN.search(href):
                    full_url = urljoin(origin, href)
                    add_candidate(full_url, title=a.get_text(" ", strip=True), source="homepage")
    except Exception as home_err:
        logger.debug("[DocDiscovery] Homepage link extraction skipped: %s", home_err)

    priority_order = {
        "🛡️ Security & Compliance": 0,
        "📐 Architecture & Specs": 1,
        "🏆 Case Studies": 2,
        "📊 Market & Analyst Reports": 3,
        "📄 General Document": 4
    }

    doc_list = list(candidates.values())
    doc_list.sort(key=lambda d: (priority_order.get(d["category"], 5), -len(d["title"])))
    return doc_list[:max_docs]


def extract_text_from_file(file_path: Union[str, Path]) -> Tuple[str, str]:
    """
    Extracts text and title from a local file using LlamaIndex readers with graceful fallback.
    Returns (clean_text, title).
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    suffix = path.suffix.lower()
    title = path.stem.replace("_", " ").replace("-", " ").title()
    clean_text = ""

    if suffix == ".pdf":
        try:
            from llama_index.readers.file import PDFReader
            reader = PDFReader()
            docs = reader.load_data(file=path)
            clean_text = "\n\n".join(d.text for d in docs if d.text)
        except Exception as e:
            logger.warning("LlamaIndex PDFReader encountered error (%s), falling back to pypdf", e)
            try:
                import pypdf
                reader = pypdf.PdfReader(str(path))
                pages = [page.extract_text() or "" for page in reader.pages]
                clean_text = "\n\n".join(pages)
            except Exception as e2:
                logger.error("Failed to extract text from PDF %s: %s", path, e2)
                raise ValueError(f"Could not read PDF file {path.name}: {e2}")

    elif suffix in (".md", ".markdown"):
        try:
            from llama_index.readers.file import MarkdownReader
            reader = MarkdownReader()
            docs = reader.load_data(file=path)
            clean_text = "\n\n".join(d.text for d in docs if d.text)
        except Exception as e:
            logger.debug("LlamaIndex MarkdownReader fallback to direct read: %s", e)
            clean_text = path.read_text(encoding="utf-8", errors="replace")

    elif suffix in (".txt", ".json", ".yaml", ".yml"):
        clean_text = path.read_text(encoding="utf-8", errors="replace")

    else:
        # Generic text read attempt
        clean_text = path.read_text(encoding="utf-8", errors="replace")

    # Try to extract a more descriptive title from the first heading if available
    first_lines = clean_text.strip().splitlines()[:5]
    for line in first_lines:
        line_clean = line.strip()
        if line_clean.startswith("# "):
            title = line_clean.lstrip("# ").strip()
            break
        elif len(line_clean) > 3 and len(line_clean) < 80 and not line_clean.startswith("{"):
            title = line_clean
            break

    return clean_text, title


def extract_document_knowledge_graph(
    file_path: Optional[Union[str, Path]] = None,
    file_bytes: Optional[bytes] = None,
    filename: Optional[str] = None,
    source_url: Optional[str] = None,
    vertical_id: str = "b2b_saas_fintech",
    custom_title: Optional[str] = None,
    subject_brand: Optional[str] = None
) -> PageKnowledgeGraph:
    """
    Build a complete, standalone Knowledge Graph from a document (PDF, Markdown, Text).
    
    Args:
        file_path: Absolute or relative path to a local document.
        file_bytes: Raw bytes of the document (for multipart uploads).
        filename: Name of the uploaded file if passing file_bytes.
        source_url: Optional remote URL where the document was discovered.
        vertical_id: Vertical domain ID (defaults to b2b_saas_fintech).
        custom_title: Optional custom document title.
        subject_brand: Optional override for the primary subject brand name.
    """
    temp_file_path: Optional[str] = None

    try:
        if file_bytes is not None:
            fname = filename or "document.txt"
            suffix = Path(fname).suffix.lower() or ".txt"
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(file_bytes)
                temp_file_path = tmp.name
            target_path = Path(temp_file_path)
            display_name = fname
        elif file_path is not None:
            target_path = Path(file_path)
            display_name = target_path.name
        else:
            raise ValueError("Either file_path or file_bytes must be provided.")

        clean_text, extracted_title = extract_text_from_file(target_path)
        title = custom_title or extracted_title or display_name

        if not clean_text or len(clean_text.strip()) < 20:
            raise ValueError(f"Document '{display_name}' contained insufficient readable text.")

        # Determine subject brand (e.g. from override, title, or first words)
        brand = subject_brand
        if not brand:
            brand = title.split("|")[0].split("-")[0].split(":")[0].strip()
            if not brand or len(brand) > 40:
                brand = display_name.split(".")[0].replace("_", " ").title()

        doc_provenance_url = source_url or f"file:///{display_name}"

        # 1. Named Entity Recognition with GLiNER & Wikidata grounding
        nodes = _extract_entities_gliner(clean_text, vertical_id=vertical_id)
        for n in nodes:
            n.source_urls = [doc_provenance_url]

        # Ensure subject brand node exists
        subject_id = f"entity:{brand.lower().replace(' ', '_')}"
        if not any(n.canonical_name.lower() == brand.lower() for n in nodes):
            nodes.insert(0, KGNode(
                id=subject_id,
                canonical_name=brand,
                entity_type="SoftwarePlatform",
                mentions_count=1,
                source_urls=[doc_provenance_url]
            ))

        # 2. Relational Semantic Edges with verbatim quotes
        edges = _extract_semantic_edges(clean_text, brand, nodes, doc_provenance_url, vertical_id=vertical_id)

        # 3. Classes and Predicates discovered
        classes_discovered = sorted(list(set(n.entity_type for n in nodes)))
        predicates_discovered = sorted(list(set(e.predicate for e in edges)))

        # 4. W3C Serializations
        export_jsonld = _build_jsonld_graph(doc_provenance_url, title, brand, nodes, edges)
        export_turtle = _build_turtle_graph(doc_provenance_url, brand, edges, nodes)

        return PageKnowledgeGraph(
            url=doc_provenance_url,
            title=title,
            nodes=nodes,
            edges=edges,
            classes_discovered=classes_discovered,
            predicates_discovered=predicates_discovered,
            embedded_schemas=[],
            vertical_id=vertical_id,
            export_jsonld=export_jsonld,
            export_turtle=export_turtle
        )

    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception as e:
                logger.debug("Failed to clean up temp file %s: %s", temp_file_path, e)


def ingest_remote_document(
    url: str,
    vertical_id: str = "b2b_saas_fintech",
    custom_title: Optional[str] = None,
    subject_brand: Optional[str] = None
) -> PageKnowledgeGraph:
    """
    Downloads a remote document from a URL and extracts its Knowledge Graph.
    """
    file_bytes, filename = fetch_document_bytes(url)
    return extract_document_knowledge_graph(
        file_bytes=file_bytes,
        filename=filename,
        source_url=url,
        vertical_id=vertical_id,
        custom_title=custom_title,
        subject_brand=subject_brand
    )
