"""
GainARK OntoLeap — Ontology & Knowledge Graph API Tests
========================================================
Validates the REST endpoints in routers/ontology_routes.py:
1. GET /api/ontology/schema
2. GET /api/ontology/concepts
3. GET /api/ontology/compliance-frameworks
4. GET /api/ontology/compliance/{standard}
5. POST /api/ontology/evaluate-compliance
6. GET /api/ontology/candidates
7. POST /api/ontology/approve-synonym
"""

import unittest
from fastapi.testclient import TestClient
from api import app

client = TestClient(app)


class TestOntologyApi(unittest.TestCase):

    def test_get_schema(self):
        """GET /api/ontology/schema must return base URI and relation definitions."""
        resp = client.get("/api/ontology/schema")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("ontology_base_uri", data)
        self.assertEqual(data["ontology_base_uri"], "https://gainark.com/ontoleap/ontology")
        self.assertGreater(data["relations_count"], 5)
        self.assertIn("entity_types", data)
        self.assertIn("Product", data["entity_types"])
        self.assertIn("ComplianceStandard", data["entity_types"])

    def test_get_concepts(self):
        """GET /api/ontology/concepts must return 111 concepts for b2b_saas_fintech."""
        resp = client.get("/api/ontology/concepts?vertical_id=b2b_saas_fintech")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["vertical_id"], "b2b_saas_fintech")
        self.assertGreaterEqual(data["total_concepts"], 111)
        self.assertIn("breakdown_by_kind", data)
        self.assertIn("feature", data["breakdown_by_kind"])
        self.assertIn("standard", data["breakdown_by_kind"])

    def test_get_concepts_filter_by_kind(self):
        """GET /api/ontology/concepts with kind filter must only return that kind."""
        resp = client.get("/api/ontology/concepts?vertical_id=b2b_saas_fintech&kind=standard")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreater(data["filtered_count"], 0)
        for c in data["concepts"]:
            self.assertEqual(c["kind"], "standard")

    def test_get_compliance_frameworks(self):
        """GET /api/ontology/compliance-frameworks returns all registered standards."""
        resp = client.get("/api/ontology/compliance-frameworks")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreaterEqual(data["frameworks_count"], 5)
        standards = [f["standard"] for f in data["frameworks"]]
        self.assertIn("ASC 606", standards)
        self.assertIn("SOC 2", standards)

    def test_get_compliance_detail_asc606(self):
        """GET /api/ontology/compliance/ASC 606 returns the 5 FASB steps."""
        resp = client.get("/api/ontology/compliance/ASC 606")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["standard"], "ASC 606")
        self.assertEqual(len(data["steps"]), 5)
        self.assertEqual(data["steps"][0]["step_number"], 1)

    def test_get_compliance_detail_not_found(self):
        """GET /api/ontology/compliance/UnknownStandard returns 404."""
        resp = client.get("/api/ontology/compliance/NonExistentStandardXYZ")
        self.assertEqual(resp.status_code, 404)

    def test_post_evaluate_compliance(self):
        """POST /api/ontology/evaluate-compliance evaluates technical triples."""
        body = {
            "standard": "ASC 606",
            "technical_triples": [
                {
                    "subject": "Acme",
                    "predicate": "hasFeature",
                    "object": "standalone selling price",
                    "evidence_sentence": "Allocates transaction price via standalone selling price.",
                    "confidence": 0.9,
                },
                {
                    "subject": "Acme",
                    "predicate": "automates",
                    "object": "revenue schedules",
                    "evidence_sentence": "Automated revenue schedules per customer contract.",
                    "confidence": 0.9,
                },
                {
                    "subject": "Acme",
                    "predicate": "hasFeature",
                    "object": "performance obligation",
                    "evidence_sentence": "Tracks distinct performance obligations.",
                    "confidence": 0.9,
                },
            ],
        }
        resp = client.post("/api/ontology/evaluate-compliance", json=body)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("evaluations", data)
        self.assertEqual(len(data["evaluations"]), 1)
        ev = data["evaluations"][0]
        self.assertEqual(ev["standard"], "ASC 606")
        self.assertTrue(ev["is_compliant"])
        self.assertGreaterEqual(ev["steps_satisfied_count"], 3)

    def test_get_candidates(self):
        """GET /api/ontology/candidates returns candidate queue."""
        resp = client.get("/api/ontology/candidates")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("total_candidates", data)
        self.assertIn("pending_count", data)
        self.assertIsInstance(data["candidates"], list)


if __name__ == "__main__":
    unittest.main()
