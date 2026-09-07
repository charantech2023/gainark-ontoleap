"""
Machine-readable audit history for competitor change tracking.

log.md is the human-readable ledger and stays that way. It is also lossy on purpose -
verified claims are truncated to the first five with "(+N more)" - so it cannot answer
"what did this brand start claiming". This module writes a complete snapshot per audit
alongside it, one JSON object per line, and diffs consecutive snapshots for a brand.

Deliberately append-only and dependency-free: an audit must never fail because history
could not be written.
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
