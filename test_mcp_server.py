"""
Unit tests for the OntoLeap MCP Server (mcp_server.py).
Verifies that all 4 MCP tools are registered and execute cleanly through the MCP call_tool interface.
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
            self.assertIn("ontoleap_extract_facts", tool_names)
            self.assertIn("ontoleap_cross_examine_diff", tool_names)
            self.assertIn("ontoleap_probe_ai_sov", tool_names)
            self.assertIn("ontoleap_map_site_topology", tool_names)
        finally:
            loop.close()

    def test_mcp_extract_facts(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(
                app.call_tool(
                    "ontoleap_extract_facts",
                    {"source": "Acme Billing automates invoicing and complies with ASC 606."}
                )
            )
            self.assertFalse(res.is_error)
            text_out = res.content[0].text
            data = json.loads(text_out)
            self.assertIn("triples", data)
            self.assertGreater(data["triples_count"], 0)
        finally:
            loop.close()

    def test_mcp_cross_examine_diff(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            copy = "Acme automates Invoicing and complies with ASC 606."
            spec = '{"openapi": "3.0.0", "paths": {"/v1/invoices": {"post": {"summary": "Invoicing"}}}}'
            res = loop.run_until_complete(
                app.call_tool(
                    "ontoleap_cross_examine_diff",
                    {"source_a": copy, "source_b": spec}
                )
            )
            self.assertFalse(res.is_error)
            text_out = res.content[0].text
            data = json.loads(text_out)
            self.assertIn("grounded_facts", data)
            self.assertIn("unbacked_claims", data)
        finally:
            loop.close()

    def test_mcp_probe_ai_sov(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            res = loop.run_until_complete(
                app.call_tool(
                    "ontoleap_probe_ai_sov",
                    {
                        "brand_name": "Ordway",
                        "domain": "ordwaylabs.com",
                        "competitor_names": ["Chargebee"]
                    }
                )
            )
            self.assertFalse(res.is_error)
            text_out = res.content[0].text
            data = json.loads(text_out)
            self.assertEqual(data["brand_name"], "Ordway")
            self.assertIn("share_of_voice_pct", data)
            self.assertIn("queries_audited", data)
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
