"""
Unit & integration test suite for W3C SHACL Governance Validation in OntoLeap.
Verifies shapes compliance, violation reporting, and API endpoints.
"""

import unittest
from rdflib import Graph, Literal, RDF, URIRef, Namespace
from rdflib.namespace import PROV, SKOS

from models import SemanticTriple, ProductTruthMatrixResponse, VerticalConfig
from product_truth import export_product_truth_to_prov_ttl
from shacl_validator import validate_rdf_graph_shacl
from fastapi.testclient import TestClient
from api import app

client = TestClient(app)


class TestShaclGovernance(unittest.TestCase):

    def setUp(self):
        self.vertical_config = VerticalConfig(
            vertical_id="b2b_saas_fintech",
            display_name="B2B SaaS & Financial Software",
            gliner_labels=["Billing Feature"],
            mandatory_schema_types=["SoftwareApplication"],
            core_seed_concepts=["Revenue Recognition"],
            concept_hierarchy={"ASC 606": "Revenue Recognition"}
        )

    def test_compliant_graph_passes_shacl(self):
        """A valid OntoLeap Product Truth graph with PROV-O and SKOS must pass SHACL."""
        matrix = ProductTruthMatrixResponse(
            brand_name="CompliantBrand",
            marketing_url="https://compliant.com",
            tech_docs_url="https://docs.compliant.com/openapi.json",
            marketing_grounding_index=100.0,
            total_marketing_claims=1,
            total_technical_capabilities=1,
            verified_claims_count=1,
            unbacked_claims_count=0,
            hidden_capabilities_count=0,
            verified_triples=[
                SemanticTriple(
                    subject="CompliantBrand",
                    predicate="automates",
                    object="Subscription Billing",
                    evidence_sentence="Automated subscription billing workflows",
                    source_type="verified_truth",
                    provenance="https://compliant.com ⟷ https://docs.compliant.com/v1/billing"
                )
            ],
            executive_summary="Compliant summary"
        )
        ttl = export_product_truth_to_prov_ttl(matrix, self.vertical_config)
        report = validate_rdf_graph_shacl(ttl)

        self.assertTrue(report.conforms, f"Compliant graph should pass SHACL. Errors: {report.report_text}")
        self.assertEqual(report.violations_count, 0)
        self.assertGreater(report.evaluated_triples_count, 10)

    def test_missing_source_fails_shacl(self):
        """A claim lacking both primary source and derivation must trigger ClaimProvenanceShape."""
        bad_ttl = """
        @prefix prov: <http://www.w3.org/ns/prov#> .
        @prefix onto: <https://gainark.com/ontology/> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

        <https://bad.com/entity/PhantomClaim> a prov:Entity ;
            rdfs:label "Phantom Claim" ;
            onto:groundingStatus "MARKETING_DRIFT_FLUFF" .
        """
        report = validate_rdf_graph_shacl(bad_ttl)
        self.assertFalse(report.conforms)
        self.assertGreaterEqual(report.violations_count, 1)
        self.assertTrue(any("ClaimProvenanceShape" in (v.source_shape or "") or "hadPrimarySource" in v.message for v in report.violations))

    def test_verified_truth_with_single_derivation_fails_shacl(self):
        """A claim marked VERIFIED_TRUTH with only 1 derivation must trigger VerifiedTruthDerivationShape."""
        bad_ttl = """
        @prefix prov: <http://www.w3.org/ns/prov#> .
        @prefix onto: <https://gainark.com/ontology/> .
        @prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .

        <https://bad.com/entity/SingleSourceClaim> a prov:Entity ;
            rdfs:label "Single Source Claim" ;
            onto:groundingStatus "VERIFIED_TRUTH" ;
            prov:wasDerivedFrom <https://bad.com/source/marketing_only> .
        """
        report = validate_rdf_graph_shacl(bad_ttl)
        self.assertFalse(report.conforms)
        self.assertGreaterEqual(report.violations_count, 1)
        self.assertTrue(any("VERIFIED_TRUTH" in v.message or "VerifiedTruthDerivationShape" in (v.source_shape or "") for v in report.violations))

    def test_skos_concept_missing_preflabel_fails_shacl(self):
        """A skos:Concept missing a prefLabel must trigger SKOSConceptShape."""
        bad_ttl = """
        @prefix skos: <http://www.w3.org/2004/02/skos/core#> .

        <https://bad.com/concept/NoLabelConcept> a skos:Concept ;
            skos:inScheme <https://bad.com/taxonomy/fintech> .
        """
        report = validate_rdf_graph_shacl(bad_ttl)
        self.assertFalse(report.conforms)
        self.assertGreaterEqual(report.violations_count, 1)
        self.assertTrue(any("skos:prefLabel" in v.message or "SKOSConceptShape" in (v.source_shape or "") for v in report.violations))

    def test_api_validate_kg_shacl_endpoint(self):
        """Test POST /api/validate-kg-shacl endpoint via TestClient."""
        matrix = ProductTruthMatrixResponse(
            brand_name="APITestBrand",
            marketing_url="https://apitest.com",
            tech_docs_url="https://docs.apitest.com/spec.json",
            marketing_grounding_index=100.0,
            total_marketing_claims=1,
            total_technical_capabilities=1,
            verified_claims_count=1,
            unbacked_claims_count=0,
            hidden_capabilities_count=0,
            verified_triples=[
                SemanticTriple(
                    subject="APITestBrand",
                    predicate="automates",
                    object="Invoicing",
                    evidence_sentence="Automates recurring invoicing",
                    source_type="verified_truth",
                    provenance="https://apitest.com ⟷ https://docs.apitest.com/invoices"
                )
            ],
            executive_summary="API summary"
        )
        ttl = export_product_truth_to_prov_ttl(matrix, self.vertical_config)

        resp = client.post("/api/validate-kg-shacl", json={"rdf_turtle": ttl})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["conforms"])
        self.assertEqual(data["violations_count"], 0)
        self.assertEqual(data["status"], "success")


if __name__ == "__main__":
    unittest.main()
