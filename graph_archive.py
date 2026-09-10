"""
GainARK OntoLeap - Durable Archive for the Knowledge Graph
==========================================================
Where stored graphs actually survive.

graph_store keeps quads in SQLite, which is durable on a normal machine and not durable
at all on Cloud Run: each instance gets its own ephemeral filesystem, so history lasts
until that container is recycled, and two instances never see each other's runs. A
store that looks like it is accumulating knowledge and silently is not is worse than
one that never claimed to.

So the archive is the substrate and SQLite becomes a local index over it:

    persist  -> write an immutable object to the archive, and index it locally
    start up -> pull anything the archive holds that this instance has not indexed

Runs are append-only and each gets its own key, so concurrent instances cannot conflict
by construction - no locking, no leader, no transactions across instances. Only the
ontology object is rewritten, and it is derived from the vertical profile, so
last-write-wins is the correct resolution rather than a compromise.

Backends
--------
DirectoryArchive  a filesystem path. Use for local runs, and on Cloud Run for a
                  mounted volume (GCS FUSE or Filestore) - the mount makes durability
                  the platform's problem rather than this module's.
GcsArchive        a gs:// bucket, addressed directly through google-cloud-storage.
                  Needs no mount and no VPC, only IAM on the service account.

Selected by ONTOLEAP_GRAPH_ARCHIVE. Unset means no archive: SQLite alone, which is the
right behaviour locally and the wrong one in a container, so warn_if_ephemeral() exists
to say so out loud rather than let it pass unnoticed.
"""

import io
import json
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

from rdflib import Graph

logger = logging.getLogger("gainark.graph_archive")

ARCHIVE_URI = os.environ.get("ONTOLEAP_GRAPH_ARCHIVE", "").strip()


def _safe_key(graph_id: str) -> str:
    """A filesystem- and object-store-safe name that still shows what it holds."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", graph_id).strip("_")[:180]


def object_key(kind: str, graph_id: str) -> str:
    return "%s/%s" % (kind, _safe_key(graph_id))


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

class DirectoryArchive:
    """Objects as files under a directory."""

    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(self.root, exist_ok=True)

    def _path(self, key: str) -> str:
        p = os.path.abspath(os.path.join(self.root, key))
        if not p.startswith(self.root + os.sep) and p != self.root:
            raise ValueError("archive key escapes the archive root: %r" % key)
        return p

    def put(self, key: str, payload: bytes) -> None:
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Write then rename: a reader must never see a half-written graph.
        tmp = path + ".partial"
        with open(tmp, "wb") as f:
            f.write(payload)
        os.replace(tmp, path)

    def get(self, key: str) -> Optional[bytes]:
        try:
            with open(self._path(key), "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def list(self, prefix: str = "") -> List[str]:
        base = self.root
        found = []
        for dirpath, _dirs, files in os.walk(base):
            for name in files:
                if name.endswith(".partial"):
                    continue
                rel = os.path.relpath(os.path.join(dirpath, name), base).replace(os.sep, "/")
                if rel.startswith(prefix):
                    found.append(rel)
        return sorted(found)

    def describe(self) -> str:
        return "directory:%s" % self.root


class GcsArchive:
    """Objects in a Google Cloud Storage bucket."""

    def __init__(self, bucket: str, prefix: str = ""):
        try:
            from google.cloud import storage  # imported here so the dependency is
        except ImportError as e:                # optional for anyone not using GCS
            raise RuntimeError(
                "ONTOLEAP_GRAPH_ARCHIVE points at gs:// but google-cloud-storage is not "
                "installed. Add it to requirements.txt, or use a mounted volume path "
                "instead of a gs:// URI."
            ) from e
        self._client = storage.Client()
        self._bucket = self._client.bucket(bucket)
        self.bucket_name = bucket
        self.prefix = prefix.strip("/")

    def _blob(self, key: str):
        return self._bucket.blob("%s/%s" % (self.prefix, key) if self.prefix else key)

    def put(self, key: str, payload: bytes) -> None:
        self._blob(key).upload_from_string(payload)

    def get(self, key: str) -> Optional[bytes]:
        blob = self._blob(key)
        return blob.download_as_bytes() if blob.exists() else None

    def list(self, prefix: str = "") -> List[str]:
        full = "%s/%s" % (self.prefix, prefix) if self.prefix else prefix
        cut = len(self.prefix) + 1 if self.prefix else 0
        return sorted(b.name[cut:] for b in self._client.list_blobs(
            self._bucket, prefix=full) if not b.name.endswith(".partial"))

    def describe(self) -> str:
        return "gs://%s/%s" % (self.bucket_name, self.prefix)


def open_archive(uri: Optional[str] = None):
    """The configured archive, or None when history is local-only."""
    target = (uri if uri is not None else ARCHIVE_URI).strip()
    if not target:
        return None
    if target.startswith("gs://"):
        rest = target[len("gs://"):]
        bucket, _, prefix = rest.partition("/")
        return GcsArchive(bucket, prefix)
    return DirectoryArchive(target)


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------

def encode(graph: Graph, meta: Dict) -> Tuple[bytes, bytes]:
    """(graph payload, metadata payload) for one stored graph.

    N-Triples rather than Turtle: it is line-based, so a partially written or truncated
    object is detectably broken instead of silently parsing as a smaller graph.
    """
    return (graph.serialize(format="nt").encode("utf-8"),
            json.dumps(meta, ensure_ascii=False, sort_keys=True).encode("utf-8"))


def decode(payload: bytes) -> Graph:
    g = Graph()
    g.parse(io.BytesIO(payload), format="nt")
    return g


def warn_if_ephemeral() -> Optional[str]:
    """Say plainly when stored history will not survive.

    Cloud Run sets K_SERVICE. With no archive configured there, the graph store is
    writing to a container filesystem that disappears with the instance - which looks
    exactly like a working store until someone asks where last month went.
    """
    if ARCHIVE_URI:
        return None
    if os.environ.get("K_SERVICE") or os.environ.get("KUBERNETES_SERVICE_HOST"):
        msg = ("Knowledge graph has no durable archive: ONTOLEAP_GRAPH_ARCHIVE is unset "
               "while running in a container. Stored runs will be lost when this "
               "instance is recycled and are invisible to other instances. Set it to a "
               "mounted volume path or a gs:// bucket.")
        logger.error(msg)
        return msg
    return None
