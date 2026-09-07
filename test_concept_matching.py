"""
Test suite for Concept Matching Accuracy & Integrity Fixes (Task T1 & T2)
Verifies:
1. False-positive elimination: 'payment processing' vs 'payment fraud detection', etc.
2. Semantic head matching: 'dunning automation' vs 'automated dunning'
3. Match strength reporting in SemanticTriple and ProductTruthMatrixResponse
4. Ordway demo mode gating via ONTOLEAP_DEMO_MODE and exact hostname check
"""

import os
import unittest
from unittest.mock import patch

from models import SemanticTriple
from product_truth import (
    _concepts_match,
    _match_concept_details,
    _extract_semantic_head,
    build_product_truth_matrix,
    fetch_docs_content,
)


class TestConceptMatching(unittest.TestCase):

    def test_semantic_head_extraction(self):
        """Test extraction of core content head noun after stripping generic wrappers."""
        self.assertEqual(_extract_semantic_head("dunning automation"), "dunning")
        self.assertEqual(_extract_semantic_head("automated dunning"), "dunning")
        self.assertEqual(_extract_semantic_head("payment processing"), "processing")
        self.assertEqual(_extract_semantic_head("payment fraud detection"), "detection")
        self.assertEqual(_extract_semantic_head("invoice automation"), "invoice")
        self.assertEqual(_extract_semantic_head("invoice fraud"), "fraud")
        self.assertEqual(_extract_semantic_head("revenue recognition platform"), "recognition")

    def test_concepts_match_regression_negatives(self):
        """Regression tests from TODO.md T1: single shared token must NOT match when heads differ."""
        # Case 1: payment processing vs payment fraud detection
        matched, strength = _match_concept_details("payment processing", "payment fraud detection")
        self.assertFalse(matched)
        self.assertIsNone(strength)
        self.assertFalse(_concepts_match("payment processing", "payment fraud detection"))

        # Case 2: invoice automation vs invoice fraud
        matched, strength = _match_concept_details("invoice automation", "invoice fraud")
        self.assertFalse(matched)
        self.assertIsNone(strength)
        self.assertFalse(_concepts_match("invoice automation", "invoice fraud"))

        # Case 3: billing platform vs billing dispute
        matched, strength = _match_concept_details("billing platform", "billing dispute")
        self.assertFalse(matched)
        self.assertIsNone(strength)
        self.assertFalse(_concepts_match("billing platform", "billing dispute"))

    def test_concepts_match_positives_and_strengths(self):
        """Verify genuine matches and correct strength classification."""
        # Exact match
        m, s = _match_concept_details("Revenue Recognition", "revenue recognition")
        self.assertTrue(m)
        self.assertEqual(s, "exact")

        # Substring match
        m, s = _match_concept_details("ASC 606", "ASC 606 Compliance")
        self.assertTrue(m)
        self.assertEqual(s, "substring")

        # Multi-token match
        m, s = _match_concept_details("automated subscription billing", "subscription billing engine")
        self.assertTrue(m)
        self.assertEqual(s, "multi_token")

        # Semantic head token match
        m, s = _match_concept_details("dunning automation", "automated dunning")
        self.assertTrue(m)
        self.assertEqual(s, "head_token")

    def test_matrix_records_match_strength_and_breakdown(self):
        """Verify build_product_truth_matrix records match_strength and breakdown."""
        marketing = [
            SemanticTriple(subject="Acme", predicate="automates", object="Revenue Recognition"),
            SemanticTriple(subject="Acme", predicate="automates", object="dunning automation"),
            SemanticTriple(subject="Acme", predicate="automates", object="Payment Processing"),
        ]
        technical = [
            SemanticTriple(subject="Acme", predicate="automates", object="revenue recognition", source_type="technical_truth"),
            SemanticTriple(subject="Acme", predicate="automates", object="automated dunning", source_type="technical_truth"),
            SemanticTriple(subject="Acme", predicate="automates", object="Payment Fraud Detection", source_type="technical_truth"),
        ]

        matrix = build_product_truth_matrix(
            marketing_triples=marketing,
            technical_triples=technical,
            brand_name="Acme",
            marketing_url="https://acme.example",
            tech_docs_url="https://docs.acme.example",
        )

        self.assertEqual(matrix.verified_claims_count, 2)
        self.assertEqual(matrix.unbacked_claims_count, 1)  # Payment Processing unbacked because heads differ!

        # Check breakdown
        self.assertIn("exact", matrix.verified_claims_breakdown)
        self.assertIn("head_token", matrix.verified_claims_breakdown)
        self.assertEqual(matrix.verified_claims_breakdown["exact"], 1)
        self.assertEqual(matrix.verified_claims_breakdown["head_token"], 1)

        # Check match_strength on verified triples
        strengths = {t.object: t.match_strength for t in matrix.verified_triples}
        self.assertEqual(strengths.get("Revenue Recognition"), "exact")
        self.assertEqual(strengths.get("dunning automation"), "head_token")

    def test_ordway_fallback_gating(self):
        """Verify Ordway fallback is strictly gated behind ONTOLEAP_DEMO_MODE and exact host."""
        # 1. Default (no DEMO_MODE) -> must raise exception on fetch error
        with patch.dict(os.environ, {}, clear=True), \
             patch("product_truth.smart_fetch", side_effect=RuntimeError("Connection refused")):
            with self.assertRaises(RuntimeError):
                fetch_docs_content("https://ordwaylabs.com/docs")

        # 2. Unrelated host with ordway in path/subdomain (ordway-consulting.com) -> must raise exception even if DEMO_MODE=1
        with patch.dict(os.environ, {"ONTOLEAP_DEMO_MODE": "1"}), \
             patch("product_truth.smart_fetch", side_effect=RuntimeError("Connection refused")):
            with self.assertRaises(RuntimeError):
                fetch_docs_content("https://ordway-consulting.com/docs")

        # 3. Exactly ordwaylabs.com with DEMO_MODE=1 and fixture present -> returns fixture
        if os.path.exists("ordway_extraction.json"):
            with patch.dict(os.environ, {"ONTOLEAP_DEMO_MODE": "1"}), \
                 patch("product_truth.smart_fetch", side_effect=RuntimeError("Connection refused")):
                raw, text = fetch_docs_content("https://www.ordwaylabs.com/docs")
                self.assertIn("ordway", raw.lower())


if __name__ == "__main__":
    unittest.main()
