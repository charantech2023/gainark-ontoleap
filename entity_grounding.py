"""
GainARK OntoLeap - Entity Grounding
===================================
One authority over which external identity a phrase has.

Grounding answers a different question from the synonym layer. sector_ontology decides
which concept a surface form *is*; this decides what that concept is called *outside*
this system - the Wikidata Q-ID that makes "SOC 2" in an Ordway graph the same resource
as "SOC 2" in a Chargebee graph, and the same resource as SOC 2 anywhere else.

Why this module exists
----------------------
That question was answered by a dict literal. constants.WIKIDATA_KB holds 40 hand-
verified Q-IDs, and four call sites read it directly:

  knowledge_graph.py      - schema:sameAs on the brand, on triple objects, on hubs
  link_prediction.py      - grounding a predicted link
  competitive_alignment.py- the wikidata_grounded flag on a competitor report
  pipeline.py             - WIKIDATA_MAP, an alias of the same dict

Forty entries is good coverage of compliance standards, which are a closed set, and
poor coverage of everything else. The targets are small private B2B companies; the
integrations and concepts they name mostly are not in that dict, so they came back
ungrounded and the graph said so with the same silence it uses for a phrase that has no
Q-ID at all. Nothing distinguished "we have never heard of this" from "this has no
external identity".

remediation.resolve_wikidata already resolves names against the live Wikidata API,
conservatively - it drops scholarly-article and place-name matches, requires a
business/software/finance description, and refuses ambiguous ones. It was wired into
industry_profiler and nowhere else. This module is that resolver made available to the
graph, with the static KB kept in front of it.

Latency is the whole design constraint
--------------------------------------
The call sites are tight loops inside a synchronous graph build. A miss that reaches the
network would cost up to the resolver's 4s timeout, and a marketing page yields dozens
of phrases the static KB has never seen, so a naive fallback turns one graph build into
minutes of sequential timeouts.

So the lookups here NEVER touch the network:

    ground_url()/ground_id()/is_grounded()  static KB, then the warm cache. Pure.
    prefetch()                              the only network path: one bounded,
                                            concurrent batch, called once per run.

A caller that never prefetches behaves exactly as the dict did. A caller that prefetches
pays one concurrent round trip and then reads from memory. Negative results are cached
too, so a phrase Wikidata cannot ground is asked about once per process, not once per
graph.
"""

import asyncio
import concurrent.futures
import logging
import os
from typing import Dict, Iterable, List, Optional

from constants import WIKIDATA_KB

logger = logging.getLogger("gainark.entity_grounding")

# Live resolution is opt-out rather than opt-in: the static KB is the fast path either
# way, and a deployment that cannot reach Wikidata degrades to exactly the behaviour
# this module replaced rather than failing.
LIVE_ENABLED = (os.environ.get("ONTOLEAP_WIKIDATA_LIVE", "1").strip().lower()
                not in ("0", "false", "no", "off"))

# Ceiling on live lookups per prefetch call. Bounds both the latency an audit can spend
# grounding and the load put on a free public API. Phrases past the cap stay ungrounded
# rather than queueing - a graph missing a sameAs is ordinary; an audit that hangs is not.
LIVE_BUDGET = int(os.environ.get("ONTOLEAP_WIKIDATA_BUDGET", "24") or 24)

# Concurrency within one prefetch. Wikidata is a shared public service and this is a
# courtesy limit as much as a technical one.
LIVE_CONCURRENCY = int(os.environ.get("ONTOLEAP_WIKIDATA_CONCURRENCY", "6") or 6)

# phrase -> URL, or None for "asked, and Wikidata could not ground it". Holding the
# negative is the point: without it every graph build re-asks about the same phrases.
_live_cache: Dict[str, Optional[str]] = {}


def normalise(phrase: str) -> str:
    """The key form. Matches what the call sites already did: lower(), strip()."""
    return " ".join(str(phrase or "").lower().split())


# ---------------------------------------------------------------------------
# Lookups - never touch the network
# ---------------------------------------------------------------------------

def ground_url(phrase: str) -> Optional[str]:
    """The Wikidata URL for a phrase, or None.

    Static KB first, always. Those 40 entries are hand-verified against the Q-ID, which
    is a stronger claim than the resolver's heuristics can make, so a curated answer is
    never overridden by a fetched one.
    """
    key = normalise(phrase)
    if not key:
        return None
    hit = WIKIDATA_KB.get(key)
    if hit:
        return hit
    return _live_cache.get(key)


def ground_id(phrase: str) -> Optional[str]:
    """The Q-ID for a phrase, or None."""
    url = ground_url(phrase)
    return url.rsplit("/", 1)[-1] if url else None


def is_grounded(phrase: str) -> bool:
    """Whether this phrase has an external identity. Drop-in for `x in WIKIDATA_KB`."""
    return ground_url(phrase) is not None


def cache_stats() -> Dict[str, int]:
    """What this process has learned beyond the static KB."""
    resolved = sum(1 for v in _live_cache.values() if v)
    return {"static": len(WIKIDATA_KB), "live_resolved": resolved,
            "live_unresolvable": len(_live_cache) - resolved}


# ---------------------------------------------------------------------------
# Prefetch - the only network path
# ---------------------------------------------------------------------------

def _unknown(phrases: Iterable[str]) -> List[str]:
    """Phrases with no static entry and no cached answer, in first-seen order."""
    out, seen = [], set()
    for p in phrases:
        key = normalise(p)
        if not key or key in seen or key in WIKIDATA_KB or key in _live_cache:
            continue
        seen.add(key)
        out.append(key)
    return out


async def _resolve_many(keys: List[str], resolver) -> Dict[str, Optional[str]]:
    """Resolve keys concurrently, bounded by LIVE_CONCURRENCY. Never raises."""
    gate = asyncio.Semaphore(max(1, LIVE_CONCURRENCY))

    async def one(key: str):
        async with gate:
            return await resolver(key)

    results = await asyncio.gather(*(one(k) for k in keys), return_exceptions=True)
    out: Dict[str, Optional[str]] = {}
    for key, res in zip(keys, results):
        if isinstance(res, BaseException):
            # An exception is not evidence that the phrase is ungroundable, only that
            # this attempt failed - so it is left out of the cache entirely and may be
            # retried on a later run.
            logger.debug("[Grounding] %r could not be resolved: %s", key, res)
            continue
        out[key] = (res or {}).get("sameAs") if isinstance(res, dict) else None
    return out


def prefetch(phrases: Iterable[str], resolver=None) -> Dict[str, int]:
    """Warm the cache for every phrase a run is about to ground.

    Call this once, before the loops that read ground_url(). One concurrent batch of at
    most LIVE_BUDGET lookups replaces what would otherwise be a sequential network call
    per unknown phrase inside a graph build.

    Never raises. Grounding is an enrichment: an audit that cannot reach Wikidata should
    produce a graph with fewer sameAs links, not fail.
    """
    if not LIVE_ENABLED:
        return {"requested": 0, "resolved": 0, "unresolved": 0, "skipped_over_budget": 0}

    keys = _unknown(phrases)
    over = max(0, len(keys) - LIVE_BUDGET)
    keys = keys[:LIVE_BUDGET]
    if not keys:
        return {"requested": 0, "resolved": 0, "unresolved": 0, "skipped_over_budget": over}

    if resolver is None:
        from remediation import resolve_wikidata as resolver

    try:
        resolved = _run(_resolve_many(keys, resolver))
    except Exception as exc:
        logger.warning("[Grounding] Live lookup failed; static KB only. %s", exc)
        return {"requested": len(keys), "resolved": 0, "unresolved": 0,
                "skipped_over_budget": over}

    _live_cache.update(resolved)
    hits = sum(1 for v in resolved.values() if v)
    if hits:
        logger.info("[Grounding] Resolved %d of %d phrase(s) live; %d now cached.",
                    hits, len(keys), len(_live_cache))
    if over:
        logger.info("[Grounding] %d phrase(s) past the per-run budget stay ungrounded.", over)
    return {"requested": len(keys), "resolved": hits,
            "unresolved": len(resolved) - hits, "skipped_over_budget": over}


def _run(coro):
    """Run a coroutine whether or not a loop is already turning.

    Same shape as remediation.resolve_wikidata_batch: FastAPI request handlers call the
    graph builders from inside a running loop, and asyncio.run() cannot nest, so that
    case is handed to a worker thread with a loop of its own.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(coro)).result()
    return asyncio.run(coro)
