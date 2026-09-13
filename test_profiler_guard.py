"""
test_profiler_guard.py — Unit Tests for Guardrails AI Validation & Schema Enforcement
"""

import unittest
from profiler_guard import (
    sanitize_concept_id,
    validate_concept_id,
    validate_evidence_citation,
    validate_relation_semantics,
    guard_discovered_vertical_profile
)


class TestProfilerGuard(unittest.TestCase):

    def test_sanitize_concept_id(self):
        self.assertEqual(sanitize_concept_id("Revenue Recognition"), "revenue_recognition")
        self.assertEqual(sanitize_concept_id("ASC-606 Compliance"), "asc_606_compliance")
        self.assertEqual(sanitize_concept_id("  Multi-Currency Billing!  "), "multi_currency_billing")
        self.assertEqual(sanitize_concept_id(""), "unnamed_concept")
        self.assertEqual(sanitize_concept_id("___test___"), "test")

    def test_validate_concept_id(self):
        self.assertTrue(validate_concept_id("revenue_recognition"))
        self.assertTrue(validate_concept_id("asc_606"))
        self.assertTrue(validate_concept_id("billing"))
        self.assertFalse(validate_concept_id("Revenue Recognition"))
        self.assertFalse(validate_concept_id("asc-606"))
        self.assertFalse(validate_concept_id("billing__dunning"))
        self.assertFalse(validate_concept_id(""))

    def test_validate_evidence_citation(self):
        valid_quote = "Ordway automates revenue recognition compliant with ASC 606 and IFRS 15 standards."
        self.assertTrue(validate_evidence_citation(valid_quote))

        # Too short
        self.assertFalse(validate_evidence_citation("SOC 2"))
        # Obvious placeholders
        self.assertFalse(validate_evidence_citation("N/A"))
        self.assertFalse(validate_evidence_citation("Found on page"))
        self.assertFalse(validate_evidence_citation("None"))
        self.assertFalse(validate_evidence_citation(None))

    def test_validate_relation_semantics(self):
        # Valid relation registered in ontology_schema.CORE_RELATIONS
        is_valid, err = validate_relation_semantics(
            predicate="compliesWith",
            subject_type="SoftwarePlatform",
            object_type="Standard"
        )
        self.assertTrue(is_valid)
        self.assertIsNone(err)

        # Unknown predicate
        is_valid, err = validate_relation_semantics(predicate="fictionalPredicate")
        self.assertFalse(is_valid)
        self.assertIn("not registered", err)

    def test_guard_discovered_vertical_profile(self):
        raw_profile = {
            "vertical_id": "Fintech & Billing Platform!",
            "display_name": "  B2B Fintech Vertical  ",
            "seed_concepts": [
                "Revenue Recognition",
                "revenue_recognition",  # Duplicate once sanitized
                "Dunning Management",
                {"concept_id": "Usage-Based Metering", "label": "Usage-Based Metering"}
            ],
            "compliance_frameworks": ["SOC 2 Type II", "  ", "HIPAA", "A"],
            "direct_competitors": ["Chargebee", "Stripe Billing", ""]
        }

        guarded = guard_discovered_vertical_profile(raw_profile)

        # Checked sanitized vertical_id
        self.assertEqual(guarded["vertical_id"], "fintech_billing_platform")
        self.assertEqual(guarded["name"], "B2B Fintech Vertical")

        # Checked deduplicated and cleaned concepts
        self.assertEqual(len(guarded["seed_concepts"]), 3)
        self.assertIn("Revenue Recognition", guarded["seed_concepts"])
        self.assertIn("Dunning Management", guarded["seed_concepts"])
        self.assertIn("Usage-Based Metering", guarded["seed_concepts"])

        # Checked compliance frameworks
        self.assertEqual(guarded["compliance_frameworks"], ["SOC 2 Type II", "HIPAA"])

        # Checked competitors
        self.assertEqual(guarded["direct_competitors"], ["Chargebee", "Stripe Billing"])


if __name__ == "__main__":
    unittest.main()
