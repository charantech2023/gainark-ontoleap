"""
Machine-readable audit history for competitor change tracking.

log.md is the human-readable ledger and stays that way. It is also lossy on purpose -
verified claims are truncated to the first five with "(+N more)" - so it cannot answer
"what did this brand start claiming". This module writes a complete snapshot per audit
alongside it, one JSON object per line, and diffs consecutive snapshots for a brand.

Deliberately append-only: an audit must never fail because history could not be
written.

Durability
----------
history.jsonl is a local INDEX, not the record. See the Durability section at the foot
of this module: snapshots are mirrored to graph_archive on write and pulled back once
at startup, so a recycled container recovers what it recorded instead of quietly
reseeding a lossy summary from log.md.
"""

import io
import json
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("gainark.truth_ledger.history")

LEDGER_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_FILE = os.path.join(LEDGER_DIR, "history.jsonl")

# Concurrent audits append to one file. A single write() of a long line is not
# guaranteed atomic, so two simultaneous audits could interleave and corrupt a record.
_WRITE_LOCK = threading.Lock()

# The ledger is append-only and every audit adds a snapshot, so it grows without bound
# and is re-read in full on every /api/competitor-changes call. Rotate past this size so
# neither disk nor request latency grows indefinitely.
MAX_HISTORY_BYTES = int(os.environ.get("MAX_HISTORY_BYTES", str(32 * 1024 * 1024)) or 32 * 1024 * 1024)

# Ceiling on snapshots held in memory while answering one read.
MAX_SNAPSHOTS_LOADED = int(os.environ.get("MAX_SNAPSHOTS_LOADED", "20000") or 20000)


def _rotate_if_oversized() -> None:
    """Move the ledger aside once it passes MAX_HISTORY_BYTES. Never raises."""
    try:
        if os.path.exists(HISTORY_FILE) and os.path.getsize(HISTORY_FILE) > MAX_HISTORY_BYTES:
            archive = HISTORY_FILE + ".1"
            if os.path.exists(archive):
                os.remove(archive)
            os.replace(HISTORY_FILE, archive)
            logger.info("[TruthLedger] Rotated history to %s", archive)
    except OSError as exc:
        logger.warning("[TruthLedger] Could not rotate history file: %s", exc)


def _append_line(payload: Dict[str, Any]) -> None:
    """Serialise and append one snapshot under the write lock."""
    line = json.dumps(payload, ensure_ascii=False) + "\n"
    with _WRITE_LOCK:
        _rotate_if_oversized()
        with io.open(HISTORY_FILE, "a", encoding="utf-8") as f:
            f.write(line)

# Below this gap, two audits are measuring the same reality twice rather than observing
# a change. Marketing sites do not turn over hourly; the existing ledger has the same
# brand swinging 80.0% -> 23.5% grounding inside a minute.
MIN_MEANINGFUL_INTERVAL_HOURS = 6.0


def _elapsed_hours(earlier: Optional[str], later: Optional[str]) -> Optional[float]:
    """Hours between two ISO timestamps, or None if either is unusable."""
    if not earlier or not later:
        return None
    try:
        a = datetime.fromisoformat(str(earlier))
        b = datetime.fromisoformat(str(later))
    except (TypeError, ValueError):
        return None
    if a.tzinfo is None:
        a = a.replace(tzinfo=timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=timezone.utc)
    return abs((b - a).total_seconds()) / 3600.0


def _claim_key(predicate: str, obj: str) -> str:
    """Stable identity for a claim, so wording changes do not read as new claims."""
    pred = " ".join(str(predicate or "").lower().split())
    concept = " ".join(str(obj or "").lower().split())
    return f"{pred}|{concept}"


def _claim_set(triples) -> List[Dict[str, str]]:
    out, seen = [], set()
    for t in triples or []:
        predicate = getattr(t, "predicate", None) or ""
        obj = getattr(t, "object", None) or ""
        if not obj:
            continue
        key = _claim_key(predicate, obj)
        if key in seen:
            continue
        seen.add(key)
        out.append({"predicate": predicate, "object": obj, "key": key})
    return out


def record_snapshot(matrix, source: str = "product_truth") -> bool:
    """Append one complete snapshot of a Product Truth audit. Never raises."""
    try:
        snapshot = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "brand": matrix.brand_name,
            "domain": matrix.marketing_url,
            "source": source,
            "grounding_index": matrix.marketing_grounding_index,
            "evidence_status": getattr(matrix, "evidence_status", "conclusive"),
            "tech_docs_url": getattr(matrix, "tech_docs_url", None),
            "total_marketing_claims": matrix.total_marketing_claims,
            "total_technical_capabilities": matrix.total_technical_capabilities,
            "verified": _claim_set(matrix.verified_triples),
            "unbacked": _claim_set(matrix.unbacked_claims),
            "hidden": _claim_set(matrix.hidden_capabilities),
        }
        _append_line(snapshot)
        # Local file first, archive second. The local write is what makes the snapshot
        # exist at all; the mirror is only what makes it outlive this container, and
        # neither is allowed to be the reason an audit fails.
        _mirror_to_archive(snapshot)
        return True
    except Exception as exc:
        logger.warning("[TruthLedger] Could not append history snapshot: %s", exc)
        return False


_backfill_attempted = False


def _ensure_seeded() -> None:
    """Seed history from log.md the first time it is read on a fresh filesystem.

    history.jsonl is derived data and is not committed, and Cloud Run containers start
    empty on every revision. Without this the feature would report "no history" on a
    freshly deployed instance despite log.md carrying months of audits. Attempted once
    per process; a failure here must never break a read.
    """
    global _backfill_attempted
    if _backfill_attempted or os.path.exists(HISTORY_FILE):
        return
    _backfill_attempted = True
    try:
        seeded = backfill_from_markdown()
        if seeded:
            logger.info("[TruthLedger] Seeded %d snapshots from log.md", seeded)
    except Exception as exc:
        logger.warning("[TruthLedger] Could not seed history from log.md: %s", exc)


def load_snapshots(brand: Optional[str] = None) -> List[Dict[str, Any]]:
    """Read snapshots oldest-first, optionally for one brand. Skips malformed lines."""
    _ensure_seeded()
    if not os.path.exists(HISTORY_FILE):
        return []
    snapshots = []
    truncated = False
    with io.open(HISTORY_FILE, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                snap = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("[TruthLedger] Skipping malformed history line %d", lineno)
                continue
            if brand and str(snap.get("brand", "")).lower() != brand.lower():
                continue
            snapshots.append(snap)
            if len(snapshots) > MAX_SNAPSHOTS_LOADED:
                # Keep the newest window rather than the oldest: a timeline is read
                # forwards from recent history, and an unbounded list would let the
                # ledger size dictate this endpoint response time and memory use.
                snapshots = snapshots[-MAX_SNAPSHOTS_LOADED:]
                truncated = True
    if truncated:
        logger.warning(
            "[TruthLedger] History exceeded %d snapshots; returning the most recent window.",
            MAX_SNAPSHOTS_LOADED,
        )
    snapshots.sort(key=lambda s: s.get("recorded_at", ""))
    return snapshots


def diff_snapshots(previous: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """Compare two snapshots of the same brand.

    A claim vanishing is reported as `dropped`, never as "they stopped claiming it".
    The crawler is not reliable enough to support that reading: the same site has
    yielded 15, 10 and 5 capabilities across runs with no change at the source, so a
    disappearance is at least as likely to be a bad crawl as a real retraction.
    """
    def index(snap, bucket):
        return {c["key"]: c for c in snap.get(bucket, [])}

    result: Dict[str, Any] = {
        "brand": current.get("brand"),
        "from": previous.get("recorded_at"),
        "to": current.get("recorded_at"),
        "grounding_from": previous.get("grounding_index"),
        "grounding_to": current.get("grounding_index"),
        "added": [],
        "dropped": [],
        "newly_verified": [],
        "lost_backing": [],
        "low_confidence": False,
        "notes": [],
    }

    prev_all = {}
    curr_all = {}
    for bucket in ("verified", "unbacked"):
        prev_all.update(index(previous, bucket))
        curr_all.update(index(current, bucket))

    for key, claim in curr_all.items():
        if key not in prev_all:
            result["added"].append(claim)
    for key, claim in prev_all.items():
        if key not in curr_all:
            result["dropped"].append(claim)

    prev_verified = index(previous, "verified")
    curr_verified = index(current, "verified")
    for key, claim in curr_verified.items():
        if key in prev_all and key not in prev_verified:
            result["newly_verified"].append(claim)
    for key, claim in prev_verified.items():
        if key in curr_all and key not in curr_verified:
            result["lost_backing"].append(claim)

    # Flag comparisons that should not be read as real movement.
    for snap, label in ((previous, "earlier"), (current, "latest")):
        if snap.get("evidence_status") == "inconclusive":
            result["low_confidence"] = True
            result["notes"].append(
                f"The {label} audit could not read the technical documentation, so "
                "differences here reflect what was readable, not what changed."
            )
    prev_tech = previous.get("total_technical_capabilities") or 0
    curr_tech = current.get("total_technical_capabilities") or 0
    if prev_tech and curr_tech and (max(prev_tech, curr_tech) >= 2 * min(prev_tech, curr_tech)):
        result["low_confidence"] = True
        result["notes"].append(
            f"Technical capabilities read moved {prev_tech} -> {curr_tech}, a swing large "
            "enough that crawl variance is the more likely explanation than product change."
        )

    # The strongest signal of all: nobody rewrites their marketing in minutes. Two audits
    # close together cannot be measuring product change, whatever the numbers say. The
    # existing ledger has Ordway going 80.0% -> 23.5% grounding in 55 seconds, which the
    # capability-swing check above does not catch because 15 -> 10 is under its threshold.
    elapsed = _elapsed_hours(previous.get("recorded_at"), current.get("recorded_at"))
    if elapsed is not None and elapsed < MIN_MEANINGFUL_INTERVAL_HOURS:
        result["low_confidence"] = True
        minutes = elapsed * 60.0
        if minutes < 1:
            span = "under a minute"
        elif minutes < 90:
            span = f"{minutes:.0f} minute{'s' if round(minutes) != 1 else ''}"
        else:
            span = f"{elapsed:.1f} hours"
        result["notes"].append(
            f"These audits are {span} apart. A brand does not change what it claims in "
            "that time, so treat any difference as measurement noise rather than news."
        )
    result["hours_apart"] = round(elapsed, 2) if elapsed is not None else None
    return result


def brand_timeline(brand: str) -> Dict[str, Any]:
    """Every consecutive change for one brand, oldest first."""
    snapshots = load_snapshots(brand=brand)
    if len(snapshots) < 2:
        return {
            "brand": brand,
            "snapshots": len(snapshots),
            "changes": [],
            "note": (
                "At least two audits of this brand are needed before anything can be "
                "said to have changed."
            ),
        }
    changes = [
        diff_snapshots(snapshots[i], snapshots[i + 1])
        for i in range(len(snapshots) - 1)
    ]
    return {"brand": brand, "snapshots": len(snapshots), "changes": changes, "note": None}


def tracked_brands() -> List[Dict[str, Any]]:
    """Brands with history, and how much of it there is."""
    counts: Dict[str, Dict[str, Any]] = {}
    for snap in load_snapshots():
        brand = snap.get("brand") or "Unknown"
        entry = counts.setdefault(brand, {"brand": brand, "snapshots": 0, "last_seen": None})
        entry["snapshots"] += 1
        entry["last_seen"] = snap.get("recorded_at")
    return sorted(counts.values(), key=lambda e: e["brand"].lower())


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------
# log.md predates history.jsonl, so without this the feature starts empty despite
# months of audits sitting on disk. What it can recover is limited by the markdown:
# grounding index, claim counts and the first five verified claims. Backfilled
# snapshots are marked so a diff built on them is never mistaken for a complete one.

_ENTRY_RE = None


def backfill_from_markdown(log_path: Optional[str] = None) -> int:
    """Import Product Truth entries from log.md. Returns snapshots written.

    Idempotent: entries already present (same brand and timestamp) are skipped, so
    this can be run repeatedly without duplicating history.
    """
    import re

    path = log_path or os.path.join(LEDGER_DIR, "log.md")
    if not os.path.exists(path):
        return 0

    existing = {(s.get("brand"), s.get("recorded_at")) for s in load_snapshots()}
    text = io.open(path, encoding="utf-8").read()

    blocks = re.split(r"\n(?=### )", text)
    written = 0
    for block in blocks:
        if "[PRODUCT TRUTH AUDIT]" not in block:
            continue
        ts = re.search(r"###\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})\s*UTC", block)
        target = re.search(r"\*\*Target\*\*:\s*`([^`]+)`\s*\(([^)]+)\)", block)
        if not ts or not target:
            continue
        recorded_at = f"{ts.group(1)}T{ts.group(2)}+00:00"
        brand = target.group(1).strip()
        if (brand, recorded_at) in existing:
            continue

        mgi_m = re.search(r"\*\*Grounding Index\*\*:\s*\*\*([\d.]+)%\*\*", block)
        claims_m = re.search(r"Marketing Claims:\s*(\d+)", block)
        tech_m = re.search(r"Technical Capabilities:\s*(\d+)", block)
        verified_m = re.search(r"\*\*Verified Truth\*\*:\s*(.+)", block)

        verified = []
        if verified_m and "None verified" not in verified_m.group(1):
            for part in verified_m.group(1).split(","):
                m = re.match(r"\s*(.+?)\s*\(([^)]+)\)", part)
                if m:
                    obj, pred = m.group(1).strip(), m.group(2).strip()
                    verified.append({"predicate": pred, "object": obj,
                                     "key": _claim_key(pred, obj)})

        snapshot = {
            "recorded_at": recorded_at,
            "brand": brand,
            "domain": target.group(2).strip(),
            "source": "backfill_markdown",
            # The markdown truncates verified claims at five, so a diff spanning a
            # backfilled snapshot can show a claim "appearing" that was simply cut off.
            "partial": True,
            "grounding_index": float(mgi_m.group(1)) if mgi_m else None,
            "evidence_status": "conclusive" if mgi_m else "inconclusive",
            "total_marketing_claims": int(claims_m.group(1)) if claims_m else 0,
            "total_technical_capabilities": int(tech_m.group(1)) if tech_m else 0,
            "verified": verified,
            "unbacked": [],
            "hidden": [],
        }
        _append_line(snapshot)
        existing.add((brand, recorded_at))
        written += 1
    return written


# ---------------------------------------------------------------------------
# Durability
# ---------------------------------------------------------------------------
# history.jsonl is a local INDEX, not the record. Cloud Run gives every instance its own
# ephemeral filesystem, so a container writing only to this file holds its snapshots
# until the instance is recycled, and never sees an audit any other instance recorded.
#
# The silent version of that failure is the one worth engineering against. When the file
# vanishes, _ensure_seeded() refills it from log.md - which truncates verified claims at
# five - so the endpoint comes back populated and confident, having lost every complete
# snapshot it ever held. Nothing in the response distinguishes that from working.
#
# graph_archive already holds the record for the knowledge graph. It holds this too,
# under its own "ledger/" prefix, so a single ONTOLEAP_GRAPH_ARCHIVE covers both and a
# deployment cannot end up half-durable by setting one and forgetting the other.
#
# Reads never touch the archive. Snapshots are mirrored on write and pulled once at
# startup, so load_snapshots() stays a local file scan: a timeline request costs what it
# always did, rather than one object fetch per audit ever recorded.

ARCHIVE_KIND = "ledger"


def _snapshot_key(snapshot: Dict[str, Any]) -> str:
    """The archive object key for one snapshot.

    (brand, recorded_at) is already this module's identity for a snapshot - it is what
    backfill_from_markdown() dedupes on - so keying the object on it makes an archive
    write idempotent for free: the same audit mirrored twice lands on the same object
    instead of duplicating history.

    Timestamp first, so a lexical sort of keys is chronological. That is what lets
    sync_from_archive() take the newest snapshots under a cap without fetching every
    object to discover which ones those are. Every writer here stamps UTC, so the
    offsets are uniform and the ordering holds.
    """
    import graph_archive as ga
    return ga.object_key(ARCHIVE_KIND, "%s__%s" % (
        snapshot.get("recorded_at") or "", snapshot.get("brand") or "unknown"))


def _mirror_to_archive(snapshot: Dict[str, Any], archive=None) -> bool:
    """Mirror one snapshot to the durable archive. Never raises into the caller.

    A failed archive write costs durability, not the audit. The snapshot is already in
    the local file by the time this runs, and record_snapshot()'s standing contract is
    that recording history can never be what makes an audit fail - so the outcome is
    degraded and logged, not lost.
    """
    try:
        import graph_archive as ga
        archive = archive if archive is not None else ga.open_archive()
        if archive is None:
            return False
        archive.put(
            _snapshot_key(snapshot),
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True).encode("utf-8"),
        )
        return True
    except Exception as exc:
        logger.error(
            "[TruthLedger] Snapshot recorded locally but NOT durably (%s): %s",
            getattr(archive, "describe", lambda: "archive")(), exc)
        return False


def sync_from_archive(archive=None, limit: Optional[int] = None) -> Dict[str, int]:
    """Append every archived snapshot this instance does not already hold.

    A fresh container starts with no history.jsonl while the archive holds every audit
    any instance ever recorded. Without this, competitor tracking answers "at least two
    audits are needed before anything can be said to have changed" about a brand with a
    year of history sitting in the bucket.

    Local snapshots are never overwritten. A recorded audit is immutable, so an object
    already present has nothing left to tell us; that also makes the whole operation
    safe to repeat.
    """
    try:
        import graph_archive as ga
        archive = archive if archive is not None else ga.open_archive()
    except Exception as exc:
        logger.error("[TruthLedger] Could not open the history archive: %s", exc)
        return {"pulled": 0, "skipped": 0, "unreadable": 0}
    if archive is None:
        return {"pulled": 0, "skipped": 0, "unreadable": 0}

    known = {(s.get("brand"), s.get("recorded_at")) for s in load_snapshots()}
    try:
        keys = sorted(k for k in archive.list(ARCHIVE_KIND + "/")
                      if not k.endswith(".partial"))
    except Exception as exc:
        # Opening an archive proves configuration, not access. A bucket the service
        # account cannot list fails here, at startup, and must degrade to local-only
        # history rather than propagate - a caller asking for a timeline should get the
        # history this instance has, not an exception about object storage.
        logger.error("[TruthLedger] Could not list the history archive (%s): %s",
                     getattr(archive, "describe", lambda: "archive")(), exc)
        return {"pulled": 0, "skipped": 0, "unreadable": 0}

    # Bounded, newest last. The archive can hold more history than one instance should
    # pull at boot, and load_snapshots() keeps only the recent window regardless. If the
    # appends here do trip rotation, they trip it having written oldest first, so what
    # stays live is the newest - which is the window a timeline is read from anyway.
    cap = MAX_SNAPSHOTS_LOADED if limit is None else limit
    if len(keys) > cap:
        keys = keys[-cap:]

    pulled = skipped = unreadable = 0
    for key in keys:
        try:
            raw = archive.get(key)
            if raw is None:
                continue
            snap = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            logger.warning("[TruthLedger] Unreadable archived snapshot %r: %s", key, exc)
            unreadable += 1
            continue
        identity = (snap.get("brand"), snap.get("recorded_at"))
        if identity in known:
            skipped += 1
            continue
        # Local append only. This came out of the archive; mirroring it back would be a
        # write with nothing to say.
        _append_line(snap)
        known.add(identity)
        pulled += 1

    if pulled:
        logger.info("[TruthLedger] Restored %d snapshot(s) from %s", pulled,
                    getattr(archive, "describe", lambda: "archive")())
    return {"pulled": pulled, "skipped": skipped, "unreadable": unreadable}


def warn_if_ephemeral() -> Optional[str]:
    """Say plainly when audit history will not survive this container.

    Mirrors graph_archive.warn_if_ephemeral() for the ledger. Worth its own call rather
    than leaning on that one: the graph and the ledger fail differently. A forgetful
    graph store answers "no history"; a forgetful ledger answers from log.md and looks
    fine, which is harder to notice and easier to believe.
    """
    import graph_archive as ga
    if ga.ARCHIVE_URI:
        return None
    if os.environ.get("K_SERVICE") or os.environ.get("KUBERNETES_SERVICE_HOST"):
        msg = ("Truth ledger has no durable archive: ONTOLEAP_GRAPH_ARCHIVE is unset "
               "while running in a container. Complete audit snapshots will be lost "
               "when this instance is recycled, and competitor change tracking will "
               "silently fall back to the lossy log.md summary. Set it to a mounted "
               "volume path or a gs:// bucket.")
        logger.error(msg)
        return msg
    return None
