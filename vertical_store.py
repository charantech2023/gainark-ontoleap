"""
vertical_store.py — durable storage for vertical ontology profiles.

A discovered profile is written to the verticals directory, which on Cloud Run is the
container filesystem: it disappears when the instance is recycled and is invisible to
every other instance. That is fatal to the point of matching a site into an existing
vertical rather than minting a new one - a category can only deepen with each customer if
what the last customer contributed is still there.

The directory stays the working copy, because the pipeline loader and the vertical routes
resolve profiles by path. Durability is a mirror alongside it, addressed by URI and backed
by the same archive used for graph history: a local directory, or a gs:// bucket.

    ONTOLEAP_VERTICALS_MIRROR=gs://gainark-ontoleap-verticals/profiles

Unset means local-only, which is correct for development and a data-loss bug in a
container - `warn_if_ephemeral` says so plainly rather than letting it look like it works.

Every change to a profile goes through update(): read the mirror's copy, apply the change,
write it back only if nobody wrote in between, and otherwise re-read and apply it again.
A profile accumulates - discovery adds a site's vocabulary, a reviewer adds a synonym - so
last-writer-wins would quietly drop one of two concurrent contributions.

update() raises MirrorWriteError when the mirror cannot take the write, rather than
keeping the change locally: sync_down() treats the mirror as the truth and would replace
that local copy within a minute, so a change that only reached the local disk is one that
has not happened. The caller decides whether that fails the request.
"""

import copy
import json
import logging
import os
import random
import threading
import time
from typing import Callable, Dict, List, Optional

import graph_archive
from security import verticals_dir

logger = logging.getLogger("gainark.vertical_store")

MIRROR_URI = os.environ.get("ONTOLEAP_VERTICALS_MIRROR", "").strip()

# Seconds before a read re-checks the mirror. A vertical changes when someone runs
# discovery, which is rare, so this trades a minute of staleness for not issuing a list
# call on every page load.
SYNC_TTL_SECONDS = int(os.environ.get("ONTOLEAP_VERTICALS_SYNC_TTL", "60") or 60)

_last_sync: float = 0.0


def mirror():
    """The configured mirror, or None when profiles are local-only."""
    if not MIRROR_URI:
        return None
    try:
        return graph_archive.open_archive(MIRROR_URI)
    except Exception as err:
        # A mirror that cannot be opened must not take the service down: profiles on disk
        # still work, they just stop being durable, and that is worth an error not a crash.
        logger.error("Vertical mirror %r could not be opened: %s", MIRROR_URI, err)
        return None


def _key(vertical_id: str) -> str:
    return "%s.json" % vertical_id


def describe_key(vertical_id: str) -> Optional[str]:
    """Where a profile lives in the mirror, or None when there is no mirror."""
    return "%s/%s" % (MIRROR_URI.rstrip("/"), _key(vertical_id)) if MIRROR_URI else None


class MirrorWriteError(RuntimeError):
    """The mirror could not take a profile change, so the change was not made."""


# How many times update() re-reads and re-applies after losing a race before giving up.
UPDATE_ATTEMPTS = 5

# Serialises updates within one process when there is no mirror to arbitrate.
_local_lock = threading.Lock()


def read_local(path: str) -> Optional[Dict]:
    """The profile on local disk, or None when it is missing or unreadable."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return None
    except ValueError as err:
        # Treated as absent, but never silently: a profile that cannot be parsed is also
        # one whose curated content cannot be protected.
        logger.warning("Local profile %s is unreadable (%s); treating it as absent.", path, err)
        return None
    if not isinstance(data, dict):
        logger.warning("Local profile %s is not an object; treating it as absent.", path)
        return None
    return data


def write_local(path: str, data: Dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = path + ".partial"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path)


def update(vertical_id: str, mutate: Callable[[Optional[Dict]], Optional[Dict]],
           path: Optional[str] = None) -> Optional[Dict]:
    """Change one profile without losing a concurrent change to it.

    `mutate` gets the current profile (None if there is none yet) as a private copy and
    returns the new one, or None to leave it as it is. It may be called more than once -
    once per lost race - so it must work from what it is given and nothing it saw before.

    Returns the profile as written, or None when `mutate` declined. Raises
    MirrorWriteError when a mirror is configured and the write could not be made there.
    """
    path = path or os.path.join(verticals_dir(), _key(vertical_id))
    store = mirror()

    if store is None:
        with _local_lock:
            new = mutate(read_local(path))
            if new is not None:
                write_local(path, new)
            return new

    key = _key(vertical_id)
    for attempt in range(UPDATE_ATTEMPTS):
        try:
            payload, version = store.get_versioned(key)
            # A profile the mirror does not have yet - one shipped in the image - starts
            # from the local copy, and the write then publishes it.
            current = json.loads(payload.decode("utf-8")) if payload else read_local(path)
        except Exception as err:
            raise MirrorWriteError("Could not read vertical %r from %s: %s"
                                   % (vertical_id, MIRROR_URI, err)) from err

        new = mutate(copy.deepcopy(current))
        if new is None:
            if payload and current is not None:
                write_local(path, current)   # the read was free; keep the cache current
            return None

        body = json.dumps(new, indent=2, ensure_ascii=False).encode("utf-8")
        try:
            landed = store.put_if(key, body, version)
        except Exception as err:
            raise MirrorWriteError("Could not write vertical %r to %s: %s"
                                   % (vertical_id, MIRROR_URI, err)) from err
        if landed:
            write_local(path, new)
            logger.info("Mirrored vertical %r to %s", vertical_id, MIRROR_URI)
            return new
        logger.info("Vertical %r changed under us on %s; re-applying (attempt %d).",
                    vertical_id, MIRROR_URI, attempt + 1)
        time.sleep(random.uniform(0.05, 0.2) * (attempt + 1))

    raise MirrorWriteError("Vertical %r kept changing on %s; gave up after %d attempts."
                           % (vertical_id, MIRROR_URI, UPDATE_ATTEMPTS))


def sync_down(force: bool = False) -> List[str]:
    """Bring profiles the mirror has into the local directory. Never raises.

    Returns the ids pulled. Existing local files are overwritten: the mirror is what other
    instances have contributed, and the local copy is a cache of it. A profile that exists
    only locally - one shipped in the image, or written while the mirror was unreachable -
    is left alone.
    """
    global _last_sync

    store = mirror()
    if store is None:
        return []

    now = time.time()
    if not force and (now - _last_sync) < SYNC_TTL_SECONDS:
        return []
    _last_sync = now

    target = verticals_dir()
    pulled: List[str] = []
    try:
        # Top-level keys only: buyer profiles share the mirror under buyers/ and are not
        # verticals (see buyer_profiles.py).
        keys = [k for k in store.list() if k.endswith(".json") and "/" not in k]
    except Exception as err:
        logger.error("Could not list vertical mirror %s: %s", MIRROR_URI, err)
        return []

    os.makedirs(target, exist_ok=True)
    for key in keys:
        vertical_id = os.path.basename(key)[:-5]
        try:
            payload = store.get(key)
            if not payload:
                continue
            # Parsed before it is written, so a truncated or corrupt object cannot replace
            # a working profile on disk.
            data = json.loads(payload.decode("utf-8"))
            if not isinstance(data, dict):
                logger.warning("Mirrored vertical %r is not an object; skipping.", vertical_id)
                continue
            path = os.path.join(target, "%s.json" % vertical_id)
            tmp = path + ".partial"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, path)
            pulled.append(vertical_id)
        except Exception as err:
            logger.error("Could not pull vertical %r from mirror: %s", vertical_id, err)

    if pulled:
        logger.info("Pulled %d vertical profile(s) from %s", len(pulled), MIRROR_URI)
    return pulled


def warn_if_ephemeral() -> Optional[str]:
    """Say plainly when a discovered profile will not survive.

    Same reasoning as graph_archive: in a container with no mirror, discovery writes to a
    filesystem that disappears with the instance, which looks exactly like a working store
    until someone asks why a vertical stopped accumulating.
    """
    if MIRROR_URI:
        return None
    if os.environ.get("K_SERVICE") or os.environ.get("KUBERNETES_SERVICE_HOST"):
        msg = ("Vertical profiles have no durable storage: ONTOLEAP_VERTICALS_MIRROR is "
               "unset while running in a container. A discovered buyer profile will be "
               "lost when this instance is recycled and is invisible to other instances, "
               "so a category cannot accumulate. Set it to a mounted volume path or a "
               "gs:// bucket.")
        logger.error(msg)
        return msg
    return None


def describe() -> str:
    """Where profiles live, for diagnostics."""
    store = mirror()
    return "%s (mirror: %s)" % (verticals_dir(), store.describe() if store else "none")
