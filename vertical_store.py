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

Consistency is last-writer-wins. Two instances discovering into the same vertical at the
same moment will keep one contribution and lose the other; the profile stays valid, it is
just missing one site's vocabulary. That race exists on a single instance too and is not
introduced here.
"""

import json
import logging
import os
import time
from typing import Dict, List, Optional

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


def publish(vertical_id: str, payload: Dict) -> Optional[str]:
    """Copy one profile to the mirror. Never raises.

    Returns where it landed, or None when there is no mirror or the write failed. The
    caller has already written the local copy, so a failure here costs durability rather
    than the profile.
    """
    store = mirror()
    if store is None:
        return None

    try:
        store.put(_key(vertical_id),
                  json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"))
    except Exception as err:
        logger.error("Could not mirror vertical %r to %s: %s", vertical_id, MIRROR_URI, err)
        return None

    logger.info("Mirrored vertical %r to %s", vertical_id, MIRROR_URI)
    return "%s/%s" % (MIRROR_URI.rstrip("/"), _key(vertical_id))


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
        keys = [k for k in store.list() if k.endswith(".json")]
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
