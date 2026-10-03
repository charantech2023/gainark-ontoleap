"""Durable storage for vertical profiles.

Discovery writes a profile to the verticals directory, which on Cloud Run is the container
filesystem. Left there, a discovered buyer profile disappears when the instance is
recycled and is invisible to every other instance - which defeats matching a site into an
existing vertical, because a category can only deepen if the last customer's contribution
survived.

These tests use a directory-backed mirror, so nothing here touches Cloud Storage.
"""
import contextlib
import json
import os
import shutil
import tempfile

import graph_archive
import industry_profiler as ip
import vertical_store


def mirrored(remote, vertical_id="billing_ops"):
    payload = graph_archive.open_archive(remote).get(vertical_id + ".json")
    return json.loads(payload.decode("utf-8")) if payload else None


@contextlib.contextmanager
def patched(*swaps):
    saved = [(mod, name, getattr(mod, name)) for mod, name, _ in swaps]
    try:
        for mod, name, value in swaps:
            setattr(mod, name, value)
        yield
    finally:
        for mod, name, value in saved:
            setattr(mod, name, value)


@contextlib.contextmanager
def store(profiles=None):
    """A local verticals directory plus a directory-backed mirror."""
    local = tempfile.mkdtemp(prefix="ontoleap_local_")
    remote = tempfile.mkdtemp(prefix="ontoleap_mirror_")
    for profile in (profiles or []):
        with open(os.path.join(local, profile["vertical_id"] + ".json"), "w", encoding="utf-8") as fh:
            json.dump(profile, fh)
    try:
        with patched((vertical_store, "MIRROR_URI", remote),
                     (vertical_store, "verticals_dir", lambda: local),
                     (vertical_store, "_last_sync", 0.0),
                     (ip, "verticals_dir", lambda: local)):
            yield local, remote
    finally:
        shutil.rmtree(local, ignore_errors=True)
        shutil.rmtree(remote, ignore_errors=True)


def a_profile(vertical_id="billing_ops", **extra):
    base = {
        "vertical_id": vertical_id,
        "display_name": "Billing Operations",
        "gliner_labels": ["Billing Model"],
        "mandatory_schema_types": ["SoftwareApplication"],
        "core_seed_concepts": ["Subscription Billing"],
        "known_integrations": ["NetSuite"],
        "known_compliance": ["ASC 606"],
        "known_pricing": ["Usage-Based Pricing"],
    }
    base.update(extra)
    return base


def test_a_written_profile_reaches_the_mirror():
    with store() as (local, remote):
        written = vertical_store.update("billing_ops", lambda current: a_profile())
        assert written["display_name"] == "Billing Operations"
        assert mirrored(remote)["display_name"] == "Billing Operations"
        with open(os.path.join(local, "billing_ops.json"), encoding="utf-8") as fh:
            assert json.load(fh) == written, "local copy and mirror disagree"


def test_a_concurrent_change_is_reapplied_not_lost():
    """Two writers, one profile: the one that loses the race re-reads and re-applies,
    so both contributions survive. Last-writer-wins kept only one."""
    with store() as (local, remote):
        archive = graph_archive.open_archive(remote)
        archive.put("billing_ops.json", json.dumps(a_profile(known_integrations=["NetSuite"])).encode())
        calls = []

        def add_stripe(current):
            calls.append(list(current["known_integrations"]))
            if len(calls) == 1:
                # Another instance writes between our read and our write.
                archive.put("billing_ops.json",
                            json.dumps(a_profile(known_integrations=["NetSuite", "Xero"])).encode())
            current["known_integrations"].append("Stripe")
            return current

        vertical_store.update("billing_ops", add_stripe)

        assert calls == [["NetSuite"], ["NetSuite", "Xero"]], calls
        assert mirrored(remote)["known_integrations"] == ["NetSuite", "Xero", "Stripe"]


def test_a_mirror_that_keeps_changing_fails_loudly():
    with store() as (local, remote):
        archive = graph_archive.open_archive(remote)
        archive.put("billing_ops.json", json.dumps(a_profile()).encode())
        n = [0]

        def always_raced(current):
            n[0] += 1
            archive.put("billing_ops.json", json.dumps(a_profile(display_name=str(n[0]))).encode())
            return dict(current, display_name="mine")

        with patched((vertical_store.time, "sleep", lambda s: None)):
            try:
                vertical_store.update("billing_ops", always_raced)
            except vertical_store.MirrorWriteError:
                pass
            else:
                raise AssertionError("a write that never landed reported success")
        assert n[0] == vertical_store.UPDATE_ATTEMPTS
        assert mirrored(remote)["display_name"] != "mine"


def test_declining_writes_nothing():
    with store(profiles=[a_profile()]) as (local, remote):
        assert vertical_store.update("billing_ops", lambda current: None) is None
        assert mirrored(remote) is None


def test_a_profile_only_local_is_published_by_its_first_update():
    """A profile shipped in the image is the starting point when the mirror lacks it."""
    with store(profiles=[a_profile()]) as (local, remote):
        vertical_store.update("billing_ops", lambda current: dict(current, display_name="Billing"))
        assert mirrored(remote)["known_integrations"] == ["NetSuite"]
        assert mirrored(remote)["display_name"] == "Billing"


def test_a_profile_only_the_mirror_has_is_pulled_down():
    """A fresh instance starts with an empty directory and must inherit what exists."""
    with store() as (local, remote):
        graph_archive.open_archive(remote).put(
            "billing_ops.json", json.dumps(a_profile()).encode("utf-8"))

        pulled = vertical_store.sync_down(force=True)

        assert pulled == ["billing_ops"], pulled
        with open(os.path.join(local, "billing_ops.json"), encoding="utf-8") as fh:
            assert json.load(fh)["vertical_id"] == "billing_ops"


def test_a_local_only_profile_is_left_alone():
    """Profiles shipped in the image must survive a sync that does not know them."""
    with store(profiles=[a_profile("shipped_with_image")]) as (local, remote):
        vertical_store.sync_down(force=True)
        assert os.path.exists(os.path.join(local, "shipped_with_image.json"))


def test_a_corrupt_mirrored_object_does_not_replace_a_working_profile():
    with store(profiles=[a_profile()]) as (local, remote):
        graph_archive.open_archive(remote).put("billing_ops.json", b"{ not json")

        vertical_store.sync_down(force=True)

        with open(os.path.join(local, "billing_ops.json"), encoding="utf-8") as fh:
            assert json.load(fh)["display_name"] == "Billing Operations", "clobbered by garbage"


def test_syncing_is_rate_limited():
    """A list call on every page load is a cost with no benefit; profiles change rarely."""
    with store() as (local, remote):
        graph_archive.open_archive(remote).put(
            "billing_ops.json", json.dumps(a_profile()).encode("utf-8"))

        assert vertical_store.sync_down(force=True) == ["billing_ops"]
        assert vertical_store.sync_down() == [], "re-synced inside the TTL"
        assert vertical_store.sync_down(force=True) == ["billing_ops"]


def test_no_mirror_configured_is_not_an_error():
    """Local-only is the correct configuration for development."""
    with patched((vertical_store, "MIRROR_URI", "")):
        assert vertical_store.mirror() is None
        assert vertical_store.sync_down(force=True) == []
        local = tempfile.mkdtemp(prefix="ontoleap_local_")
        try:
            path = os.path.join(local, "billing_ops.json")
            assert vertical_store.update("billing_ops", lambda c: a_profile(), path=path)
            assert vertical_store.read_local(path)["vertical_id"] == "billing_ops"
        finally:
            shutil.rmtree(local, ignore_errors=True)


def test_an_unreachable_mirror_fails_the_write_not_the_service():
    """Reads degrade to the local copy. A write does not: kept only locally, the next
    sync would replace it with the mirror's copy, so it is refused out loud instead."""
    class Broken:
        def list(self): raise RuntimeError("network gone")
        def get_versioned(self, key): raise RuntimeError("network gone")
        def put_if(self, key, payload, version): raise RuntimeError("network gone")
        def describe(self): return "broken"

    with store(profiles=[a_profile()]) as (local, remote):
        with patched((vertical_store, "mirror", lambda: Broken())):
            assert vertical_store.sync_down(force=True) == []
            try:
                vertical_store.update("billing_ops", lambda c: dict(c, display_name="changed"))
            except vertical_store.MirrorWriteError:
                pass
            else:
                raise AssertionError("an unmirrored write reported success")
        with open(os.path.join(local, "billing_ops.json"), encoding="utf-8") as fh:
            assert json.load(fh)["display_name"] == "Billing Operations", "changed locally anyway"


def test_a_container_without_a_mirror_says_so():
    """Silence here looks exactly like a working store until a vertical stops growing."""
    with patched((vertical_store, "MIRROR_URI", "")):
        os.environ["K_SERVICE"] = "gainark-ontoleap"
        try:
            assert vertical_store.warn_if_ephemeral()
        finally:
            del os.environ["K_SERVICE"]

    with patched((vertical_store, "MIRROR_URI", "gs://bucket/prefix")):
        os.environ["K_SERVICE"] = "gainark-ontoleap"
        try:
            assert vertical_store.warn_if_ephemeral() is None
        finally:
            del os.environ["K_SERVICE"]


def test_outside_a_container_silence_is_correct():
    with patched((vertical_store, "MIRROR_URI", "")):
        assert vertical_store.warn_if_ephemeral() is None


def test_discovery_writes_survive_for_the_next_instance():
    """End to end: one instance discovers, a fresh one inherits the result."""
    with store() as (local, remote):
        path, mode = ip.save_vertical_configuration(
            vertical_id="billing_ops",
            display_name="Billing Operations",
            gliner_labels=["Billing Model"],
            core_seed_concepts=["Subscription Billing"],
            known_integrations=["NetSuite"],
            known_compliance=["ASC 606"],
            known_pricing=["Usage-Based Pricing"],
            known_segments=["Order fulfillment provider for eCommerce merchants"],
        )
        assert mode == "created"
        assert os.path.exists(path)

        # A new instance: empty directory, same mirror.
        os.remove(path)
        assert vertical_store.sync_down(force=True) == ["billing_ops"]
        with open(path, encoding="utf-8") as fh:
            restored = json.load(fh)
        assert restored["known_segments"] == ["Order fulfillment provider for eCommerce merchants"]
        print("  Buyer profile survived the instance it was written on")


TESTS = [
    test_a_written_profile_reaches_the_mirror,
    test_a_concurrent_change_is_reapplied_not_lost,
    test_a_mirror_that_keeps_changing_fails_loudly,
    test_declining_writes_nothing,
    test_a_profile_only_local_is_published_by_its_first_update,
    test_a_profile_only_the_mirror_has_is_pulled_down,
    test_a_local_only_profile_is_left_alone,
    test_a_corrupt_mirrored_object_does_not_replace_a_working_profile,
    test_syncing_is_rate_limited,
    test_no_mirror_configured_is_not_an_error,
    test_an_unreachable_mirror_fails_the_write_not_the_service,
    test_a_container_without_a_mirror_says_so,
    test_outside_a_container_silence_is_correct,
    test_discovery_writes_survive_for_the_next_instance,
]


if __name__ == "__main__":
    for test in TESTS:
        print("[%s]" % test.__name__)
        test()
    print("\n" + "=" * 55)
    print("ALL %d VERTICAL STORE TESTS PASSED" % len(TESTS))
    print("=" * 55)
