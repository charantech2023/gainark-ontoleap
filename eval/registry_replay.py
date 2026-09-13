"""
Replay stored crawls through site-graph assembly, so an identity change is measured on
real pages rather than judged from a handful of fixtures.

A crawl job keeps every page's raw nodes and edges in its state. Assembly - resolving
those into one graph - is deterministic given that state, so replaying the same state
through two versions of the code isolates exactly what the change did. A fresh crawl
cannot: page timeouts vary run to run, and the difference would be half crawl noise.

    # fetch job state from the configured archive (ONTOLEAP_GRAPH_ARCHIVE) and measure
    python eval/registry_replay.py measure --jobs 4dd9ad460a55443e9f2d377484dbb0f4 --out after.json

    # compare two measurements, and two runs of one domain for id stability
    python eval/registry_replay.py compare before.json after.json

Job state is cached under .ontoleap_cache/replay/ (gitignored): it holds a client's
crawled pages and does not belong in the repository.
"""

import argparse
import json
import os
import re
import sys
import tempfile
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

CACHE_DIR = os.path.join(ROOT, ".ontoleap_cache", "replay")
GOLD_PATH = os.path.join(HERE, "entity_gold.json")


def load_job(job_id):
    """A stored crawl job, from the local cache or the archive."""
    path = os.path.join(CACHE_DIR, "%s.json" % job_id)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    from graph_archive import open_archive
    archive = open_archive()
    if archive is None:
        raise SystemExit("No cached state for %s and ONTOLEAP_GRAPH_ARCHIVE is unset." % job_id)
    payload = archive.get("jobs/%s/job.json" % job_id)
    if payload is None:
        raise SystemExit("Job %s is not in the archive." % job_id)
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(path, "wb") as f:
        f.write(payload)
    return json.loads(payload.decode("utf-8"))


def surface_key(name):
    """A deliberately blunt key for counting duplicates that no reader would keep apart.

    Case, punctuation, a trailing plural, a legal suffix or a trailing .com. It is a
    yardstick, not a resolver: it must not share code with the thing it measures.
    """
    s = name.lower()
    s = re.sub(r"\.(com|io|ai|co)\b", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(r"\b(inc|llc|ltd|plc|corp)\b", "", s).strip()
    words = s.split()
    if words and len(words[-1]) > 3 and words[-1].endswith("s") and not words[-1].endswith("ss"):
        words[-1] = words[-1][:-1]
    return " ".join(words)


def load_gold():
    if not os.path.exists(GOLD_PATH):
        return {"same": [], "different": []}
    with open(GOLD_PATH, encoding="utf-8") as f:
        return json.load(f)


def _name_to_id(kg):
    """Every observed surface form and canonical name, lowercased, mapped to its node id."""
    out = {}
    for n in list(kg.nodes) + list(getattr(kg, "mentions", []) or []):
        for form in [n.canonical_name] + list(n.aliases or []):
            out.setdefault(form.strip().lower(), n.id)
    return out


def gold_violations(name_to_id, gold, mention_forms=None):
    """Gold judgements the graph got wrong. A judgement whose forms did not occur is skipped.

    split     a `same` pair that ended up as two identities
    merged    a `different` pair that ended up as one - the worse error
    misfiled  a form listed under `nodes` that became a mention, or under `mentions`
              that became a node
    """
    split, merged, misfiled = [], [], []
    for a, b in gold.get("same", []):
        ia, ib = name_to_id.get(a.lower()), name_to_id.get(b.lower())
        if ia and ib and ia != ib:
            split.append([a, b])
    for a, b in gold.get("different", []):
        ia, ib = name_to_id.get(a.lower()), name_to_id.get(b.lower())
        if ia and ib and ia == ib:
            merged.append([a, b])
    if mention_forms is not None:
        for form in gold.get("nodes", []):
            if form.lower() in name_to_id and form.lower() in mention_forms:
                misfiled.append(form)
        for form in gold.get("mentions", []):
            if form.lower() in name_to_id and form.lower() not in mention_forms:
                misfiled.append(form)
    return split, merged, misfiled


def measure_job(job_id, gold):
    from site_graph import assemble_site_kg
    from industry_ontology import align_graph_with_industry

    job = load_job(job_id)
    kg = assemble_site_kg(job["state"], job["vertical_id"], job["max_pages"], persist=False)
    alignment = align_graph_with_industry(kg)

    things = [g for g in kg.export_jsonld["@graph"] if g.get("@type") == "Thing"]
    groups = defaultdict(list)
    for g in things:
        groups[surface_key(g["name"])].append(g["name"])
    dup_groups = sorted([sorted(v) for v in groups.values() if len(v) > 1])

    name_to_id = _name_to_id(kg)
    mentions = getattr(kg, "mentions", None) or []
    mention_forms = {f.strip().lower() for m in mentions for f in [m.canonical_name] + list(m.aliases)}
    split, merged, misfiled = gold_violations(name_to_id, gold, mention_forms)

    return {
        "job_id": job_id,
        "domain": kg.domain,
        "raw_nodes": len(job["state"]["nodes"]),
        "nodes": len(kg.nodes),
        "mentions": len(mentions),
        "edges": len(kg.edges),
        "jsonld_things": len(things),
        "sameas": sum(1 for g in things if g.get("sameAs")),
        "sameas_links": sorted("%s -> %s" % (g["name"], g["sameAs"].rsplit("/", 1)[-1])
                               for g in things if g.get("sameAs")),
        "duplicate_groups": len(dup_groups),
        "duplicate_examples": dup_groups,
        "gold_split": split,
        "gold_merged": merged,
        "gold_misfiled": misfiled,
        "resolution": getattr(kg, "resolution_summary", {}),
        "coverage_score": alignment.coverage_score,
        "seed_coverage_score": alignment.seed_coverage_score,
        "covered_concepts": sorted(alignment.covered_concepts),
        "compliance_covered": sorted(alignment.compliance_standards_covered),
        "integrations_covered": sorted(alignment.integrations_covered),
        "proprietary_concepts": alignment.proprietary_concepts,
        "turtle": kg.export_turtle,
        "name_to_id": name_to_id,
    }


def run_diff(turtle_a, turtle_b):
    """Claims added and removed between two runs, through the real store's diff."""
    import graph_store
    from rdflib import Graph
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "replay.sqlite")
        for gid, ttl in (("urn:run:a", turtle_a), ("urn:run:b", turtle_b)):
            g = Graph()
            g.parse(data=ttl, format="turtle")
            graph_store.persist_graph(g, gid, kind="run", domain="replay", path=path,
                                      archive_write=False)
        d = graph_store.diff_runs("urn:run:a", "urn:run:b", path=path)
    return len(d["added"]), len(d["removed"])


def cmd_measure(args):
    gold = load_gold()
    results = [measure_job(j, gold) for j in args.jobs]
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    for r in results:
        print("%-22s raw %4d -> nodes %4d (+%d mentions) | things %4d sameAs %3d | "
              "dup groups %3d | gold split %d merged %d misfiled %d | coverage %.1f%%"
              % (r["domain"], r["raw_nodes"], r["nodes"], r["mentions"], r["jsonld_things"],
                 r["sameas"], r["duplicate_groups"], len(r["gold_split"]),
                 len(r["gold_merged"]), len(r.get("gold_misfiled", [])), r["coverage_score"]))


def cmd_compare(args):
    with open(args.before, encoding="utf-8") as f:
        before = {r["job_id"]: r for r in json.load(f)}
    with open(args.after, encoding="utf-8") as f:
        after = {r["job_id"]: r for r in json.load(f)}

    for job_id, a in after.items():
        b = before.get(job_id)
        if not b:
            continue
        print("\n== %s (%s)" % (a["domain"], job_id[:8]))
        for key in ("nodes", "mentions", "jsonld_things", "sameas", "duplicate_groups",
                    "coverage_score", "seed_coverage_score"):
            print("  %-20s %8s -> %s" % (key, b[key], a[key]))
        for key in ("covered_concepts", "compliance_covered", "integrations_covered"):
            lost = sorted(set(b[key]) - set(a[key]))
            gained = sorted(set(a[key]) - set(b[key]))
            print("  %-20s %s" % (key, "unchanged" if not (lost or gained)
                                  else "lost %s gained %s" % (lost, gained)))
        print("  sameAs lost   %s" % sorted(set(b["sameas_links"]) - set(a["sameas_links"])))
        print("  sameAs gained %s" % sorted(set(a["sameas_links"]) - set(b["sameas_links"])))
        print("  gold split %s | gold merged %s | misfiled %s"
              % (a["gold_split"], a["gold_merged"], a.get("gold_misfiled", [])))
        print("  duplicates left %s" % a["duplicate_examples"])

    # Two runs of the same domain: do forms keep their ids, and how noisy is the diff?
    by_domain = defaultdict(list)
    for r in after.values():
        by_domain[r["domain"]].append(r)
    for domain, runs in by_domain.items():
        if len(runs) < 2:
            continue
        r1, r2 = runs[0], runs[1]
        shared = set(r1["name_to_id"]) & set(r2["name_to_id"])
        same = sum(1 for k in shared if r1["name_to_id"][k] == r2["name_to_id"][k])
        added, removed = run_diff(r1["turtle"], r2["turtle"])
        rb1, rb2 = before.get(r1["job_id"]), before.get(r2["job_id"])
        line = ("\n== %s, two runs: %d/%d shared forms keep their id; diff +%d -%d"
                % (domain, same, len(shared), added, removed))
        if rb1 and rb2:
            ba, br = run_diff(rb1["turtle"], rb2["turtle"])
            line += " (before: +%d -%d)" % (ba, br)
        print(line)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--jobs", nargs="+", required=True)
    m.add_argument("--out", required=True)
    c = sub.add_parser("compare")
    c.add_argument("before")
    c.add_argument("after")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    {"measure": cmd_measure, "compare": cmd_compare}[args.cmd](args)


if __name__ == "__main__":
    main()
