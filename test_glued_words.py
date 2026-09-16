"""
Bylines and blog cards whose parts arrive with no space between them.

Run:  python test_glued_words.py
"""

import unittest

from scraper import repair_glued_words


class GluedWordsTest(unittest.TestCase):
    def test_the_joins_seen_on_ordwaylabs_are_split(self):
        # Text as quoted in the 14 Sep 2026 ordwaylabs.com run.
        self.assertEqual(repair_glued_words("ByOrdway LabsDecember 2, 2025"),
                         "By Ordway Labs December 2, 2025")
        self.assertEqual(repair_glued_words("BySteve KeiferJune 15, 2023 May 11th, 2026Healthcare"),
                         "By Steve Keifer June 15, 2023 May 11th, 2026 Healthcare")
        self.assertEqual(repair_glued_words("Sameer GulatiCEO"), "Sameer Gulati CEO")
        self.assertEqual(repair_glued_words("Subbu VenkiteswaranSVP"), "Subbu Venkiteswaran SVP")
        self.assertEqual(repair_glued_words("BlogUsage-Based Billing"), "Blog Usage-Based Billing")

    def test_camel_cased_names_are_left_alone(self):
        for name in ("HubSpot", "QuickBooks", "ChurnZero", "LinkedIn", "SaaSOptics",
                     "DigitalOcean", "MongoDB", "CardConnect", "Bylaws", "ByteDance"):
            self.assertEqual(repair_glued_words(name), name)

    def test_a_month_word_without_a_date_is_not_a_date(self):
        self.assertEqual(repair_glued_words("the MayApple plan"), "the MayApple plan")
        self.assertEqual(repair_glued_words("ASC 606 in 2025 was adopted"), "ASC 606 in 2025 was adopted")

    def test_empty_text(self):
        self.assertEqual(repair_glued_words(""), "")
        self.assertEqual(repair_glued_words(None), "")


if __name__ == "__main__":
    unittest.main()
