"""
Crawling confirmed competitors: which story pages are read, under which name, and what a
site that cannot be read reports. The crawl itself is site_graph's, tested on its own;
nothing here touches the network.
"""

import unittest
from unittest import mock

import competitor_crawl as cc


def plan(*cands):
    return {"candidates": {c[0]: {"url": c[0], "kind": c[1], "order": i}
                           for i, c in enumerate(cands)}}


class StoryUrlTest(unittest.TestCase):

    def test_customer_stories_not_hubs_in_discovery_order(self):
        p = plan(("https://maxio.com/customers/", "customers"),
                 ("https://maxio.com/customers/acme", "customers"),
                 ("https://maxio.com/pricing", "pricing"),
                 ("https://maxio.com/resources/case-studies/", "customers"),
                 ("https://maxio.com/resources/case-studies/globex-billing", "customers"))
        self.assertEqual(cc.story_urls(p), ["https://maxio.com/customers/acme",
                                            "https://maxio.com/resources/case-studies/globex-billing"])

    def test_the_limit_holds(self):
        p = plan(*[("https://x.com/customers/c%d" % i, "customers") for i in range(15)])
        self.assertEqual(len(cc.story_urls(p, limit=10)), 10)


class BrandTest(unittest.TestCase):

    def test_the_current_name_is_the_one_the_domain_carries(self):
        entry = {"name": "SaaSOptics (Maxio)", "forms": ["SaaSOptics", "Maxio"], "domain": "maxio.com"}
        self.assertEqual(cc._brand(entry), "Maxio")


class UnreadableTest(unittest.TestCase):

    def test_a_site_that_cannot_be_read_is_reported_not_raised(self):
        entry = {"name": "Recurly", "forms": ["Recurly"], "domain": "recurly.com"}
        state = {"crawled": [], "failed": [{"url": "https://recurly.com", "error": "challenge"}],
                 "plan": {"candidates": {}}}
        with mock.patch("site_graph.new_crawl_state", return_value=state), \
                mock.patch("site_graph.crawl_slice"), \
                mock.patch("site_graph.assemble_site_kg", side_effect=ValueError("nothing was read")):
            result = cc.crawl_competitor(entry, "b2b_saas_fintech")
        self.assertEqual(result["status"], "unreadable")
        self.assertEqual(result["failed"], ["https://recurly.com"])
        self.assertIn("nothing was read", result["reason"])


if __name__ == "__main__":
    unittest.main()
