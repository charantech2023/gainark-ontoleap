"""Measure the extractor against the labelled set.

Answers one question: is extraction getting better or worse. Every change to the
vocabulary, the matcher or the pipeline can be judged against a number instead of against
whoever last read the output and formed an impression.

Two numbers per predicate, because they trade against each other and one alone is
misleading:

  precision   of the relationships the extractor asserted, how many are true.
              Falls when the extractor invents things.
  recall      of the true relationships, how many it found.
              Falls when the extractor misses things.

A change that raises recall by loosening a match will usually lower precision. Watching
only recall makes that look like progress.

Usage:
    python eval/run_eval.py                  # measure, and compare to the baseline
    python eval/run_eval.py --save-baseline  # record today's numbers as the baseline
"""
import csv
import json
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from page_graph import build_page_kg  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
RELATIONS = os.path.join(HERE, "relations.csv")
BASELINE = os.path.join(HERE, "baseline.json")


def _norm(value):
    return (value or "").strip().lower()


def _load_labels():
    """Labelled rows only. A blank `correct` is unreviewed, not a negative."""
    truth, claimed_false, skipped = defaultdict(set), defaultdict(set), 0
    pages = {}
    with open(RELATIONS, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            verdict = _norm(row["correct"])
            if verdict not in ("yes", "no"):
                skipped += 1
                continue
            url = row["url"]
            pages[url] = row["vertical_id"]
            triple = (_norm(row["subject"]), row["predicate"].strip(), _norm(row["object"]))
            if verdict == "yes":
                if triple[1] == "?":
                    # A recall candidate marked true but left without a predicate cannot
                    # be matched against an extracted edge; count it as a miss, loudly.
                    truth[url].add((triple[0], "UNSPECIFIED", triple[2]))
                else:
                    truth[url].add(triple)
            else:
                claimed_false[url].add(triple)
    return truth, claimed_false, pages, skipped


def _extract(url, vertical_id):
    kg = build_page_kg(url_or_html=url, url=url, vertical_id=vertical_id)
    return {(_norm(e.source), e.predicate.strip(), _norm(e.target)) for e in kg.edges}


def _score(tp, fp, fn):
    precision = tp / (tp + fp) if (tp + fp) else None
    recall = tp / (tp + fn) if (tp + fn) else None
    return precision, recall


def main(save_baseline=False):
    if not os.path.exists(RELATIONS):
        print("No labelled set at %s. Run eval/seed_candidates.py first." % RELATIONS)
        return 1

    truth, claimed_false, pages, skipped = _load_labels()
    if not pages:
        print("Nothing labelled yet: every `correct` cell in relations.csv is blank.")
        print("Fill in yes/no and run again. %d rows are waiting." % skipped)
        return 1

    per_pred = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})
    unspecified = 0

    for url, vertical_id in sorted(pages.items()):
        print("[*] %s" % url)
        extracted = _extract(url, vertical_id)
        expected = truth.get(url, set())
        known_false = claimed_false.get(url, set())

        for triple in expected:
            if triple[1] == "UNSPECIFIED":
                unspecified += 1
                continue
            bucket = per_pred[triple[1]]
            if triple in extracted:
                bucket["tp"] += 1
            else:
                bucket["fn"] += 1

        # Only count a false positive when the reviewer actually judged it false.
        # An extracted triple nobody has reviewed is unknown, not wrong.
        for triple in extracted & known_false:
            per_pred[triple[1]]["fp"] += 1

        print("    extracted %d, labelled true %d, labelled false %d"
              % (len(extracted), len(expected), len(known_false)))

    print()
    print("%-24s %9s %9s %6s %6s %6s" % ("predicate", "precision", "recall", "tp", "fp", "fn"))
    print("-" * 64)
    results, tot = {}, {"tp": 0, "fp": 0, "fn": 0}
    for pred in sorted(per_pred):
        b = per_pred[pred]
        p, r = _score(b["tp"], b["fp"], b["fn"])
        results[pred] = {"precision": p, "recall": r, **b}
        for k in tot:
            tot[k] += b[k]
        print("%-24s %9s %9s %6d %6d %6d" % (
            pred,
            "n/a" if p is None else "%.2f" % p,
            "n/a" if r is None else "%.2f" % r,
            b["tp"], b["fp"], b["fn"]))

    p, r = _score(tot["tp"], tot["fp"], tot["fn"])
    results["_overall"] = {"precision": p, "recall": r, **tot}
    print("-" * 64)
    print("%-24s %9s %9s %6d %6d %6d" % (
        "OVERALL",
        "n/a" if p is None else "%.2f" % p,
        "n/a" if r is None else "%.2f" % r,
        tot["tp"], tot["fp"], tot["fn"]))

    if skipped:
        print("\n%d row(s) unreviewed and skipped." % skipped)
    if unspecified:
        print("%d row(s) marked true but left with predicate '?'; counted as neither."
              % unspecified)

    if save_baseline:
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(results, fh, indent=2)
        print("\nBaseline written to %s" % BASELINE)
        return 0

    if os.path.exists(BASELINE):
        with open(BASELINE, encoding="utf-8") as fh:
            base = json.load(fh)
        print("\nAgainst baseline:")
        regressed = False
        for pred in sorted(set(base) | set(results)):
            b, n = base.get(pred, {}), results.get(pred, {})
            for metric in ("precision", "recall"):
                ov, nv = b.get(metric), n.get(metric)
                if ov is None or nv is None:
                    continue
                delta = nv - ov
                if abs(delta) >= 0.005:
                    mark = "WORSE" if delta < 0 else "better"
                    if delta < 0:
                        regressed = True
                    print("  %-22s %-9s %.2f -> %.2f  (%+.2f) %s"
                          % (pred, metric, ov, nv, delta, mark))
        if regressed:
            print("\nRegression against the baseline.")
            return 2
        print("  no regression")
    else:
        print("\nNo baseline yet. Run with --save-baseline to record these numbers.")
    return 0


if __name__ == "__main__":
    sys.exit(main(save_baseline="--save-baseline" in sys.argv))
