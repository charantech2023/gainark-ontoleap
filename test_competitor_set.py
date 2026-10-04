"""
The competitor set: names from a buyer profile, resolved to domains a crawl can use.

Rung 1 reads the registry, rung 2 a link from the page that proved the competitor, rung 3
proposes <name>.com and waits for a reviewer. These hold the rungs to their rules - a
link only counts when the domain is the name, a bot check is reported rather than got
past - and hold the set to keeping reviewer decisions. No network; a temporary mirror and
registry stand in for the shared brain.
"""

import json
import os
import shutil
import tempfile
import unittest
import urllib.error
from unittest import mock

import competitor_set as cs
import vertical_store
from entity_registry import RegistryStore
from graph_archive import DirectoryArchive

PROFILE = {
    "known_competitors": ["Recurly", "SaaSOptics (Maxio)", "Zuora"],
    "icp_evidence": {"known_competitors": {
        "Recurly": {"source_url": "https://ordway.example/gentreo", "quote": "..."},
        "SaaSOptics (Maxio)": {"source_url": "https://ordway.example/happyco", "quote": "..."},
        "Zuora": {"source_url": "https://ordway.example/zuora-alternative", "quote": "..."},
    }},
}

PAGES = {
    # The pages that proved each competitor. The Zuora page names Zuora only in links to
    # news sites, as ordwaylabs.com's did on 3 Oct 2026.
    "https://ordway.example/gentreo": "<a href='https://ordway.example/x'>Ordway</a>",
    "https://ordway.example/happyco": "<p>HappyCo used SaaSOptics</p>",
    "https://ordway.example/zuora-alternative":
        "<a href='https://techcrunch.com/zuora-acquires'>Zuora acquired Leeyo</a>",
    "https://saasoptics.com": ("https://www.maxio.com/", "<title>SaaSOptics is now Maxio</title>"),
    "https://zuora.com": ("https://www.zuora.com/", "<title>Subscription Management | Zuora</title>"),
    "https://recurly.com": ("https://recurly.com/",
                            "<title>Just a moment...</title> cf-chl-bypass challenge-platform"),
}


def fetch(url):
    page = PAGES.get(url)
    if page is None:
        raise urllib.error.URLError("no such host")
    return page if isinstance(page, tuple) else (url, page)


class _Store(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        local = os.path.join(self.tmp, "verticals")
        os.makedirs(os.path.join(local, "buyers"))
        for patch in (mock.patch.object(vertical_store, "MIRROR_URI", os.path.join(self.tmp, "mirror")),
                      mock.patch.object(vertical_store, "verticals_dir", lambda: local),
                      mock.patch("security.verticals_dir", lambda: local)):
            patch.start()
            self.addCleanup(patch.stop)
        self.registry_store = RegistryStore(archive=DirectoryArchive(os.path.join(self.tmp, "registry")))

    def registry(self):
        return self.registry_store.registry(force=True)

    def by_name(self, doc):
        return {c["name"]: c for c in doc["competitors"]}


class NamesTest(unittest.TestCase):

    def test_a_rebrand_written_as_one_name_is_two_forms(self):
        self.assertEqual(cs.name_forms("SaaSOptics (Maxio)"), ["SaaSOptics", "Maxio"])
        self.assertEqual(cs.name_forms("Zuora"), ["Zuora"])

    def test_a_domain_is_a_name_only_by_its_first_label(self):
        self.assertTrue(cs.domain_names("recurly.com", ["Recurly"]))
        self.assertTrue(cs.domain_names("sage-intacct.com", ["Sage Intacct"]))
        self.assertFalse(cs.domain_names("recurly.zendesk.com", ["Zendesk Inc"]))
        self.assertFalse(cs.domain_names("techcrunch.com", ["Zuora"]))


class RungTest(_Store):

    def test_rung_1_is_the_registry(self):
        hit = cs.by_registry(["Stripe Billing"], self.registry())
        self.assertEqual((hit["domain"], hit["method"], hit["status"]), ("stripe.com", "registry", "automatic"))

    def test_rung_2_needs_the_domain_to_be_the_name(self):
        self.assertIsNone(cs.by_link(["Zuora"], ["https://ordway.example/zuora-alternative"],
                                     "ordway.example", fetch))
        page = {"https://ordway.example/p": "<a href='https://www.recurly.com/pricing'>them</a>"}
        hit = cs.by_link(["Recurly"], ["https://ordway.example/p"], "ordway.example",
                         lambda u: (u, page[u]))
        self.assertEqual((hit["domain"], hit["status"]), ("recurly.com", "automatic"))

    def test_rung_3_follows_the_rebrand(self):
        hit = cs.by_proposal(["SaaSOptics", "Maxio"], fetch)
        self.assertEqual((hit["domain"], hit["status"]), ("maxio.com", "pending"))
        self.assertIn("redirected from saasoptics.com", hit["evidence"])

    def test_a_bot_check_is_reported_not_passed(self):
        hit = cs.by_proposal(["Recurly"], fetch)
        self.assertEqual((hit["domain"], hit["status"], hit["verified"]), ("recurly.com", "pending", False))
        self.assertIn("bot check", hit["evidence"])

    def test_a_refusal_is_reported_too(self):
        def refuse(url):
            raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
        hit = cs.by_proposal(["Chargebee"], refuse)
        self.assertEqual((hit["domain"], hit["verified"]), ("chargebee.com", False))

    def test_a_page_that_does_not_name_the_competitor_is_not_proposed(self):
        self.assertIsNone(cs.by_proposal(["Acme"], lambda u: (u, "<title>Domain for sale</title>")))


class SetTest(_Store):

    def build(self, **kw):
        return cs.build("ordway.example", profile=PROFILE, registry=self.registry(), fetch=fetch, **kw)

    def test_build_resolves_every_profile_competitor(self):
        got = self.by_name(self.build())
        self.assertEqual(got["SaaSOptics (Maxio)"]["domain"], "maxio.com")
        self.assertEqual(got["Zuora"]["domain"], "zuora.com")
        self.assertEqual(got["Recurly"]["verified"], False)
        self.assertEqual({c["status"] for c in got.values()}, {"pending"})
        self.assertEqual(cs.crawlable("ordway.example"), [], "nothing is crawled before review")

    def test_confirming_teaches_the_registry(self):
        self.build()
        cs.confirm("ordway.example", "SaaSOptics (Maxio)", store=self.registry_store)
        reg = self.registry()
        maxio = reg.by_domain("maxio.com")
        self.assertEqual((maxio.id, maxio.prefLabel), ("maxio", "Maxio"))
        self.assertIn("SaaSOptics", maxio.forms())
        # The next customer naming SaaSOptics resolves at rung 1, with no lookup.
        self.assertEqual(cs.by_registry(["SaaSOptics"], reg)["domain"], "maxio.com")
        self.assertEqual([c["name"] for c in cs.crawlable("ordway.example")], ["SaaSOptics (Maxio)"])

    def test_confirming_an_existing_entity_adds_its_domain(self):
        self.build(extra=["NetSuite"])
        cs.confirm("ordway.example", "NetSuite", domain="netsuite.com", store=self.registry_store)
        self.assertEqual(self.registry().by_domain("netsuite.com").id, "netsuite")

    def test_reviewer_decisions_survive_a_rebuild(self):
        self.build()
        cs.confirm("ordway.example", "Zuora", store=self.registry_store)
        cs.remove("ordway.example", "Recurly")
        got = self.by_name(self.build())
        self.assertEqual(got["Zuora"]["status"], "confirmed")
        self.assertEqual(got["Recurly"]["status"], "removed")
        self.assertEqual(got["SaaSOptics (Maxio)"]["status"], "pending")

    def test_a_reviewer_can_add_a_competitor(self):
        got = self.by_name(self.build(extra=["Stripe Billing"]))
        self.assertEqual((got["Stripe Billing"]["domain"], got["Stripe Billing"]["source"]),
                         ("stripe.com", "reviewer"))
        self.assertEqual([c["name"] for c in cs.crawlable("ordway.example")], ["Stripe Billing"])

    def test_the_set_is_stored_beside_the_buyer_profile(self):
        self.build()
        stored = json.loads(DirectoryArchive(os.path.join(self.tmp, "mirror"))
                            .get("buyers/ordway.example.competitors.json").decode("utf-8"))
        self.assertEqual(len(stored["competitors"]), 3)

    def test_confirming_an_unknown_name_says_so(self):
        self.build()
        with self.assertRaises(ValueError):
            cs.confirm("ordway.example", "Nobody", store=self.registry_store)


if __name__ == "__main__":
    unittest.main()
