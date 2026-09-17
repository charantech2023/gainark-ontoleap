"""
Coverage finds the vertical's terms however the page writes them, and proprietary
concepts are capabilities, not places or companies.

Run:  python test_whitespace_matching.py
"""

import unittest
from unittest.mock import patch

from industry_ontology import align_graph_with_industry
from models import IndustryConcept, IndustryOntologyModel, KGNode, SiteKnowledgeGraph
import page_graph

ONTO = IndustryOntologyModel(
    vertical_id="test_vertical", display_name="Test",
    concepts=[IndustryConcept(id="po", pref_label="Performance Obligation"),
              IndustryConcept(id="mrr", pref_label="MRR"),
              IndustryConcept(id="vat", pref_label="VAT"),
              IndustryConcept(id="as", pref_label="Accounting Standards"),
              IndustryConcept(id="cn", pref_label="Credit Notes")])


class ConceptOccurrenceTest(unittest.TestCase):
    def hits(self, text):
        with patch.object(page_graph, "load_industry_ontology", return_value=ONTO):
            return sorted(n.canonical_name for n in page_graph._concept_occurrences(text, "test_vertical"))

    def test_terms_the_ordway_run_called_whitespace(self):
        text = ("Automatically handle performance obligations (POBs). "
                "Ordway tracks core SaaS metrics including MRR/ARR and net dollar retention.")
        self.assertEqual(self.hits(text), ["MRR", "Performance Obligation"])

    def test_plural_label_matches_singular_and_hyphens(self):
        self.assertEqual(self.hits("Meets every accounting-standard we audit."), ["Accounting Standards"])
        self.assertEqual(self.hits("Issue a credit note in one click."), ["Credit Notes"])

    def test_an_acronym_only_in_capitals(self):
        self.assertEqual(self.hits("Stored in a vat of data."), [])
        self.assertEqual(self.hits("Calculates VAT for EU invoices."), ["VAT"])


class ProprietaryConceptsTest(unittest.TestCase):
    def test_places_companies_and_the_brand_are_not_proprietary(self):
        def node(name, etype, resolution="observed", pages=1, wikidata=None):
            return KGNode(id=name, canonical_name=name, entity_type=etype, resolution=resolution,
                          source_urls=["https://ordwaylabs.com/%d" % i for i in range(pages)],
                          wikidata_id=wikidata)
        kg = SiteKnowledgeGraph(domain="ordwaylabs.com", pages_crawled=3, nodes=[
            node("Ordwaylabs", "Organization", resolution="brand"),
            node("Europe", "Place"), node("SpaceX", "SoftwarePlatform"), node("Paubox", "Customer"),
            node("Cloud computing", "Topic", wikidata="Q483639"),
            node("Revenue Sub-Ledger Sync", "Feature", pages=3),
            node("Stair-Step Pricing", "PricingModel", pages=2),
        ])
        result = align_graph_with_industry(kg, industry=ONTO)
        self.assertEqual(result.proprietary_concepts, ["Revenue Sub-Ledger Sync", "Stair-Step Pricing"])


class CoverageMatchingTest(unittest.TestCase):
    """17 Sep 2026: ordwaylabs.com wrote "PCI DSS", the vertical says "PCI-DSS", and the
    standard was reported as whitespace although the resolver had linked the two."""

    ONTO = IndustryOntologyModel(
        vertical_id="test_vertical", display_name="Test",
        concepts=[IndustryConcept(id="pci-dss", pref_label="PCI-DSS"),
                  IndustryConcept(id="ubp", pref_label="Usage Based Pricing"),
                  IndustryConcept(id="soc1", pref_label="SOC 1 Type II")])

    def covered(self, *nodes):
        kg = SiteKnowledgeGraph(domain="acme.com", pages_crawled=1, nodes=list(nodes))
        return sorted(align_graph_with_industry(kg, industry=self.ONTO).covered_concepts)

    def test_hyphens_and_spaces_are_the_same_term(self):
        self.assertEqual(self.covered(KGNode(id="a", canonical_name="PCI DSS")), ["PCI-DSS"])
        self.assertEqual(self.covered(KGNode(id="b", canonical_name="usage-based  pricing")),
                         ["Usage Based Pricing"])

    def test_a_node_the_resolver_linked_to_a_concept_covers_it(self):
        from ontology_schema import concept_uri
        node = KGNode(id="entity:pci", canonical_name="Payment card security",
                      concept_uri=concept_uri("test_vertical", "pci-dss"))
        self.assertEqual(self.covered(node), ["PCI-DSS"])
        # A link into another vertical covers nothing here.
        stray = KGNode(id="entity:x", canonical_name="Something",
                       concept_uri=concept_uri("other_vertical", "soc1"))
        self.assertEqual(self.covered(stray), [])

    def test_joining_hyphens_does_not_join_distinct_terms(self):
        self.assertEqual(self.covered(KGNode(id="c", canonical_name="SOC 2 Type II")), [])


if __name__ == "__main__":
    unittest.main()
