"""Seed the evaluation set from real extractions.

Writing a labelled set from a blank page is slow and biased towards what you remember the
extractor doing. Seeding it from actual output means the reviewer marks yes/no against a
sentence the crawler really found, which is a much smaller ask and produces a set that
covers what the system actually emits.

Two kinds of candidate row are produced:

  extracted=yes   a relationship the extractor asserted. Marking one `no` records a false
                  positive, which is how precision gets measured.
  extracted=no    a vocabulary term that appears in the page text but that no edge
                  mentions. Marking one `yes` records a false negative, which is how
                  recall gets measured. Without these the set can only ever flatter the
                  extractor: it would contain nothing the extractor failed to find.

Usage:
    python eval/seed_candidates.py            # append candidates for the default pages
    python eval/seed_candidates.py URL:VERTICAL ...
"""
import csv
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from page_graph import build_page_kg          # noqa: E402
from industry_ontology import load_industry_ontology  # noqa: E402
from scraper import smart_fetch               # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RELATIONS = os.path.join(HERE, "relations.csv")

FIELDS = ["url", "vertical_id", "subject", "predicate", "object",
          "extracted", "correct", "evidence"]

DEFAULT_PAGES = [
    ("https://ordwaylabs.com/products/usage-based-billing-software/", "b2b_saas_fintech"),
    ("https://ordwaylabs.com/products/revenue-recognition-software-asc-606-ifrs-15/", "b2b_saas_fintech"),
    ("https://ordwaylabs.com/products/integrations/", "b2b_saas_fintech"),
]


def _page_text(url: str) -> str:
    try:
        import trafilatura
        html = smart_fetch(url)
        return (trafilatura.extract(html) or html or "").lower()
    except Exception as err:
        print("    ! could not read page text: %s" % err)
        return ""


def _recall_candidates(kg, vertical_id, text):
    """Vocabulary terms the page mentions that no edge accounts for."""
    if not text:
        return []
    industry = load_industry_ontology(vertical_id)
    covered = {e.target.strip().lower() for e in kg.edges}
    terms = list(industry.known_compliance) + list(industry.known_integrations)
    terms += [c.pref_label for c in industry.concepts]

    out, seen = [], set()
    for term in terms:
        low = term.strip().lower()
        if not low or low in covered or low in seen or len(low) < 4:
            continue
        if re.search(r"\b%s\b" % re.escape(low), text):
            seen.add(low)
            out.append(term)
    return out[:15]


def seed(pages):
    existing = set()
    rows = []
    if os.path.exists(RELATIONS):
        with open(RELATIONS, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                rows.append(r)
                existing.add((r["url"], r["subject"].lower(), r["predicate"], r["object"].lower()))

    added = 0
    for url, vertical_id in pages:
        print("[*] %s (%s)" % (url, vertical_id))
        kg = build_page_kg(url_or_html=url, url=url, vertical_id=vertical_id)
        subject = kg.nodes[0].canonical_name if kg.nodes else "Unknown"
        print("    %d nodes, %d edges" % (len(kg.nodes), len(kg.edges)))

        for e in kg.edges:
            key = (url, e.source.lower(), e.predicate, e.target.lower())
            if key in existing:
                continue
            existing.add(key)
            rows.append({
                "url": url, "vertical_id": vertical_id,
                "subject": e.source, "predicate": e.predicate, "object": e.target,
                "extracted": "yes", "correct": "",
                "evidence": (e.provenance_sentence or "").replace("\n", " ")[:200],
            })
            added += 1

        text = _page_text(url)
        missed = _recall_candidates(kg, vertical_id, text)
        print("    %d recall candidates the page mentions but no edge covers" % len(missed))
        for term in missed:
            key = (url, subject.lower(), "?", term.lower())
            if key in existing:
                continue
            existing.add(key)
            rows.append({
                "url": url, "vertical_id": vertical_id,
                "subject": subject, "predicate": "?", "object": term,
                "extracted": "no", "correct": "",
                "evidence": "MENTIONED ON PAGE, NOT EXTRACTED - set predicate if this is a real claim",
            })
            added += 1

    with open(RELATIONS, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    print("\nWrote %s: %d rows (%d new)" % (RELATIONS, len(rows), added))
    print("Now fill the `correct` column with yes or no. Rows left blank are skipped.")


if __name__ == "__main__":
    args = sys.argv[1:]
    pages = DEFAULT_PAGES
    if args:
        pages = []
        for a in args:
            u, _, v = a.rpartition(":")
            pages.append((u, v or "b2b_saas_fintech"))
    seed(pages)
