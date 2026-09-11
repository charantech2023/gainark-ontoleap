"""A crawl that outlives the request that started it.

The synchronous endpoint assembles everything in memory inside one request, so it is
capped by the request timeout: cross it and the caller waits eleven minutes to receive a
504 and nothing. A job has to survive being stopped after any page and picked up later,
possibly by a different process, which means every piece of crawl state has to round-trip
through JSON and storage intact.

These tests replace fetching, extraction and the job store, so nothing here touches the
network or the configured archive.
"""
import json
import shutil
import tempfile

import crawl_jobs
import site_graph
from graph_archive import DirectoryArchive

HOME = "https://example.com"

LINKS = {
    "example.com/": ["/pricing", "/product", "/about", "/blog"],
    "example.com/product": ["/product/billing", "/product/revenue"],
    "example.com/pricing": ["/pricing/enterprise"],
    "example.com/about": [],
    "example.com/blog": [],
    "example.com/product/billing": [],
    "example.com/product/revenue": [],
    "example.com/pricing/enterprise": [],
}


class _FakeNode:
    pass


class _FakePageKG:
    nodes = []
    edges = []


def _html_for(url):
    hrefs = LINKS.get(site_graph._page_key(url), [])
    return "<html><body>" + "".join('<a href="%s">x</a>' % h for h in hrefs) + "</body></html>"


def _fake_fetch(url, *args, **kwargs):
    return _html_for(url)


def _fake_build_page_kg(payload, url=None, **kwargs):
    return _FakePageKG()


class _Harness:
    """Fakes for the network, the extractor and the job store."""

    def __enter__(self):
        self.tmp = tempfile.mkdtemp(prefix="ontoleap-jobs-")
        self._archive = DirectoryArchive(self.tmp)
        self._orig = (site_graph.smart_fetch, site_graph.build_page_kg, crawl_jobs._store)
        site_graph.smart_fetch = _fake_fetch
        site_graph.build_page_kg = _fake_build_page_kg
        crawl_jobs._store = lambda: self._archive
        return self

    def __exit__(self, *exc):
        site_graph.smart_fetch, site_graph.build_page_kg, crawl_jobs._store = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)


def test_job_state_survives_storage():
    print("\n[1] Testing that a job can be written out and read back...")
    with _Harness():
        job = crawl_jobs.create_job(HOME, max_pages=6, vertical_id="b2b_saas_fintech")

        # The whole point of the job: it is state, not a running process. If any of it
        # cannot be serialised, the crawl cannot be resumed anywhere but in this process.
        raw = json.dumps(job)
        print("  Serialised job:", len(raw), "bytes")

        reloaded = crawl_jobs.load_job(job["job_id"])
        assert reloaded is not None, "Job was not readable after being created."
        assert reloaded["state"]["priority"] == [HOME]
        assert reloaded["vertical_id"] == "b2b_saas_fintech"
        assert reloaded["status"] == crawl_jobs.STATUS_RUNNING
        print("  PASS - a job round-trips through storage.")


def test_job_advances_in_slices_until_done():
    print("\n[2] Testing that repeated slices finish the crawl...")
    with _Harness():
        job = crawl_jobs.create_job(HOME, max_pages=6, vertical_id="b2b_saas_fintech")
        job_id = job["job_id"]

        slices = 0
        status = crawl_jobs.public_status(job)
        while status["status"] == crawl_jobs.STATUS_RUNNING and slices < 20:
            # Two pages per slice, so finishing takes several calls - the behaviour that
            # keeps any single request short.
            # Budgeted in pages rather than seconds so the test is deterministic
            # rather than dependent on how fast the machine runs.
            status = crawl_jobs.advance_job(job_id, slice_pages=2)
            slices += 1
            print("  slice %d -> %s, %d read" % (slices, status["status"], status["pages_crawled"]))
            if slices > 1 and status["pages_crawled"] == 0:
                break

        assert status["status"] == crawl_jobs.STATUS_DONE, status
        assert slices > 1, "Finished in one slice; slicing is not actually happening."
        assert status["pages_crawled"] == 6, status
        assert status["pages_remaining"] == 0

        # Pages found below the start page must survive the resumption, which is where a
        # frontier that did not round-trip properly would show up.
        result = crawl_jobs.load_result(job_id)
        assert result is not None, "Finished job stored no result."
        assert "https://example.com/product/billing" in result["page_urls"], result["page_urls"]
        assert result["vertical_id"] == "b2b_saas_fintech"
        assert result["pages_crawled"] == 6
        print("  PASS - %d slices, %d pages, result stored." % (slices, result["pages_crawled"]))


def test_advancing_a_finished_job_changes_nothing():
    print("\n[3] Testing that advancing a finished job is harmless...")
    with _Harness():
        job = crawl_jobs.create_job(HOME, max_pages=3, vertical_id="b2b_saas_fintech")
        job_id = job["job_id"]
        status = crawl_jobs.advance_job(job_id)
        assert status["status"] == crawl_jobs.STATUS_DONE, status

        again = crawl_jobs.advance_job(job_id)
        assert again["status"] == crawl_jobs.STATUS_DONE
        assert again["pages_crawled"] == status["pages_crawled"]
        assert again["pages_attempted"] == status["pages_attempted"]
        print("  PASS - a finished job stays finished.")


def test_a_leased_job_is_not_advanced_twice():
    print("\n[4] Testing that a job mid-slice is left alone...")
    with _Harness():
        job = crawl_jobs.create_job(HOME, max_pages=6, vertical_id="b2b_saas_fintech")
        job_id = job["job_id"]

        # Simulate another caller partway through a slice.
        job["leased_at"] = crawl_jobs._now()
        crawl_jobs._write(crawl_jobs._job_key(job_id), job)

        status = crawl_jobs.advance_job(job_id)
        # Two callers crawling the same frontier waste a slice and can lose one writer's
        # work. The lease is advisory, but it should hold in the ordinary case.
        assert status["pages_attempted"] == 0, status
        assert status["status"] == crawl_jobs.STATUS_RUNNING
        print("  PASS - the second caller did no work.")


def test_unreadable_site_fails_the_job_rather_than_producing_a_graph():
    print("\n[5] Testing that a site nobody could read fails the job...")
    with _Harness():
        def dead_fetch(url, *args, **kwargs):
            raise RuntimeError("connection refused")

        site_graph.smart_fetch = dead_fetch
        job = crawl_jobs.create_job(HOME, max_pages=3, vertical_id="b2b_saas_fintech")
        job_id = job["job_id"]

        status = crawl_jobs.advance_job(job_id)
        print("  status:", status["status"], "|", status["error"])

        # An empty graph would be scored as covering nothing, so the job has to end with
        # an error rather than a result.
        assert status["status"] == crawl_jobs.STATUS_FAILED, status
        assert "Could not read any page" in (status["error"] or "")
        assert crawl_jobs.load_result(job_id) is None, "Failed job stored a result."
        print("  PASS - no graph is produced for a site that was never read.")


if __name__ == "__main__":
    print("=" * 55)
    print("CRAWL JOB TESTS")
    print("=" * 55)
    test_job_state_survives_storage()
    test_job_advances_in_slices_until_done()
    test_advancing_a_finished_job_changes_nothing()
    test_a_leased_job_is_not_advanced_twice()
    test_unreadable_site_fails_the_job_rather_than_producing_a_graph()
    print("\n" + "=" * 55)
    print("ALL CRAWL JOB TESTS PASSED")
    print("=" * 55)
