"""
Routing a site to a vertical before a crawl or page graph is measured against it.

Runs offline: verticals live in a throwaway directory, and the category check's model call
is replaced, so no test reaches Gemini or the network.

Run:  python test_vertical_routing.py
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

import industry_ontology
import industry_profiler
import site_graph

BILLING = {
    "vertical_id": "billing",
    "display_name": "Subscription Billing",
    "core_seed_concepts": ["Subscription Billing", "Revenue Recognition"],
    "known_compliance": ["SOC 2", "HIPAA", "ISO 27001", "ASC 606"],
    "known_integrations": ["Slack", "Salesforce"],
    "concepts": [{"id": c.lower().replace(" ", "-"), "prefLabel": c, "definition": c}
                 for c in ("Approvals", "Dashboards", "Permissions", "Invoicing", "Dunning",
                           "Proration", "Credit Notes", "Usage-Based Pricing", "Payment Gateways",
                           "Churn", "Revenue Schedules", "Accounts Receivable")],
}

SECURITY = {
    "vertical_id": "security",
    "display_name": "Security Compliance",
    "core_seed_concepts": ["Compliance Automation", "Audit Readiness"],
    "known_compliance": ["SOC 2", "HIPAA", "ISO 27001"],
    "known_integrations": ["Slack"],
    "concepts": [{"id": c.lower().replace(" ", "-"), "prefLabel": c, "definition": c}
                 for c in ("Encryption", "Least Privilege", "MFA", "Antivirus",
                           "Awareness Training", "GRC", "Policy Management")],
}

# What vanta.com's pages gave the router: six security terms, and billing terms that are
# either generic, an integration name, or compliance both verticals carry.
SECURITY_SITE = ("Get SOC 2, HIPAA and ISO 27001 ready. Encryption, least privilege and MFA "
                 "checks, antivirus and awareness training, GRC in one place. Approvals, "
                 "permissions and dashboards, with alerts in Slack.")

BILLING_SITE = ("Subscription billing and revenue recognition with dunning, proration, credit "
                "notes, invoicing, usage-based pricing, payment gateways, churn reporting, "
                "revenue schedules and accounts receivable, ASC 606 ready.")


class RoutingTest(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="ontoleap_routing_test_")
        for profile in (BILLING, SECURITY):
            with open(os.path.join(self.tmpdir, profile["vertical_id"] + ".json"), "w", encoding="utf-8") as fh:
                json.dump(profile, fh)
        p = patch.object(industry_ontology, "verticals_dir", lambda: self.tmpdir)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(shutil.rmtree, self.tmpdir, True)

    # ------------------------------------------------------------ distinctive text

    def test_shared_compliance_and_integration_names_do_not_decide_a_route(self):
        """Live failure: vanta.com was refused as a tie, security 9 against billing 8."""
        plain = industry_ontology.classify_vertical(SECURITY_SITE)
        self.assertIsNone(plain["vertical_id"], "the fixture must reproduce the tie")

        routed = industry_ontology.classify_vertical(SECURITY_SITE, distinctive=True)
        self.assertEqual(routed["vertical_id"], "security", routed["reason"])
        billing = next(c for c in routed["candidates"] if c["vertical_id"] == "billing")
        for term in ("soc 2", "hipaa", "iso 27001", "slack"):
            self.assertNotIn(term, billing["evidence"])

    def test_a_plural_is_one_term(self):
        text = "dashboard dashboards"
        billing = next(c for c in industry_ontology.classify_vertical(text, distinctive=True)["candidates"]
                       if c["vertical_id"] == "billing")
        self.assertEqual(billing["matched"], 1, billing)

    def test_strong_evidence_is_marked_confident(self):
        routed = industry_ontology.classify_vertical(BILLING_SITE, distinctive=True)
        self.assertEqual(routed["vertical_id"], "billing")
        self.assertTrue(routed["confident"], routed["matched"])

    # ------------------------------------------------------------ category check

    def test_a_confident_route_does_not_ask_the_model(self):
        with patch.object(site_graph, "confirm_vertical_by_category") as confirm:
            routed = site_graph.route_text_to_vertical(BILLING_SITE)
        self.assertEqual(routed["vertical_id"], "billing")
        confirm.assert_not_called()

    def test_a_weak_route_is_kept_when_the_category_agrees(self):
        verdict = {"confirmed": True, "reason": "The site's own category (Cybersecurity) shares 4 phrases."}
        with patch.object(site_graph, "confirm_vertical_by_category", return_value=verdict):
            routed = site_graph.route_text_to_vertical(SECURITY_SITE)
        self.assertEqual(routed["vertical_id"], "security")
        self.assertIn("category was checked", routed["reason"])

    def test_a_weak_route_is_refused_when_the_category_disagrees(self):
        """Live failure: pos.toasttab.com, restaurant point of sale, routed to billing on 6 terms."""
        verdict = {"confirmed": False,
                   "reason": "The site's own category (Hospitality) shares 2 of the 3 phrases needed."}
        with patch.object(site_graph, "confirm_vertical_by_category", return_value=verdict):
            routed = site_graph.route_text_to_vertical(SECURITY_SITE)
        self.assertIsNone(routed["vertical_id"])
        self.assertIn("too thin to trust", routed["reason"])
        self.assertTrue(routed["candidates"], "the scores stay visible after a refusal")

    def test_a_weak_route_that_cannot_be_checked_is_refused(self):
        verdict = {"confirmed": None, "reason": "The site's category could not be read (Gemini unavailable)."}
        with patch.object(site_graph, "confirm_vertical_by_category", return_value=verdict):
            routed = site_graph.route_text_to_vertical(SECURITY_SITE)
        self.assertIsNone(routed["vertical_id"])
        self.assertIn("could not be checked", routed["reason"])

    def test_the_check_reads_the_model_vocabulary_against_the_vertical(self):
        agreeing = {"category": "Cybersecurity", "display_name": "Security Compliance Automation",
                    "core_seed_concepts": ["Compliance Automation", "Audit Readiness", "Policy Management"]}
        unrelated = {"category": "Hospitality", "display_name": "Restaurant POS",
                     "core_seed_concepts": ["Menu Management", "Compliance Automation"]}
        degraded = dict(agreeing, discovery_degraded="Gemini unavailable")
        pages = [("https://example.com", "<html><body><h1>Example</h1></body></html>")]

        for response, expected in ((agreeing, True), (unrelated, False), (degraded, None)):
            with patch.object(industry_profiler, "call_gemini_industry_discovery", return_value=response):
                verdict = industry_profiler.confirm_vertical_by_category(pages, "security")
            self.assertIs(verdict["confirmed"], expected, verdict)


if __name__ == "__main__":
    unittest.main(verbosity=2)
