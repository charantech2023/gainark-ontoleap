"""
Relation proofs: whole words, no page chrome, and the subject named after the site.

Run:  python test_proof_quotes.py
"""

import unittest

from page_graph import _domain_brand, _proof_units, _proof_window


class ProofQuotesTest(unittest.TestCase):
    def test_search_overlay_and_form_labels_are_not_proof(self):
        text = "Hit enter to search or ESC to close\nLast name**\nLead source\nOrdway automates invoicing for SaaS companies."
        self.assertEqual(_proof_units(text), ["Ordway automates invoicing for SaaS companies."])

    def test_a_list_item_keeps_its_heading(self):
        text = "General Ledger Integration\nPost to Quickbooks, NetSuite, Sage Intacct and more today."
        units = _proof_units(text)
        self.assertIn("General Ledger Integration — Post to Quickbooks, NetSuite, Sage Intacct and more today.", units)

    def test_each_line_of_a_list_is_its_own_unit(self):
        text = ("Card and ACH processing for recurring billing, across every plan\n"
                "Automated past-due reminders sent to every overdue customer")
        self.assertEqual(len(_proof_units(text)), 2)

    def test_a_long_sentence_is_cut_on_words_around_the_match(self):
        sentence = ("word " * 120) + "integrates natively with Salesforce for quotes " + ("tail " * 80)
        proof = _proof_window(sentence.strip(), "Salesforce")
        self.assertIn("Salesforce", proof)
        self.assertLessEqual(len(proof), 282)
        body = proof.strip("…")
        for w in body.split():
            self.assertIn(w, ("word", "integrates", "natively", "with", "Salesforce", "for", "quotes", "tail"))

    def test_a_short_sentence_is_kept_whole(self):
        self.assertEqual(_proof_window("Ordway integrates with Stripe.", "Stripe"), "Ordway integrates with Stripe.")

    def test_subject_is_the_registered_name_not_the_subdomain(self):
        self.assertEqual(_domain_brand("https://support.ordwaylabs.com/hc/en-us"), "Ordwaylabs")
        self.assertEqual(_domain_brand("https://www.ordwaylabs.com/"), "Ordwaylabs")
        self.assertEqual(_domain_brand("https://shop.acme.co.uk/"), "Acme")
        self.assertEqual(_domain_brand("https://ordwaylabs.stoplight.io/docs"), "Ordwaylabs")


if __name__ == "__main__":
    unittest.main()
