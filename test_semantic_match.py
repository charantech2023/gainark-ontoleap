"""
Semantic synonym proposals: they only ever propose, and ambiguity is refused.

Most tests replace the encoder with fixed vectors, so the logic is tested exactly and
offline. RealModelTest runs the actual model against the real curated vertical, because
the thresholds are claims about that model - a fake encoder cannot say whether they still
hold. It is skipped when the model cannot be loaded.

Run:  python test_semantic_match.py
"""

import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

import torch

import semantic_match as sm
from models import IndustryConcept


def concept(label, definition="", alts=()):
    return IndustryConcept(id=label.lower().replace(" ", "-"), pref_label=label,
                           definition=definition or None, alt_labels=list(alts))


# Unit vectors on hand-picked axes, so every similarity in these tests is known exactly.
# The fifth axis belongs to no concept: energy put there counts against every score at
# once, which is how a term can lead one concept by a wide margin and still score low.
_AXES = {
    "Dunning": [1, 0, 0, 0, 0],
    "SOC 1": [0, 1, 0, 0, 0],
    "SOC 2": [0, 0.995, 0.0999, 0, 0],   # nearly the same direction as SOC 1
    "Payroll": [0, 0, 0, 1, 0],
}


def _vector(text):
    for label, axis in _AXES.items():
        if text.startswith(label + ":"):
            return axis
    lookup = {
        "failed payment retries": [0.9, 0, 0, 0.436, 0],       # clearly Dunning
        "soc audit report": [0, 0.8, 0.07, 0.596, 0],          # between SOC 1 and SOC 2
        # Spread evenly, so it is 0.577 from every concept - under MIN_SCORE everywhere.
        "employee onboarding": [0.5, 0.5, 0, 0.5, 0],
        # Leans towards Dunning and nothing else - a margin of 0.5 - but only at 0.5.
        "vaguely about money": [0.5, 0, 0, 0, 0.866],
    }
    return lookup.get(text.lower(), [0, 0, 0, 0, 1])


def fake_encode(texts):
    vectors = torch.tensor([_vector(t) for t in texts], dtype=torch.float32)
    return torch.nn.functional.normalize(vectors, p=2, dim=1)


CONCEPTS = [
    concept("Dunning", "Chasing a failed payment with retries."),
    concept("SOC 1", "An audit of controls over financial reporting."),
    concept("SOC 2", "An audit of security and availability controls."),
    concept("Payroll", "Paying employees."),
]


class WithFakeEncoder(unittest.TestCase):
    def setUp(self):
        self.real_encode = sm.encode
        sm.encode = fake_encode
        self.saved_switch = os.environ.pop("ONTOLEAP_SEMANTIC_MATCH", None)
        self.tmp = tempfile.mkdtemp()
        self.queue = os.path.join(self.tmp, "alt_label_candidates.json")

    def tearDown(self):
        sm.encode = self.real_encode
        if self.saved_switch is not None:
            os.environ["ONTOLEAP_SEMANTIC_MATCH"] = self.saved_switch
        else:
            os.environ.pop("ONTOLEAP_SEMANTIC_MATCH", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def queued(self):
        if not os.path.exists(self.queue):
            return {}
        with open(self.queue, encoding="utf-8") as fh:
            return json.load(fh)


class FindCandidatesTest(WithFakeEncoder):
    def test_a_clear_match_is_proposed(self):
        found = sm.find_candidates(["failed payment retries"], CONCEPTS)
        self.assertEqual([c.concept for c in found], ["Dunning"])
        self.assertGreaterEqual(found[0].score, sm.MIN_SCORE)
        self.assertGreaterEqual(found[0].margin, sm.MIN_MARGIN)

    def test_a_term_between_two_close_concepts_is_refused(self):
        """The SOC 1 / SOC 2 case: a high score is not enough when the runner-up is close."""
        scored = sm.find_candidates(["SOC audit report"], CONCEPTS, min_margin=0.0)
        self.assertEqual(len(scored), 1)
        self.assertGreaterEqual(scored[0].score, sm.MIN_SCORE, "the score alone would pass")
        self.assertEqual({scored[0].concept, scored[0].runner_up}, {"SOC 1", "SOC 2"})
        self.assertEqual(sm.find_candidates(["SOC audit report"], CONCEPTS), [],
                         "the margin guard must refuse it")

    def test_a_weak_match_is_refused(self):
        self.assertEqual(sm.find_candidates(["employee onboarding"], CONCEPTS), [])

    def test_a_wide_lead_does_not_rescue_a_low_score(self):
        """The floor stands on its own, not only behind the margin guard."""
        scored = sm.find_candidates(["vaguely about money"], CONCEPTS, min_score=0.0)
        self.assertEqual(len(scored), 1)
        self.assertGreaterEqual(scored[0].margin, sm.MIN_MARGIN, "the margin alone would pass")
        self.assertLess(scored[0].score, sm.MIN_SCORE)
        self.assertEqual(sm.find_candidates(["vaguely about money"], CONCEPTS), [])

    def test_the_runner_up_is_searched_across_every_concept(self):
        """Limiting the search to whitespace would hide SOC 1 and let SOC 2 win clean."""
        found = sm.find_candidates(["SOC audit report"], CONCEPTS, whitespace=["SOC 2"])
        self.assertEqual(found, [])

    def test_a_term_the_ontology_already_places_is_left_to_exact_matching(self):
        concepts = CONCEPTS + [concept("Collections", "Getting paid.", alts=["failed payment retries"])]
        self.assertEqual(sm.find_candidates(["Failed Payment Retries"], concepts), [])
        self.assertEqual(sm.find_candidates(["dunning"], CONCEPTS), [], "a label is not a candidate for itself")

    def test_a_concept_without_a_definition_is_not_searched(self):
        """A bare label embeds too weakly to trust: Dunning scored -0.014 without one."""
        bare = [concept("Dunning"), concept("Payroll")]
        self.assertEqual(sm.find_candidates(["failed payment retries"], bare), [])

    def test_fragments_and_repeats_are_dropped(self):
        found = sm.find_candidates(["ab", "failed payment retries", "Failed payment retries"], CONCEPTS)
        self.assertEqual(len(found), 1)

    def test_a_candidate_that_closes_a_reported_gap_comes_first(self):
        found = sm.find_candidates(["failed payment retries"], CONCEPTS, whitespace=["Dunning"])
        self.assertTrue(found[0].closes_gap)
        found = sm.find_candidates(["failed payment retries"], CONCEPTS, whitespace=["Payroll"])
        self.assertFalse(found[0].closes_gap)

    def test_switched_off_it_proposes_nothing(self):
        os.environ["ONTOLEAP_SEMANTIC_MATCH"] = "0"
        self.assertFalse(sm.enabled())
        self.assertEqual(sm.find_candidates(["failed payment retries"], CONCEPTS), [])

    def test_an_unavailable_model_proposes_nothing(self):
        sm.encode = lambda texts: None
        self.assertEqual(sm.find_candidates(["failed payment retries"], CONCEPTS), [])


class ProposeFromAlignmentTest(WithFakeEncoder):
    def alignment(self, proprietary, whitespace=()):
        return SimpleNamespace(proprietary_concepts=list(proprietary),
                               category_whitespace=list(whitespace))

    def industry(self, concepts=CONCEPTS):
        return SimpleNamespace(concepts=list(concepts))

    def test_a_candidate_lands_in_the_review_queue_with_its_reasons(self):
        stats = sm.propose_from_alignment(
            self.alignment(["failed payment retries", "SOC audit report"], whitespace=["Dunning"]),
            self.industry(), brand="acme.com", queue_path=self.queue)
        self.assertEqual(stats["proposed"], 1)
        self.assertEqual(stats["closes_gap"], 1)
        entry = self.queued()["dunning|failed payment retries"]
        self.assertEqual(entry["status"], "pending")
        self.assertEqual(entry["generated_canonical"], "Dunning")
        self.assertEqual(entry["method"], "embedding")
        self.assertEqual(entry["brand"], "acme.com")
        for field in ("score", "runner_up", "margin", "model"):
            self.assertIn(field, entry)
        self.assertTrue(entry["closes_gap"])

    def test_nothing_is_queued_twice(self):
        args = (self.alignment(["failed payment retries"]), self.industry())
        sm.propose_from_alignment(*args, brand="acme.com", queue_path=self.queue)
        stats = sm.propose_from_alignment(*args, brand="acme.com", queue_path=self.queue)
        self.assertEqual(stats["proposed"], 0)
        self.assertEqual(stats["already_queued"], 1)
        self.assertEqual(len(self.queued()), 1)

    def test_an_existing_queue_entry_is_kept(self):
        with open(self.queue, "w", encoding="utf-8") as fh:
            json.dump({"other|x": {"surface_form": "x", "status": "approved"}}, fh)
        sm.propose_from_alignment(self.alignment(["failed payment retries"]), self.industry(),
                                  brand="acme.com", queue_path=self.queue)
        self.assertEqual(self.queued()["other|x"]["status"], "approved")
        self.assertEqual(len(self.queued()), 2)

    def test_a_cold_vertical_says_why_it_proposed_nothing(self):
        stats = sm.propose_from_alignment(self.alignment(["failed payment retries"]),
                                          self.industry(concepts=[]),
                                          brand="acme.com", queue_path=self.queue)
        self.assertEqual(stats["proposed"], 0)
        self.assertIn("no concept layer", stats["skipped"])
        stats = sm.propose_from_alignment(self.alignment(["failed payment retries"]),
                                          self.industry(concepts=[concept("Dunning")]),
                                          brand="acme.com", queue_path=self.queue)
        self.assertIn("definition", stats["skipped"])
        self.assertFalse(os.path.exists(self.queue))

    def test_it_never_raises(self):
        def broken(texts):
            raise RuntimeError("encoder exploded")
        sm.encode = broken
        stats = sm.propose_from_alignment(self.alignment(["failed payment retries"]),
                                          self.industry(), brand="acme.com", queue_path=self.queue)
        self.assertIn("encoder exploded", stats["error"])

    def test_an_approval_closes_the_candidate(self):
        """The reviewer's path: record_reviewer_synonym matches on surface_form."""
        import sector_ontology
        sm.propose_from_alignment(self.alignment(["failed payment retries"]), self.industry(),
                                  brand="acme.com", queue_path=self.queue)
        vertical = os.path.join(self.tmp, "billing.json")
        with open(vertical, "w", encoding="utf-8") as fh:
            json.dump({"concepts": [{"id": "dunning", "prefLabel": "Dunning", "altLabels": []}]}, fh)
        self.assertTrue(sector_ontology.record_reviewer_synonym(
            "failed payment retries", "Dunning", vertical, queue_path=self.queue))
        self.assertEqual(self.queued()["dunning|failed payment retries"]["status"], "approved")
        with open(vertical, encoding="utf-8") as fh:
            self.assertIn("failed payment retries", json.load(fh)["concepts"][0]["altLabels"])


class RealModelTest(unittest.TestCase):
    """The thresholds are claims about a real model. Check them against it."""

    @classmethod
    def setUpClass(cls):
        if sm._load() is None:
            raise unittest.SkipTest("semantic model unavailable: %s" % sm._model_error)
        from industry_ontology import load_industry_ontology
        try:
            cls.concepts = load_industry_ontology("b2b_saas_fintech").concepts
        except ValueError:
            raise unittest.SkipTest("curated vertical b2b_saas_fintech not present")

    def proposed(self, term):
        found = sm.find_candidates([term], self.concepts)
        return found[0].concept if found else None

    def test_different_words_for_a_defined_concept_are_proposed(self):
        self.assertEqual(self.proposed("failed payment retries"), "Payment Retry")
        self.assertEqual(self.proposed("matching payments to invoices"), "Cash Application")

    def test_an_audit_that_could_be_either_soc_report_is_refused(self):
        self.assertIsNone(self.proposed("SOC audit report"))

    def test_a_change_that_could_be_an_upgrade_or_a_downgrade_is_refused(self):
        self.assertIsNone(self.proposed("subscription plan change"))

    def test_off_domain_terms_are_refused(self):
        for term in ("employee onboarding", "applicant tracking", "kubernetes clusters"):
            self.assertIsNone(self.proposed(term), term)


if __name__ == "__main__":
    unittest.main()
