"""
Buyer profiles are stored per site, so two sites in one vertical never share one.

Run:  python test_buyer_profiles.py
"""

import os
import tempfile
import unittest

import buyer_profiles


class BuyerProfilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("ONTOLEAP_VERTICALS_DIR")
        os.environ["ONTOLEAP_VERTICALS_DIR"] = self.tmp.name
        os.environ.pop("ONTOLEAP_VERTICALS_MIRROR", None)

    def tearDown(self):
        if self.old is None:
            os.environ.pop("ONTOLEAP_VERTICALS_DIR", None)
        else:
            os.environ["ONTOLEAP_VERTICALS_DIR"] = self.old
        self.tmp.cleanup()

    def test_domain_key(self):
        self.assertEqual(buyer_profiles.domain_key("https://www.ordwaylabs.com/pricing"), "ordwaylabs.com")
        self.assertEqual(buyer_profiles.domain_key("OrdwayLabs.com"), "ordwaylabs.com")
        self.assertEqual(buyer_profiles.domain_key("../../etc/passwd"), "")
        self.assertEqual(buyer_profiles.domain_key(""), "")

    def test_two_sites_in_one_vertical_keep_their_own_buyers(self):
        # The 14 Sep 2026 failure: Chargebee's profile shown for Ordway.
        buyer_profiles.save("https://www.chargebee.com", {"vertical_id": "b2b_saas_fintech",
                                                          "known_competitors": ["Bold Commerce"]})
        self.assertIsNone(buyer_profiles.load("https://www.ordwaylabs.com"))
        buyer_profiles.save("ordwaylabs.com", {"vertical_id": "b2b_saas_fintech",
                                               "known_competitors": ["Zuora", "Chargebee"]})
        self.assertEqual(buyer_profiles.load("www.ordwaylabs.com")["known_competitors"], ["Zuora", "Chargebee"])
        self.assertEqual(buyer_profiles.load("chargebee.com")["known_competitors"], ["Bold Commerce"])

    def test_the_endpoint_serves_by_domain(self):
        from fastapi.testclient import TestClient
        from fastapi import FastAPI
        from routers.system_routes import router
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        self.assertEqual(client.get("/api/buyer-profile", params={"domain": "ordwaylabs.com"}).status_code, 404)
        buyer_profiles.save("ordwaylabs.com", {"known_segments": ["Finance teams at SaaS companies"]})
        r = client.get("/api/buyer-profile", params={"domain": "https://www.ordwaylabs.com/"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["known_segments"], ["Finance teams at SaaS companies"])


if __name__ == "__main__":
    unittest.main()
