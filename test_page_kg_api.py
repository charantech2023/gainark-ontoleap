"""
GainARK OntoLeap — Page Knowledge Graph API & MCP Integration Tests
===================================================================
Validates:
1. POST /api/page-knowledge-graph with raw text content.
2. Error handling when both url and text are omitted (validation rejection).
3. W3C RDF Turtle (.ttl) and JSON-LD serialization output.
4. Retention of 100% evidentiary sentence quotes in response triples.
5. Dynamic SKOS Concept Scheme generation with Universal B2B Facets.
6. Execution of ontoleap_build_page_knowledge_graph MCP tool in mcp_server.py.
"""

import unittest
import json
import asyncio
from fastapi.testclient import TestClient
from api import app
from mcp_server import ontoleap_build_page_knowledge_graph

client = TestClient(app)


class TestPageKnowledgeGraphApi(unittest.TestCase):

    def test_page_kg_text_extraction(self):
        """POST /api/page-knowledge-graph with cybersecurity copy."""
        snyk_copy = (
            "Snyk automates container vulnerability scanning and source code security for cloud-native teams. "
            "The platform integrates with GitHub, GitLab, and Jira. Snyk is SOC 2 Type II compliant and ISO 27001 certified. "
            "We offer developers programmatic access through our REST API and GraphQL API."
        )
        payload = {
            "text": snyk_copy,
            "subject": "Snyk",
            "title": "Snyk Cloud Security Platform",
            "url": "https://snyk.io/platform"
        }
        resp = client.post("/api/page-knowledge-graph", json=payload)
        self.assertEqual(resp.status_code, 200, f"Expected 200, got {resp.status_code}: {resp.text}")
        data = resp.json()

        # Subject & Domain
        self.assertEqual(data["subject"], "Snyk")
        self.assertEqual(data["url"], "https://snyk.io/platform")

        # Graph Metrics
        self.assertGreater(data["node_count"], 10)
        self.assertGreater(data["edge_count"], 10)
        self.assertGreaterEqual(len(data["triples"]), 4)
        self.assertGreaterEqual(len(data["concepts"]), 4)

        # 100% Provenance Retention
        for t in data["triples"]:
            self.assertIsNotNone(t.get("evidence_sentence"), f"Missing evidence sentence on triple: {t}")
            self.assertGreater(len(t["evidence_sentence"]), 10)

        # W3C Serialization
        self.assertTrue("@prefix" in data["turtle"] or "PREFIX" in data["turtle"] or "<https://snyk.io" in data["turtle"])
        self.assertTrue(isinstance(data["json_ld"], (dict, list)))

        # Predicate Distribution
        preds = {t["predicate"] for t in data["triples"]}
        self.assertIn("integratesWith", preds)
        self.assertIn("hasAPI", preds)

    def test_page_kg_empty_payload_rejected(self):
        """POST /api/page-knowledge-graph without url or text must return 422 validation error."""
        resp = client.post("/api/page-knowledge-graph", json={"subject": "NoContent"})
        self.assertEqual(resp.status_code, 422)

    def test_page_kg_observability_sla(self):
        """POST /api/page-knowledge-graph with Datadog observability & SLA copy."""
        datadog_copy = (
            "Datadog automates log aggregation and provides real-time infrastructure monitoring for modern engineering. "
            "The platform integrates with Kubernetes and AWS. Datadog guarantees 99.99% uptime reliability. "
            "Available as a Multi-Tenant SaaS deployment model for global engineering teams."
        )
        payload = {
            "text": datadog_copy,
            "subject": "Datadog",
            "title": "Datadog Infrastructure Monitoring"
        }
        resp = client.post("/api/page-knowledge-graph", json=payload)
        self.assertEqual(resp.status_code, 200)
        data = resp.json()

        preds = {t["predicate"] for t in data["triples"]}
        self.assertIn("guarantees", preds)
        self.assertIn("integratesWith", preds)

    def test_mcp_tool_execution(self):
        """Verify ontoleap_build_page_knowledge_graph tool in mcp_server.py."""
        sample_copy = (
            "Drata automates continuous compliance monitoring and audit readiness for high-growth tech companies. "
            "Drata complies with SOC 2 Type II, HIPAA, and ISO 27001. Integrates with AWS, Google Cloud, and GitHub."
        )
        res_str = asyncio.run(ontoleap_build_page_knowledge_graph(
            text=sample_copy,
            subject="Drata",
            title="Drata Compliance Platform"
        ))
        res = json.loads(res_str)
        self.assertEqual(res["subject"], "Drata")
        self.assertGreater(res["node_count"], 5)
        self.assertGreater(res["triples_count"], 3)
        self.assertIn("turtle", res)
        self.assertIn("json_ld", res)


if __name__ == "__main__":
    unittest.main()
