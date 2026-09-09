"""
Unit Test Suite: Extended Ontological Predicates
Validates extraction, RDF Schema.org mappings, and OWL 2 DL inverse axioms
for the 10 extended predicates:
- hasFeature
- replacesWorkflow
- targetsSegment
- servesIndustry
- deployedAs
- certifiedBy
- hasAPI
- supportsLocale
- guarantees
- competesAgainst
"""

import unittest
from rdflib import RDF, OWL, Namespace, URIRef
from models import SemanticTriple, EntityMatch
from pipeline import OntologyPipeline
from knowledge_graph import (
    build_rdf_graph,
    build_owl_ontology,
    export_to_rdf_turtle,
    export_to_owl_xml
)

SCHEMA = Namespace("https://schema.org/")


class TestExtendedPredicates(unittest.TestCase):

    def setUp(self):
        self.pipeline = OntologyPipeline()

    def test_pipeline_rule_extraction_all_extended_predicates(self):
        """Verify that OntologyPipeline extracts all 10 extended predicates from natural language text."""
        test_text = (
            "Acme Platform offers Automated Invoicing and Subscription Management capabilities. "
            "It eliminates manual spreadsheets and replaces manual journal entries. "
            "Built for Enterprise and High-Growth SaaS finance teams. "
            "Leading solution for SaaS and FinTech companies. "
            "Our Cloud-Native and Multi-Tenant SaaS platform is SOC 2 Type II certified and ISO 27001 compliant. "
            "Provides a developer-first REST API and Webhooks for integration. "
            "Available in the United States and European Union with multi-currency support. "
            "Backed by a 99.99% Uptime guarantee with 24/7 Support. "
            "Migrate from Zuora and Chargebee seamlessly."
        )

        triples = self.pipeline.extract_semantic_triples(
            text=test_text,
            subject="Acme",
            entities=[],
            seed_concepts=[]
        )

        predicates = {t.predicate for t in triples}
        expected_predicates = [
            "hasFeature",
            "replacesWorkflow",
            "targetsSegment",
            "servesIndustry",
            "deployedAs",
            "certifiedBy",
            "hasAPI",
            "supportsLocale",
            "guarantees",
            "competesAgainst"
        ]

        for pred in expected_predicates:
            self.assertIn(pred, predicates, f"Expected predicate '{pred}' to be extracted by pipeline")

    def test_pipeline_entity_label_mapping(self):
        """Verify that named entities with new labels produce corresponding semantic triples."""
        mock_entities = [
            EntityMatch(text="Automated Invoicing", label="Product Feature", score=0.95, start=0, end=19),
            EntityMatch(text="Mid-Market", label="Customer Segment", score=0.92, start=0, end=10),
            EntityMatch(text="HealthTech", label="Industry Vertical", score=0.90, start=0, end=10),
            EntityMatch(text="Cloud-Native", label="Deployment Model", score=0.88, start=0, end=12),
            EntityMatch(text="ISO 27001", label="Trust Certification", score=0.96, start=0, end=9),
            EntityMatch(text="GraphQL API", label="API Standard", score=0.89, start=0, end=11),
            EntityMatch(text="United Kingdom", label="Geographic Market", score=0.85, start=0, end=14),
            EntityMatch(text="99.9% Uptime", label="SLA Commitment", score=0.91, start=0, end=12),
            EntityMatch(text="Recurly", label="Competitor", score=0.87, start=0, end=7)
        ]

        triples = self.pipeline.extract_semantic_triples(
            text="Platform overview.",
            subject="Acme",
            entities=mock_entities,
            seed_concepts=[]
        )

        extracted_pairs = {(t.predicate, t.object) for t in triples}
        # "Automated Invoicing" is an alt_label of the canonical process "Invoicing",
        # so the entity normalises to that canonical - and the predicate follows the
        # canonical's bucket rather than the NER label, making this `automates` rather
        # than `hasFeature`. Emitting the raw span would put the marketing spelling and
        # the documentation spelling into the graph as two unrelated concepts.
        self.assertIn(("automates", "Invoicing"), extracted_pairs)
        self.assertIn(("targetsSegment", "Mid-Market"), extracted_pairs)
        self.assertIn(("servesIndustry", "HealthTech"), extracted_pairs)
        self.assertIn(("deployedAs", "Cloud-Native"), extracted_pairs)
        self.assertIn(("certifiedBy", "ISO 27001"), extracted_pairs)
        self.assertIn(("hasAPI", "GraphQL API"), extracted_pairs)
        self.assertIn(("supportsLocale", "United Kingdom"), extracted_pairs)
        self.assertIn(("guarantees", "99.9% Uptime"), extracted_pairs)
        self.assertIn(("competesAgainst", "Recurly"), extracted_pairs)

    def test_build_rdf_graph_extended_predicates(self):
        """Verify build_rdf_graph maps extended predicates to Schema.org properties."""
        triples = [
            SemanticTriple(subject="Acme", predicate="hasFeature", object="Real-Time Analytics", confidence=0.9),
            SemanticTriple(subject="Acme", predicate="targetsSegment", object="Enterprise", confidence=0.9),
            SemanticTriple(subject="Acme", predicate="servesIndustry", object="FinTech", confidence=0.9),
            SemanticTriple(subject="Acme", predicate="certifiedBy", object="SOC 2 Type II", confidence=0.95),
            SemanticTriple(subject="Acme", predicate="hasAPI", object="REST API", confidence=0.9),
            SemanticTriple(subject="Acme", predicate="competesAgainst", object="Chargebee", confidence=0.85)
        ]

        g = build_rdf_graph("acme.com", triples, hubs={}, entities=[])
        root_uri = URIRef("https://acme.com/#Acme")

        # Verify Schema.org predicates
        features = list(g.objects(root_uri, SCHEMA.featureList))
        self.assertEqual(len(features), 1)

        audiences = list(g.objects(root_uri, SCHEMA.audience))
        self.assertEqual(len(audiences), 1)

        industries = list(g.objects(root_uri, SCHEMA.industry))
        self.assertEqual(len(industries), 1)

        awards = list(g.objects(root_uri, SCHEMA.award))
        self.assertEqual(len(awards), 1)

        apis = list(g.objects(root_uri, SCHEMA.interface))
        self.assertEqual(len(apis), 1)

        competitors = list(g.objects(root_uri, SCHEMA.isSimilarTo))
        self.assertEqual(len(competitors), 1)

        # Test Turtle export
        ttl_str = export_to_rdf_turtle("acme.com", triples, hubs={}, entities=[])
        self.assertIn("featureList", ttl_str)
        self.assertIn("isSimilarTo", ttl_str)

    def test_build_owl_ontology_extended_axioms(self):
        """Verify build_owl_ontology produces OWL 2 DL inverse axioms and typed instances for all extended predicates."""
        triples = [
            SemanticTriple(subject="Acme", predicate="hasFeature", object="Contract Management", confidence=0.9),
            SemanticTriple(subject="Acme", predicate="replacesWorkflow", object="Manual Spreadsheets", confidence=0.9),
            SemanticTriple(subject="Acme", predicate="targetsSegment", object="Mid-Market", confidence=0.9),
            SemanticTriple(subject="Acme", predicate="servesIndustry", object="SaaS", confidence=0.9),
            SemanticTriple(subject="Acme", predicate="deployedAs", object="Cloud-Native", confidence=0.9),
            SemanticTriple(subject="Acme", predicate="certifiedBy", object="ISO 27001", confidence=0.95),
            SemanticTriple(subject="Acme", predicate="hasAPI", object="Webhooks", confidence=0.9),
            SemanticTriple(subject="Acme", predicate="supportsLocale", object="European Union", confidence=0.9),
            SemanticTriple(subject="Acme", predicate="guarantees", object="99.99% Uptime", confidence=0.9),
            SemanticTriple(subject="Acme", predicate="competesAgainst", object="Zuora", confidence=0.85)
        ]

        g = build_owl_ontology("acme.com", triples, hubs={}, entities=[])
        ONTO = Namespace("https://acme.com/ontology#")

        # Verify inverse properties
        expected_inverses = [
            (ONTO.hasFeature, ONTO.isFeatureOf),
            (ONTO.replacesWorkflow, ONTO.isReplacedBy),
            (ONTO.targetsSegment, ONTO.isTargetedBy),
            (ONTO.servesIndustry, ONTO.isServedBy),
            (ONTO.deployedAs, ONTO.isDeploymentModelOf),
            (ONTO.certifiedBy, ONTO.certifies),
            (ONTO.hasAPI, ONTO.isAPIOf),
            (ONTO.supportsLocale, ONTO.isLocaleSupportedBy),
            (ONTO.guarantees, ONTO.isGuaranteedBy),
            (ONTO.competesAgainst, ONTO.competesWith)
        ]

        for fwd, inv in expected_inverses:
            fwd_inv_objs = list(g.objects(fwd, OWL.inverseOf))
            self.assertIn(inv, fwd_inv_objs, f"{fwd} must have owl:inverseOf {inv}")

            rev_inv_objs = list(g.objects(inv, OWL.inverseOf))
            self.assertIn(fwd, rev_inv_objs, f"{inv} must have owl:inverseOf {fwd}")

        # Verify instance typing
        self.assertIn(ONTO.ProductFeature, list(g.objects(ONTO.ContractManagement, RDF.type)))
        self.assertIn(ONTO.LegacyWorkflow, list(g.objects(ONTO.ManualSpreadsheets, RDF.type)))
        self.assertIn(ONTO.CustomerSegment, list(g.objects(ONTO.MidMarket, RDF.type)))
        self.assertIn(ONTO.IndustryVertical, list(g.objects(ONTO.SaaS, RDF.type)))
        self.assertIn(ONTO.DeploymentModel, list(g.objects(ONTO.CloudNative, RDF.type)))
        self.assertIn(ONTO.TrustCertification, list(g.objects(ONTO.ISO27001, RDF.type)))
        self.assertIn(ONTO.APIStandard, list(g.objects(ONTO.Webhooks, RDF.type)))
        self.assertIn(ONTO.GeographicMarket, list(g.objects(ONTO.EuropeanUnion, RDF.type)))
        self.assertIn(ONTO.SLAGuarantee, list(g.objects(ONTO["9999Uptime"], RDF.type)))
        self.assertIn(ONTO.CompetitorEntity, list(g.objects(ONTO.Zuora, RDF.type)))

        # Verify OWL XML export
        owl_xml = export_to_owl_xml("acme.com", triples, hubs={}, entities=[])
        self.assertIn("owl#Ontology", owl_xml)
        self.assertIn("hasFeature", owl_xml)
        self.assertIn("isFeatureOf", owl_xml)


if __name__ == "__main__":
    unittest.main()
