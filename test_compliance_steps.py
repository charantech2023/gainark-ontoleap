"""
GainARK OntoLeap — Multi-Step Compliance Ontology Tests
========================================================
Validates that:
1. Standards are modeled as structured multi-step regulatory frameworks (ASC 606 5 steps, SOC 2 5 criteria).
2. Step evaluation verifies individual steps independently against technical evidence.
3. Partial vs full compliance is distinguished with evidence attribution.
4. Injected compliesWith triples cite the exact steps satisfied in their provenance note.
"""

import unittest
from models import SemanticTriple
import compliance_ontology as comp_onto


class TestComplianceSteps(unittest.TestCase):

    def test_framework_structures_defined(self):
        """ASC 606 must have 5 steps and SOC 2 must have 5 criteria."""
        asc = comp_onto.COMPLIANCE_FRAMEWORKS.get("ASC 606")
        self.assertIsNotNone(asc)
        self.assertEqual(len(asc["steps"]), 5)
        self.assertEqual(asc["min_steps_satisfied"], 3)

        soc2 = comp_onto.COMPLIANCE_FRAMEWORKS.get("SOC 2")
        self.assertIsNotNone(soc2)
        self.assertEqual(len(soc2["steps"]), 5)

        # Check step numbers 1 through 5 in ASC 606
        step_numbers = [s["step_number"] for s in asc["steps"]]
        self.assertEqual(step_numbers, [1, 2, 3, 4, 5])

    def test_asc606_step_level_evaluation(self):
        """Evidence covering contract modification, POB, SSP, and schedules satisfies 4 of 5 steps."""
        triples = [
            SemanticTriple(
                subject="Ordway",
                predicate="automates",
                object="contract modification",
                evidence_sentence="Ordway handles mid-term contract modification and terms.",
            ),
            SemanticTriple(
                subject="Ordway",
                predicate="hasFeature",
                object="performance obligation",
                evidence_sentence="Define distinct performance obligations per customer contract.",
            ),
            SemanticTriple(
                subject="Ordway",
                predicate="hasFeature",
                object="standalone selling price",
                evidence_sentence="Allocate contract revenue based on standalone selling price (SSP).",
            ),
            SemanticTriple(
                subject="Ordway",
                predicate="automates",
                object="revenue schedules",
                evidence_sentence="Generates automated revenue recognition schedules and waterfalls.",
            ),
        ]

        eval_res = comp_onto.evaluate_compliance_framework(triples, "ASC 606")
        self.assertIsNotNone(eval_res)
        self.assertTrue(eval_res["is_compliant"])
        self.assertEqual(eval_res["steps_satisfied_count"], 4)
        self.assertEqual(eval_res["steps_total"], 5)

        # Step 1, 2, 4, 5 satisfied; Step 3 unbacked
        step_map = {s["step_number"]: s["satisfied"] for s in eval_res["steps"]}
        self.assertTrue(step_map[1])
        self.assertTrue(step_map[2])
        self.assertFalse(step_map[3])
        self.assertTrue(step_map[4])
        self.assertTrue(step_map[5])

    def test_soc2_criteria_evaluation(self):
        """Evidence covering permissions and audit trail satisfies Security criterion."""
        triples = [
            SemanticTriple(
                subject="Ordway",
                predicate="hasFeature",
                object="permissions",
                evidence_sentence="Fine-grained role-based access control and user permissions.",
            ),
            SemanticTriple(
                subject="Ordway",
                predicate="hasFeature",
                object="audit trail",
                evidence_sentence="Complete immutable financial audit trail and change history.",
            ),
        ]

        eval_res = comp_onto.evaluate_compliance_framework(triples, "SOC 2")
        self.assertIsNotNone(eval_res)
        self.assertTrue(eval_res["is_compliant"])

        sec_step = next(s for s in eval_res["steps"] if s["step_number"] == 1)
        self.assertTrue(sec_step["satisfied"])
        self.assertTrue(len(sec_step["matched_evidence"]) > 0)

    def test_inject_compliance_triples_cites_steps(self):
        """Injected compliesWith triple must cite the exact steps in evidence_sentence."""
        triples = [
            SemanticTriple(
                subject="Ordway",
                predicate="automates",
                object="contract modification",
                evidence_sentence="Supports contract amendment.",
            ),
            SemanticTriple(
                subject="Ordway",
                predicate="hasFeature",
                object="performance obligation",
                evidence_sentence="Tracks distinct obligations.",
            ),
            SemanticTriple(
                subject="Ordway",
                predicate="hasFeature",
                object="standalone selling price",
                evidence_sentence="Calculates relative SSP allocation.",
            ),
            SemanticTriple(
                subject="Ordway",
                predicate="automates",
                object="revenue recognition",
                evidence_sentence="Recognizes revenue over time.",
            ),
        ]

        expanded = comp_onto.inject_compliance_triples(triples, "Ordway")
        asc_triples = [t for t in expanded if t.predicate == "compliesWith" and t.object == "ASC 606"]
        self.assertEqual(len(asc_triples), 1)

        t = asc_triples[0]
        self.assertEqual(t.source_type, "compliance_ontology_inference")
        self.assertIn("Step", t.evidence_sentence)
        self.assertIn("ASC 606", t.evidence_sentence)

    def test_list_all_frameworks(self):
        """list_compliance_frameworks must return metadata for all standards."""
        frameworks = comp_onto.list_compliance_frameworks()
        self.assertGreaterEqual(len(frameworks), 5)
        standards = {f["standard"] for f in frameworks}
        self.assertIn("ASC 606", standards)
        self.assertIn("SOC 2", standards)
        self.assertIn("IFRS 15", standards)


if __name__ == "__main__":
    unittest.main()
