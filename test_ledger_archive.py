"""
Audit history survives the container that recorded it.

truth_ledger/history.py kept complete snapshots in a JSONL file, which is durable on a
workstation and not durable at all on Cloud Run. Every instance gets its own ephemeral
filesystem, so history lasted until the container was recycled and no instance could see
another instance's audits.

The graph store had the same problem and it announced itself: a cold store answers "no
history". The ledger's version is quieter and therefore worse. When the file goes,
_ensure_seeded() refills it from log.md - which truncates verified claims at five - and
the endpoint comes back populated, confident, and missing most of what it recorded.
Nothing in the response says so.

So the archive is the record and the JSONL is an index over it. These tests cover the
write-through, the cold start, two instances, idempotent sync, the reseed that must not
win over real snapshots, and the promise that reads never pay for any of it.

Offline: a directory archive, a temporary ledger dir, no network, no GCS, no GLiNER.
"""

import io
import json
import os
import tempfile

import graph_archive as ga
import truth_ledger.history as history


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------

class Claim:
    def __init__(self, predicate, obj):
        self.predicate, self.object = predicate, obj


class Matrix:
    """The parts of a ProductTruthMatrixResponse that record_snapshot() reads."""

    def __init__(self, brand, verified=(), unbacked=(), mgi=50.0, tech=10):
        self.brand_name = brand
        self.marketing_url = "https://%s.example" % brand.lower()
        self.marketing_grounding_index = mgi
        self.evidence_status = "conclusive"
        self.tech_docs_url = None
        self.total_marketing_claims = len(verified) + len(unbacked)
        self.total_technical_capabilities = tech
        self.verified_triples = [Claim("automates", c) for c in verified]
        self.unbacked_claims = [Claim("automates", c) for c in unbacked]
        self.hidden_capabilities = []


class CountingArchive:
    """A directory archive that records how it was used."""

    def __init__(self, inner):
        self.inner, self.puts, self.gets, self.lists = inner, 0, 0, 0

    def put(self, key, payload):
        self.puts += 1
        return self.inner.put(key, payload)

    def get(self, key):
        self.gets += 1
        return self.inner.get(key)

    def list(self, prefix=""):
        self.lists += 1
        return self.inner.list(prefix)

    def describe(self):
        return self.inner.describe()


class BrokenArchive:
    """Every operation fails, the way a bucket with the wrong IAM would."""

    def put(self, key, payload):
        raise IOError("archive unreachable")

    def get(self, key):
        raise IOError("archive unreachable")

    def list(self, prefix=""):
        raise IOError("archive unreachable")

    def describe(self):
        return "broken://"


def fresh_instance(archive_root=None):
    """Point the module at an empty ledger, as a newly started container would have.

    LEDGER_DIR moves too, so backfill_from_markdown() finds no log.md and these tests
    never depend on whatever the repo's real ledger happens to contain.
    """
    tmp = tempfile.mkdtemp()
    history.LEDGER_DIR = tmp
    history.HISTORY_FILE = os.path.join(tmp, "history.jsonl")
    history._backfill_attempted = False
    if archive_root is not None:
        ga.ARCHIVE_URI = archive_root
    return tmp


def local_snapshots():
    """Read the local index directly, without going through load_snapshots()."""
    if not os.path.exists(history.HISTORY_FILE):
        return []
    with io.open(history.HISTORY_FILE, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_recorded_audit_is_mirrored():
    print("\n[1] A recorded audit reaches the archive ...")
    root = tempfile.mkdtemp()
    fresh_instance(root)

    assert history.record_snapshot(Matrix("Chargebee", verified=["Invoicing", "SOC 2"]))

    keys = ga.DirectoryArchive(root).list("ledger/")
    print("    local snapshots: %d | archived objects: %s" % (len(local_snapshots()), keys))
    assert len(local_snapshots()) == 1, "Not written locally."
    assert len(keys) == 1, "Audit was not mirrored to the archive: %s" % keys
    assert keys[0].startswith("ledger/"), "Wrong prefix; would collide with graph objects."
    print("  PASS")


def test_cold_instance_rebuilds_its_history():
    print("\n[2] A recycled container recovers what it recorded ...")
    root = tempfile.mkdtemp()
    fresh_instance(root)
    history.record_snapshot(Matrix("Chargebee", verified=["Invoicing"], mgi=40.0))
    history.record_snapshot(Matrix("Chargebee", verified=["Invoicing", "SOC 2"], mgi=70.0))
    before = len(local_snapshots())

    fresh_instance(root)  # container recycled: same archive, empty filesystem
    assert local_snapshots() == [], "Test setup did not actually start cold."
    result = history.sync_from_archive()

    timeline = history.brand_timeline("Chargebee")
    restored = sorted(s["grounding_index"] for s in history.load_snapshots("Chargebee"))
    print("    recorded %d, cold start held 0, pulled %d -> timeline sees %d %s"
          % (before, result["pulled"], timeline["snapshots"], restored))
    assert result["pulled"] == 2, result
    assert timeline["snapshots"] == 2, "History did not survive the recycle."
    assert restored == [40.0, 70.0], "A snapshot came back altered: %s" % restored
    # Deliberately no assertion on the direction of the diff here. These two audits are
    # written back to back, so on a coarse clock they can share a timestamp, and two
    # snapshots stamped the same instant have no defined order to restore. That costs
    # nothing: diff_snapshots already treats anything under MIN_MEANINGFUL_INTERVAL_HOURS
    # as measurement noise rather than news. Test [2b] covers ordering on the realistic
    # spacing, and test [6] covers the collision itself.
    print("  PASS")


def test_change_tracking_survives_a_restore():
    print("\n[2b] A diff still reads correctly after a restore ...")
    root = tempfile.mkdtemp()
    fresh_instance(root)

    # Three weeks apart, which is what audits of one brand actually look like and what
    # brand_timeline() is for. Written directly so the timestamps are the point rather
    # than whatever the clock happened to say.
    for stamp, verified, mgi in (
            ("2026-08-01T09:00:00+00:00", ["Invoicing"], 40.0),
            ("2026-08-22T09:00:00+00:00", ["Invoicing", "SOC 2"], 70.0)):
        snap = {
            "recorded_at": stamp, "brand": "Chargebee", "domain": "https://x.example",
            "source": "test", "grounding_index": mgi, "evidence_status": "conclusive",
            "total_marketing_claims": len(verified), "total_technical_capabilities": 10,
            "verified": [{"predicate": "automates", "object": v,
                          "key": history._claim_key("automates", v)} for v in verified],
            "unbacked": [], "hidden": [],
        }
        history._append_line(snap)
        history._mirror_to_archive(snap)

    fresh_instance(root)
    history.sync_from_archive()

    change = history.brand_timeline("Chargebee")["changes"][0]
    added = [c["object"] for c in change["added"]]
    print("    after restore: added %s | %.0f days apart"
          % (added, change["hours_apart"] / 24))
    assert added == ["SOC 2"], "Change tracking broke across the restore: %s" % added
    assert not change["dropped"], change["dropped"]
    print("  PASS")


def test_two_instances_see_each_others_audits():
    print("\n[3] A replica sees an audit it did not run ...")
    root = tempfile.mkdtemp()

    fresh_instance(root)                       # instance A
    history.record_snapshot(Matrix("Ordway", verified=["Billing"]))

    fresh_instance(root)                       # instance B, scaled up alongside
    history.record_snapshot(Matrix("Zuora", verified=["Rev Rec"]))
    history.sync_from_archive()

    brands = sorted(b["brand"] for b in history.tracked_brands())
    print("    instance B tracks: %s" % brands)
    assert brands == ["Ordway", "Zuora"], "A replica cannot see the other's work: %s" % brands
    print("  PASS")


def test_sync_is_idempotent():
    print("\n[4] Syncing twice does not duplicate history ...")
    root = tempfile.mkdtemp()
    fresh_instance(root)
    history.record_snapshot(Matrix("Recurly", verified=["Dunning"]))

    fresh_instance(root)
    first = history.sync_from_archive()
    second = history.sync_from_archive()

    print("    first: pulled %d | second: pulled %d, skipped %d | local rows %d"
          % (first["pulled"], second["pulled"], second["skipped"], len(local_snapshots())))
    assert first["pulled"] == 1 and second["pulled"] == 0, (first, second)
    assert second["skipped"] == 1, second
    assert len(local_snapshots()) == 1, "Re-sync duplicated a snapshot."
    print("  PASS")


def test_complete_snapshots_outrank_the_markdown_reseed():
    print("\n[5] The restored snapshot is the complete one, not log.md's summary ...")
    root = tempfile.mkdtemp()
    fresh_instance(root)

    eight = ["Invoicing", "Dunning", "Rev Rec", "SOC 2", "Webhooks",
             "Metering", "Proration", "Tax"]
    history.record_snapshot(Matrix("Chargebee", verified=eight))

    # A recycled container that also has log.md to fall back on. The markdown keeps only
    # the first five verified claims, so if the reseed were the source of truth here,
    # three claims would vanish and nothing would report that they had.
    fresh_instance(root)
    markdown = (
        "### 2026-08-01 09:00:00 UTC [PRODUCT TRUTH AUDIT]\n"
        "**Target**: `Chargebee` (https://chargebee.example)\n"
        "**Grounding Index**: **50.0%**\n"
        "Marketing Claims: 8\nTechnical Capabilities: 10\n"
        "**Verified Truth**: Invoicing (automates), Dunning (automates), "
        "Rev Rec (automates), SOC 2 (automates), Webhooks (automates) (+3 more)\n"
    )
    io.open(os.path.join(history.LEDGER_DIR, "log.md"), "w",
            encoding="utf-8").write(markdown)
    history.sync_from_archive()

    for_brand = history.load_snapshots("Chargebee")
    complete = [s for s in for_brand if not s.get("partial")]
    print("    snapshots for Chargebee: %d | complete one carries %d verified claims"
          % (len(for_brand), len(complete[0]["verified"]) if complete else 0))
    assert complete, "The complete snapshot did not survive; only the summary did."
    assert len(complete[0]["verified"]) == 8, (
        "Restored snapshot lost claims: %d" % len(complete[0]["verified"]))
    print("  PASS")


def test_same_tick_audits_both_survive():
    print("\n[6] Two audits inside one clock tick are both kept ...")
    root = tempfile.mkdtemp()
    fresh_instance(root)

    # A coarse clock makes this ordinary, not exotic: Windows ticks about every 15ms and
    # back-to-back audits of one brand land inside a single tick, stamping an identical
    # recorded_at. Keyed on (brand, recorded_at) the second write overwrote the first,
    # the local file kept both, and the loss appeared only after a recycle. Forcing the
    # timestamp here reproduces on any clock what a coarse one produces by itself.
    stamp = "2026-08-01T09:00:00+00:00"
    for verified, mgi in ((["Invoicing"], 40.0), (["Invoicing", "SOC 2"], 70.0)):
        snap = {
            "recorded_at": stamp, "brand": "Chargebee", "domain": "https://x.example",
            "source": "test", "grounding_index": mgi, "evidence_status": "conclusive",
            "total_marketing_claims": len(verified), "total_technical_capabilities": 10,
            "verified": [{"predicate": "automates", "object": v,
                          "key": history._claim_key("automates", v)} for v in verified],
            "unbacked": [], "hidden": [],
        }
        history._append_line(snap)
        history._mirror_to_archive(snap)

    archived = ga.DirectoryArchive(root).list("ledger/")
    fresh_instance(root)
    pulled = history.sync_from_archive()

    print("    2 audits at one timestamp -> %d archived object(s), %d pulled back"
          % (len(archived), pulled["pulled"]))
    assert len(archived) == 2, (
        "The second audit overwrote the first in the archive: %s" % archived)
    assert pulled["pulled"] == 2, pulled
    groundings = sorted(s["grounding_index"] for s in local_snapshots())
    assert groundings == [40.0, 70.0], groundings
    print("  PASS")


def test_identical_snapshots_still_collapse():
    print("\n[7] The same snapshot mirrored twice is still one object ...")
    root = tempfile.mkdtemp()
    fresh_instance(root)

    # The flip side of keying on content: uniqueness must not cost idempotence, or a
    # backfill regenerating history from log.md would duplicate it on every restart.
    snap = {"recorded_at": "2026-08-01T09:00:00+00:00", "brand": "Ordway",
            "domain": "https://o.example", "source": "backfill_markdown",
            "grounding_index": 50.0, "evidence_status": "conclusive",
            "total_marketing_claims": 1, "total_technical_capabilities": 10,
            "verified": [], "unbacked": [], "hidden": []}
    history._mirror_to_archive(snap)
    history._mirror_to_archive(dict(snap))

    archived = ga.DirectoryArchive(root).list("ledger/")
    print("    same snapshot mirrored twice -> %d object(s)" % len(archived))
    assert len(archived) == 1, "Idempotence was lost: %s" % archived
    print("  PASS")


def test_unreachable_archive_does_not_fail_the_audit():
    print("\n[8] A broken archive costs durability, not the audit ...")
    fresh_instance(tempfile.mkdtemp())
    saved = ga.open_archive
    ga.open_archive = lambda uri=None: BrokenArchive()
    try:
        ok = history.record_snapshot(Matrix("Stripe", verified=["Billing"]))
        synced = history.sync_from_archive()
    finally:
        ga.open_archive = saved

    print("    record_snapshot -> %s | local rows %d | sync -> %s"
          % (ok, len(local_snapshots()), synced))
    assert ok is True, "An unreachable archive was allowed to fail the audit."
    assert len(local_snapshots()) == 1, "The local write was lost too."
    assert synced["pulled"] == 0, synced
    print("  PASS")


def test_reads_never_touch_the_archive():
    print("\n[9] Reading a timeline costs no archive traffic ...")
    root = tempfile.mkdtemp()
    fresh_instance(root)
    history.record_snapshot(Matrix("Paddle", verified=["Invoicing"], mgi=40.0))
    history.record_snapshot(Matrix("Paddle", verified=["Invoicing", "Tax"], mgi=70.0))

    counting = CountingArchive(ga.DirectoryArchive(root))
    saved = ga.open_archive
    ga.open_archive = lambda uri=None: counting
    try:
        history.brand_timeline("Paddle")
        history.tracked_brands()
        history.load_snapshots()
    finally:
        ga.open_archive = saved

    print("    gets: %d | lists: %d | puts: %d"
          % (counting.gets, counting.lists, counting.puts))
    assert (counting.gets, counting.lists, counting.puts) == (0, 0, 0), (
        "A read reached the archive; every timeline request would pay per snapshot.")
    print("  PASS")


def test_ephemeral_deployment_is_reported():
    print("\n[10] An unarchived container says so out loud ...")
    saved_uri, saved_k = ga.ARCHIVE_URI, os.environ.get("K_SERVICE")
    try:
        ga.ARCHIVE_URI = ""
        os.environ.pop("K_SERVICE", None)
        assert history.warn_if_ephemeral() is None, "Warned on a normal workstation run."

        os.environ["K_SERVICE"] = "gainark-ontoleap"
        warning = history.warn_if_ephemeral()
        assert warning and "ONTOLEAP_GRAPH_ARCHIVE" in warning, warning
        assert "log.md" in warning, "The warning does not name the failure that follows."

        ga.ARCHIVE_URI = tempfile.mkdtemp()
        assert history.warn_if_ephemeral() is None, "Warned despite an archive being set."
        print("    silent locally, loud in a container, silent once configured")
    finally:
        ga.ARCHIVE_URI = saved_uri
        if saved_k is None:
            os.environ.pop("K_SERVICE", None)
        else:
            os.environ["K_SERVICE"] = saved_k
    print("  PASS")


if __name__ == "__main__":
    original_dir, original_file = history.LEDGER_DIR, history.HISTORY_FILE
    original_uri = ga.ARCHIVE_URI
    print("=" * 78)
    print("DURABLE TRUTH LEDGER")
    print("=" * 78)
    try:
        test_recorded_audit_is_mirrored()
        test_cold_instance_rebuilds_its_history()
        test_change_tracking_survives_a_restore()
        test_two_instances_see_each_others_audits()
        test_sync_is_idempotent()
        test_complete_snapshots_outrank_the_markdown_reseed()
        test_same_tick_audits_both_survive()
        test_identical_snapshots_still_collapse()
        test_unreachable_archive_does_not_fail_the_audit()
        test_reads_never_touch_the_archive()
        test_ephemeral_deployment_is_reported()
    finally:
        history.LEDGER_DIR, history.HISTORY_FILE = original_dir, original_file
        ga.ARCHIVE_URI = original_uri
    print("\n" + "=" * 78)
    print("ALL TRUTH LEDGER ARCHIVE TESTS PASSED")
    print("Snapshots outlive the container, and reads still cost one local file scan.")
    print("=" * 78)
