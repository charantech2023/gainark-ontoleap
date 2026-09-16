"""
Competitor and Customer only where a page states the role.

Run:  python test_role_evidence.py
"""

import unittest

from models import KGNode
from page_graph import _apply_role_evidence


def nodes(*pairs):
    return [KGNode(id=n, canonical_name=n, entity_type=t) for n, t in pairs]


def types(ns):
    return {n.canonical_name: n.entity_type for n in ns}


class RoleEvidenceTest(unittest.TestCase):
    def test_card_networks_on_a_payments_page_are_not_competitors(self):
        ns = nodes(("Visa", "Competitor"), ("Mastercard", "Competitor"), ("FedEx", "Competitor"))
        text = "Accept Visa, Mastercard and American Express. FedEx bills shipping by weight."
        _apply_role_evidence(ns, text, "https://www.ordwaylabs.com/payments", brand="Ordwaylabs")
        self.assertEqual(set(types(ns).values()), {"Organization"})

    def test_a_named_alternative_is_a_competitor(self):
        ns = nodes(("Zuora", "SoftwarePlatform"), ("Chargebee", "SoftwarePlatform"), ("Maxio", "Organization"))
        text = ("The Best Zuora Alternative. See how Ordway compares to Chargebee. "
                "Teams migrating from Maxio keep their history.")
        _apply_role_evidence(ns, text, "https://www.ordwaylabs.com/product", brand="Ordwaylabs")
        self.assertEqual(set(types(ns).values()), {"Competitor"})

    def test_a_customer_needs_its_story(self):
        ns = nodes(("Paubox", "SoftwarePlatform"), ("Claude", "Customer"))
        text = "Paubox Automates Billing and Revenue Recognition. Connect Claude to your billing data."
        _apply_role_evidence(ns, text, "https://www.ordwaylabs.com/blog/mcp", brand="Ordwaylabs")
        self.assertEqual(types(ns), {"Paubox": "Customer", "Claude": "Organization"})

    def test_a_customer_page_keeps_the_label(self):
        ns = nodes(("ListReports", "Customer"))
        _apply_role_evidence(ns, "ListReports grew fast.", "https://www.ordwaylabs.com/customers/listreports",
                             brand="Ordwaylabs")
        self.assertEqual(types(ns)["ListReports"], "Customer")

    def test_the_brand_never_takes_a_role(self):
        ns = nodes(("Ordway", "SoftwarePlatform"))
        _apply_role_evidence(ns, "Ordway automates invoicing.", "https://www.ordwaylabs.com/", brand="Ordwaylabs")
        self.assertEqual(types(ns)["Ordway"], "SoftwarePlatform")


if __name__ == "__main__":
    unittest.main()
