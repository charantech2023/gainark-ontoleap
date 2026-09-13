"""
test_document_discovery.py — Automated Domain Document Discovery & Ingestion Tests
"""

import io
import unittest
from unittest.mock import patch, MagicMock
from reportlab.pdfgen import canvas

from document_graph import (
    classify_document,
    discover_domain_documents,
    fetch_document_bytes,
    ingest_remote_document
)


def _make_pdf_bytes(title: str = "Compliance Overview", body_text: str = "Acme provides SOC2 Type II compliance.") -> bytes:
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    c.drawString(100, 750, title)
    c.drawString(100, 700, body_text)
    c.save()
    return buffer.getvalue()


class TestDocumentClassification(unittest.TestCase):
    def test_security_classification(self):
        self.assertIn("Security & Compliance", classify_document("https://example.com/assets/soc2-type2-report.pdf"))
        self.assertIn("Security & Compliance", classify_document("https://example.com/download?file=iso27001_cert.pdf"))
        self.assertIn("Security & Compliance", classify_document("https://example.com/docs/security-whitepaper.pdf", "HIPAA Compliance Guide"))

    def test_architecture_classification(self):
        self.assertIn("Architecture & Specs", classify_document("https://example.com/datasheets/billing_engine_spec.pdf"))
        self.assertIn("Architecture & Specs", classify_document("https://example.com/assets/api-architecture.pdf"))

    def test_case_study_classification(self):
        self.assertIn("Case Studies", classify_document("https://example.com/customers/globex_case_study.pdf"))
        self.assertIn("Case Studies", classify_document("https://example.com/assets/roi-report.pdf", "Acme Customer Success Story"))

    def test_market_report_classification(self):
        self.assertIn("Market & Analyst Reports", classify_document("https://example.com/reports/forrester_wave_2025.pdf"))
        self.assertIn("Market & Analyst Reports", classify_document("https://example.com/docs/gartner-magic-quadrant.pdf"))

    def test_general_fallback(self):
        self.assertIn("General Document", classify_document("https://example.com/files/brochure.pdf", "Company Overview"))


class TestDomainDocumentDiscovery(unittest.TestCase):
    def test_sitemap_document_extraction(self):
        mock_sitemap_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
            <url><loc>https://acme.com/pages/home</loc></url>
            <url><loc>https://acme.com/assets/soc2-audit-2025.pdf</loc></url>
            <url><loc>https://acme.com/docs/api-specification.pdf</loc></url>
        </urlset>
        """

        with patch("document_graph.validate_url_for_fetch", return_value=None), \
             patch("document_graph.smart_fetch") as mock_fetch:
            
            def side_effect(url, **kwargs):
                if "sitemap.xml" in url:
                    return mock_sitemap_xml
                return ""

            mock_fetch.side_effect = side_effect
            docs = discover_domain_documents("https://acme.com", max_docs=10)

            urls = [d["url"] for d in docs]
            self.assertIn("https://acme.com/assets/soc2-audit-2025.pdf", urls)
            self.assertIn("https://acme.com/docs/api-specification.pdf", urls)
            
            soc2_doc = next(d for d in docs if "soc2" in d["url"])
            self.assertIn("Security & Compliance", soc2_doc["category"])

    def test_resource_hub_probing(self):
        mock_hub_html = """
        <html>
            <body>
                <h1>Resource Library</h1>
                <a href="/downloads/enterprise_datasheet.pdf">Download Architecture Datasheet</a>
                <a href="https://cdn.acme.com/reports/globex_case_study.pdf">Globex Case Study</a>
                <a href="/pricing">Pricing Plans</a>
            </body>
        </html>
        """

        with patch("document_graph.validate_url_for_fetch", return_value=None), \
             patch("document_graph.smart_fetch") as mock_fetch:
            
            def side_effect(url, **kwargs):
                if "/resources" in url:
                    return mock_hub_html
                return ""

            mock_fetch.side_effect = side_effect
            docs = discover_domain_documents("https://acme.com", max_docs=5)

            urls = [d["url"] for d in docs]
            self.assertIn("https://acme.com/downloads/enterprise_datasheet.pdf", urls)
            self.assertIn("https://cdn.acme.com/reports/globex_case_study.pdf", urls)

            ds_doc = next(d for d in docs if "datasheet" in d["url"])
            self.assertIn("Architecture & Specs", ds_doc["category"])
            self.assertIn("Architecture Datasheet", ds_doc["title"])


class TestFetchDocumentBytes(unittest.TestCase):
    def test_ssrf_protection_triggered(self):
        with self.assertRaises(ValueError):
            fetch_document_bytes("http://127.0.0.1/report.pdf")

        with self.assertRaises(ValueError):
            fetch_document_bytes("http://169.254.169.254/latest/meta-data")

    def test_oversized_document_rejected(self):
        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Length": "25000000"}  # 25 MB > 20 MB cap
        mock_resp.status_code = 200

        with patch("document_graph.validate_url_for_fetch", return_value=None), \
             patch("document_graph._resolve_redirects", return_value="https://acme.com/large.pdf"), \
             patch("requests.get", return_value=mock_resp):
            
            with self.assertRaises(ValueError):
                fetch_document_bytes("https://acme.com/large.pdf", max_bytes=20_000_000)

    def test_successful_binary_fetch(self):
        sample_bytes = b"%PDF-1.4 dummy pdf content"
        mock_resp = MagicMock()
        mock_resp.headers = {
            "Content-Length": str(len(sample_bytes)),
            "Content-Disposition": 'attachment; filename="acme_soc2.pdf"'
        }
        mock_resp.status_code = 200
        mock_resp.iter_content = MagicMock(return_value=[sample_bytes])

        with patch("document_graph.validate_url_for_fetch", return_value=None), \
             patch("document_graph._resolve_redirects", return_value="https://acme.com/acme_soc2.pdf"), \
             patch("requests.get", return_value=mock_resp):
            
            bytes_out, filename = fetch_document_bytes("https://acme.com/acme_soc2.pdf")
            self.assertEqual(bytes_out, sample_bytes)
            self.assertEqual(filename, "acme_soc2.pdf")


class TestRemoteDocumentIngestion(unittest.TestCase):
    def test_ingest_remote_document_end_to_end(self):
        pdf_content = _make_pdf_bytes(
            title="Acme Security Architecture",
            body_text="Acme provides SOC2 Type II compliance and integrates with Snowflake."
        )

        mock_resp = MagicMock()
        mock_resp.headers = {"Content-Length": str(len(pdf_content))}
        mock_resp.status_code = 200
        mock_resp.iter_content = MagicMock(return_value=[pdf_content])

        with patch("document_graph.validate_url_for_fetch", return_value=None), \
             patch("document_graph._resolve_redirects", return_value="https://acme.com/docs/security.pdf"), \
             patch("requests.get", return_value=mock_resp):

            kg = ingest_remote_document(
                url="https://acme.com/docs/security.pdf",
                vertical_id="b2b_saas_fintech",
                subject_brand="Acme"
            )

            self.assertEqual(kg.url, "https://acme.com/docs/security.pdf")
            self.assertTrue(kg.title != "")
            self.assertTrue(len(kg.nodes) > 0)
            self.assertTrue(any(n.source_urls == ["https://acme.com/docs/security.pdf"] for n in kg.nodes))
            self.assertTrue(kg.export_turtle != "")
            self.assertTrue(bool(kg.export_jsonld))


if __name__ == "__main__":
    unittest.main()
