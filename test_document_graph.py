"""
test_document_graph.py — Unit Tests for Universal Document Knowledge Graph Extraction
Tests LlamaIndex document readers (Markdown, Text, PDF) and RDF Turtle serialization.
"""

import os
import tempfile
import unittest
from pathlib import Path

from reportlab.pdfgen import canvas

from document_graph import (
    extract_text_from_file,
    extract_document_knowledge_graph
)


class TestDocumentGraph(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dir_path = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_markdown_document_extraction(self):
        md_content = """# Ordway Labs — Enterprise Revenue Automation

Ordway automates revenue recognition and billing workflows compliant with ASC 606 and IFRS 15.
Our platform integrates with NetSuite, Salesforce, and QuickBooks to synchronize invoices in real time.
Ordway is certified with SOC 2 Type II compliance.
"""
        md_path = self.dir_path / "ordway_overview.md"
        md_path.write_text(md_content, encoding="utf-8")

        # 1. Test text extraction
        text, title = extract_text_from_file(md_path)
        self.assertIn("Ordway automates revenue recognition", text)
        self.assertIn("Ordway Labs", title)

        # 2. Test full knowledge graph extraction
        kg = extract_document_knowledge_graph(
            file_path=md_path,
            vertical_id="b2b_saas_fintech",
            subject_brand="Ordway"
        )

        self.assertIsNotNone(kg)
        self.assertEqual(kg.title, title)
        self.assertTrue(len(kg.nodes) > 0)
        self.assertTrue(len(kg.edges) > 0)

        # Check for expected entity nodes
        canonical_names = [n.canonical_name.lower() for n in kg.nodes]
        self.assertTrue(any("ordway" in name for name in canonical_names))

        # Check for expected relational predicates (automates, compliesWith, or integratesWith)
        predicates = [e.predicate for e in kg.edges]
        self.assertTrue(any(p in ["automates", "compliesWith", "integratesWith"] for p in predicates))

        # Check W3C Turtle and JSON-LD exports exist
        self.assertIn("@prefix", kg.export_turtle)
        self.assertEqual(kg.export_jsonld.get("@context"), "https://schema.org")
        self.assertTrue(len(kg.export_jsonld.get("@graph", [])) > 0)

    def test_pdf_document_extraction(self):
        # Generate a real minimal PDF with reportlab
        pdf_path = self.dir_path / "compliance_report.pdf"
        c = canvas.Canvas(str(pdf_path))
        c.drawString(100, 750, "Ordway Security and Compliance Whitepaper")
        c.drawString(100, 720, "Ordway maintains strict SOC 2 Type II compliance.")
        c.drawString(100, 690, "The platform complies with HIPAA and GDPR regulations.")
        c.drawString(100, 660, "Ordway automates billing and revenue recognition schedules.")
        c.save()

        # 1. Test PDF text extraction
        text, title = extract_text_from_file(pdf_path)
        self.assertIn("SOC 2 Type II", text)
        self.assertIn("HIPAA", text)

        # 2. Test Knowledge Graph extraction from PDF
        kg = extract_document_knowledge_graph(
            file_path=pdf_path,
            vertical_id="b2b_saas_fintech",
            subject_brand="Ordway"
        )

        self.assertIsNotNone(kg)
        self.assertTrue(len(kg.nodes) > 0)
        node_names = [n.canonical_name.lower() for n in kg.nodes]
        self.assertTrue(any("ordway" in name for name in node_names))

    def test_in_memory_file_bytes_extraction(self):
        sample_text = b"""# Cloud Security Platform
Snyk automates vulnerability scanning across developer repositories.
Snyk integrates with GitHub and GitLab.
Snyk complies with SOC 2 standards.
"""
        kg = extract_document_knowledge_graph(
            file_bytes=sample_text,
            filename="snyk_security.txt",
            vertical_id="cybersecurity",
            subject_brand="Snyk"
        )

        self.assertIsNotNone(kg)
        self.assertEqual(kg.vertical_id, "cybersecurity")
        self.assertTrue(len(kg.nodes) > 0)
        self.assertTrue(len(kg.edges) > 0)
        self.assertIn("Snyk", [n.canonical_name for n in kg.nodes])

    def test_insufficient_text_raises_error(self):
        tiny_path = self.dir_path / "empty.txt"
        tiny_path.write_text("tiny", encoding="utf-8")

        with self.assertRaises(ValueError):
            extract_document_knowledge_graph(file_path=tiny_path)


if __name__ == "__main__":
    unittest.main()
