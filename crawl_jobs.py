"""
crawl_jobs.py — Long site crawls as resumable jobs.

A forty-page crawl takes about eleven minutes against a request timeout of fifteen, and
the whole result is assembled in memory: cross the line and the caller gets a 504 with
nothing, having waited the full time to lose everything. Raising the page limit further
only moves the cliff.

The obvious fix - answer immediately and crawl in a background thread - does not work
here. Cloud Run throttles CPU outside request handling, so a thread stops making progress
the moment the response is returned, and with the service scaling to zero and up to ten
instances, a job started on one instance is invisible to the instance that gets polled
next. Both problems are about where work and state live, not about threads.

So a job is a piece of durable state, and progress happens inside ordinary requests. Each
call to `advance` reads the job, crawls for a bounded slice of time, writes the state back
and returns. Nothing runs between requests, nothing is held in a process, and any instance
can pick up any job. A crawl of any length is then a series of short requests rather than
one long one, which also gives the caller real progress to show instead of a spinner.

What drives those calls is deliberately not this module's business. Today the dashboard
polls; a Cloud Tasks queue calling the same endpoint would make it fire-and-forget without
changing anything here.

The lease is advisory. Two callers advancing the same job at the same instant can both
crawl a slice; the crawl's own de-duplication means no page is read twice, but one
writer's slice can be lost. Properly excluding that needs a compare-and-set the archive
abstraction does not expose. It is a wasted slice, not a corrupted job.
"""

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from graph_archive import open_archive, DirectoryArchive
from graph_store import DEFAULT_STORE_PATH
from site_graph import (
    new_crawl_state,
    crawl_slice,
    crawl_is_complete,
    assemble_site_kg,
)

logger = logging.getLogger("gainark.crawl_jobs")

_JOBS_PREFIX = "jobs"

# One slice's crawling budget. Roughly six pages at the measured sixteen seconds each,
# which keeps a request far below any gateway timeout and makes progress visible often
# enough that a subscriber can see it moving.
SLICE_SECONDS = float(os.environ.get("ONTOLEAP_JOB_SLICE_SECONDS", "100"))

# How long a slice may hold a job before another caller may take it over. Longer than
# SLICE_SECONDS because a slice checks its budget before starting a page, so the last page
# of a slice can run past the budget by the length of one page.
_LEASE_SECONDS = SLICE_SECONDS + 120

STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_seconds(stamp: Optional[str]) -> float:
    if not stamp:
        return float("inf")
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds()
    except Exception:
        return float("inf")


def _store():
    """Where jobs live: the configured archive, or a local directory beside the store.

    A local directory is correct for a workstation and wrong for a container, where it is
    invisible to other instances and dies with this one. That is the same condition the
    graph store already warns about, and it is the archive's absence that causes it.
    """
    archive = open_archive()
    if archive is not None:
        return archive
    root = os.path.join(os.path.dirname(os.path.abspath(DEFAULT_STORE_PATH)), "jobs")
    if os.environ.get("K_SERVICE"):
        logger.error(
            "Crawl jobs are being written to %s because ONTOLEAP_GRAPH_ARCHIVE is unset. "
            "In a container that is invisible to other instances and is lost when this "
            "one is recycled, so a job can be created and then never found again.", root)
    return DirectoryArchive(root)


def _job_key(job_id: str) -> str:
    return "%s/%s/job.json" % (_JOBS_PREFIX, job_id)


def _result_key(job_id: str) -> str:
    return "%s/%s/result.json" % (_JOBS_PREFIX, job_id)


def _read(key: str) -> Optional[Dict[str, Any]]:
    payload = _store().get(key)
    if payload is None:
        return None
    return json.loads(payload.decode("utf-8"))


def _write(key: str, doc: Dict[str, Any]) -> None:
    _store().put(key, json.dumps(doc, ensure_ascii=False).encode("utf-8"))


def create_job(
    start_url: str,
    max_pages: int,
    vertical_id: str,
    routing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Record a crawl that has not started. Does no crawling.

    The vertical is resolved by the caller before this point and stored, so every slice
    of the crawl uses the same vocabulary. Re-deciding it per slice could change the
    entity labels halfway through and produce a graph assembled from two vocabularies.
    """
    job_id = uuid.uuid4().hex
    job = {
        "job_id": job_id,
        "start_url": start_url,
        "max_pages": max_pages,
        "vertical_id": vertical_id,
        "vertical_source": "auto-detected" if routing else "requested",
        "vertical_reason": (routing or {}).get("reason"),
        "vertical_evidence": list((routing or {}).get("evidence") or []),
        "status": STATUS_RUNNING,
        "error": None,
        "created_at": _now(),
        "updated_at": _now(),
        "leased_at": None,
        "state": new_crawl_state(start_url),
    }
    _write(_job_key(job_id), job)
    logger.info("[CrawlJob] %s created for %s (limit %d, vertical %s)",
                job_id, start_url, max_pages, vertical_id)
    return job


def load_job(job_id: str) -> Optional[Dict[str, Any]]:
    return _read(_job_key(job_id))


def load_result(job_id: str) -> Optional[Dict[str, Any]]:
    return _read(_result_key(job_id))


def public_status(job: Dict[str, Any]) -> Dict[str, Any]:
    """The job without its frontier.

    The state holds every node and edge gathered so far and the whole unread frontier,
    which is megabytes on a large site and of no use to a caller watching progress. A
    poll should not pay to download the crawl in order to learn how far along it is.
    """
    state = job.get("state") or {}
    attempted = state.get("attempts", 0)
    crawled = len(state.get("crawled") or [])
    failed = len(state.get("failed") or [])
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "start_url": job["start_url"],
        "vertical_id": job.get("vertical_id"),
        "vertical_source": job.get("vertical_source"),
        "vertical_reason": job.get("vertical_reason"),
        "pages_requested": job["max_pages"],
        "pages_attempted": attempted,
        "pages_crawled": crawled,
        "pages_failed": failed,
        "pages_discovered": len(state.get("seen") or []),
        "pages_remaining": max(job["max_pages"] - attempted, 0),
        "recent_failures": (state.get("failed") or [])[-3:],
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "error": job.get("error"),
    }


def advance_job(
    job_id: str,
    slice_seconds: Optional[float] = None,
    slice_pages: Optional[int] = None,
) -> Dict[str, Any]:
    """Crawl one slice of a job and persist what it found.

    Returns the public status. Safe to call on a finished job, and safe to call on a job
    another caller is mid-slice on - it returns the current status rather than crawling
    the same pages again.
    """
    job = load_job(job_id)
    if job is None:
        raise KeyError(job_id)

    if job["status"] in (STATUS_DONE, STATUS_FAILED):
        return public_status(job)

    if _age_seconds(job.get("leased_at")) < _LEASE_SECONDS:
        logger.info("[CrawlJob] %s already being advanced; returning current status", job_id)
        return public_status(job)

    job["leased_at"] = _now()
    _write(_job_key(job_id), job)

    try:
        crawl_slice(
            job["state"],
            job["vertical_id"],
            job["max_pages"],
            budget_seconds=slice_seconds if slice_seconds is not None else SLICE_SECONDS,
            budget_pages=slice_pages,
        )

        if crawl_is_complete(job["state"], job["max_pages"]):
            # Canonicalization and the exports run once, here, rather than per slice.
            site_kg = assemble_site_kg(
                job["state"], job["vertical_id"], job["max_pages"], persist=True
            )
            result = site_kg.model_dump()
            result["vertical_source"] = job.get("vertical_source")
            result["vertical_reason"] = job.get("vertical_reason")
            result["vertical_evidence"] = job.get("vertical_evidence") or []
            _write(_result_key(job_id), result)
            job["status"] = STATUS_DONE
            logger.info("[CrawlJob] %s finished: %d pages read, %d failed",
                        job_id, len(job["state"]["crawled"]), len(job["state"]["failed"]))

    except Exception as err:
        # A crawl that read nothing raises rather than assembling an empty graph, and that
        # is a finished job with an answer, not a job to retry forever.
        job["status"] = STATUS_FAILED
        job["error"] = str(err)[:500]
        logger.warning("[CrawlJob] %s failed: %s", job_id, err)

    job["leased_at"] = None
    job["updated_at"] = _now()
    _write(_job_key(job_id), job)
    return public_status(job)
