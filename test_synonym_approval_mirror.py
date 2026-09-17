"""
An approved synonym must survive the container.

On Cloud Run the verticals directory is the instance filesystem, and vertical_store.sync_down()
overwrites it from the mirror. record_reviewer_synonym() used to write only the local file, so
an approval vanished on the next sync or recycle. It now syncs first, so the approval applies to
what other instances contributed, and publishes the result.

No network calls.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import sector_ontology
import vertical_store


def profile(alts):
    return {"concepts": [{"id": "dunning", "prefLabel": "Dunning", "altLabels": list(alts)}]}


class ApprovalIsMirroredTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.vertical = os.path.join(self.tmp, "billing_ops.json")
        self.queue = os.path.join(self.tmp, "candidates.json")
        with open(self.vertical, "w", encoding="utf-8") as fh:
            json.dump(profile([]), fh)

    def test_syncs_then_publishes_the_updated_profile(self):
        calls = []

        def sync_down(force=False):
            # Another instance approved "retry logic" and mirrored it; the sync brings it here.
            calls.append(("sync", force))
            with open(self.vertical, "w", encoding="utf-8") as fh:
                json.dump(profile(["retry logic"]), fh)
            return ["billing_ops"]

        def publish(vertical_id, payload):
            calls.append(("publish", vertical_id, json.loads(json.dumps(payload))))
            return "gs://bucket/billing_ops.json"

        with mock.patch.object(vertical_store, "sync_down", side_effect=sync_down), \
                mock.patch.object(vertical_store, "publish", side_effect=publish) as published:
            self.assertTrue(sector_ontology.record_reviewer_synonym(
                "failed payment retries", "Dunning", self.vertical, queue_path=self.queue))

        published.assert_called_once()
        self.assertEqual(calls[0], ("sync", True), "the approval did not start from the mirror")
        kind, vertical_id, payload = calls[1]
        self.assertEqual((kind, vertical_id), ("publish", "billing_ops"))
        self.assertEqual(payload["concepts"][0]["altLabels"],
                         ["retry logic", "failed payment retries"],
                         "the published profile lost the mirrored alternate or the new one")
        self.assertEqual(payload["alt_labels"]["Dunning"],
                         ["retry logic", "failed payment retries"])
        with open(self.vertical, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), payload, "local copy and mirror disagree")

    def test_a_refused_approval_publishes_nothing(self):
        with mock.patch.object(vertical_store, "sync_down", return_value=[]), \
                mock.patch.object(vertical_store, "publish") as published:
            self.assertFalse(sector_ontology.record_reviewer_synonym(
                "anything", "No Such Concept", self.vertical, queue_path=self.queue))
        published.assert_not_called()


if __name__ == "__main__":
    unittest.main()
