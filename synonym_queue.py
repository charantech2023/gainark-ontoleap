"""
GainARK OntoLeap - The Shared Synonym Review Queue
==================================================
Candidate alt_labels wait here for a reviewer: pairs the synonym generator proposed
(sector_ontology.propose_alt_labels) and pairs the encoder matched by meaning
(semantic_match.propose_from_alignment).

The queue used to be one JSON file, truth_ledger/alt_label_candidates.json, rewritten in
place on the instance's own disk. On Cloud Run that disk is ephemeral and private to one
of several instances, so pending proposals vanished on recycle, a reviewer saw only the
proposals of whichever instance answered, and the "approved" marks were lost the same
way. A queue nobody can rely on teaches nobody anything.

So it is an append-only log in the archive the registry and the vocabulary already use
(ONTOLEAP_GRAPH_ARCHIVE, or truth_ledger/registry_archive when that is unset):

    review/alt_labels/proposed/<event_id>.json   one per proposed pair
    review/alt_labels/decided/<event_id>.json    one per reviewer decision

and the queue is the fold of those events. Every event has its own key, so two instances
proposing or deciding at once cannot overwrite each other: there is no shared file to
lose a race on, and no lock.

Folding rules
-------------
* The first proposal of a key wins; a later identical proposal adds nothing.
* An approval is about a surface form within one vertical, not one pairing: once
  "variable pricing" means Dynamic Pricing in billing, every billing candidate with that
  surface form is settled, and a later run proposing it again finds it already decided.
  It says nothing about another vertical - "pipeline" means one thing to sales and
  another to HR - so a candidate elsewhere stays pending. A candidate with no vertical
  (one queued before candidates carried theirs) is settled by an approval in any.
* A rejection is about one pairing: "variable pricing" is not Transaction Pricing says
  nothing about whether it is Dynamic Pricing. It is kept as negative knowledge, so the
  same pair is never proposed to a reviewer again.
* Rejecting a pairing that was approved also withdraws the approval: the event carries
  withdraws_approval, and the surface form's other pairings in that vertical go back to
  pending, since the approval that settled them no longer stands.
* Between decisions on the same thing the latest wins, so a reviewer can change their mind.
"""

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from entity_registry import ACTOR_AUTOMATION, ACTOR_REVIEWER, default_store, make_event

logger = logging.getLogger("gainark.synonym_queue")

PROPOSED_PREFIX = "review/alt_labels/proposed"
DECIDED_PREFIX = "review/alt_labels/decided"
DECISIONS = ("approved", "rejected")

# How long a process trusts what it has folded before listing the archive again. Matches
# the registry and the verticals mirror: a proposal on one instance reaches a reviewer on
# another within a minute.
SYNC_TTL_SECONDS = int(os.environ.get("ONTOLEAP_SYNONYM_QUEUE_TTL", "60") or 60)

LEGACY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "truth_ledger", "alt_label_candidates.json")


def entry_key(canonical: str, surface_form: str, vertical_id: Optional[str] = None) -> str:
    """How a pairing is keyed. Both proposers use it, so they queue one entry, not two.

    The vertical leads when known: the same concept label can exist in two verticals,
    and a pairing reviewed in one is not reviewed in the other.
    """
    key = "%s|%s" % (canonical.strip().lower(), surface_form.strip().lower())
    return "%s|%s" % (vertical_id.strip().lower(), key) if vertical_id else key


def _norm(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def fold(proposed: List[Dict[str, Any]], decided: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """The queue as of these events, keyed by entry_key. Events must be in id order."""
    queue: Dict[str, Dict[str, Any]] = {}
    for ev in proposed:
        if ev.get("key") and isinstance(ev.get("entry"), dict) and ev["key"] not in queue:
            queue[ev["key"]] = dict(ev["entry"], status="pending", proposed_at=ev.get("at"))

    # The latest approval or withdrawal of each surface form, per vertical and in any.
    by_surface: Dict[Tuple[str, str], Dict[str, Any]] = {}
    by_surface_any: Dict[str, Dict[str, Any]] = {}
    by_key: Dict[str, Dict[str, Any]] = {}
    for ev in decided:
        surface = _norm(ev.get("surface_form"))
        if ev.get("decision") == "approved" and surface:
            by_surface[(_norm(ev.get("vertical_id")), surface)] = ev
            by_surface_any[surface] = ev
        elif ev.get("decision") == "rejected" and ev.get("key"):
            by_key[ev["key"]] = ev
            if ev.get("withdraws_approval") and surface:
                by_surface[(_norm(ev.get("vertical_id")), surface)] = ev
                by_surface_any[surface] = ev

    for key, entry in queue.items():
        surface, vertical = _norm(entry.get("surface_form")), _norm(entry.get("vertical_id"))
        settled = by_surface.get((vertical, surface)) if vertical else by_surface_any.get(surface)
        if settled is not None and settled.get("decision") != "approved":
            settled = None                      # withdrawn: nothing settles it any more
        candidates = [ev for ev in (settled, by_key.get(key)) if ev]
        if not candidates:
            continue
        ev = max(candidates, key=lambda e: e["event_id"])
        entry["status"] = ev["decision"]
        entry["decided_at"] = ev.get("at")
        if ev["decision"] == "approved":
            entry["approved_as"] = ev.get("approved_as")
            if ev.get("vertical_id"):
                entry["vertical_id"] = ev["vertical_id"]
    return queue


class SynonymQueue:
    """The review queue over an archive. Reads fold the log; writes append to it."""

    def __init__(self, archive=None, legacy_path: Optional[str] = None):
        self._archive = archive
        self._legacy_path = legacy_path
        self._events: Dict[str, Dict[str, Any]] = {}
        self._synced_at = 0.0
        self._lock = threading.Lock()

    def archive(self):
        return self._archive or default_store().archive()

    # -- reading -------------------------------------------------------------

    def _sync(self, force: bool = False) -> None:
        with self._lock:
            if not force and time.monotonic() - self._synced_at < SYNC_TTL_SECONDS:
                return
        self._import_legacy()
        store = self.archive()
        # Events are immutable, so one read once is read forever: only new keys are fetched.
        for prefix in (PROPOSED_PREFIX, DECIDED_PREFIX):
            try:
                keys = store.list(prefix + "/")
            except Exception as err:
                logger.warning("[SynonymQueue] Could not list %s; using what is cached. %s",
                               prefix, err)
                continue
            for key in keys:
                if key in self._events:
                    continue
                try:
                    payload = store.get(key)
                    if payload:
                        self._events[key] = json.loads(payload.decode("utf-8"))
                except Exception as err:
                    logger.warning("[SynonymQueue] Skipping unreadable %s: %s", key, err)
        with self._lock:
            self._synced_at = time.monotonic()

    def _split(self) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        proposed, decided = [], []
        for key in sorted(self._events):
            (proposed if key.startswith(PROPOSED_PREFIX + "/") else decided).append(self._events[key])
        return proposed, decided

    def load(self, force: bool = False) -> Dict[str, Dict[str, Any]]:
        """Every candidate with its current status. force=True re-lists the archive now."""
        self._sync(force=force)
        return fold(*self._split())

    # -- writing -------------------------------------------------------------

    def _append(self, prefix: str, event: Dict[str, Any]) -> None:
        key = "%s/%s.json" % (prefix, event["event_id"])
        self.archive().put(key, json.dumps(event, ensure_ascii=False).encode("utf-8"))
        self._events[key] = event

    def propose(self, entries: Dict[str, Dict[str, Any]]) -> List[str]:
        """Queue the entries whose keys are not already in the queue. Returns the keys
        written. A key already there - pending, approved or rejected - is left alone."""
        current = self.load(force=True)
        written = []
        for key, entry in entries.items():
            if key in current or key in written:
                continue
            entry = {k: v for k, v in entry.items() if k != "status"}
            self._append(PROPOSED_PREFIX, make_event(
                "alt_label_proposed", ACTOR_AUTOMATION, key=key, entry=entry))
            written.append(key)
        return written

    def approve(self, surface_form: str, approved_as: str, vertical_id: Optional[str] = None) -> None:
        """Record that a surface form means a concept. Settles every candidate with it."""
        self._append(DECIDED_PREFIX, make_event(
            "alt_label_decided", ACTOR_REVIEWER, decision="approved",
            surface_form=surface_form.strip(), approved_as=approved_as,
            vertical_id=vertical_id))

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """One candidate as of now, re-read from the archive. None for an unknown key."""
        return self.load(force=True).get(key)

    def reject(self, key: str, withdraws_approval: bool = False) -> Dict[str, Any]:
        """Record that one proposed pairing is wrong. Raises ValueError for an unknown key.

        withdraws_approval=True when the pairing had been approved and the caller has
        taken the alternate back out of the vertical: the approval then stops settling
        the surface form's other pairings too.
        """
        current = self.load(force=True)
        if key not in current:
            raise ValueError("No candidate %r in the queue." % key)
        entry = current[key]
        self._append(DECIDED_PREFIX, make_event(
            "alt_label_decided", ACTOR_REVIEWER, decision="rejected", key=key,
            surface_form=entry.get("surface_form"),
            vertical_id=entry.get("vertical_id"),
            withdraws_approval=bool(withdraws_approval)))
        return self.load()[key]

    # -- migration -----------------------------------------------------------

    def _import_legacy(self) -> None:
        """Move a pre-archive queue file into the log, once. Never raises.

        Its pending entries become proposals and its approved marks become approvals,
        then the file is renamed so it is not imported twice.
        """
        path = self._legacy_path
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                legacy = json.load(f)
            self._legacy_path = None   # before writing: propose() syncs, which re-enters
            if isinstance(legacy, dict) and legacy:
                self.propose({k: v for k, v in legacy.items() if isinstance(v, dict)})
                # Idempotent, so a copy of the file turning up again - in an image, on a
                # second machine - cannot re-append approvals. A re-appended approval is
                # newer than any rejection since, and would silently overturn it.
                decided = {_norm(ev.get("surface_form")) for key, ev in self._events.items()
                           if key.startswith(DECIDED_PREFIX + "/")}
                for entry in legacy.values():
                    surface = _norm(entry.get("surface_form")) if isinstance(entry, dict) else ""
                    if surface and entry.get("status") == "approved" and surface not in decided:
                        self.approve(entry["surface_form"], entry.get("approved_as"))
                        decided.add(surface)
            os.replace(path, path + ".imported")
            logger.info("[SynonymQueue] Imported %d legacy candidate(s) from %s",
                        len(legacy) if isinstance(legacy, dict) else 0, path)
        except Exception as err:
            logger.warning("[SynonymQueue] Could not import the legacy queue %s: %s", path, err)


_default: Optional[SynonymQueue] = None
_default_lock = threading.Lock()


def default_queue() -> SynonymQueue:
    global _default
    with _default_lock:
        if _default is None:
            _default = SynonymQueue(legacy_path=LEGACY_PATH)
        return _default
