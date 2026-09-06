"""
Competitor change tracking. Closes ANTIGRAVITY_TASKS.md Task 5.

Competitive intelligence is mostly noticing change, and the platform has been
recording every audit to truth_ledger/log.md since it was built - write-only, never
read back. This suite covers the module that reads it.

The hard part is not the diff. It is refusing to report noise as news. This crawler
produced 15, 10 and 5 technical capabilities for one site inside a single morning, and
the ledger has a brand moving 80.0% -> 23.5% grounding in 55 seconds. Telling a product
marketer "Chargebee dropped their SOC 2 claim" on that basis would be wrong more often
than right, and wrong in a way that reaches a customer.

Offline: writes to a temporary history file, no network and no GLiNER.
"""

import io
import json
import os
import tempfile

import truth_ledger.history as history


def snapshot(brand, recorded_at, verified=(), unbacked=(), tech=10, mgi=50.0,
             status="conclusive"):
    def claims(items):
        return [{"predicate": "automates", "object": o,
                 "key": history._claim_key("automates", o)} for o in items]
    return {
        "recorded_at": recorded_at, "brand": brand, "domain": "https://x.example",
        "source": "test", "grounding_index": mgi, "evidence_status": status,
        "total_marketing_claims": len(verified) + len(unbacked),
        "total_technical_capabilities": tech,
        "verified": claims(verified), "unbacked": claims(unbacked), "hidden": [],
    }


def with_temp_history(snapshots):
    """Point the module at a throwaway history file containing `snapshots`."""
    tmp = tempfile.mkstemp(suffix=".jsonl")[1]
    with io.open(tmp, "w", encoding="utf-8") as f:
        for s in snapshots:
            f.write(json.dumps(s) + "\n")
    history.HISTORY_FILE = tmp
    return tmp


def test_detects_a_new_claim():
    print("\n[1] A claim appearing between audits ...")
    with_temp_history([
        snapshot("Chargebee", "2026-08-01T09:00:00+00:00", verified=["Invoicing"]),
        snapshot("Chargebee", "2026-08-22T09:00:00+00:00", verified=["Invoicing", "SOC 2"]),
    ])
    t = history.brand_timeline("Chargebee")
    change = t["changes"][0]
    added = [c["object"] for c in change["added"]]
    print("  added: %s | dropped: %s | %.0f days apart"
          % (added, [c["object"] for c in change["dropped"]], change["hours_apart"] / 24))

    assert added == ["SOC 2"], added
    assert not change["dropped"]
    assert not change["low_confidence"], (
        "Three weeks apart with a stable capability count is exactly the case that "
        "should be reportable. Flagging it makes the feature useless."
    )
    print("  PASS - reported, and not flagged.")


def test_audits_minutes_apart_are_not_news():
    """The failure mode the real ledger is full of."""
    print("\n[2] Audits a minute apart ...")
    with_temp_history([
        snapshot("Ordway", "2026-08-01T09:00:00+00:00", verified=["ASC 606"], mgi=80.0, tech=15),
        snapshot("Ordway", "2026-08-01T09:00:55+00:00", verified=["Revenue Recognition"], mgi=23.5, tech=10),
    ])
    change = history.brand_timeline("Ordway")["changes"][0]
    print("  grounding %s -> %s, audits 55 seconds apart"
          % (change["grounding_from"], change["grounding_to"]))
    for n in change["notes"]:
        print("   ! %s" % n[:96])

    assert change["low_confidence"], (
        "A brand cannot change what it claims in 55 seconds. Reporting this as a real "
        "change is how a product marketer ends up telling a customer something false."
    )
    assert any("minute" in n for n in change["notes"])
    print("  PASS - flagged as measurement noise.")


def test_unreadable_docs_are_not_a_retraction():
    print("\n[3] One audit could not read the docs ...")
    with_temp_history([
        snapshot("Rival", "2026-08-01T09:00:00+00:00", verified=["HIPAA", "SOC 2"], tech=12),
        snapshot("Rival", "2026-09-01T09:00:00+00:00", verified=[], tech=0,
                 mgi=None, status="inconclusive"),
    ])
    change = history.brand_timeline("Rival")["changes"][0]
    print("  dropped: %s" % [c["object"] for c in change["dropped"]])
    assert change["low_confidence"], (
        "Every claim vanished because the crawl failed, not because the brand retracted "
        "anything. Unflagged, this reads as a competitor abandoning HIPAA."
    )
    assert any("could not read" in n for n in change["notes"])
    print("  PASS - flagged rather than reported as a retraction.")


def test_identical_audits_report_nothing():
    print("\n[4] Nothing changed ...")
    with_temp_history([
        snapshot("Steady", "2026-08-01T09:00:00+00:00", verified=["Invoicing", "Dunning"]),
        snapshot("Steady", "2026-09-01T09:00:00+00:00", verified=["Dunning", "Invoicing"]),
    ])
    change = history.brand_timeline("Steady")["changes"][0]
    print("  added=%d dropped=%d" % (len(change["added"]), len(change["dropped"])))
    assert not change["added"] and not change["dropped"], (
        "Reported a change when only claim ordering differed."
    )
    print("  PASS - order does not count as change.")


def test_backing_gained_and_lost():
    print("\n[5] A claim gaining technical backing ...")
    with_temp_history([
        snapshot("Acme", "2026-08-01T09:00:00+00:00", unbacked=["SOC 2"]),
        snapshot("Acme", "2026-09-01T09:00:00+00:00", verified=["SOC 2"]),
    ])
    change = history.brand_timeline("Acme")["changes"][0]
    print("  newly verified: %s" % [c["object"] for c in change["newly_verified"]])
    assert [c["object"] for c in change["newly_verified"]] == ["SOC 2"]
    assert not change["added"], "A claim that only gained backing is not a new claim."
    print("  PASS")


def test_single_audit_says_so():
    print("\n[6] Only one audit on record ...")
    with_temp_history([snapshot("Lonely", "2026-08-01T09:00:00+00:00", verified=["X"])])
    t = history.brand_timeline("Lonely")
    print("  %s" % t["note"])
    assert t["changes"] == [] and t["note"], (
        "With one snapshot there is nothing to compare; say so rather than returning "
        "an empty list that looks like 'nothing changed'."
    )
    print("  PASS")


if __name__ == "__main__":
    original = history.HISTORY_FILE
    print("=" * 78)
    print("COMPETITOR CHANGE TRACKING")
    print("=" * 78)
    try:
        test_detects_a_new_claim()
        test_audits_minutes_apart_are_not_news()
        test_unreadable_docs_are_not_a_retraction()
        test_identical_audits_report_nothing()
        test_backing_gained_and_lost()
        test_single_audit_says_so()
    finally:
        history.HISTORY_FILE = original
    print("\n" + "=" * 78)
    print("ALL COMPETITOR CHANGE TRACKING TESTS PASSED")
    print("Real movement is reported; crawl noise is flagged, not announced.")
    print("=" * 78)
