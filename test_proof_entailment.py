"""
A relation's proof has to be about the site: the cases from the ordwaylabs.com run.

Run:  python test_proof_entailment.py
"""

import unittest
from unittest.mock import patch

import page_graph
from models import IndustryOntologyModel, KGNode

ONTO = IndustryOntologyModel(vertical_id="test_vertical", display_name="Test",
                             known_integrations=["Salesforce", "QuickBooks", "NetSuite"],
                             known_compliance=["HIPAA", "SOC 2", "SOC 2 Type II", "ASC 606"])


def edges(text, url, nodes=()):
    with patch.object(page_graph, "load_industry_ontology", return_value=ONTO):
        found = page_graph._extract_semantic_edges(text, "Ordwaylabs", list(nodes), url, "test_vertical")
    return {(e.predicate, e.target) for e in found}


PAUBOX = KGNode(id="paubox", canonical_name="Paubox", entity_type="Customer")


class ProofEntailmentTest(unittest.TestCase):
    def test_a_customer_headline_does_not_make_the_site_compliant(self):
        text = "Paubox Automates Billing and Revenue Recognition for HIPAA healthcare email."
        self.assertEqual(edges(text, "https://www.ordwaylabs.com/customers", [PAUBOX]), set())

    def test_naming_a_standard_is_not_complying_with_it(self):
        text = "Healthcare providers often ask about HIPAA when they choose vendors."
        self.assertEqual(edges(text, "https://www.ordwaylabs.com/product"), set())

    def test_a_compliance_statement_proves_compliance(self):
        text = "SOC 1 and SOC 2 audited by an independent accounting firm every year."
        self.assertIn(("compliesWith", "SOC 2"), edges(text, "https://www.ordwaylabs.com/security"))

    def test_a_customer_testimonial_proves_no_integration(self):
        text = "Paubox connects its CRM so Salesforce and QuickBooks stay in sync, says their director."
        self.assertEqual(edges(text, "https://www.ordwaylabs.com/", [PAUBOX]), set())

    def test_a_blog_sentence_must_name_the_site(self):
        blog = "https://www.ordwaylabs.com/blog/evaluate-billing"
        generic = "Most billing tools integrate with Salesforce and NetSuite through connectors."
        self.assertEqual(edges(generic, blog), set())
        own = "Ordway integrates with Salesforce so quotes become subscriptions."
        self.assertEqual(edges(own, blog), {("integratesWith", "Salesforce")})

    def test_an_evaluation_table_row_proves_nothing(self):
        text = "| Security | SOC 2 Type II audited | Red flag if the vendor has no current report |"
        self.assertEqual(edges(text, "https://www.ordwaylabs.com/resources/checklist"), set())

    def test_the_sites_own_integration_page_still_works(self):
        text = "General Ledger Integration\nSync journal entries to QuickBooks, NetSuite and Sage Intacct nightly."
        self.assertEqual(edges(text, "https://www.ordwaylabs.com/integrations"),
                         {("integratesWith", "QuickBooks"), ("integratesWith", "NetSuite")})


if __name__ == "__main__":
    unittest.main()
