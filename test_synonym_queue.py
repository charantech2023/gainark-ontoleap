"""
The synonym review queue is shared through the archive (TODO T7).

It used to be a JSON file rewritten on each instance's own disk, so on Cloud Run pending
proposals vanished on recycle and differed between instances. These tests hold the
replacement to what that file could not do: two instances see one queue, neither can
overwrite the other, a reviewer's decision outlives the instance that recorded it, and a
rejected pair stays rejected.
"""

import json
import os
import shutil
import tempfile
import unittest

from graph_archive import DirectoryArchive
from synonym_queue import (DECIDED_PREFIX, PROPOSED_PREFIX, SynonymQueue, entry_key, fold)


def entry(surface, canonical="Dunning", brand="acme.com"):
    return {"surface_form": surface, "generated_canonical": canonical, "brand": brand}


class SynonymQueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.archive_dir = os.path.join(self.tmp, "archive")

    def instance(self, **kw):
        """A fresh process over the same archive: nothing cached, everything read back."""
        return SynonymQueue(DirectoryArchive(self.archive_dir), **kw)

    def test_two_instances_see_one_queue(self):
        a, b = self.instance(), self.instance()
        a.propose({entry_key("Dunning", "smart retries"): entry("smart retries")})
        b.propose({entry_key("Dunning", "failed payment retries"): entry("failed payment retries")})
        for q in (a, b):
            self.assertEqual(set(q.load(force=True)),
                             {"dunning|smart retries", "dunning|failed payment retries"})

    def test_concurrent_proposals_of_one_pair_queue_it_once(self):
        a, b = self.instance(), self.instance()
        key = entry_key("Dunning", "smart retries")
        # Neither has seen the other's write when it proposes.
        a.propose({key: entry("smart retries", brand="a.com")})
        b._events.clear(); b._synced_at = 0.0
        b.propose({key: entry("smart retries", brand="b.com")})
        queue = self.instance().load()
        self.assertEqual(list(queue), [key])
        self.assertEqual(queue[key]["brand"], "a.com", "the first proposal wins")

    def test_every_write_is_its_own_object(self):
        q = self.instance()
        q.propose({entry_key("Dunning", "x"): entry("x"), entry_key("Dunning", "y"): entry("y")})
        q.approve("x", "Dunning")
        store = DirectoryArchive(self.archive_dir)
        self.assertEqual(len(store.list(PROPOSED_PREFIX + "/")), 2)
        self.assertEqual(len(store.list(DECIDED_PREFIX + "/")), 1)

    def test_an_approval_on_one_instance_settles_it_on_another(self):
        self.instance().propose({entry_key("Dunning", "smart retries"): entry("smart retries")})
        self.instance().approve("Smart Retries", "Dunning", "billing_ops")
        got = self.instance().load()["dunning|smart retries"]
        self.assertEqual(got["status"], "approved")
        self.assertEqual(got["approved_as"], "Dunning")
        self.assertEqual(got["vertical_id"], "billing_ops")

    def test_an_approval_settles_every_pairing_of_the_surface_form(self):
        q = self.instance()
        q.propose({entry_key("Dunning", "retries"): entry("retries"),
                   entry_key("Collections", "retries"): entry("retries", "Collections")})
        q.approve("retries", "Dunning")
        self.assertEqual({e["status"] for e in q.load().values()}, {"approved"})

    def test_a_later_proposal_of_an_approved_form_is_not_pending(self):
        q = self.instance()
        q.approve("smart retries", "Dunning")
        q.propose({entry_key("Collections", "smart retries"): entry("smart retries", "Collections")})
        self.assertEqual(q.load()["collections|smart retries"]["status"], "approved")

    def test_a_rejection_is_one_pairing_and_is_never_proposed_again(self):
        q = self.instance()
        wrong, right = entry_key("Transaction Pricing", "variable pricing"), \
            entry_key("Dynamic Pricing", "variable pricing")
        q.propose({wrong: entry("variable pricing", "Transaction Pricing"),
                   right: entry("variable pricing", "Dynamic Pricing")})
        self.assertEqual(q.reject(wrong)["status"], "rejected")
        self.assertEqual(q.load()[right]["status"], "pending", "only the one pairing")
        self.assertEqual(self.instance().propose({wrong: entry("variable pricing")}), [],
                         "a rejected pair came back for review")
        self.assertEqual(self.instance().load()[wrong]["status"], "rejected")

    def test_rejecting_an_unknown_key_says_so(self):
        with self.assertRaises(ValueError):
            self.instance().reject("no|such")

    def test_the_latest_decision_wins(self):
        q = self.instance()
        key = entry_key("Dunning", "retries")
        q.propose({key: entry("retries")})
        q.reject(key)
        q.approve("retries", "Dunning")
        self.assertEqual(q.load()[key]["status"], "approved")

    def test_a_legacy_queue_file_is_imported_once(self):
        legacy = os.path.join(self.tmp, "alt_label_candidates.json")
        with open(legacy, "w", encoding="utf-8") as f:
            json.dump({"dynamic pricing|variable pricing":
                       dict(entry("Variable pricing", "Dynamic Pricing"), status="pending",
                            score=0.62),
                       "dunning|retries":
                       dict(entry("retries"), status="approved", approved_as="Dunning")}, f)
        queue = self.instance(legacy_path=legacy).load()
        self.assertEqual(queue["dynamic pricing|variable pricing"]["status"], "pending")
        self.assertEqual(queue["dynamic pricing|variable pricing"]["score"], 0.62)
        self.assertEqual(queue["dunning|retries"]["status"], "approved")
        self.assertFalse(os.path.exists(legacy))
        self.assertTrue(os.path.exists(legacy + ".imported"))
        self.instance(legacy_path=legacy).load()
        self.assertEqual(len(DirectoryArchive(self.archive_dir).list(PROPOSED_PREFIX + "/")), 2)

    def test_an_approval_does_not_cross_into_another_vertical(self):
        q = self.instance()
        sales, hr = entry_key("Deal Stage", "pipeline", "sales"), entry_key("Hiring", "pipeline", "hr")
        q.propose({sales: dict(entry("pipeline", "Deal Stage"), vertical_id="sales"),
                   hr: dict(entry("pipeline", "Hiring"), vertical_id="hr")})
        q.approve("pipeline", "Deal Stage", "sales")
        queue = self.instance().load()
        self.assertEqual(queue[sales]["status"], "approved")
        self.assertEqual(queue[hr]["status"], "pending",
                         "an approval in sales settled a candidate HR never reviewed")

    def test_one_concept_label_in_two_verticals_is_two_candidates(self):
        q = self.instance()
        a, b = entry_key("Onboarding", "setup", "billing"), entry_key("Onboarding", "setup", "hr")
        self.assertNotEqual(a, b)
        self.assertEqual(sorted(q.propose({a: entry("setup", "Onboarding"),
                                           b: entry("setup", "Onboarding")})), sorted([a, b]))

    def test_an_unscoped_candidate_is_settled_by_an_approval_in_any_vertical(self):
        q = self.instance()
        q.propose({entry_key("Dunning", "retries"): entry("retries")})
        q.approve("retries", "Dunning", "billing_ops")
        self.assertEqual(q.load()["dunning|retries"]["status"], "approved")

    def test_a_withdrawing_rejection_reopens_the_other_pairings(self):
        q = self.instance()
        mine, other = entry_key("Dunning", "retries", "b"), entry_key("Collections", "retries", "b")
        q.propose({mine: dict(entry("retries"), vertical_id="b"),
                   other: dict(entry("retries", "Collections"), vertical_id="b")})
        q.approve("retries", "Dunning", "b")
        q.reject(mine, withdraws_approval=True)
        queue = self.instance().load()
        self.assertEqual(queue[mine]["status"], "rejected")
        self.assertEqual(queue[other]["status"], "pending")

    def test_a_plain_rejection_leaves_the_approval_standing(self):
        q = self.instance()
        mine, other = entry_key("Dunning", "retries", "b"), entry_key("Collections", "retries", "b")
        q.propose({mine: dict(entry("retries"), vertical_id="b"),
                   other: dict(entry("retries", "Collections"), vertical_id="b")})
        q.approve("retries", "Dunning", "b")
        q.reject(other)
        queue = self.instance().load()
        self.assertEqual((queue[mine]["status"], queue[other]["status"]), ("approved", "rejected"))

    def test_a_legacy_file_seen_again_does_not_overturn_a_later_rejection(self):
        """The file turning up a second time - in an image, on another machine - used to
        re-append its approvals, each newer than any rejection made since."""
        legacy = os.path.join(self.tmp, "alt_label_candidates.json")
        content = {"dunning|retries": dict(entry("retries"), status="approved", approved_as="Dunning")}
        with open(legacy, "w", encoding="utf-8") as f:
            json.dump(content, f)
        self.instance(legacy_path=legacy).load()
        self.instance().reject("dunning|retries")

        with open(legacy, "w", encoding="utf-8") as f:     # it comes back
            json.dump(content, f)
        queue = self.instance(legacy_path=legacy).load()
        self.assertEqual(queue["dunning|retries"]["status"], "rejected")
        self.assertEqual(len(DirectoryArchive(self.archive_dir).list(DECIDED_PREFIX + "/")), 2)

    def test_fold_ignores_malformed_events(self):
        self.assertEqual(fold([{"key": "a|b"}, {"entry": {}}], [{"decision": "approved"}]), {})


if __name__ == "__main__":
    unittest.main()
