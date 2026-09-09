"""
Unit and integration tests for W3C PROV-O and W3C SKOS in GainARK OntoLeap.
Verifies knowledge graph provenance, concept taxonomies, and SPARQL queries.
"""

import sys
import unittest
from rdflib import Graph, URIRef, Literal, RDF
from rdflib.namespace import PROV, SKOS

from models import SemanticTriple, ProductTruthMatrixResponse, VerticalConfig
from linking import build_rdf_graph, export_to_rdf_turtle, execute_sparql_query_on_ttl
from product_truth import export_product_truth_to_prov_ttl


class TestProvSkosOntology(unittest.TestCase):

    def setUp(self):
        self.domain = "testplatform.com"
        self.triples = [
            SemanticTriple(
                subject="TestPlatform",
                predicate="automates",
                object="Subscription Billing",
                confidence=0.95,
                evidence_sentence="TestPlatform automates subscription billing workflows across global currencies.",
                source_type="marketing_claim",
                provenance="https://testplatform.com/billing"
            ),
            SemanticTriple(
                subject="TestPlatform",
                predicate="compliesWith",
                object="ASC 606",
                confidence=0.92,
                evidence_sentence="Fully compliant with ASC 606 and IFRS 15 revenue recognition standards.",
                source_type="verified_truth",
                provenance="https://testplatform.com/security ⟷ https://docs.testplatform.com/api/v1/revrec"
            ),
            SemanticTriple(
                subject="TestPlatform",
                predicate="integratesWith",
                object="Salesforce CRM",
                confidence=0.88,
                evidence_sentence="Bi-directional synchronization with Salesforce CRM accounts.",
                source_type="marketing_claim",
                provenance="https://testplatform.com/integrations"
            )
        ]
        self.hubs = {
            "Subscription Billing": "https://testplatform.com/features/billing",
            "Revenue Recognition": "https://testplatform.com/solutions/rev-rec"
        }
        self.entities = ["Subscription Billing", "ASC 606", "Salesforce CRM", "NetSuite", "PCI-DSS"]
        self.concept_hierarchy = {
            "ASC 606": "Revenue Recognition",
            "Revenue Recognition": "Revenue Operations",
            "Subscription Billing": "Billing Automation"
        }
        self.vertical_config = VerticalConfig(
            vertical_id="b2b_saas_fintech",
            display_name="B2B SaaS & Financial Software",
            gliner_labels=["Billing Feature", "Accounting Standard"],
            mandatory_schema_types=["SoftwareApplication", "Organization", "Offer"],
            core_seed_concepts=["Revenue Recognition", "ASC 606", "Subscription Billing"],
            concept_hierarchy=self.concept_hierarchy
        )

    def test_build_rdf_graph_prov_o_structure(self):
        """Verify PROV-O SoftwareAgent, Activity, and Entity assertions exist in graph."""
        g = build_rdf_graph(
            domain=self.domain,
            triples=self.triples,
            hubs=self.hubs,
            entities=self.entities,
            concept_hierarchy=self.concept_hierarchy,
            vertical_id="b2b_saas_fintech"
        )

        # 1. Check SoftwareAgent
        agent_matches = list(g.subjects(RDF.type, PROV.SoftwareAgent))
        self.assertGreater(len(agent_matches), 0, "Graph must declare at least one prov:SoftwareAgent")
        agent_uri = agent_matches[0]
        self.assertTrue(str(agent_uri).endswith("#ontoleap-agent"))

        # 2. Check Activity
        activity_matches = list(g.subjects(RDF.type, PROV.Activity))
        self.assertGreater(len(activity_matches), 0, "Graph must declare at least one prov:Activity")
        act_uri = activity_matches[0]
        self.assertTrue(bool(list(g.triples((act_uri, PROV.wasAssociatedWith, agent_uri)))))

        # 3. Check prov:Entity on extracted concepts
        entities_found = list(g.subjects(RDF.type, PROV.Entity))
        self.assertGreater(len(entities_found), 3, "Extracted capabilities and sources must be prov:Entity")

        # 4. Check prov:wasQuotedFrom
        quotes = list(g.objects(None, PROV.wasQuotedFrom))
        self.assertGreater(len(quotes), 0, "Evidence sentences must be attached via prov:wasQuotedFrom")
        self.assertTrue(any("automates subscription billing" in str(q) for q in quotes))

        # 5. Check prov:hadPrimarySource for single source
        sources = list(g.objects(None, PROV.hadPrimarySource))
        self.assertGreater(len(sources), 0, "Single-source claims must have prov:hadPrimarySource")
        self.assertTrue(any("billing" in str(s) for s in sources))

        # 6. Check prov:wasDerivedFrom for dual-source triple
        derivations = list(g.objects(None, PROV.wasDerivedFrom))
        self.assertGreater(len(derivations), 0, "Dual-source verified claim must have prov:wasDerivedFrom")

    def test_build_rdf_graph_skos_taxonomy(self):
        """Verify SKOS ConceptScheme, Concept, broader, and narrower relations."""
        g = build_rdf_graph(
            domain=self.domain,
            triples=self.triples,
            hubs=self.hubs,
            entities=self.entities,
            concept_hierarchy=self.concept_hierarchy,
            vertical_id="b2b_saas_fintech"
        )

        # 1. Check ConceptScheme
        schemes = list(g.subjects(RDF.type, SKOS.ConceptScheme))
        self.assertEqual(len(schemes), 1, "Graph must have exactly one skos:ConceptScheme for the vertical")

        # 2. Check Concepts
        concepts = list(g.subjects(RDF.type, SKOS.Concept))
        self.assertGreaterEqual(len(concepts), 4, "Taxonomy must create skos:Concept nodes for hierarchy terms")

        # 3. Check broader & narrower
        broader_links = list(g.subject_objects(SKOS.broader))
        self.assertGreaterEqual(len(broader_links), 3, "Hierarchy must emit skos:broader links")

        narrower_links = list(g.subject_objects(SKOS.narrower))
        self.assertGreaterEqual(len(narrower_links), 3, "Hierarchy must emit inverse skos:narrower links")

        # Verify the hierarchy passed in by the caller is honoured. The URIs live in the
        # shared vertical namespace, not under the audited domain: concepts belong to
        # the ontology, so the same concept is one resource across every client's graph.
        # Minting them per client made "Revenue Recognition" for one vendor a different
        # thing from "Revenue Recognition" for another, and nothing could be compared.
        from ontology_schema import concept_uri as onto_concept_uri
        asc_concept_uri = URIRef(onto_concept_uri("b2b_saas_fintech", "asc-606"))
        revrec_concept_uri = URIRef(onto_concept_uri("b2b_saas_fintech", "revenue-recognition"))
        self.assertIn((asc_concept_uri, SKOS.broader, revrec_concept_uri), g)
        self.assertIn((revrec_concept_uri, SKOS.narrower, asc_concept_uri), g)

        # No concept may be minted under the client's domain.
        client_scoped = [c for c in concepts if self.domain in str(c)]
        self.assertEqual(
            client_scoped, [],
            "Concepts were minted under the audited domain (%s), which forks the shared "
            "ontology into a private copy per client." % client_scoped[:3],
        )

    def test_export_to_rdf_turtle_and_sparql(self):
        """Verify Turtle serialization parses correctly and answers SPARQL 1.1 queries."""
        ttl = export_to_rdf_turtle(
            domain=self.domain,
            triples=self.triples,
            hubs=self.hubs,
            entities=self.entities,
            concept_hierarchy=self.concept_hierarchy,
            vertical_id="b2b_saas_fintech"
        )
        self.assertIn("@prefix prov: <http://www.w3.org/ns/prov#>", ttl)
        self.assertIn("@prefix skos: <http://www.w3.org/2004/02/skos/core#>", ttl)

        # Re-parse into a fresh graph to ensure validity
        g2 = Graph()
        g2.parse(data=ttl, format="turtle")
        self.assertGreater(len(g2), 20)

        # SPARQL 1: Find entities with evidence quotes
        sparql_prov = """
        PREFIX prov: <http://www.w3.org/ns/prov#>
        SELECT ?entity ?quote WHERE {
            ?entity a prov:Entity ;
                    prov:wasQuotedFrom ?quote .
        }
        """
        res_prov = execute_sparql_query_on_ttl(ttl, sparql_prov)
        self.assertGreater(res_prov["row_count"], 0)
        self.assertIn("quote", res_prov["columns"])

        # SPARQL 2: Find SKOS broader relationships
        sparql_skos = """
        PREFIX skos: <http://www.w3.org/2004/02/skos/core#>
        SELECT ?child ?parent WHERE {
            ?child skos:broader ?parent .
        }
        """
        res_skos = execute_sparql_query_on_ttl(ttl, sparql_skos)
        self.assertGreaterEqual(res_skos["row_count"], 3)

    def test_product_truth_matrix_prov_export(self):
        """Verify that ProductTruthMatrix exports complete PROV-O derivation chains."""
        matrix = ProductTruthMatrixResponse(
            brand_name="TestBrand",
            marketing_url="https://testbrand.com",
            tech_docs_url="https://docs.testbrand.com/openapi.json",
            marketing_grounding_index=66.7,
            total_marketing_claims=3,
            total_technical_capabilities=3,
            verified_claims_count=2,
            unbacked_claims_count=1,
            hidden_capabilities_count=1,
            verified_triples=[
                SemanticTriple(
                    subject="TestBrand",
                    predicate="automates",
                    object="Subscription Invoicing",
                    evidence_sentence="Invoicing automated via background cron",
                    source_type="verified_truth",
                    provenance="https://testbrand.com ⟷ /v1/invoices"
                ),
                SemanticTriple(
                    subject="TestBrand",
                    predicate="compliesWith",
                    object="SOC 2",
                    evidence_sentence="Certified SOC 2 Type II compliance",
                    source_type="verified_truth",
                    provenance="https://testbrand.com/trust ⟷ /security"
                )
            ],
            unbacked_claims=[
                SemanticTriple(
                    subject="TestBrand",
                    predicate="compliesWith",
                    object="HIPAA",
                    evidence_sentence="Enterprise HIPAA ready",
                    source_type="marketing_claim",
                    provenance="https://testbrand.com/security"
                )
            ],
            hidden_capabilities=[
                SemanticTriple(
                    subject="TestBrand",
                    predicate="integratesWith",
                    object="Workday ERP",
                    evidence_sentence="Workday integration endpoint present in OpenAPI",
                    source_type="technical_truth",
                    provenance="/v1/connectors/workday"
                )
            ],
            drift_alerts=["Unbacked HIPAA claim detected"],
            growth_recommendations=["Market Workday ERP capability"],
            executive_summary="Test summary"
        )

        ttl = export_product_truth_to_prov_ttl(matrix, self.vertical_config)
        self.assertIn("prov:SoftwareAgent", ttl)
        self.assertIn("prov:wasDerivedFrom", ttl)
        self.assertIn("VERIFIED_TRUTH", ttl)
        self.assertIn("MARKETING_DRIFT_FLUFF", ttl)
        self.assertIn("UNMARKETED_CAPABILITY", ttl)

        # Parse with RDFLib
        g = Graph()
        g.parse(data=ttl, format="turtle")
        self.assertGreater(len(g), 15)

        # SPARQL: Query Verified Claims derived from two sources
        sparql_query = """
        PREFIX prov: <http://www.w3.org/ns/prov#>
        PREFIX onto: <https://testbrand.com/ontology/>
        SELECT ?cap (COUNT(?src) AS ?src_count) WHERE {
            ?cap onto:groundingStatus "VERIFIED_TRUTH" ;
                 prov:wasDerivedFrom ?src .
        }
        GROUP BY ?cap
        """
        res = execute_sparql_query_on_ttl(ttl, sparql_query)
        self.assertGreater(res["row_count"], 0)
        # Verified claims should have 2 derivations (marketing + tech)
        for row in res["rows"]:
            src_count = int(row[1])
            self.assertEqual(src_count, 2, "Verified truth claims must derive from exactly 2 sources (marketing + tech)")


if __name__ == "__main__":
    unittest.main()
