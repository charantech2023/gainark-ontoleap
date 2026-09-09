"""
GainARK OntoLeap - Durable Knowledge Graph Store
================================================
A graph that survives the request that produced it.

`knowledge_graph.build_rdf_graph()` assembles an rdflib Graph, serialises it, and
returns the string. Nothing was retained, so the system could only ever answer one
question: does this page's marketing match this page's docs, right now. "What do we
know about Ordway", "what changed since last month" and "which vendors cover revenue
schedules" had nowhere to be asked.

rdflib 7 ships no persistent store plugin in this environment - only Memory and the
SPARQL remotes - so the store is quads in SQLite, which is stdlib.

Shape
-----
Two kinds of graph, matching the naming split in ontology_schema:

  ontology graph   one per vertical, id = the SKOS scheme URI. The concepts, their
                   labels, definitions and hierarchy. Shared by every client, written
                   idempotently, overwritten wholesale when the vertical changes.

  run graph        one per audit, id = https://{domain}/audit/{utc timestamp}. The
                   claims found on that crawl and their provenance. Never rewritten,
                   so history accumulates and two runs can be diffed.

Keeping runs as separate named graphs is what makes change-over-time free: a run is
already the unit of "when", so no triple needs a timestamp of its own to be placed in
time, and nothing has to be deleted to record that a claim went away.

Deployment note
---------------
The default location is under the repo. Cloud Run gives each instance an ephemeral
filesystem, so a container writing here keeps its graph only for the life of that
instance. Point ONTOLEAP_GRAPH_STORE at a mounted volume, or move to Cloud SQL,
before treating stored history as durable in a deployed environment.
"""

import os
import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

from rdflib import Graph, Dataset, URIRef, Literal, BNode
from rdflib.term import Node

logger = logging.getLogger("gainark.graph_store")

DEFAULT_STORE_PATH = os.environ.get(
    "ONTOLEAP_GRAPH_STORE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "truth_ledger", "graph.sqlite"),
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS graphs (
    graph_id    TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,           -- 'ontology' | 'run'
    domain      TEXT,
    vertical_id TEXT,
    created_at  TEXT NOT NULL,
    metadata    TEXT
);

CREATE TABLE IF NOT EXISTS quads (
    graph_id    TEXT NOT NULL REFERENCES graphs(graph_id) ON DELETE CASCADE,
    s           TEXT NOT NULL,
    s_kind      TEXT NOT NULL,
    p           TEXT NOT NULL,
    o           TEXT NOT NULL,
    o_kind      TEXT NOT NULL,           -- 'uri' | 'literal' | 'bnode'
    o_datatype  TEXT,
    o_lang      TEXT
);

-- Re-persisting the same graph must not double its contents. The ontology graph is
-- rewritten on every audit with identical triples, so without this the store grows
-- without bound while saying nothing new.
CREATE UNIQUE INDEX IF NOT EXISTS quads_unique
    ON quads (graph_id, s, p, o, o_kind, IFNULL(o_datatype,''), IFNULL(o_lang,''));

CREATE INDEX IF NOT EXISTS quads_spo ON quads (s, p);
CREATE INDEX IF NOT EXISTS quads_o   ON quads (o) WHERE o_kind = 'uri';
CREATE INDEX IF NOT EXISTS graphs_domain ON graphs (domain, created_at);
"""


# ---------------------------------------------------------------------------
# Term encoding
# ---------------------------------------------------------------------------

def _encode(term: Node) -> Tuple[str, str, Optional[str], Optional[str]]:
    """(value, kind, datatype, lang) for one RDF term."""
    if isinstance(term, URIRef):
        return str(term), "uri", None, None
    if isinstance(term, BNode):
        return str(term), "bnode", None, None
    if isinstance(term, Literal):
        return (str(term), "literal",
                str(term.datatype) if term.datatype else None,
                term.language or None)
    return str(term), "literal", None, None


def _decode(value: str, kind: str, datatype: Optional[str], lang: Optional[str]) -> Node:
    if kind == "uri":
        return URIRef(value)
    if kind == "bnode":
        return BNode(value)
    if datatype:
        return Literal(value, datatype=URIRef(datatype))
    return Literal(value, lang=lang) if lang else Literal(value)


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

@contextmanager
def connect(path: Optional[str] = None):
    """Open the store, creating it if absent. Commits on clean exit."""
    target = path or DEFAULT_STORE_PATH
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(target)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def run_graph_id(domain: str, when: Optional[datetime] = None) -> str:
    when = when or datetime.now(timezone.utc)
    return "https://%s/audit/%s" % (domain, when.strftime("%Y%m%dT%H%M%SZ"))


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def persist_graph(
    graph: Graph,
    graph_id: str,
    kind: str = "run",
    domain: Optional[str] = None,
    vertical_id: Optional[str] = None,
    metadata: Optional[str] = None,
    path: Optional[str] = None,
) -> int:
    """Store every triple of `graph` under `graph_id`. Returns quads written.

    Idempotent per graph: re-persisting the same content is a no-op rather than a
    duplicate, which is what lets the shared ontology graph be written on every audit
    without the store growing.
    """
    if kind not in ("ontology", "run"):
        raise ValueError("kind must be 'ontology' or 'run', got %r" % kind)

    rows = []
    for s, p, o in graph:
        s_val, s_kind, _, _ = _encode(s)
        o_val, o_kind, o_dt, o_lang = _encode(o)
        rows.append((graph_id, s_val, s_kind, str(p), o_val, o_kind, o_dt, o_lang))

    with connect(path) as conn:
        # Upsert, never INSERT OR REPLACE. REPLACE deletes the existing row before
        # re-inserting it, and quads cascade off graph_id - so re-persisting a graph
        # silently wiped every quad it already held before writing the new ones. That
        # turns "add what is new" into "erase and rewrite", which for a run graph would
        # destroy the historical record it exists to keep.
        conn.execute(
            "INSERT INTO graphs (graph_id, kind, domain, vertical_id, created_at, metadata)"
            " VALUES (?,?,?,?,?,?)"
            " ON CONFLICT(graph_id) DO UPDATE SET"
            "   kind=excluded.kind, domain=excluded.domain,"
            "   vertical_id=excluded.vertical_id, metadata=excluded.metadata",
            (graph_id, kind, domain, vertical_id,
             datetime.now(timezone.utc).isoformat(), metadata),
        )
        before = conn.execute(
            "SELECT COUNT(*) FROM quads WHERE graph_id = ?", (graph_id,)).fetchone()[0]
        conn.executemany(
            "INSERT OR IGNORE INTO quads"
            " (graph_id, s, s_kind, p, o, o_kind, o_datatype, o_lang)"
            " VALUES (?,?,?,?,?,?,?,?)", rows)
        after = conn.execute(
            "SELECT COUNT(*) FROM quads WHERE graph_id = ?", (graph_id,)).fetchone()[0]

    written = after - before
    logger.info("Persisted %d new quads to %s (%d submitted)", written, graph_id, len(rows))
    return written


def replace_graph(graph: Graph, graph_id: str, **kwargs) -> int:
    """Persist `graph_id` from scratch, dropping whatever was there.

    For the ontology graph, where a concept that has been removed or renamed must not
    linger. Never use it on a run graph: a run is a record of what was seen at a
    moment, and rewriting one falsifies history.
    """
    path = kwargs.get("path")
    with connect(path) as conn:
        conn.execute("DELETE FROM quads WHERE graph_id = ?", (graph_id,))
    return persist_graph(graph, graph_id, **kwargs)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def list_runs(domain: Optional[str] = None, limit: int = 50,
              path: Optional[str] = None) -> List[Dict]:
    """Audit runs, newest first."""
    sql = ("SELECT g.graph_id, g.domain, g.vertical_id, g.created_at, g.metadata,"
           " (SELECT COUNT(*) FROM quads q WHERE q.graph_id = g.graph_id)"
           " FROM graphs g WHERE g.kind = 'run'")
    args: List = []
    if domain:
        sql += " AND g.domain = ?"
        args.append(domain)
    sql += " ORDER BY g.created_at DESC LIMIT ?"
    args.append(limit)
    with connect(path) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [
        {"graph_id": r[0], "domain": r[1], "vertical_id": r[2],
         "created_at": r[3], "metadata": r[4], "quads": r[5]}
        for r in rows
    ]


def load_graph(graph_id: str, path: Optional[str] = None) -> Graph:
    """One stored graph, back as rdflib - so SPARQL still works on it."""
    g = Graph()
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT s, s_kind, p, o, o_kind, o_datatype, o_lang FROM quads WHERE graph_id = ?",
            (graph_id,)).fetchall()
    for s, s_kind, p, o, o_kind, o_dt, o_lang in rows:
        g.add((_decode(s, s_kind, None, None), URIRef(p), _decode(o, o_kind, o_dt, o_lang)))
    return g


def load_dataset(graph_ids: Optional[Iterable[str]] = None,
                 path: Optional[str] = None) -> Dataset:
    """Several stored graphs as one queryable Dataset, each kept as a named graph.

    Named rather than merged so a SPARQL query can still tell which run a triple came
    from - the whole reason runs are stored separately.
    """
    ds = Dataset()
    with connect(path) as conn:
        if graph_ids is None:
            ids = [r[0] for r in conn.execute("SELECT graph_id FROM graphs").fetchall()]
        else:
            ids = list(graph_ids)
        for gid in ids:
            g = ds.graph(URIRef(gid))
            for s, s_kind, p, o, o_kind, o_dt, o_lang in conn.execute(
                "SELECT s, s_kind, p, o, o_kind, o_datatype, o_lang"
                " FROM quads WHERE graph_id = ?", (gid,)):
                g.add((_decode(s, s_kind, None, None), URIRef(p),
                       _decode(o, o_kind, o_dt, o_lang)))
    return ds


# Predicates that record HOW a run happened rather than WHAT it found. Every one of
# them differs between any two runs - a fresh timestamp, a fresh activity id - so a raw
# diff reports a dozen changes for a single changed claim and buries the answer.
_PROVENANCE_PREFIXES = (
    "http://www.w3.org/ns/prov#",
    "http://purl.org/dc/terms/",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
)


def diff_runs(earlier_id: str, later_id: str, claims_only: bool = True,
              path: Optional[str] = None) -> Dict[str, List[Tuple[str, str, str]]]:
    """What appeared and what disappeared between two runs.

    `claims_only` drops provenance, which is what makes the result readable: the
    question being asked is almost always "what does this vendor claim now that it did
    not before", not "did the audit run at a different time".

    Answered in SQL rather than by loading both graphs, because the interesting case is
    a long history where most runs are never loaded.
    """
    q = ("SELECT s, p, o FROM quads WHERE graph_id = ?"
         " EXCEPT SELECT s, p, o FROM quads WHERE graph_id = ?")
    with connect(path) as conn:
        added = conn.execute(q, (later_id, earlier_id)).fetchall()
        removed = conn.execute(q, (earlier_id, later_id)).fetchall()

    def keep(row) -> bool:
        return not any(str(row[1]).startswith(pfx) for pfx in _PROVENANCE_PREFIXES)

    if claims_only:
        added = [r for r in added if keep(r)]
        removed = [r for r in removed if keep(r)]
    return {"added": [tuple(r) for r in added], "removed": [tuple(r) for r in removed]}


def vendors_covering(concept_uri: str, path: Optional[str] = None) -> List[Dict]:
    """Every domain whose stored graphs reference this concept, and when.

    This query is the point of the shared concept namespace. While concepts were minted
    under each audited client's own domain, the same concept was a different resource in
    every graph and no question could span vendors - there was no subject to ask about.
    """
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT g.domain, COUNT(DISTINCT g.graph_id), MAX(g.created_at)"
            " FROM quads q JOIN graphs g ON g.graph_id = q.graph_id"
            " WHERE q.o = ? AND q.o_kind = 'uri' AND g.kind = 'run'"
            " GROUP BY g.domain ORDER BY 3 DESC",
            (concept_uri,)).fetchall()
    return [{"domain": r[0], "runs": r[1], "last_seen": r[2]} for r in rows]


def split_by_namespace(graph: Graph, ontology_base: str) -> Tuple[Graph, Graph]:
    """Separate the shared ontology from this client's instance data.

    A built audit graph holds both: the vertical's concepts, identical for every client,
    and the claims found on one site. Stored together they would repeat the entire
    ontology once per run, and a query for "the concepts" would have to know which run
    to look in.

    The subject's namespace decides. That works only because concepts are minted in a
    shared namespace rather than under the audited domain.
    """
    ontology, instance = Graph(), Graph()
    for s, p, o in graph:
        (ontology if str(s).startswith(ontology_base) else instance).add((s, p, o))
    return ontology, instance


def persist_audit(
    graph: Graph,
    domain: str,
    vertical_id: str,
    when: Optional[datetime] = None,
    metadata: Optional[str] = None,
    path: Optional[str] = None,
) -> Dict:
    """Store one audit: its claims as a new run, its concepts into the shared ontology.

    The ontology graph is replaced rather than appended, so a concept that has been
    renamed or dropped does not linger; the run graph is new every time, so history is
    never rewritten.
    """
    from ontology_schema import ONTOLOGY_BASE, scheme_uri

    ontology, instance = split_by_namespace(graph, ONTOLOGY_BASE)
    gid = run_graph_id(domain, when)

    run_quads = persist_graph(
        instance, gid, kind="run", domain=domain, vertical_id=vertical_id,
        metadata=metadata, path=path)
    onto_quads = replace_graph(
        ontology, scheme_uri(vertical_id), kind="ontology",
        vertical_id=vertical_id, path=path)

    return {"run_graph": gid, "run_quads": run_quads,
            "ontology_graph": scheme_uri(vertical_id), "ontology_quads": onto_quads}


def store_stats(path: Optional[str] = None) -> Dict:
    with connect(path) as conn:
        graphs = conn.execute(
            "SELECT kind, COUNT(*) FROM graphs GROUP BY kind").fetchall()
        quads = conn.execute("SELECT COUNT(*) FROM quads").fetchone()[0]
        domains = conn.execute(
            "SELECT COUNT(DISTINCT domain) FROM graphs WHERE domain IS NOT NULL").fetchone()[0]
    return {"graphs": dict(graphs), "quads": quads, "domains": domains,
            "path": path or DEFAULT_STORE_PATH}
