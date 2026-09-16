"""
One name written two ways on a site resolves to one node.

Run:  python test_spelling_merge.py
"""

import unittest

from entity_resolver import resolve_site
from test_entity_registry import raw, seed_registry


def names(res):
    return sorted(n.canonical_name for n in res.nodes + res.mentions)


def together(res, *forms):
    ids = {n.id for n in res.nodes + res.mentions if set(forms) & set(n.aliases)}
    return len(ids) == 1


class SpellingMergeTest(unittest.TestCase):
    def resolve(self, *forms):
        nodes = [raw(f, count=(3 if i == 0 else 1)) for i, f in enumerate(forms)]
        return resolve_site(nodes, [], "ordwaylabs.com", "test_vertical", seed_registry())

    def test_spaces_inside_a_name(self):
        # Pairs from the 14 Sep 2026 ordwaylabs.com run.
        for a, b in (("CardConnect", "Card Connect"), ("HashiCorp", "Hashi Corp"),
                     ("DigitalOcean", "Digital Ocean")):
            res = self.resolve(a, b)
            self.assertTrue(together(res, a, b), names(res))

    def test_a_capitalised_plural(self):
        res = self.resolve("Coupons", "Coupon")
        self.assertTrue(together(res, "Coupons", "Coupon"), names(res))

    def test_an_acronym_the_site_also_spells_out(self):
        res = self.resolve("Amazon Web Services", "AWS")
        self.assertTrue(together(res, "Amazon Web Services", "AWS"), names(res))
        res = self.resolve("Google Cloud Platform", "GCP")
        self.assertTrue(together(res, "Google Cloud Platform", "GCP"), names(res))

    def test_different_names_stay_apart(self):
        res = self.resolve("Sage Intacct", "Sage")
        self.assertFalse(together(res, "Sage Intacct", "Sage"), names(res))
        res = self.resolve("Zuora", "Zenskar")
        self.assertFalse(together(res, "Zuora", "Zenskar"), names(res))

    def test_an_acronym_with_two_expansions_is_left_alone(self):
        res = self.resolve("Single Sign On", "Service Status Overview", "SSO")
        self.assertFalse(together(res, "SSO", "Single Sign On"), names(res))
        self.assertFalse(together(res, "SSO", "Service Status Overview"), names(res))


if __name__ == "__main__":
    unittest.main()
