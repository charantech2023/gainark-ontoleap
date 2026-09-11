"""
Unit tests for the Pure Knowledge Graph & Ontology MCP Server (mcp_server.py).
"""

import unittest
import asyncio
import json
from mcp_server import app


class TestMCPServer(unittest.TestCase):

    def test_tools_registered(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            tools = loop.run_until_complete(app.list_tools())
            tool_names = [t.name for t in tools]
            self.assertIn("ontoleap_build_page_kg", tool_names)
            self.assertIn("ontoleap_build_site_kg", tool_names)
            self.assertIn("ontoleap_align_industry_ontology", tool_names)
            self.assertIn("ontoleap_list_industry_ontologies", tool_names)
        finally:
            loop.close()

    def test_mcp_list_industries(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(
                app.call_tool("ontoleap_list_industry_ontologies", {})
            )
            self.assertFalse(res.is_error)
            data = json.loads(res.content[0].text)
            self.assertIn("industries", data)
            self.assertGreaterEqual(len(data["industries"]), 1)
        finally:
            loop.close()

    def test_mcp_build_page_kg(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            sample_html = """
            <html>
            <head><title>Test Billing Software</title></head>
            <body>
                <h1>Ordway Billing Platform</h1>
                <p>Ordway automates revenue recognition and complies with ASC 606 and SOC 2. Integrates with Salesforce.</p>
            </body>
            </html>
            """
            # Named explicitly because this covers extraction, not routing. Two sentences
            # carry too little vocabulary to classify, and the tool now says so rather
            # than quietly measuring against billing - see the next test.
            res = loop.run_until_complete(
                app.call_tool(
                    "ontoleap_build_page_kg",
                    {"source": sample_html, "url": "https://www.ordwaylabs.com",
                     "vertical_id": "b2b_saas_fintech"}
                )
            )
            self.assertFalse(res.is_error)
            data = json.loads(res.content[0].text)
            self.assertIn("nodes", data)
            self.assertIn("edges", data)
            self.assertGreater(len(data["nodes"]), 0)
            self.assertGreater(len(data["edges"]), 0)
            self.assertEqual(data["vertical_id"], "b2b_saas_fintech")
        finally:
            loop.close()

    def test_mcp_refuses_rather_than_assuming_a_vertical(self):
        """Too little evidence must be an error, not a quiet fallback to billing.

        Every MCP tool used to default vertical_id to b2b_saas_fintech. The vertical
        decides which entity labels are looked for and which concepts count as covered,
        so a wrong one returns a complete, plausible answer about the wrong industry -
        and an agent calling this has no prior with which to notice.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(
                app.call_tool(
                    "ontoleap_build_page_kg",
                    {"source": "<html><body><p>We make things.</p></body></html>",
                     "url": "https://example.com"}
                )
            )
            data = json.loads(res.content[0].text)
            self.assertEqual(data.get("status"), "failed", data)
            self.assertIn("vertical", data.get("message", "").lower())
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
