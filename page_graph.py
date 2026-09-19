"""
page_graph.py — Pure Page-Level Knowledge Graph & Ontology Extractor

Extracts a clean, W3C-compliant Knowledge Graph from a single web page:
1. Clean text & document structure extraction (trafilatura).
2. Existing Schema.org / Microdata / RDFa extraction (extruct).
3. Zero-shot Named Entity Recognition with vertical classes (GLiNER).
4. Relational semantic triple extraction with sentence-level provenance.
5. Entity linking with Wikidata QIDs.
6. Exports W3C JSON-LD @graph and RDF Turtle (.ttl).
"""

import re
import logging
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import urlparse
from bs4 import BeautifulSoup
import trafilatura
import extruct

from models import PageKnowledgeGraph, KGNode, KGEdge
from scraper import repair_glued_words, smart_fetch, validate_url_for_fetch
from constants import (
    WIKIDATA_KB, KNOWN_INTEGRATIONS, KNOWN_COMPLIANCE,
    KNOWN_PRICING, KNOWN_AUTOMATION, KNOWN_FEATURES,
    resolve_surface_forms
)
from entity_grounding import wikidata_uri
from concept_roles import claim_vocabulary
from ontology_schema import canonical_class
from industry_ontology import load_industry_ontology
from pipeline import _load_shared_gliner

logger = logging.getLogger("gainark.page_graph")

# Only a guard against a pathological page. A real page yields tens of entities.
_MAX_EXPORT_NODES = 200


def _extract_text_and_title(html: str) -> Tuple[str, str]:
    """Extract clean readability text and page title from raw HTML."""
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    elif soup.find("meta", property="og:title"):
        title = soup.find("meta", property="og:title").get("content", "").strip()

    # Trafilatura extracts clean article/page text stripping nav and footer boilerplates
    clean_text = trafilatura.extract(html, include_links=False, include_tables=True)
    if not clean_text or len(clean_text.strip()) < 50:
        # Fallback to BeautifulSoup get_text
        clean_text = soup.get_text(separator=" ", strip=True)

    return repair_glued_words(clean_text), title


def _extract_embedded_schemas(html: str, url: str) -> List[str]:
    """Extract Schema.org types already embedded in the HTML markup."""
    try:
        data = extruct.extract(html, base_url=url, uniform=True)
        schema_types = set()
        for block in data.get("json-ld", []):
            stype = block.get("@type")
            if isinstance(stype, list):
                schema_types.update(stype)
            elif isinstance(stype, str):
                schema_types.add(stype)

        for block in data.get("microdata", []):
            stype = block.get("type")
            if stype:
                schema_types.add(stype.split("/")[-1])

        return sorted(list(schema_types))
    except Exception as e:
        logger.debug("Schema extraction error for %s: %s", url, e)
        return []


# Spans GLiNER tags that name nothing. On the 14 Sep 2026 ordwaylabs.com run: "Customer A",
# "customer B", "Customer ABC", "tier A" and "Acme Corp" from worked examples, "A100" to
# "A400" from a pricing table, and "Search", "Filters", "More" from page controls.
# These are shapes, not a word list, so they do not grow with every site read.
_PLACEHOLDER = re.compile(
    r"^(?:(?:customer|client|company|tenant|vendor|user|account|tier|plan|org|organi[sz]ation)"
    r"\s+(?:[a-z]{1,3}|\d{1,3})|acme(?:\s+\w+)?)$",
    re.IGNORECASE,
)
_BARE_CODE = re.compile(r"^[A-Z]\d{2,4}$")


def _prose_words(text: str) -> set:
    """Lowercase words the page uses mid-sentence, after another lowercase word."""
    return set(re.findall(r"(?<=[a-z,] )([a-z][a-z-]+)\b", text or ""))


def _is_not_a_name(span: str, prose_words: set, concept_keys: set) -> bool:
    """True for a placeholder, a bare code, or a common word GLiNER took for a name.

    A single capitalised word the same page also writes in lowercase mid-sentence is an
    ordinary word in a heading or a button ("Search", "More", "Support"), not a name:
    Stripe and Zuora are not written "stripe" and "zuora". The vertical's own terms
    ("Dunning", "Invoicing") are kept however the page writes them.
    """
    if span.lower() in concept_keys:
        return False
    if _PLACEHOLDER.match(span) or _BARE_CODE.match(span):
        return True
    if re.fullmatch(r"[A-Z][a-z]+", span):
        word = span.lower()
        # "Qu": a fragment. Three letters is left alone - Wix, Box and Olo are names.
        if len(word) <= 2:
            return True
        singular = word[:-1] if word.endswith("s") else word
        return bool({word, singular, word + "s"} & prose_words)
    return False


def _extract_entities_gliner(text: str, vertical_id: str = "b2b_saas_fintech") -> List[KGNode]:
    """Extract named entities using GLiNER zero-shot model and ground to Wikidata."""
    onto = load_industry_ontology(vertical_id)
    gliner_labels = onto.classes or [
        "Software Platform", "Billing Feature", "Accounting Standard",
        "Integration Partner", "Security Standard", "Pricing Model", "Product Feature"
    ]

    model = _load_shared_gliner("urchade/gliner_small-v2.1")
    words = text.split()
    chunks = []
    chunk_size = 200
    chunk_overlap = 25
    step = chunk_size - chunk_overlap

    for i in range(0, len(words), step):
        c_words = words[i:i + chunk_size]
        if not c_words:
            break
        chunks.append(" ".join(c_words))
        if i + chunk_size >= len(words):
            break

    seen_entities: Dict[str, KGNode] = {}
    concept_keys = {label.lower() for c in (onto.concepts or [])
                    for label in [c.pref_label] + list(c.alt_labels or [])}
    prose_words = _prose_words(text)

    for chunk in chunks:
        preds = model.predict_entities(chunk, gliner_labels, threshold=0.50)
        for p in preds:
            raw_text = p["text"].strip()
            if len(raw_text) < 2 or _is_not_a_name(raw_text, prose_words, concept_keys):
                continue

            canonical = raw_text
            clean_key = canonical.lower()

            # Wikidata lookup
            wiki_qid = WIKIDATA_KB.get(clean_key)
            if not wiki_qid:
                # check partial match
                for kb_name, qid in WIKIDATA_KB.items():
                    if kb_name.lower() == clean_key:
                        wiki_qid = qid
                        break

            node_id = f"entity:{re.sub(r'[^a-zA-Z0-9_-]', '_', clean_key)}"

            if clean_key in seen_entities:
                seen_entities[clean_key].mentions_count += 1
                if raw_text not in seen_entities[clean_key].aliases:
                    seen_entities[clean_key].aliases.append(raw_text)
            else:
                seen_entities[clean_key] = KGNode(
                    id=node_id,
                    canonical_name=canonical,
                    entity_type=canonical_class(p["label"]),
                    aliases=[raw_text],
                    wikidata_id=wiki_qid,
                    mentions_count=1,
                    confidence=round(float(p["score"]), 3)
                )

    return list(seen_entities.values())


# Page chrome that trafilatura keeps: a search overlay, skip links, form field labels. On
# the 14 Sep 2026 ordwaylabs.com run "Hit enter to search or ESC to close" opened about a
# dozen proofs, and one proof for Hybrid Pricing was a lead form's field list.
_CHROME_LINE = re.compile(
    r"^(hit enter to search|skip to (main )?content|search( for)?[:.]?$|close( menu)?$|menu$|"
    r"(first|last) ?name\**$|e-?mail\**$|stage$|lead source$|last call to action( details)?$|"
    r"article created .* ago$|number of comments: ?\d+$|recent activity$|promoted articles$)",
    re.IGNORECASE,
)
_PROOF_MAX = 280


def _proof_units(text: str) -> List[str]:
    """The sentences a relation may be proven by.

    A line is a boundary as well as sentence punctuation: a feature list or a menu has no
    full stops, so splitting on punctuation alone made one "sentence" of a whole list,
    which was then cut at 280 characters - mid-word, and often before the very name the
    relation was about.
    """
    units = []
    heading = ""
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-*• ").strip()
        if not line or _CHROME_LINE.match(line):
            continue
        # A short line with no closing full stop heads the lines under it: "General Ledger
        # Integration" over "Post to QuickBooks, NetSuite, Sage Intacct". The item keeps
        # its heading, or the cue that makes it an integration is lost.
        if len(line) <= 60 and not line.endswith((".", "!", "?")):
            heading = line.rstrip(":")
            if len(line) > 15:
                units.append(line)
            continue
        for s in re.split(r'(?<=[.!?])\s+', line):
            s = s.strip()
            if len(s) > 15:
                units.append("%s — %s" % (heading, s) if heading else s)
    return units


def _proof_window(sentence: str, match: str) -> str:
    """At most _PROOF_MAX characters of a sentence, cut on word boundaries around `match`."""
    if len(sentence) <= _PROOF_MAX:
        return sentence
    found = re.search(re.escape(match), sentence, re.IGNORECASE) if match else None
    centre = found.start() if found else 0
    start = max(0, centre - _PROOF_MAX // 3)
    end = min(len(sentence), start + _PROOF_MAX)
    start = max(0, end - _PROOF_MAX)
    # Pull both cuts in to the nearest space, so no word is split.
    if start > 0:
        space = sentence.find(" ", start)
        start = space + 1 if 0 <= space < centre else start
    if end < len(sentence):
        space = sentence.rfind(" ", centre, end)
        end = space if space > centre else end
    return ("…" if start > 0 else "") + sentence[start:end].strip() + ("…" if end < len(sentence) else "")


# Competitor and Customer are roles a company plays toward the site, not kinds of thing,
# and GLiNER guesses them from loose context. On the 14 Sep 2026 ordwaylabs.com run Visa,
# Mastercard, FedEx and State Farm were competitors (a payments page and a pricing blog
# named them), while Zuora - on "The Best Zuora Alternative" - and Chargebee were software
# platforms, and Claude was a customer. A role now needs the page to state it, and a
# page that states it assigns it whatever GLiNER said.
_ROLE_FALLBACK = "Organization"
_COMPETITOR_BEFORE = r"(?:alternatives?\s+to|vs\.?|versus|compar(?:e|es|ed|ing)\s+(?:to|with|against)|switch(?:ed|ing)?\s+from|migrat\w*\s+(?:away\s+)?from|mov(?:e|ed|ing)\s+(?:away\s+)?from|replac\w*|instead\s+of|leaving)"
_COMPETITOR_AFTER = r"(?:alternatives?|vs\.?|versus|competitors?|comparison|migration)"
_CUSTOMER_AFTER = r"(?:automates|scales|uses|used|chose|selected|switched|saved|cut|reduced|grew|case\s+study|customer\s+story)"


def _apply_role_evidence(nodes: List[KGNode], text: str, url: str, brand: str = "") -> None:
    """Keep Competitor and Customer only where the page says so; assign them where it does."""
    try:
        from industry_profiler import _evidence_group
        group = _evidence_group(url)
    except Exception:
        group = None
    brand_key = re.sub(r"[^a-z0-9]", "", brand.lower())
    for node in nodes:
        key = re.sub(r"[^a-z0-9]", "", node.canonical_name.lower())
        # The site's own name: "Ordway automates billing" does not make Ordway a customer.
        if brand_key and key and (key in brand_key or brand_key in key):
            continue
        name = re.escape(node.canonical_name)
        competitor = bool(
            re.search(r"%s\s+(?:the\s+)?%s\b" % (_COMPETITOR_BEFORE, name), text, re.IGNORECASE)
            or re.search(r"\b%s\s+%s\b" % (name, _COMPETITOR_AFTER), text, re.IGNORECASE))
        customer = bool(re.search(r"\b%s\s+%s\b" % (name, _CUSTOMER_AFTER), text, re.IGNORECASE))
        if node.entity_type in ("Organization", "SoftwarePlatform", "Competitor", "Customer", "Entity"):
            if competitor:
                node.entity_type = "Competitor"
                continue
            if customer and node.entity_type != "Competitor":
                node.entity_type = "Customer"
                continue
        if node.entity_type == "Competitor" and not (competitor or group == "comparison"):
            node.entity_type = _ROLE_FALLBACK
        elif node.entity_type == "Customer" and not (customer or group == "customer"):
            node.entity_type = _ROLE_FALLBACK


def _concept_pattern(form: str) -> Optional["re.Pattern"]:
    """A vertical term as the page may write it: any case, hyphen or space between its
    words, a plural. An acronym ("MRR", "VAT") only in capitals, so "vat" in prose is not
    value-added tax."""
    words = re.findall(r"[A-Za-z0-9]+", form or "")
    if not words:
        return None
    if len(words) == 1 and re.fullmatch(r"[A-Z0-9]{2,6}", words[0]):
        return re.compile(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(words[0]))
    # A label already plural ("Accounting Standards") still matches its singular.
    if len(words[-1]) > 4 and words[-1].lower().endswith("s") and not words[-1].lower().endswith("ss"):
        words[-1] = words[-1][:-1]
    # A bracket separates words as a space does: "401(k) providers".
    body = r"[\s()-]+".join(re.escape(w) for w in words)
    return re.compile(r"(?<![A-Za-z0-9])%s(?:s|es)?(?![A-Za-z0-9])" % body, re.IGNORECASE)


def _concept_occurrences(text: str, vertical_id: str) -> List[KGNode]:
    """The vertical's own terms this page writes, one node each.

    GLiNER tags spans it judges to be names, so a term the page states plainly -
    "performance obligations (POBs)", "MRR/ARR" - could be on a page that was read and
    still be reported as unclaimed whitespace, as both were on the 14 Sep 2026
    ordwaylabs.com run. The resolver identifies these nodes as the vertical's concepts.
    """
    try:
        onto = load_industry_ontology(vertical_id)
    except Exception:
        return []
    hits = []
    for c in onto.concepts or []:
        for form in [c.pref_label] + list(c.alt_labels or []):
            pattern = _concept_pattern(form)
            found = pattern.search(text or "") if pattern else None
            if found:
                hits.append(KGNode(
                    id="concept:%s" % re.sub(r"[^a-z0-9]+", "_", c.pref_label.lower()),
                    canonical_name=c.pref_label,
                    entity_type="Concept",
                    aliases=[found.group(0)],
                    mentions_count=1,
                    confidence=1.0,
                ))
                break
    return hits


def _brand_forms(subject_name: str) -> List[str]:
    """How a site writes its own name: "Ordwaylabs" is written "Ordway" as well."""
    low = (subject_name or "").lower()
    forms = {low} if low else set()
    short = re.sub(r"(labs|lab|hq|app|inc|software|tech|io)$", "", low)
    if len(short) >= 4:
        forms.add(short)
    return sorted(forms)


def _speaks_for_subject(unit: str, subject_name: str, nodes: List[KGNode], url: str) -> bool:
    """Whether a sentence can prove something about the site's own product.

    A relation is written as "<site> predicate <target>", so its proof has to be about
    the site. On the 14 Sep 2026 ordwaylabs.com run it often was not: a Paubox testimonial
    proved five integrations, a blog list of SaaS companies proved that Ordway has
    contract management because Docusign does, and a buyer's evaluation table proved SOC 2
    Type II. Without naming the site, none of these may prove anything:
      - a sentence naming a customer or a competitor, which is about that company;
      - a table row, which on these sites is a comparison or evaluation grid;
      - any sentence on a blog, glossary or guide page, which is about the industry.
    """
    low = unit.lower()
    if any(re.search(r"\b%s" % re.escape(f), low) for f in _brand_forms(subject_name)):
        return True
    others = [n.canonical_name.lower() for n in nodes if n.entity_type in ("Customer", "Competitor")]
    if any(re.search(r"\b%s\b" % re.escape(o), low) for o in others if o):
        return False
    if unit.count("|") >= 3:
        return False
    try:
        from constants import ICP_EDITORIAL_PATHS
        from industry_profiler import _path_matches
        if _path_matches(urlparse(url).path.rstrip("/").lower(), ICP_EDITORIAL_PATHS):
            return False
    except Exception:
        pass
    return True


def _extract_semantic_edges(
    text: str,
    subject_name: str,
    nodes: List[KGNode],
    url: str,
    vertical_id: str = "b2b_saas_fintech"
) -> List[KGEdge]:
    """Extract relational semantic edges with exact sentence provenance.

    What a sentence can claim comes from the vertical's concept layer
    (concept_roles.claim_vocabulary), so a claim's target is a concept and resolves to its
    URI. The billing lists in constants.py are only the fallback for a vertical with no
    concepts, which is where they came from: every vertical used to be read for them, and
    the 18 Sep 2026 HR crawls found "Overage Pricing" on gusto.com and nothing about
    payroll.
    """
    onto = load_industry_ontology(vertical_id)
    claims = {predicate: [(target, [(form, _concept_pattern(form)) for form in forms])
                          for target, forms in pairs]
              for predicate, pairs in claim_vocabulary(onto).items()}
    vocab_integrations = onto.known_integrations or KNOWN_INTEGRATIONS
    vocab_compliance = onto.known_compliance or KNOWN_COMPLIANCE
    vocab_pricing = KNOWN_PRICING
    vocab_automation = KNOWN_AUTOMATION
    forms_features = resolve_surface_forms(None, 'known_features', KNOWN_FEATURES)

    sentences = [s for s in _proof_units(text) if _speaks_for_subject(s, subject_name, nodes, url)]

    edges: List[KGEdge] = []
    seen_edges = set()

    def add_edge(predicate: str, target: str, conf: float, sentence: str, target_type: str = "Entity",
                 match: Optional[str] = None):
        edge_key = (subject_name.lower(), predicate.lower(), target.lower())
        if edge_key in seen_edges:
            return
        seen_edges.add(edge_key)

        target_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', target.lower())
        subject_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', subject_name.lower())
        edge_id = f"edge:{subject_slug}-{predicate.lower()}-{target_slug}"

        edges.append(KGEdge(
            id=edge_id,
            source=subject_name,
            target=target,
            predicate=predicate,
            source_type="SoftwarePlatform",
            target_type=target_type,
            confidence=conf,
            provenance_sentence=_proof_window(sentence, match or target),
            source_url=url
        ))

    def claimed(predicate: str, sentence: str, cues: Optional[List[str]] = None) -> None:
        """Every target of one predicate that a sentence writes, in any of its forms."""
        low = sentence.lower()
        if cues is not None and not any(cue in low for cue in cues):
            return
        for target, forms in claims.get(predicate, []):
            for form, pattern in forms:
                found = pattern.search(sentence) if pattern else None
                if found:
                    add_edge(predicate, target, _CLAIM_CONFIDENCE[predicate], sentence,
                             target_type=_CLAIM_TYPES[predicate], match=found.group(0))
                    break

    auto_cues = ["automate", "automates", "automated", "streamline", "streamlines", "orchestrate", "auto-", "elimina"]
    int_cues = ["integrat", "connect", "sync", "export to", "import from", "native connector", "api for"]
    # Naming a standard is not complying with it. With no cue, a case study headline for a
    # HIPAA-regulated customer read as "Ordway compliesWith HIPAA" on 14 Sep 2026.
    compliance_cues = ["complian", "complies", "comply", "certif", "audit", "attest", "accordance",
                       "conform", "adhere", "meets", "support", "report available", "aligned with"]

    if claims:
        for sent in sentences:
            claimed("automates", sent, auto_cues)
            claimed("integratesWith", sent, int_cues)
            claimed("compliesWith", sent, compliance_cues)
            claimed("supportsPricingModel", sent)
            claimed("hasFeature", sent)
        # A concept layer with no integrations or rules of its own still has the seed
        # lists the vertical was discovered with, or the general ones.
        if "integratesWith" not in claims or "compliesWith" not in claims:
            _named_claims(sentences, add_edge, int_cues, compliance_cues,
                          [] if "integratesWith" in claims else vocab_integrations,
                          [] if "compliesWith" in claims else vocab_compliance)
        return edges

    # No concept layer: the name lists, and the billing lists for the rest.
    _named_claims(sentences, add_edge, int_cues, compliance_cues, vocab_integrations,
                  vocab_compliance)

    # automates
    for sent in sentences:
        s_lower = sent.lower()
        if any(cue in s_lower for cue in auto_cues):
            for cap in vocab_automation:
                if re.search(rf'\b{re.escape(cap.lower())}\b', s_lower):
                    add_edge("automates", cap, 0.90, sent, target_type="Process")

    # supportsPricingModel
    pricing_cues = ["pricing", "bill", "billing", "model", "monetiz", "plans", "tier"]
    for sent in sentences:
        s_lower = sent.lower()
        for pm in vocab_pricing:
            pm_simple = pm.lower().replace(" pricing", "").replace(" billing", "")
            if pm.lower() in s_lower or (pm_simple in s_lower and any(cue in s_lower for cue in pricing_cues)):
                add_edge("supportsPricingModel", pm, 0.90, sent, target_type="PricingModel")

    # hasFeature
    for sent in sentences:
        s_lower = sent.lower()
        for canonical, surface_forms in forms_features:
            for form in surface_forms:
                if re.search(rf'\b{re.escape(form.lower())}\b', s_lower):
                    add_edge("hasFeature", canonical, 0.85, sent, target_type="Feature", match=form)
                    break

    return edges


# How sure each kind of claim is, and the class its object takes.
_CLAIM_CONFIDENCE = {"automates": 0.90, "integratesWith": 0.92, "compliesWith": 0.95,
                     "supportsPricingModel": 0.90, "hasFeature": 0.85}
_CLAIM_TYPES = {"automates": "Process", "integratesWith": "IntegrationPartner",
                "compliesWith": "Standard", "supportsPricingModel": "PricingModel",
                "hasFeature": "Feature"}


def _named_claims(sentences: List[str], add_edge, int_cues: List[str],
                  compliance_cues: List[str], integrations: List[str],
                  compliance: List[str]) -> None:
    """integratesWith and compliesWith against plain name lists, as before concepts."""
    for sent in sentences:
        s_lower = sent.lower()
        if any(cue in s_lower for cue in int_cues):
            for partner in integrations:
                if re.search(rf'\b{re.escape(partner.lower())}\b', s_lower):
                    add_edge("integratesWith", partner, 0.92, sent, target_type="IntegrationPartner")
        if any(cue in s_lower for cue in compliance_cues):
            for std in compliance:
                if re.search(rf'\b{re.escape(std.lower())}\b', s_lower):
                    add_edge("compliesWith", std, 0.95, sent, target_type="Standard")


def _build_jsonld_graph(url: str, title: str, subject_name: str, nodes: List[KGNode], edges: List[KGEdge]) -> Dict[str, Any]:
    """Serializes the page knowledge graph into standard W3C JSON-LD @graph."""
    graph_items = []

    # Subject entity (SoftwareApplication / Organization)
    subject_item: Dict[str, Any] = {
        "@type": "SoftwareApplication",
        "@id": f"{url}#{subject_name.lower()}",
        "name": subject_name,
        "url": url
    }
    if title:
        subject_item["description"] = title

    # Collect relations
    integrations = [e.target for e in edges if e.predicate == "integratesWith"]
    features = [e.target for e in edges if e.predicate in ("hasFeature", "automates")]

    if integrations:
        subject_item["availableOnDevice"] = integrations
    if features:
        subject_item["featureList"] = features

    graph_items.append(subject_item)

    # Add entity nodes into about / mentions
    for n in order_nodes_for_export(nodes, _MAX_EXPORT_NODES):
        entity_item: Dict[str, Any] = {
            "@type": "Thing",
            "@id": n.id,
            "name": n.canonical_name,
            "additionalType": n.entity_type
        }
        same_as = wikidata_uri(n.wikidata_id)
        if same_as:
            entity_item["sameAs"] = same_as
        graph_items.append(entity_item)

    return {
        "@context": "https://schema.org",
        "@graph": graph_items
    }


def order_nodes_for_export(nodes: List[KGNode], limit: int) -> List[KGNode]:
    """Order nodes by how much they are worth publishing, then bound the list.

    A grounded entity carries an external identity, which is the one thing a consumer of
    the export cannot derive for itself, so those come first. Salience breaks the
    remaining ties, and the name keeps the order stable across runs of the same page.

    The bound is a guard against pathological input, not a payload budget: the caller
    returns the complete node list alongside this export regardless.
    """
    return sorted(
        nodes,
        key=lambda n: (0 if n.wikidata_id else 1, -n.mentions_count, n.canonical_name.lower()),
    )[:limit]


def _build_turtle_graph(url: str, subject_name: str, edges: List[KGEdge], nodes: List[KGNode]) -> str:
    """Serializes the knowledge graph into W3C RDF Turtle format."""
    sub_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', subject_name.lower())
    lines = [
        "@prefix schema: <http://schema.org/> .",
        "@prefix ex: <https://gainark.com/kg/> .",
        "@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .",
        "",
        f"ex:{sub_slug} a schema:SoftwareApplication ;",
        f'    schema:name "{subject_name}" ;',
        f'    schema:url <{url}> .'
    ]

    for e in edges:
        tgt_slug = re.sub(r'[^a-zA-Z0-9_-]', '_', e.target.lower())
        lines.append(f'ex:{sub_slug} ex:{e.predicate} ex:{tgt_slug} .')
        lines.append(f'ex:{tgt_slug} schema:name "{e.target}" .')

    return "\n".join(lines)


_HOSTED_PLATFORMS = {
    "stoplight.io", "readme.io", "gitbook.io", "mintlify.app", "zendesk.com", "freshdesk.com",
    "helpscoutdocs.com", "intercom.help", "notion.site", "github.io", "document360.io",
}


def _domain_brand(url: str) -> str:
    """The brand label of a host: the registered name, never a subdomain.

    Taking the first label made support.ordwaylabs.com's subject "Support", which then
    stood beside Ordway as a platform that integrates with Salesforce and Stripe.
    """
    labels = [p for p in (urlparse(url).hostname or "").lower().split(".") if p]
    # On a hosted docs or help platform the tenant is the subdomain: ordwaylabs.stoplight.io.
    if len(labels) >= 3 and ".".join(labels[-2:]) in _HOSTED_PLATFORMS:
        return labels[-3].capitalize()
    if len(labels) < 2:
        return (labels[0] if labels else "").capitalize()
    # "acme.co.uk": a two-letter country code under a generic second level.
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in ("co", "com", "org", "net", "ac", "gov", "edu"):
        return labels[-3].capitalize()
    return labels[-2].capitalize()


def build_page_kg(
    url_or_html: str,
    url: Optional[str] = None,
    vertical_id: str = "b2b_saas_fintech"
) -> PageKnowledgeGraph:
    """
    Build a complete, standalone Knowledge Graph from a single web page or HTML.
    """
    actual_url = url or "https://example.com"
    html_content = ""

    if url_or_html.startswith("http://") or url_or_html.startswith("https://"):
        actual_url = url_or_html
        validate_url_for_fetch(actual_url)
        html_content = smart_fetch(actual_url)
    else:
        html_content = url_or_html

    clean_text, title = _extract_text_and_title(html_content)

    # Determine subject name (brand)
    domain_brand = _domain_brand(actual_url)
    if not domain_brand or domain_brand == "Example":
        domain_brand = title.split("|")[0].split("-")[0].strip() if title else "Subject"

    # 1. Embedded Schema extraction
    embedded_schemas = _extract_embedded_schemas(html_content, actual_url)

    # 2. Named Entity Recognition
    nodes = _extract_entities_gliner(clean_text, vertical_id=vertical_id)
    for n in nodes:
        n.source_urls = [actual_url]

    _apply_role_evidence(nodes, clean_text, actual_url, brand=domain_brand)

    seen = {n.canonical_name.lower() for n in nodes}
    for hit in _concept_occurrences(clean_text, vertical_id):
        if hit.canonical_name.lower() not in seen:
            hit.source_urls = [actual_url]
            nodes.append(hit)
            seen.add(hit.canonical_name.lower())

    # Ensure subject node exists
    subject_id = f"entity:{domain_brand.lower()}"
    if not any(n.canonical_name.lower() == domain_brand.lower() for n in nodes):
        nodes.insert(0, KGNode(
            id=subject_id,
            canonical_name=domain_brand,
            entity_type="SoftwarePlatform",
            mentions_count=1,
            source_urls=[actual_url]
        ))

    # 3. Relational Semantic Edges
    edges = _extract_semantic_edges(clean_text, domain_brand, nodes, actual_url, vertical_id=vertical_id)

    # 4. Classes and Predicates discovered
    classes_discovered = sorted(list(set(n.entity_type for n in nodes)))
    predicates_discovered = sorted(list(set(e.predicate for e in edges)))

    # 5. W3C Serializations
    export_jsonld = _build_jsonld_graph(actual_url, title, domain_brand, nodes, edges)
    export_turtle = _build_turtle_graph(actual_url, domain_brand, edges, nodes)

    return PageKnowledgeGraph(
        url=actual_url,
        title=title,
        nodes=nodes,
        edges=edges,
        classes_discovered=classes_discovered,
        predicates_discovered=predicates_discovered,
        embedded_schemas=embedded_schemas,
        # Set here rather than by the caller: this is the vocabulary the extraction above
        # actually ran with, so it cannot drift from what shaped the graph.
        vertical_id=vertical_id,
        export_jsonld=export_jsonld,
        export_turtle=export_turtle
    )
