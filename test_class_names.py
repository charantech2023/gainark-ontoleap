"""
Every class name spelled one way, whichever extractor wrote it.

Run:  python test_class_names.py
"""

import unittest

from ontology_schema import canonical_class


class ClassNamesTest(unittest.TestCase):
    def test_phrase_and_camel_case_give_one_name(self):
        # Both spellings appeared in the 14 Sep 2026 ordwaylabs.com run.
        self.assertEqual(canonical_class("Pricing Model"), "PricingModel")
        self.assertEqual(canonical_class("PricingModel"), "PricingModel")
        self.assertEqual(canonical_class("Software Platform"), "SoftwarePlatform")
        self.assertEqual(canonical_class("SoftwarePlatform"), "SoftwarePlatform")
        self.assertEqual(canonical_class("integration_partner"), "IntegrationPartner")

    def test_acronyms_keep_their_capitals(self):
        self.assertEqual(canonical_class("API Standard"), "APIStandard")
        self.assertEqual(canonical_class("SLA Commitment"), "SLA")

    def test_labels_naming_the_same_class_land_on_it(self):
        self.assertEqual(canonical_class("Billing Feature"), "Feature")
        self.assertEqual(canonical_class("Product Feature"), "Feature")
        self.assertEqual(canonical_class("Accounting Standard"), "Standard")
        self.assertEqual(canonical_class("Geographic Market"), "Place")
        self.assertEqual(canonical_class("Customer Segment"), "Segment")

    def test_untyped(self):
        self.assertEqual(canonical_class(""), "Entity")
        self.assertEqual(canonical_class(None), "Entity")


if __name__ == "__main__":
    unittest.main()
