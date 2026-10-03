"""
An approved synonym must survive the container, and a withdrawn one must leave it.

On Cloud Run the verticals directory is the instance filesystem, and vertical_store.sync_down()
overwrites it from the mirror. So an approval is applied to the mirror's copy through
vertical_store.update(): re-applied if another writer got there first, and refused - not
kept locally - if the mirror cannot take it, because a local-only change is undone by the
next sync while the queue goes on saying "approved".

A directory stands in for the bucket. No network calls.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import sector_ontology
import vertical_store
from graph_archive import DirectoryArchive
from synonym_queue import SynonymQueue, entry_key

VID = "billing_ops"


def profile(alts, extra_concepts=()):
    return {"vertical_id": VID,
            "concepts": [{"id": "dunning", "prefLabel": "Dunning", "altLabels": list(alts)}]
            + list(extra_concepts)}


class _Mirrored(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        local = os.path.join(self.tmp, "verticals")
        os.makedirs(local)
        self.vertical = os.path.join(local, VID + ".json")
        self.mirror_dir = os.path.join(self.tmp, "mirror")
        self.mirror = DirectoryArchive(self.mirror_dir)
        self.queue = SynonymQueue(DirectoryArchive(os.path.join(self.tmp, "queue")))
        for patch in (mock.patch.object(vertical_store, "MIRROR_URI", self.mirror_dir),
                      mock.patch.object(vertical_store, "verticals_dir", lambda: local)):
            patch.start()
            self.addCleanup(patch.stop)
        self.write_local(profile([]))

    def write_local(self, data):
        with open(self.vertical, "w", encoding="utf-8") as fh:
            json.dump(data, fh)

    def write_mirror(self, data):
        self.mirror.put(VID + ".json", json.dumps(data).encode("utf-8"))

    def mirrored(self):
        payload = self.mirror.get(VID + ".json")
        return json.loads(payload.decode("utf-8")) if payload else None

    def local(self):
        with open(self.vertical, encoding="utf-8") as fh:
            return json.load(fh)

    def alts(self, data):
        return data["concepts"][0]["altLabels"]

    def approve(self, surface, concept="Dunning"):
        return sector_ontology.record_reviewer_synonym(surface, concept, self.vertical,
                                                       queue=self.queue)


class ApprovalIsMirroredTest(_Mirrored):

    def test_applies_to_the_mirrors_copy_and_writes_it_back(self):
        # Another instance approved "retry logic"; this instance's local copy is stale.
        self.write_mirror(profile(["retry logic"]))
        self.assertTrue(self.approve("failed payment retries"))
        self.assertEqual(self.alts(self.mirrored()), ["retry logic", "failed payment retries"],
                         "the mirror lost the other instance's alternate or the new one")
        self.assertEqual(self.mirrored()["alt_labels"]["Dunning"],
                         ["retry logic", "failed payment retries"])
        self.assertEqual(self.local(), self.mirrored(), "local copy and mirror disagree")

    def test_a_concurrent_write_is_kept_not_overwritten(self):
        self.write_mirror(profile([]))
        real_put_if, raced = self.mirror.put_if, []

        def put_if(key, payload, version):
            if not raced:
                raced.append(True)              # another reviewer lands first
                self.write_mirror(profile(["retry logic"]))
            return real_put_if(key, payload, version)

        with mock.patch.object(vertical_store, "mirror", lambda: self.mirror), \
                mock.patch.object(self.mirror, "put_if", side_effect=put_if):
            self.assertTrue(self.approve("failed payment retries"))
        self.assertEqual(self.alts(self.mirrored()), ["retry logic", "failed payment retries"])

    def test_a_refused_approval_writes_nothing(self):
        self.assertFalse(self.approve("anything", "No Such Concept"))
        self.assertIsNone(self.mirrored())
        self.assertEqual(self.queue.load(force=True), {})

    def test_an_unreachable_mirror_fails_the_approval_and_changes_nothing(self):
        key = entry_key("Dunning", "failed payment retries", VID)
        self.queue.propose({key: {"surface_form": "failed payment retries",
                                  "generated_canonical": "Dunning", "vertical_id": VID}})
        with mock.patch.object(self.mirror, "put_if", side_effect=OSError("network gone")), \
                mock.patch.object(vertical_store, "mirror", lambda: self.mirror):
            with self.assertRaises(vertical_store.MirrorWriteError):
                self.approve("failed payment retries")
        self.assertEqual(self.alts(self.local()), [], "kept locally, to be undone by the next sync")
        self.assertEqual(self.queue.load(force=True)[key]["status"], "pending",
                         "the queue says approved for an approval that did not happen")


class RejectionWithdrawsAnApprovalTest(_Mirrored):

    def setUp(self):
        super().setUp()
        self.write_mirror(profile([], [{"id": "collections", "prefLabel": "Collections",
                                         "altLabels": []}]))
        self.dunning = entry_key("Dunning", "retries", VID)
        self.collections = entry_key("Collections", "retries", VID)
        self.queue.propose({
            self.dunning: {"surface_form": "retries", "generated_canonical": "Dunning",
                           "vertical_id": VID},
            self.collections: {"surface_form": "retries", "generated_canonical": "Collections",
                               "vertical_id": VID}})

    def reject(self, key):
        return sector_ontology.reject_synonym_candidate(
            key, lambda vid: self.vertical if vid == VID else None, queue=self.queue)

    def test_rejecting_an_approved_pairing_takes_it_out_of_the_vertical(self):
        self.approve("retries")
        self.assertEqual(self.alts(self.mirrored()), ["retries"])

        self.assertEqual(self.reject(self.dunning)["status"], "rejected")

        self.assertEqual(self.alts(self.mirrored()), [], "extraction would keep applying it")
        self.assertEqual(self.alts(self.local()), [])
        self.assertEqual(self.queue.load(force=True)[self.collections]["status"], "pending",
                         "the withdrawn approval still settles the other pairing")

    def test_rejecting_another_pairing_leaves_the_approval(self):
        self.approve("retries")
        self.reject(self.collections)
        self.assertEqual(self.alts(self.mirrored()), ["retries"],
                         "'retries is not Collections' says nothing against Dunning")
        self.assertEqual(self.queue.load(force=True)[self.dunning]["status"], "approved")

    def test_a_withdrawal_that_cannot_reach_the_mirror_records_nothing(self):
        self.approve("retries")
        with mock.patch.object(self.mirror, "put_if", side_effect=OSError("network gone")), \
                mock.patch.object(vertical_store, "mirror", lambda: self.mirror):
            with self.assertRaises(vertical_store.MirrorWriteError):
                self.reject(self.dunning)
        self.assertEqual(self.queue.load(force=True)[self.dunning]["status"], "approved")
        self.assertEqual(self.alts(self.mirrored()), ["retries"])

    def test_rejecting_a_pending_pairing_touches_no_vertical(self):
        before = self.mirrored()
        self.reject(self.dunning)
        self.assertEqual(self.mirrored(), before)


if __name__ == "__main__":
    unittest.main()
