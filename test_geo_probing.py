"""
Unit & Integration Test Suite: Generative Engine Optimization (GEO) & Live AI Citation Probing
"""

import unittest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from api import app
from models import (
    GeoAuditRequest,
    GeoAuditResponse,
    GeoProbeResult,
    GeoQueryItem,
    SemanticTriple,
)
from geo_engine import (
    _audit_hallucination_against_ontology,
    _extract_brand_rank_and_competitors,
    execute_geo_citation_audit,
    generate_buyer_queries,
    probe_ai_citation,
)


class TestGeoProbing(unittest.TestCase):

    def setUp(self):
        self.client = TestClient(app)
        self.sample_triples = [
            SemanticTriple(subject="Ordway", predicate="compliesWith", object="ASC 606", confidence=0.95),
            SemanticTriple(subject="Ordway", predicate="compliesWith", object="SOC 2 Type II", confidence=0.95),
            SemanticTriple(subject="Ordway", predicate="integratesWith", object="NetSuite", confidence=0.90),
            SemanticTriple(subject="Ordway", predicate="integratesWith", object="Salesforce", confidence=0.90),
            SemanticTriple(subject="Ordway", predicate="automates", object="Revenue Recognition", confidence=0.85),
        ]

    def test_buyer_query_generation(self):
        queries = generate_buyer_queries(
            brand_name="Ordway",
            domain="ordwaylabs.com",
            triples=self.sample_triples,
            competitors=["Chargebee", "Stripe"],
            count=5,
        )
        self.assertEqual(len(queries), 5)
        # Verify queries contain key ontology concepts
        all_q_text = " ".join([q.query_text for q in queries])
        self.assertTrue(any(c in all_q_text for c in ["ASC 606", "SOC 2 Type II", "NetSuite"]))
        self.assertTrue(all(isinstance(q, GeoQueryItem) for q in queries))

    def test_brand_rank_and_competitor_extraction(self):
        # Case 1: Competitor 1st, Brand 2nd, Competitor 3rd
        sample_answer = (
            "For mid-market SaaS companies, Chargebee is often the top recommendation for subscription management. "
            "However, Ordway is strongly suited for complex revenue recognition and NetSuite ERP integrations, "
            "while Zuora caters to large-scale enterprises."
        )
        cited, rank, comps = _extract_brand_rank_and_competitors(
            text=sample_answer,
            brand_name="Ordway",
            domain="ordwaylabs.com",
            competitor_list=["Chargebee", "Zuora", "Stripe"],
        )
        self.assertTrue(cited)
        self.assertEqual(rank, 2)
        self.assertIn("Chargebee", comps)
        self.assertIn("Zuora", comps)
        self.assertNotIn("Stripe", comps)

        # Case 2: Brand omitted completely
        sample_answer_omitted = (
            "The market leaders in recurring billing are Stripe and Chargebee, offering turnkey payments and checkout."
        )
        cited, rank, comps = _extract_brand_rank_and_competitors(
            text=sample_answer_omitted,
            brand_name="Ordway",
            domain="ordwaylabs.com",
            competitor_list=["Chargebee", "Zuora", "Stripe"],
        )
        self.assertFalse(cited)
        self.assertIsNone(rank)
        self.assertIn("Chargebee", comps)
        self.assertIn("Stripe", comps)

    def test_hallucination_and_grounding_audit(self):
        sample_answer_with_hallucination = (
            "Ordway offers native ASC 606 revenue recognition and NetSuite integration. "
            "In addition, the platform provides blockchain settlement and quantum encryption for security."
        )
        verified, unbacked = _audit_hallucination_against_ontology(
            text=sample_answer_with_hallucination,
            brand_name="Ordway",
            triples=self.sample_triples,
        )
        # ASC 606 and NetSuite should be verified
        self.assertTrue(any("ASC 606" in v for v in verified))
        self.assertTrue(any("NetSuite" in v for v in verified))
        # Blockchain should be flagged as unbacked
        self.assertTrue(any("Blockchain" in u for u in unbacked))

    def test_execute_geo_citation_audit_deterministic(self):
        req = GeoAuditRequest(
            brand_name="Ordway",
            domain="ordwaylabs.com",
            competitor_names=["Chargebee", "Stripe"],
            triples=self.sample_triples,
        )
        # Mock Gemini as unavailable to verify deterministic/search fallback
        with patch("vertex_ai_client.is_available", return_value=False), \
             patch("geo_engine.fetch_open_search_snippets", return_value=[]):
            response = execute_geo_citation_audit(req)

            self.assertIsInstance(response, GeoAuditResponse)
            self.assertEqual(response.brand_name, "Ordway")
            self.assertTrue(0.0 <= response.share_of_voice <= 100.0)
            self.assertTrue(0.0 <= response.weighted_sov <= 100.0)
            self.assertEqual(len(response.probe_results), 5)
            self.assertTrue(len(response.geo_recommendations) > 0)
            for res in response.probe_results:
                self.assertIsInstance(res, GeoProbeResult)
                self.assertTrue(len(res.query) > 0)

    def test_api_geo_citation_audit_endpoint(self):
        payload = {
            "brand_name": "Ordway",
            "domain": "ordwaylabs.com",
            "competitor_names": ["Chargebee", "Stripe"],
            "triples": [t.model_dump() for t in self.sample_triples],
            "vertical_id": "b2b_saas_fintech",
        }
        with patch("vertex_ai_client.is_available", return_value=False), \
             patch("geo_engine.fetch_open_search_snippets", return_value=[]):
            r = self.client.post("/api/geo/citation-audit", json=payload)
            self.assertEqual(r.status_code, 200)
            data = r.json()
            self.assertIn("share_of_voice", data)
            self.assertIn("weighted_sov", data)
            self.assertIn("competitor_sov", data)
            self.assertIn("probe_results", data)
            self.assertEqual(len(data["probe_results"]), 5)

    def test_api_geo_generate_queries_endpoint(self):
        payload = {
            "brand_name": "Ordway",
            "domain": "ordwaylabs.com",
            "competitor_names": ["Chargebee"],
            "triples": [t.model_dump() for t in self.sample_triples],
            "vertical_id": "b2b_saas_fintech",
        }
        r = self.client.post("/api/geo/generate-queries", json=payload)
        self.assertEqual(r.status_code, 200)
        queries = r.json()
        self.assertEqual(len(queries), 5)
        self.assertIn("query_text", queries[0])
        self.assertIn("category", queries[0])


if __name__ == "__main__":
    unittest.main()
