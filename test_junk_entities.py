"""
Spans GLiNER tags that name nothing: placeholders, bare codes, page controls.

Run:  python test_junk_entities.py
"""

import unittest

from page_graph import _is_not_a_name, _prose_words

PAGE = ("Use the search box to find an invoice. Apply filters to narrow the list, and click to see more. "
        "Our support team answers in minutes. Ordway integrates with Stripe and Zuora customers migrate easily. "
        "Finance teams run dunning and invoicing without spreadsheets.")
CONCEPTS = {"dunning", "invoicing"}


class JunkEntitiesTest(unittest.TestCase):
    def setUp(self):
        self.words = _prose_words(PAGE)

    def junk(self, span):
        return _is_not_a_name(span, self.words, CONCEPTS)

    def test_placeholders_from_worked_examples(self):
        for span in ("Customer A", "customer B", "Customer ABC", "tier A", "Acme Corp", "Acme", "Plan 2"):
            self.assertTrue(self.junk(span), span)

    def test_bare_codes(self):
        for span in ("A100", "A400"):
            self.assertTrue(self.junk(span), span)

    def test_page_controls_written_lowercase_elsewhere(self):
        for span in ("Search", "Filters", "More", "Support", "Qu"):
            self.assertTrue(self.junk(span), span)

    def test_names_are_kept(self):
        for span in ("Stripe", "Zuora", "HubSpot", "MT940", "SOC 2", "Customer Portal", "Acme Analytics Cloud",
                     "Wix", "G2", "Sage Intacct"):
            self.assertFalse(self.junk(span), span)

    def test_vertical_terms_are_kept_however_written(self):
        self.assertFalse(self.junk("Dunning"))
        self.assertFalse(self.junk("Invoicing"))


if __name__ == "__main__":
    unittest.main()
