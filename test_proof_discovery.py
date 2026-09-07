"""
Unit & Integration Test Suite: Autonomous Technical Proof Discovery (Pillars 1-4)
"""

import unittest
from unittest.mock import MagicMock, patch

from models import ProductTruthRequest, SemanticTriple
from proof_discovery import (
    discover_public_changelog,
    discover_public_sdks,
    discover_trust_center,
    orchestrate_autonomous_proof_discovery,
    probe_common_specs,
)


class TestProofDiscovery(unittest.TestCase):

    def test_trust_center_compliance_extraction(self):
        sample_trust_html = """
        <html>
        <head><title>Acme Trust & Security Portal</title></head>
        <body>
            <h1>Enterprise Security & Compliance</h1>
            <p>Acme is certified for SOC 2 Type II and ISO 27001:2022.</p>
            <p>We adhere to HIPAA compliance for healthcare data and PCI-DSS Level 1.</p>
            <p>Our infrastructure enforces AES-256 encryption at rest, TLS 1.3 in transit, and SAML 2.0 SSO.</p>
            <p>We require Multi-Factor Authentication (MFA) and enforce Role-Based Access Control (RBAC).</p>
        </body>
        </html>
        """
        with patch("proof_discovery._host_resolves", return_value=True), \
             patch("proof_discovery.smart_fetch", return_value=sample_trust_html):
            triples, meta = discover_trust_center("https://acme.com", "Acme")

            self.assertIsNotNone(meta)
            self.assertEqual(meta["status"], "verified")
            self.assertIn("SOC 2 Type II", meta["standards"])
            self.assertIn("ISO 27001", meta["standards"])
            self.assertIn("HIPAA", meta["standards"])
            self.assertIn("PCI-DSS Level 1", meta["standards"])
            self.assertIn("AES-256 Encryption", meta["practices"])
            self.assertIn("SAML 2.0 / SSO", meta["practices"])

            predicates = [t.predicate for t in triples]
            objects = [t.object for t in triples]
            self.assertIn("compliesWith", predicates)
            self.assertIn("supportsSecurityPractice", predicates)
            self.assertIn("SOC 2 Type II", objects)
            self.assertIn("SAML 2.0 / SSO", objects)

            # Check PROV-O attribution
            for t in triples:
                self.assertTrue(t.provenance.startswith("trust_center:"))

    def test_public_sdk_discovery(self):
        pypi_mock_response = {
            "info": {
                "name": "acme-sdk",
                "version": "2.4.0",
                "summary": "Official Acme Python SDK for automated billing and invoicing",
                "home_page": "https://acme.com",
                "keywords": ["acme", "billing", "webhook", "asyncio"],
            }
        }
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = pypi_mock_response

        with patch("requests.get", return_value=mock_resp):
            triples, sources = discover_public_sdks("Acme", "https://acme.com")

            self.assertTrue(len(triples) >= 1)
            objects = [t.object for t in triples]
            self.assertIn("Python SDK", objects)
            self.assertTrue(any(s["registry"] == "PyPI" for s in sources))

    def test_public_changelog_discovery(self):
        sample_changelog_html = """
        <html>
        <body>
            <h1>Product Changelog</h1>
            <h2>Added Bi-directional Salesforce Integration</h2>
            <p>Sync contracts and revenue schedules automatically.</p>
            <h2>Launched Oracle NetSuite ERP Sync</h2>
            <p>Real-time general ledger synchronization.</p>
        </body>
        </html>
        """
        mock_pipeline = MagicMock()
        mock_pipeline.config.known_integrations = ["Salesforce", "NetSuite"]

        with patch("proof_discovery._host_resolves", return_value=True), \
             patch("proof_discovery.smart_fetch", return_value=sample_changelog_html):
            triples, meta = discover_public_changelog("https://acme.com", "Acme", pipeline=mock_pipeline)

            self.assertIsNotNone(meta)
            self.assertEqual(meta["source_type"], "changelog")
            objects = [t.object for t in triples]
            self.assertIn("Salesforce", objects)
            self.assertIn("NetSuite", objects)

    def test_orchestrator_concurrency_and_deduplication(self):
        sample_trust_html = """
        <html><body><p>Certified SOC 2 Type II and HIPAA compliant.</p></body></html>
        """
        with patch("proof_discovery.discover_trust_center") as mock_trust, \
             patch("proof_discovery.discover_public_sdks") as mock_sdk, \
             patch("proof_discovery.discover_public_changelog") as mock_change, \
             patch("proof_discovery.probe_common_specs") as mock_spec:

            mock_trust.return_value = (
                [SemanticTriple(subject="Acme", predicate="compliesWith", object="SOC 2 Type II", provenance="trust_center:test")],
                {"source_type": "trust_center", "url": "https://trust.acme.com"}
            )
            mock_sdk.return_value = (
                [SemanticTriple(subject="Acme", predicate="providesSdk", object="Python SDK", provenance="pypi:acme")],
                [{"source_type": "public_sdk", "registry": "PyPI"}]
            )
            mock_change.return_value = (
                [SemanticTriple(subject="Acme", predicate="integratesWith", object="NetSuite", provenance="changelog:test")],
                {"source_type": "changelog", "url": "https://acme.com/changelog"}
            )
            mock_spec.return_value = ([], None)

            triples, proofs = orchestrate_autonomous_proof_discovery("https://acme.com", "Acme", time_budget=5.0)

            self.assertEqual(len(triples), 3)
            self.assertEqual(len(proofs), 3)
            predicates = {t.predicate for t in triples}
            self.assertEqual(predicates, {"compliesWith", "providesSdk", "integratesWith"})

    def test_audit_end_to_end_proof_augmentation(self):
        from product_truth import execute_product_truth_audit

        req = ProductTruthRequest(
            marketing_url="https://acme.com",
            brand_name="Acme",
        )

        m_triples = [
            SemanticTriple(subject="Acme", predicate="compliesWith", object="SOC 2 Type II", confidence=0.9),
            SemanticTriple(subject="Acme", predicate="providesSdk", object="Python SDK", confidence=0.85),
            SemanticTriple(subject="Acme", predicate="integratesWith", object="Salesforce", confidence=0.8),
        ]
        mock_pipeline = MagicMock()
        mock_pipeline.process.return_value = MagicMock(triples=m_triples)
        mock_pipeline.config = MagicMock()
        mock_pipeline.config.vertical_id = "saas"
        mock_pipeline.config.known_integrations = ["Salesforce"]

        discovered_triples = [
            SemanticTriple(subject="Acme", predicate="compliesWith", object="SOC 2 Type II", provenance="trust_center:https://trust.acme.com"),
            SemanticTriple(subject="Acme", predicate="compliesWith", object="ISO 27001", provenance="trust_center:https://trust.acme.com"),
            SemanticTriple(subject="Acme", predicate="supportsSecurityPractice", object="AES-256 Encryption", provenance="trust_center:https://trust.acme.com"),
            SemanticTriple(subject="Acme", predicate="providesSdk", object="Python SDK", provenance="pypi:acme-sdk"),
            SemanticTriple(subject="Acme", predicate="integratesWith", object="Salesforce", provenance="changelog:https://acme.com/changelog"),
        ]
        discovered_proofs = [
            {"source_type": "trust_center", "url": "https://trust.acme.com", "standards": ["SOC 2 Type II", "ISO 27001"]},
            {"source_type": "public_sdk", "registry": "PyPI", "package": "acme-sdk"},
            {"source_type": "changelog", "url": "https://acme.com/changelog"}
        ]

        with patch("product_truth.discover_docs_url", return_value=None), \
             patch("truth_ledger.checks.run_executable_checks", return_value=[]), \
             patch("proof_discovery.orchestrate_autonomous_proof_discovery", return_value=(discovered_triples, discovered_proofs)):

            matrix = execute_product_truth_audit(req, mock_pipeline)

            self.assertNotEqual(matrix.evidence_status, "inconclusive")
            self.assertEqual(matrix.evidence_status, "conclusive")
            self.assertIsNotNone(matrix.marketing_grounding_index)
            self.assertGreater(matrix.marketing_grounding_index, 0.0)

            self.assertEqual(len(matrix.proof_sources), 3)
            self.assertEqual(matrix.proof_sources[0]["source_type"], "trust_center")

            verified_objects = [c.object for c in matrix.verified_triples]
            self.assertIn("SOC 2 Type II", verified_objects)
            self.assertIn("Python SDK", verified_objects)


if __name__ == "__main__":
    unittest.main()

