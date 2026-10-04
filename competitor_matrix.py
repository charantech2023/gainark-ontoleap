"""
competitor_matrix.py - what each company claims about each concept, beside each other.

Phase 3 of COMPETITOR_PROFILES_DESIGN.md. For a customer site, one row per concept of its
vertical and one cell per company - the customer and every competitor in its set - read
from the latest stored run of each domain. Computed on request, never stored: it is a pure
function of the runs (design §6.4).

A cell is one of four states, strongest first (§4):

    claimed     a claim in the run whose subject is the company and whose object is the
                concept, with the sentence and the page that prove it
    mentioned   the concept is in the company's run graph, but no sentence claims it
    not_found   neither, in the pages that run read
    not_read    no usable run for the company (recurly.com: a bot check)

"Absent is not the same as does not offer" (§3): not_found names how many pages were read,
and not_read is never folded into it.

A row's verdict compares the customer's claim with the competitors' claims only:
customer_only, competitor_only, both, or neither. A competitor that was not read cannot
make a row competitor_only or both, so the verdicts only say what the pages show.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from rdflib import RDF, URIRef
from rdflib.namespace import PROV

logger = logging.getLogger("gainark.competitor_matrix")

_SOFTWARE = URIRef("http://schema.org/SoftwareApplication")
STATES = ("claimed", "mentioned", "not_found", "not_read")


def latest_run(domain: str, path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The newest stored run of a domain that holds anything."""
    import graph_store
    for run in graph_store.list_runs(domain=domain, limit=20, path=path):
        if run.get("quads"):
            return run
    return None


def read_run(graph) -> Dict[str, Any]:
    """Claims and mentions of concepts in one run graph."""
    brand = next(graph.subjects(RDF.type, _SOFTWARE), None)
    claims: Dict[str, List[Dict[str, str]]] = {}
    for st in graph.subjects(RDF.type, RDF.Statement):
        obj = str(graph.value(st, RDF.object) or "")
        if "/concept/" not in obj or graph.value(st, RDF.subject) != brand:
            continue
        predicate = str(graph.value(st, RDF.predicate) or "").rsplit("/", 1)[-1]
        claims.setdefault(obj, []).append({
            "predicate": predicate,
            "quote": str(graph.value(st, PROV.value) or ""),
            "url": str(graph.value(st, PROV.wasDerivedFrom) or ""),
        })
    seen = {str(o) for _, _, o in graph if "/concept/" in str(o)}
    return {"brand": str(brand or ""), "claims": claims, "mentioned": seen - set(claims)}


def _company_columns(site: str) -> List[Dict[str, Any]]:
    """The customer first, then every competitor in its set that a reviewer has not
    removed - crawlable or not, so an unread competitor shows as not_read, not missing."""
    import competitor_set
    cols = [{"name": site, "domain": site, "role": "customer"}]
    for c in competitor_set.load(site).get("competitors") or []:
        if c.get("status") == "removed" or not c.get("domain"):
            continue
        cols.append({"name": c["name"], "domain": c["domain"], "role": "competitor",
                     "status": c.get("status")})
    return cols


def matrix(site: str, vertical_id: str, concept: Optional[str] = None,
           path: Optional[str] = None) -> Dict[str, Any]:
    """The matrix for a customer site, optionally for one concept (label or id)."""
    import graph_store
    from industry_ontology import load_industry_ontology
    from ontology_schema import concept_uri

    concepts = load_industry_ontology(vertical_id).concepts or []
    if concept:
        wanted = concept.strip().lower()
        concepts = [c for c in concepts if wanted in (c.id.lower(), c.pref_label.lower())]

    columns = _company_columns(site)
    read: Dict[str, Optional[Dict[str, Any]]] = {}
    coverage = {}
    for col in columns:
        run = latest_run(col["domain"], path=path)
        if run is None:
            read[col["domain"]] = None
            coverage[col["domain"]] = {"run": None, "pages_read": 0}
            continue
        meta = json.loads(run.get("metadata") or "{}")
        read[col["domain"]] = dict(read_run(graph_store.load_graph(run["graph_id"], path=path)),
                                   run=run["graph_id"], pages=meta.get("pages_crawled"))
        coverage[col["domain"]] = {"run": run["graph_id"], "created_at": run.get("created_at"),
                                   "pages_read": meta.get("pages_crawled")}

    rows = []
    for c in concepts:
        uri = concept_uri(vertical_id, c.id)
        cells = {}
        for col in columns:
            r = read[col["domain"]]
            if r is None:
                cells[col["domain"]] = {"state": "not_read"}
            elif uri in r["claims"]:
                first = r["claims"][uri][0]
                cells[col["domain"]] = dict(first, state="claimed", run=r["run"],
                                            claims=len(r["claims"][uri]))
            elif uri in r["mentioned"]:
                cells[col["domain"]] = {"state": "mentioned", "run": r["run"]}
            else:
                cells[col["domain"]] = {"state": "not_found", "pages_read": r["pages"], "run": r["run"]}
        ours = cells[site]["state"] == "claimed"
        theirs = any(cells[col["domain"]]["state"] == "claimed" for col in columns[1:])
        verdict = ("both" if ours and theirs else "customer_only" if ours
                   else "competitor_only" if theirs else "neither")
        rows.append({"concept": uri, "label": c.pref_label, "cells": cells, "verdict": verdict})

    for col in columns:
        states = [r["cells"][col["domain"]]["state"] for r in rows]
        coverage[col["domain"]].update({s: states.count(s) for s in STATES})
        decided = states.count("claimed") + states.count("mentioned")
        coverage[col["domain"]]["determinate_share"] = round(decided / len(states), 3) if states else 0.0

    verdicts = [r["verdict"] for r in rows]
    return {
        "site": site, "vertical_id": vertical_id, "columns": columns, "coverage": coverage,
        "verdicts": {v: verdicts.count(v) for v in ("customer_only", "competitor_only", "both", "neither")},
        "rows": rows,
    }


if __name__ == "__main__":
    import sys
    print(json.dumps(matrix(sys.argv[1], sys.argv[2]), indent=1, ensure_ascii=False))
