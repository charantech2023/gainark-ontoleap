"""
Run every test file, the way the deploy gate does.

The suite is two kinds of file: unittest modules, and plain scripts that call their test
functions under `if __name__ == "__main__"` (test_synonym_governance, test_crawl_planner,
...). `python -m unittest discover` runs only the first kind and reports the second as
"0 tests, OK", so this runs each file by the means it was written for, each in its own
process so one file's module state cannot leak into the next.

Every file runs without the service's secrets and without the shared brain. A dependency
loads the nearest .env on import, and on a developer machine that is the repo's own .env:
the tests would then run with live keys (13 auth tests fail with ONTOLEAP_API_KEY set) and
write to the bucket. Setting the variables empty here wins over .env, which never
overrides what is already set, so a local run sees what Cloud Build sees.

    python scripts/run_tests.py             # everything the gate runs
    python scripts/run_tests.py vertical    # only files whose name contains "vertical"

Exits non-zero if any file fails, which is what stops a deploy.
"""

import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Files the gate does not run, and why. Each must say what would make it safe to add.
EXCLUDED = {
    "test_wikidata_kb_live.py": "checks constants.WIKIDATA_KB against live Wikidata; an "
                                "edit there must not block a deploy. Run it by hand.",
}

# Emptied for every test: secrets live on the Cloud Run service, never in the image, and
# the archive variables would point a test run at the shared brain.
BLANKED = ("GEMINI_API_KEY", "GOOGLE_KG_API_KEY", "JINA_API_KEY", "ONTOLEAP_JINA_READER",
           "ONTOLEAP_API_KEY", "ONTOLEAP_GRAPH_ARCHIVE", "ONTOLEAP_VERTICALS_MIRROR")

# Generous: the slowest file (test_api.py, which starts the whole app) takes a few minutes.
TIMEOUT_SECONDS = 900


def test_files(pattern=""):
    return sorted(f for f in os.listdir(ROOT)
                  if f.startswith("test_") and f.endswith(".py") and pattern in f)


def command(filename):
    with open(os.path.join(ROOT, filename), encoding="utf-8") as fh:
        source = fh.read()
    if "unittest.TestCase" in source or "unittest.main(" in source:
        return [sys.executable, "-m", "unittest", "-q", filename[:-3]]
    return [sys.executable, filename]


def main(argv):
    pattern = argv[1] if len(argv) > 1 else ""
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    env.update({name: "" for name in BLANKED})
    env.pop("K_SERVICE", None)               # tests must not think they are on Cloud Run

    failed, started = [], time.monotonic()
    files = test_files(pattern)
    for name in files:
        if name in EXCLUDED:
            print("skip  %-45s %s" % (name, EXCLUDED[name]), flush=True)
            continue
        t0 = time.monotonic()
        try:
            run = subprocess.run(command(name), cwd=ROOT, env=env, timeout=TIMEOUT_SECONDS,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 encoding="utf-8", errors="replace")
            ok, output = run.returncode == 0, run.stdout
        except subprocess.TimeoutExpired as err:
            ok, output = False, "%s\nTIMED OUT after %ds" % (err.stdout or "", TIMEOUT_SECONDS)
        print("%s  %-45s %6.1fs" % ("ok  " if ok else "FAIL", name, time.monotonic() - t0),
              flush=True)
        if not ok:
            failed.append(name)
            print("\n".join("      | " + line for line in output.splitlines()[-40:]), flush=True)

    ran = len([f for f in files if f not in EXCLUDED])
    print("\n%d file(s) run, %d failed, %d skipped, %.0fs"
          % (ran, len(failed), len(files) - ran, time.monotonic() - started))
    if failed:
        print("FAILED: " + ", ".join(failed))
        return 1
    if not ran:
        print("No test files matched %r." % pattern)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
