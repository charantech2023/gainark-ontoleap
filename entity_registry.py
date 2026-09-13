"""
GainARK OntoLeap - Entity Registry
==================================
One durable authority for which real-world thing a name refers to.

Concepts already have identity: a frozen id, a label, a definition and curated alternate
labels in the vertical profile. Entities had none. "Salesforce", "Salesforce.com" and
"Salesforce CRM" were three nodes in the same graph, Stripe on ordwaylabs.com and Stripe
on chargebee.com were different resources, and the two hand tables that tried to help
(constants.WIKIDATA_KB, knowledge_graph._ENTITY_ALIASES) were respectively wrong and
unread. See ENTITY_REGISTRY_DESIGN.md.

What this module is
-------------------
The data and its storage. Resolution - turning a crawl's surface forms into ids - lives
in entity_resolver, which reads a Registry and never writes one.

    Entity          a named thing: organization, product, standard, topic or place.
                    Identity is ours; a Wikidata Q-ID is an attribute, because most of
                    what matters for our customers is not on Wikidata.
    Registry        the folded state: entities, the surface-form index, blocked keys.
    RegistryStore   seed file + append-only events in the archive, folded on read.

Why events and not a document
-----------------------------
The registry changes - reviewers approve aliases, reject merges, promote candidates - and
the archive has no compare-and-set. A single rewritten document would be last-writer-
wins, which silently drops a reviewer's decision when two instances write at once. That
is the one failure a learning registry cannot afford, so the registry takes the shape
that already made run history safe: every change is its own immutable object, under its
own key, and the current state is a fold over them. Concurrent writers cannot collide.

Fold rules resolve the conflicts that remain, explicitly:

  * a reviewer outranks automation: an automated merge of two entities a reviewer has
    kept apart, or unmerged, is not applied;
  * distinct_from outranks merged, whatever order they arrive in;
  * a merged id is never deleted - it keeps resolving through merged_into, so stored run
    graphs stay valid and an unmerge is one more event.

The seed (registry/seed_entities.json) is folded first. It is curated, reviewed in git,
and identical on every instance, so it needs no events and no bootstrap race.
"""

import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

logger = logging.getLogger("gainark.entity_registry")

ENTITY_BASE = "https://gainark.com/ontoleap/entity"
KINDS = ("organization", "product", "standard", "topic", "place")
STATUSES = ("candidate", "active", "merged", "rejected")

ACTOR_SEED = "seed"
ACTOR_AUTOMATION = "automation"
ACTOR_REVIEWER = "reviewer"

SEED_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "registry", "seed_entities.json")

EVENTS_PREFIX = "registry/events"
OBSERVATIONS_PREFIX = "registry/observations"

# How long a process trusts its folded registry before listing the archive for new events.
# Matches vertical_store: a reviewer's decision on one instance reaches the others within
# a minute, and a busy crawl does not list a bucket per page.
SYNC_TTL_SECONDS = int(os.environ.get("ONTOLEAP_REGISTRY_SYNC_TTL", "60") or 60)


def entity_uri(entity_id: str) -> str:
    return "%s/%s" % (ENTITY_BASE, entity_id)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class Alias(BaseModel):
    form: str
    source: str = Field(default=ACTOR_SEED, description="seed | automation | reviewer | wikidata")


class Entity(BaseModel):
    """A named real-world thing. `kind` is what it is; how a site relates to it (competitor,
    integration) is a role, and roles live on edges, never here."""

    id: str = Field(..., pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
                    description="Frozen slug. Never regenerated from the label.")
    kind: str
    prefLabel: str
    definition: str = ""
    status: str = "active"
    merged_into: Optional[str] = None
    aliases: List[Alias] = Field(default_factory=list)
    domains: List[str] = Field(default_factory=list,
                               description="Official web domains. The strongest identity key an organization has.")
    part_of: Optional[str] = Field(default=None, description="A product's organization, or a variant's parent standard.")
    parent_org: Optional[str] = Field(default=None, description="Ownership: TaxJar is owned by Stripe.")
    external: Dict[str, str] = Field(default_factory=dict, description="System -> identifier, e.g. wikidata -> Q7624104.")
    distinct_from: List[str] = Field(default_factory=list,
                                     description="Entities this one must never be merged with.")

    def forms(self) -> List[str]:
        return [self.prefLabel] + [a.form for a in self.aliases]

    @property
    def uri(self) -> str:
        return entity_uri(self.id)

    @property
    def wikidata(self) -> Optional[str]:
        return self.external.get("wikidata")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_id_lock = threading.Lock()
_last_id_ms = 0
_id_seq = 0


def new_event_id() -> str:
    """Sortable by creation order, unique without coordination.

    Milliseconds, then a per-process sequence, then noise. The sequence matters: a
    reviewer's one action can write "entity_created" and "alias_added" inside the same
    millisecond, and with only random noise after the timestamp the alias could sort -
    and fold - before the entity it belongs to.
    """
    global _last_id_ms, _id_seq
    with _id_lock:
        ms = max(int(time.time() * 1000), _last_id_ms)
        _id_seq = _id_seq + 1 if ms == _last_id_ms else 0
        _last_id_ms = ms
        return "%013d-%05d-%s" % (ms, _id_seq, secrets.token_hex(4))


def make_event(event_type: str, actor: str, **payload) -> Dict[str, Any]:
    return {"event_id": new_event_id(), "type": event_type, "actor": actor,
            "at": _now(), **payload}


# ---------------------------------------------------------------------------
# Folded state
# ---------------------------------------------------------------------------

class Registry:
    """The registry as of some set of events. Read-only once built.

    `key_fn` is the resolver's surface-form normaliser. It is injected rather than
    imported so the index and the lookups that read it can never disagree about what
    counts as the same form.
    """

    def __init__(self, entities: Dict[str, Entity], blocked_keys: Dict[str, str],
                 key_fn, conflicts: Optional[List[str]] = None):
        self.entities = entities
        self.blocked_keys = blocked_keys
        self.key_fn = key_fn
        self.conflicts = conflicts or []
        self._index: Dict[str, List[str]] = {}
        self._domains: Dict[str, str] = {}
        for ent in entities.values():
            if ent.status != "active":
                continue
            for form in ent.forms():
                key = key_fn(form)
                if key and ent.id not in self._index.setdefault(key, []):
                    self._index[key].append(ent.id)
            for domain in ent.domains:
                self._domains[domain.lower()] = ent.id

    def canonical(self, entity_id: str) -> Optional[Entity]:
        """Follow merged_into to the surviving entity. Cycle-safe."""
        seen = set()
        ent = self.entities.get(entity_id)
        while ent is not None and ent.status == "merged" and ent.merged_into:
            if ent.id in seen:
                return None
            seen.add(ent.id)
            ent = self.entities.get(ent.merged_into)
        return ent

    def lookup(self, key: str) -> List[Entity]:
        """Active entities a normalised key names. More than one means ambiguous."""
        return [self.entities[i] for i in self._index.get(key, [])]

    def by_domain(self, domain: str) -> Optional[Entity]:
        domain = (domain or "").lower()
        if domain.startswith("www."):
            domain = domain[4:]
        # Most specific first: quickbooks.intuit.com is QuickBooks before it is Intuit.
        parts = domain.split(".")
        for i in range(len(parts) - 1):
            hit = self._domains.get(".".join(parts[i:]))
            if hit:
                return self.entities[hit]
        return None

    def is_blocked(self, key: str) -> bool:
        return key in self.blocked_keys

    def are_distinct(self, a: str, b: str) -> bool:
        ea, eb = self.entities.get(a), self.entities.get(b)
        return bool(ea and eb and (b in ea.distinct_from or a in eb.distinct_from))


def fold(seed: List[Dict[str, Any]], events: List[Dict[str, Any]], key_fn) -> Registry:
    """Build the registry from the seed and events, in event-id order.

    Pure: the same inputs give the same registry on every instance, which is what lets a
    fresh container rebuild state from the archive rather than trust a local file.
    """
    entities: Dict[str, Entity] = {}
    blocked: Dict[str, str] = {}
    conflicts: List[str] = []
    # Pairs a reviewer has said are different, or has split apart. Automation may not
    # merge across them, whichever order the events arrive in.
    reviewer_split: set = set()

    for raw in seed:
        doc = dict(raw)
        doc["aliases"] = [a if isinstance(a, dict) else {"form": a, "source": ACTOR_SEED}
                          for a in doc.get("aliases", [])]
        ent = Entity(**doc)
        if ent.kind not in KINDS:
            raise ValueError("Seed entity %r has unknown kind %r" % (ent.id, ent.kind))
        if ent.id in entities:
            raise ValueError("Seed entity id %r is declared twice" % ent.id)
        entities[ent.id] = ent

    ordered = sorted(events, key=lambda e: e.get("event_id", ""))

    for ev in ordered:
        if ev.get("type") in ("distinct_from_added", "unmerged") and ev.get("actor") == ACTOR_REVIEWER:
            a, b = ev.get("entity_id"), ev.get("other") or ev.get("into")
            if a and b:
                reviewer_split.add(frozenset((a, b)))

    for ev in ordered:
        kind, actor = ev.get("type"), ev.get("actor", ACTOR_AUTOMATION)
        eid = ev.get("entity_id")
        ent = entities.get(eid) if eid else None
        try:
            if kind == "entity_created":
                doc = dict(ev["entity"])
                if doc["id"] in entities:
                    continue  # first creation wins; a re-run bootstrap is a no-op
                doc.setdefault("status", "candidate" if actor == ACTOR_AUTOMATION else "active")
                entities[doc["id"]] = Entity(**doc)
            elif ent is None:
                if kind in ("blocked_key_added", "blocked_key_removed"):
                    if kind == "blocked_key_added":
                        blocked[ev["key"]] = ev.get("reason", "")
                    else:
                        blocked.pop(ev["key"], None)
                else:
                    conflicts.append("%s names unknown entity %r" % (kind, eid))
            elif kind == "alias_added":
                if all(a.form.lower() != ev["form"].lower() for a in ent.aliases):
                    ent.aliases.append(Alias(form=ev["form"], source=actor))
            elif kind == "alias_removed":
                ent.aliases = [a for a in ent.aliases if a.form.lower() != ev["form"].lower()]
            elif kind == "merged":
                into = ev["into"]
                if into not in entities or into == eid:
                    conflicts.append("merge of %r into %r has no valid target" % (eid, into))
                elif actor != ACTOR_REVIEWER and frozenset((eid, into)) in reviewer_split:
                    conflicts.append("automated merge %r -> %r overruled by a reviewer" % (eid, into))
                else:
                    ent.status, ent.merged_into = "merged", into
            elif kind == "unmerged":
                if ent.status == "merged":
                    ent.status, ent.merged_into = "active", None
            elif kind == "distinct_from_added":
                other = ev["other"]
                if other not in ent.distinct_from:
                    ent.distinct_from.append(other)
                if other in entities and eid not in entities[other].distinct_from:
                    entities[other].distinct_from.append(eid)
            elif kind == "external_linked":
                ent.external[ev["system"]] = ev["value"]
            elif kind == "promoted":
                if ent.status == "candidate":
                    ent.status = "active"
            elif kind == "rejected":
                ent.status = "rejected"
            else:
                conflicts.append("unknown event type %r" % kind)
        except (KeyError, ValueError) as err:
            conflicts.append("malformed %s event %s: %s" % (kind, ev.get("event_id"), err))

    # distinct_from outranks merged regardless of order: two things a reviewer (or the
    # seed) declared different cannot end up as one.
    for ent in entities.values():
        if ent.status == "merged" and ent.merged_into:
            target = ent.merged_into
            if target in ent.distinct_from or ent.id in entities.get(target, ent).distinct_from:
                conflicts.append("merge %r -> %r undone: declared distinct" % (ent.id, target))
                ent.status, ent.merged_into = "active", None

    for c in conflicts:
        logger.warning("[Registry] %s", c)
    return Registry(entities, blocked, key_fn, conflicts)


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def load_seed(path: Optional[str] = None) -> List[Dict[str, Any]]:
    with open(path or SEED_PATH, encoding="utf-8") as f:
        return json.load(f)["entities"]


def _local_root() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "truth_ledger", "registry_archive")


class RegistryStore:
    """Seed plus archived events, folded on read and cached for SYNC_TTL_SECONDS.

    Only new event objects are fetched on a refresh: events are immutable, so one read
    once is read forever.
    """

    def __init__(self, archive=None, seed_path: Optional[str] = None, key_fn=None):
        self._archive = archive
        self._seed_path = seed_path
        self._key_fn = key_fn
        self._events: Dict[str, Dict[str, Any]] = {}
        self._registry: Optional[Registry] = None
        self._synced_at = 0.0
        self._lock = threading.Lock()

    def archive(self):
        if self._archive is None:
            from graph_archive import open_archive, DirectoryArchive
            self._archive = open_archive() or DirectoryArchive(_local_root())
        return self._archive

    def _key_function(self):
        if self._key_fn is None:
            from entity_resolver import normalise_key
            self._key_fn = normalise_key
        return self._key_fn

    def registry(self, force: bool = False) -> Registry:
        with self._lock:
            fresh = (time.monotonic() - self._synced_at) < SYNC_TTL_SECONDS
            if self._registry is not None and fresh and not force:
                return self._registry
            try:
                archive = self.archive()
                for key in archive.list(EVENTS_PREFIX + "/"):
                    if key in self._events:
                        continue
                    payload = archive.get(key)
                    if payload is not None:
                        self._events[key] = json.loads(payload.decode("utf-8"))
            except Exception as err:
                # A registry that cannot reach its events is still the seed, which is
                # everything resolution had before this module existed.
                logger.warning("[Registry] Could not sync events; using what is cached. %s", err)
            self._registry = fold(load_seed(self._seed_path), list(self._events.values()),
                                  self._key_function())
            self._synced_at = time.monotonic()
            return self._registry

    def append(self, event: Dict[str, Any]) -> str:
        """Write one event. Its own key, so it cannot overwrite anyone else's."""
        key = "%s/%s.json" % (EVENTS_PREFIX, event["event_id"])
        self.archive().put(key, json.dumps(event, ensure_ascii=False).encode("utf-8"))
        with self._lock:
            self._events[key] = event
            self._synced_at = 0.0
        return key

    def record_observation(self, observation: Dict[str, Any]) -> Optional[str]:
        """Store what one run saw and how each form resolved. Never raises.

        This is the evidence the later learning loop reads - the same form resolving the
        same way on independent sites is what promotes a candidate - so it is written from
        the first run rather than reconstructed later from graphs that lost the detail.
        """
        try:
            key = "%s/%s.json" % (OBSERVATIONS_PREFIX, new_event_id())
            self.archive().put(key, json.dumps(observation, ensure_ascii=False).encode("utf-8"))
            return key
        except Exception as err:
            logger.warning("[Registry] Could not record the run observation: %s", err)
            return None


_default_store: Optional[RegistryStore] = None
_default_lock = threading.Lock()


def default_store() -> RegistryStore:
    global _default_store
    with _default_lock:
        if _default_store is None:
            _default_store = RegistryStore()
        return _default_store
