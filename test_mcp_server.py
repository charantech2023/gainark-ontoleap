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
            res = loop.run_until_complete(
                app.call_tool(
                    "ontoleap_build_page_kg",
                    {"source": sample_html, "url": "https://www.ordwaylabs.com"}
                )
            )
            self.assertFalse(res.is_error)
            data = json.loads(res.content[0].text)
            self.assertIn("nodes", data)
            self.assertIn("edges", data)
            self.assertGreater(len(data["nodes"]), 0)
            self.assertGreater(len(data["edges"]), 0)
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
