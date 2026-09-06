"""
Test Suite: Enterprise Ontology Standards (Dublin Core, DCAT & OWL 2 DL Axioms)
Validates that knowledge graph outputs and OWL ontologies conform to:
1. DCMI Dublin Core Terms (dcterms:title, dcterms:creator, dcterms:created, dcterms:modified, dcterms:license)
2. W3C DCAT (dcat:Dataset) dataset cataloging
3. W3C OWL 2 DL axioms (owl:inverseOf, owl:disjointWith, owl:TransitiveProperty)
4. W3C SHACL compliance including DCATDatasetShape
"""

import unittest
from rdflib import Graph, URIRef, Literal, RDF, RDFS, OWL, Namespace
from models import SemanticTriple, ProductTruthMatrixResponse, VerticalConfig
from linking import build_rdf_graph, export_to_rdf_turtle, build_owl_ontology, export_to_owl_xml
from product_truth import export_product_truth_to_prov_ttl
from shacl_validator import validate_rdf_graph_shacl


DCTERMS = Namespace("http://purl.org/dc/terms/")
DCAT = Namespace("http://www.w3.org/ns/dcat#")
PROV = Namespace("http://www.w3.org/ns/prov#")
SKOS = Namespace("http://www.w3.org/2004/02/skos/core#")


class TestEnterpriseOntologyStandards(unittest.TestCase):

    def test_build_rdf_graph_has_dcat_and_dublin_core(self):
        """Verify build_rdf_graph produces standard dcat:Dataset and Dublin Core metadata."""
        triples = [
            SemanticTriple(
                subject="Chargebee",
                predicate="automates",
                object="Subscription Billing",
                confidence=0.95,
                evidence_sentence="Automates recurring subscription billing.",
                provenance="https://chargebee.com/features"
            )
        ]
        g = build_rdf_graph("chargebee.com", triples, hubs={}, entities=["Subscription Billing"])
        
        # Check dcat:Dataset exists
        datasets = list(g.subjects(RDF.type, DCAT.Dataset))
        self.assertEqual(len(datasets), 1, "Expected exactly 1 dcat:Dataset node in RDF graph")
        ds_uri = datasets[0]
        
        # Check Dublin Core properties
        titles = list(g.objects(ds_uri, DCTERMS.title))
        self.assertEqual(len(titles), 1)
        self.assertIn("Enterprise Knowledge Graph", str(titles[0]))
        
        creators = list(g.objects(ds_uri, DCTERMS.creator))
        self.assertEqual(len(creators), 1)
        self.assertTrue(str(creators[0]).endswith("#ontoleap-agent"))
        
        created = list(g.objects(ds_uri, DCTERMS.created))
        self.assertEqual(len(created), 1)
        
        modified = list(g.objects(ds_uri, DCTERMS.modified))
        self.assertEqual(len(modified), 1)
        
        licenses = list(g.objects(ds_uri, DCTERMS.license))
        self.assertEqual(len(licenses), 1)
        self.assertIn("creativecommons.org", str(licenses[0]))

    def test_build_owl_ontology_has_dublin_core_and_rich_axioms(self):
        """Verify build_owl_ontology produces Dublin Core metadata, inverse properties, and disjoint classes."""
        triples = [
            SemanticTriple(
                subject="Stripe",
                predicate="automates",
                object="Revenue Recognition",
                confidence=0.98,
                evidence_sentence="Automated revenue recognition for ASC 606.",
                provenance="https://stripe.com/rev-rec"
            )
        ]
        g = build_owl_ontology("stripe.com", triples, hubs={}, entities=["Revenue Recognition"])
        
        ONTO = Namespace("https://stripe.com/ontology#")
        onto_uri = URIRef("https://stripe.com/ontology")
        
        # 1. Dublin Core on Ontology
        titles = list(g.objects(onto_uri, DCTERMS.title))
        self.assertEqual(len(titles), 1)
        self.assertIn("Enterprise Domain Ontology", str(titles[0]))
        
        creators = list(g.objects(onto_uri, DCTERMS.creator))
        self.assertEqual(len(creators), 1)
        self.assertIn("OntoLeap Engine", str(creators[0]))
        
        # 2. OWL 2 owl:inverseOf axioms
        forward_inv = list(g.objects(ONTO.automatesWorkflow, OWL.inverseOf))
        self.assertIn(ONTO.isAutomatedBy, forward_inv, "automatesWorkflow must have inverse isAutomatedBy")
        
        reverse_inv = list(g.objects(ONTO.isAutomatedBy, OWL.inverseOf))
        self.assertIn(ONTO.automatesWorkflow, reverse_inv, "isAutomatedBy must have inverse automatesWorkflow")
        
        # compliesWithStandard <-> isCompliedWithBy
        self.assertIn(ONTO.isCompliedWithBy, list(g.objects(ONTO.compliesWithStandard, OWL.inverseOf)))
        
        # 3. OWL 2 owl:disjointWith axioms
        disjoints = list(g.objects(ONTO.EnterprisePlatform, OWL.disjointWith))
        self.assertIn(ONTO.PlatformCapability, disjoints, "EnterprisePlatform must be disjoint with PlatformCapability")
        self.assertIn(ONTO.ComplianceStandard, disjoints, "EnterprisePlatform must be disjoint with ComplianceStandard")
        self.assertIn(ONTO.PricingModel, disjoints, "EnterprisePlatform must be disjoint with PricingModel")
        
        # 4. owl:TransitiveProperty on subCategoryOf
        transitives = list(g.subjects(RDF.type, OWL.TransitiveProperty))
        self.assertIn(ONTO.subCategoryOf, transitives, "subCategoryOf must be an owl:TransitiveProperty")
        
        # 5. Verify serialization to OWL/XML
        owl_xml = export_to_owl_xml("stripe.com", triples, hubs={}, entities=["Revenue Recognition"])
        self.assertTrue("owl:inverseOf" in owl_xml or "inverseOf" in owl_xml)
        self.assertTrue("owl:disjointWith" in owl_xml or "disjointWith" in owl_xml)
        self.assertIn("TransitiveProperty", owl_xml)

    def test_product_truth_has_dcat_cataloging(self):
        """Verify export_product_truth_to_prov_ttl includes DCAT dataset and Dublin Core terms."""
        matrix = ProductTruthMatrixResponse(
            brand_name="Acme",
            marketing_url="https://acme.com",
            tech_docs_url="https://acme.com/docs",
            marketing_grounding_index=100.0,
            evidence_status="conclusive",
            total_marketing_claims=1,
            total_technical_capabilities=1,
            verified_claims_count=1,
            unbacked_claims_count=0,
            hidden_capabilities_count=0,
            executive_summary="Acme executive audit summary.",
            verified_triples=[
                SemanticTriple(
                    subject="Acme",
                    predicate="automates",
                    object="Batch Invoicing",
                    confidence=0.99,
                    evidence_sentence="[Marketing]: Fast invoicing | [Tech Reality]: POST /invoices/batch",
                    source_type="verified_truth",
                    provenance="https://acme.com ⟷ https://acme.com/docs"
                )
            ]
        )
        ttl = export_product_truth_to_prov_ttl(matrix)
        g = Graph().parse(data=ttl, format="turtle")
        
        datasets = list(g.subjects(RDF.type, DCAT.Dataset))
        self.assertEqual(len(datasets), 1, "Expected dcat:Dataset in product truth turtle")
        ds_uri = datasets[0]
        
        self.assertEqual(len(list(g.objects(ds_uri, DCTERMS.title))), 1)
        self.assertIn("Product Truth Governance Matrix", str(list(g.objects(ds_uri, DCTERMS.title))[0]))
        self.assertEqual(len(list(g.objects(ds_uri, DCTERMS.creator))), 1)
        self.assertEqual(len(list(g.objects(ds_uri, DCTERMS.created))), 1)

    def test_dcat_shacl_governance_validation(self):
        """Verify that the enriched Product Truth graph conforms to W3C SHACL including DCATDatasetShape."""
        matrix = ProductTruthMatrixResponse(
            brand_name="FintechCore",
            marketing_url="https://fintechcore.com",
            tech_docs_url="https://fintechcore.com/api",
            marketing_grounding_index=100.0,
            evidence_status="conclusive",
            total_marketing_claims=1,
            total_technical_capabilities=1,
            verified_claims_count=1,
            unbacked_claims_count=0,
            hidden_capabilities_count=0,
            executive_summary="FintechCore audit summary.",
            verified_triples=[
                SemanticTriple(
                    subject="FintechCore",
                    predicate="compliesWith",
                    object="PCI-DSS Level 1",
                    confidence=0.99,
                    evidence_sentence="[Marketing]: PCI compliant | [Tech Reality]: Certified PCI-DSS Level 1 service provider",
                    source_type="verified_truth",
                    provenance="https://fintechcore.com/security ⟷ https://fintechcore.com/api/compliance"
                )
            ]
        )
        vconfig = VerticalConfig(
            vertical_id="fintech",
            display_name="Fintech",
            gliner_labels=["Compliance Feature"],
            mandatory_schema_types=["SoftwareApplication"],
            core_seed_concepts=["PCI-DSS Level 1"],
            concept_hierarchy={"PCI-DSS Level 1": "Regulatory Compliance"}
        )
        ttl = export_product_truth_to_prov_ttl(matrix, vconfig)
        
        report = validate_rdf_graph_shacl(ttl)
        self.assertTrue(report.conforms, f"Graph should conform to all SHACL shapes including DCATDatasetShape: {report.violations}")
        self.assertEqual(report.violations_count, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
