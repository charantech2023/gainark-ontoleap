"""
Unit tests for the decoupled Core Graph Engine (graph_engine.py).
"""

import unittest
import asyncio
from graph_engine import get_graph_engine, GraphEngine


class TestGraphEngine(unittest.TestCase):

    def setUp(self):
        self.engine = get_graph_engine()

    def test_extract_from_text(self):
        sample_text = (
            "Acme Platform automates Revenue Recognition and complies with ASC 606 standards. "
            "It integrates with NetSuite ERP and Stripe payment gateway."
        )
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            result = loop.run_until_complete(
                self.engine.extract_knowledge_graph(sample_text, is_url=False)
            )
            self.assertIn("triples", result)
            self.assertIn("entities", result)
            self.assertGreater(len(result["triples"]), 0)
            predicates = [t["predicate"] for t in result["triples"]]
            self.assertTrue(any(p in ["automates", "compliesWith", "integratesWith"] for p in predicates))
        finally:
            loop.close()

    def test_diff_knowledge_graphs(self):
        marketing_copy = (
            "Acme Billing automates Invoicing and complies with ASC 606. "
            "Acme also claims automated Artificial Intelligence Machine Learning Blockchain Forecasting."
        )
        spec_json = """{
            "openapi": "3.0.0",
            "info": {"title": "Acme API", "version": "1.0"},
            "paths": {
                "/v1/invoices": {"post": {"summary": "Create automated invoice and recurring billing", "tags": ["invoicing"]}},
                "/v1/revenue": {"post": {"summary": "Compliance with ASC 606", "tags": ["revrec"]}}
            }
        }"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            diff = loop.run_until_complete(
                self.engine.diff_knowledge_graphs(
                    source_a=marketing_copy,
                    source_b=spec_json,
                    source_a_is_url=False,
                    source_b_is_url=False
                )
            )
            self.assertIn("grounded_facts", diff)
            self.assertIn("unbacked_claims", diff)
            self.assertIn("summary", diff)
            self.assertIsNotNone(diff["grounding_score"])
            # The blockchain / forecasting claim has 0 endpoints in spec_json, so unbacked claims must be detected
            self.assertGreater(len(diff["grounded_facts"]), 0)
        finally:
            loop.close()

    def test_analyze_site_topology_explicit_urls(self):
        urls = ["https://example.com"]
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            topo = loop.run_until_complete(
                self.engine.analyze_site_topology(urls=urls, max_pages=1)
            )
            self.assertEqual(topo["pages_analyzed"], 1)
            self.assertEqual(topo["root_domain"], "example.com")
            self.assertIn("topic_hubs", topo)
            self.assertIn("internal_link_opportunities", topo)
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
