"""
A vertical learns its vocabulary from the sites crawled into it: terms several of its sites
write, that other verticals' sites do not, proposed for a reviewer to decide.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from entity_registry import RegistryStore
from graph_archive import DirectoryArchive
import vocabulary_learning as vl
from vocabulary_learning import VocabularyStore, add_concept, build_proposals, variant_key


def rec(key, forms, pages=1, method="mention", labels=None):
    if isinstance(forms, str):
        forms = [forms]
    return {"key": key, "forms": {f: 1 for f in forms}, "pages": pages, "method": method,
            "labels": labels or {"PayrollSoftware": 1}}


def obs(domain, vertical, *records, pages=50):
    return {"domain": domain, "vertical_id": vertical, "forms": list(records), "pages_crawled": pages}


HR = "hr_payroll_benefits"
SITES = [
    obs("www.rippling.com", HR,
        rec("payroll", ["payroll", "Payroll"], pages=40),
        rec("employer of record", "employer of record", pages=3),
        rec("eor", ["EOR", "EORs"], pages=5, method="acronym"),
        rec("rippling ai", "Rippling AI", pages=9, method="observed"),
        rec("gusto", "Gusto", pages=2, method="observed"),
        rec("hr", "HR", pages=4, method="acronym"),
        rec("customer", "customers", pages=30),
        rec("sap successfactors", "SAP SuccessFactors", method="observed"),
        rec("stripe", "Stripe", method="registry"),
        rec("compliance", "compliance", pages=5)),
    obs("gusto.com", HR,
        rec("payroll", "payroll", pages=20),
        rec("eor", "EOR", pages=2, method="observed"),
        rec("hiring regulation", "hiring regulations", pages=2),
        rec("customer", "customers", pages=10),
        rec("sap successfactors", "SAP SuccessFactors", method="observed"),
        rec("compliant", "compliant", pages=2),
        rec("payrollbenefits administrationhead", "PayrollBenefits AdministrationHead", pages=6),
        rec("compliance compliance", "Compliance Compliance", pages=2)),
    obs("deel.com", HR,
        rec("employer of record", "Employer of Record", pages=4, method="observed"),
        rec("hr", "hr", pages=3),
        rec("payrollbenefits administrationhead", "PayrollBenefits AdministrationHead", pages=2),
        rec("compliance compliance", "Compliance Compliance", pages=1),
        rec("compliantly", "compliantly")),
    # Another vertical: billing sites write "customers" too.
    obs("ordwaylabs.com", "b2b_saas_fintech", rec("customer", "customers", pages=90), pages=100),
    obs("support.ordwaylabs.com", "b2b_saas_fintech", rec("customer", "customers", pages=9),
        rec("payroll", "payroll"), pages=20),
]


def proposals(observations=SITES, vertical=HR, decisions=()):
    return {p["key"]: p for p in build_proposals(observations, vertical, decisions)["proposals"]}


class BuildProposalsTest(unittest.TestCase):

    def test_terms_several_sites_write_are_proposed_with_their_evidence(self):
        got = proposals()
        self.assertIn("payroll", got)
        self.assertEqual(got["payroll"]["sites"], {"gusto.com": 20, "rippling.com": 40})
        self.assertEqual(got["payroll"]["kind"], "concept")

    def test_one_site_is_not_evidence(self):
        self.assertNotIn(variant_key("hiring regulation"), proposals())

    def test_brands_of_the_verticals_sites_are_not_vocabulary(self):
        got = proposals()
        self.assertNotIn("rippling ai", got)
        self.assertNotIn("gusto", got)

    def test_terms_other_verticals_write_as_much_are_generic(self):
        result = build_proposals(SITES, HR)
        self.assertNotIn("custome", {p["key"] for p in result["proposals"]})
        self.assertEqual(result["skipped"].get("on other verticals too"), 1)
        # One billing page naming payroll does not make payroll generic for HR, and
        # support.ordwaylabs.com is ordwaylabs.com: one site, not two.
        self.assertIn("payroll", {p["key"] for p in result["proposals"]})
        self.assertEqual(result["compared_with"], ["ordwaylabs.com"])

    def test_a_help_centre_is_the_same_site(self):
        billing = build_proposals(SITES, "b2b_saas_fintech")
        self.assertEqual(billing["sites"], ["ordwaylabs.com"])
        self.assertEqual(billing["proposals"], [])

    def test_spelling_variants_are_one_term(self):
        got = proposals()
        self.assertEqual(sorted(got[variant_key("compliance")]["forms"]),
                         ["compliance", "compliant", "compliantly"])

    def test_an_acronym_joins_its_expansion(self):
        got = proposals()
        eor = got[variant_key("employer of record")]
        self.assertEqual(eor["label"], "Employer of Record")
        self.assertIn("EOR", eor["forms"])
        self.assertEqual(eor["site_count"], 3)
        self.assertNotIn("eor", got)

    def test_a_two_letter_acronym_stays_itself(self):
        got = proposals()
        self.assertIn("hr", got)
        self.assertEqual(got["hr"]["label"], "HR")

    def test_glued_and_stuttered_text_is_not_a_term(self):
        got = proposals()
        self.assertNotIn(variant_key("payrollbenefits administrationhead"), got)
        self.assertNotIn(variant_key("compliance compliance"), got)
        self.assertEqual(build_proposals(SITES, HR)["skipped"].get("page noise"), 2)

    def test_a_capitalised_name_is_proposed_for_the_registry(self):
        got = proposals()
        self.assertEqual(got[variant_key("sap successfactors")]["kind"], "name")
        self.assertEqual(got["hr"]["kind"], "concept")
        self.assertTrue(vl._is_name(["Google Workspace"]))
        self.assertTrue(vl._clean("SAP SuccessFactors"), "One camel-cased word is a product's spelling")
        self.assertFalse(vl._is_name(["HR compliance"]))
        self.assertFalse(vl._is_name(["401(k)"]))

    def test_a_vendors_product_spelling_is_not_a_form_of_the_term(self):
        # gusto.com's "Payroll4Free" folds into payroll on its first seven letters.
        sites = [obs("gusto.com", HR, rec("payroll", ["payroll", "Payroll4Free"], pages=5)),
                 obs("deel.com", HR, rec("payroll", "payroll", pages=5))]
        self.assertEqual(proposals(sites)["payroll"]["forms"], ["payroll"])
        self.assertTrue(vl._clean("B2B payroll"), "An acronym with a digit is not a product")
        self.assertTrue(vl._clean("W-2 forms"))
        self.assertFalse(vl._clean("Payroll4Free"))

    def test_a_word_with_the_next_word_glued_on_is_not_a_form(self):
        sites = [obs("gusto.com", HR, rec("payroll", ["payroll", "payrollin", "payrolls"], pages=5)),
                 obs("deel.com", HR, rec("compliance", ["compliant", "compliantly"], pages=5),
                     rec("payroll", "payroll", pages=5)),
                 obs("rippling.com", HR, rec("compliance", "compliance", pages=5))]
        got = proposals(sites)
        self.assertEqual(sorted(got["payroll"]["forms"]), ["payroll", "payrolls"])
        self.assertEqual(sorted(got[variant_key("compliance")]["forms"]),
                         ["compliance", "compliant", "compliantly"])

    def test_a_proposal_says_when_it_is_already_a_seed_term(self):
        seeds = ["Payroll processing", "New hire onboarding", "Time and attendance tracking"]
        sites = [obs(d, HR, rec("payroll processing", "payroll processing", pages=3),
                     rec("onboarding", "onboarding", pages=3),
                     rec("time tracking", "time tracking", pages=3),
                     rec("background check", "background checks", pages=3))
                 for d in ("gusto.com", "deel.com")]
        got = {p["label"]: p for p in build_proposals(sites, HR, seed_terms=seeds)["proposals"]}
        self.assertEqual(got["payroll processing"]["seed"], "Payroll processing")
        self.assertEqual(got["onboarding"]["near_seeds"], ["New hire onboarding"])
        self.assertIsNone(got["onboarding"]["seed"])
        self.assertEqual(got["time tracking"]["near_seeds"], ["Time and attendance tracking"])
        self.assertEqual((got["background checks"]["seed"], got["background checks"]["near_seeds"]),
                         (None, []))
        # Proposed all the same: approving one is how a seed string becomes a concept.
        self.assertEqual(len(got), 4)

    def test_resolved_forms_are_not_proposed(self):
        self.assertNotIn("stripe", proposals())

    def test_decided_terms_are_not_proposed_again(self):
        decisions = [
            {"event_id": "1", "vertical_id": HR, "key": "payroll", "decision": "reject"},
            {"event_id": "2", "vertical_id": "cybersecurity", "key": "hr", "decision": "generic"},
            {"event_id": "3", "vertical_id": "cybersecurity", "key": variant_key("compliance"),
             "decision": "reject"},
        ]
        got = proposals(decisions=decisions)
        self.assertNotIn("payroll", got)
        self.assertNotIn("hr", got, "A term called generic anywhere is generic everywhere")
        self.assertIn(variant_key("compliance"), got, "A rejection belongs to its own vertical")

    def test_the_latest_observation_of_a_site_counts(self):
        older = obs("gusto.com", HR, rec("benefits", "benefits"))
        got = proposals([older] + SITES)
        self.assertNotIn("benefit", got)


class AddConceptTest(unittest.TestCase):

    def test_a_new_concept_gets_an_id_label_forms_and_definition(self):
        data = {"concepts": [{"id": "payroll", "prefLabel": "Pay Runs", "altLabels": []}]}
        self.assertTrue(add_concept(data, "Payroll", ["payroll", "Payroll", "payrolls"], "Paying employees."))
        new = data["concepts"][-1]
        self.assertEqual((new["id"], new["prefLabel"], new["definition"]), ("payroll-2", "Payroll", "Paying employees."))
        self.assertEqual(new["altLabels"], ["payrolls"])
        self.assertEqual(new["source"], "learned")
        self.assertEqual(data["alt_labels"]["Payroll"], ["payrolls"])

    def test_forms_another_concept_owns_are_left_with_it(self):
        data = {"concepts": [{"id": "eor", "prefLabel": "Employer of Record", "altLabels": ["EOR"]}]}
        add_concept(data, "Global Employment", ["EOR", "global employment"])
        self.assertEqual(data["concepts"][1]["altLabels"], [])
        self.assertEqual(data["concepts"][0]["altLabels"], ["EOR"])

    def test_approving_an_existing_concept_adds_forms_only(self):
        data = {"concepts": [{"id": "eor", "prefLabel": "Employer of Record", "altLabels": []}]}
        self.assertTrue(add_concept(data, "employer of record", ["EORs"]))
        self.assertEqual(len(data["concepts"]), 1)
        self.assertEqual(data["concepts"][0]["altLabels"], ["EORs"])
        self.assertFalse(add_concept(data, "Employer of Record", ["EORs"]), "Nothing new, nothing changed")


class BroaderTest(unittest.TestCase):

    def test_a_parent_is_named_by_any_of_its_labels(self):
        data = {"concepts": []}
        add_concept(data, "HR compliance", ["HR Compliance"])
        self.assertTrue(add_concept(data, "ACA compliance", [], broader="hr compliance"))
        self.assertEqual(data["concepts"][1]["broader"], "hr-compliance")
        self.assertFalse(add_concept(data, "ACA compliance", [], broader="HR Compliance"),
                         "Already there: nothing changed")

    def test_the_tree_never_becomes_a_cycle(self):
        data = {"concepts": []}
        add_concept(data, "HR software", [])
        add_concept(data, "Payroll", [], broader="HR software")
        self.assertFalse(add_concept(data, "HR software", [], broader="Payroll"))
        self.assertFalse(add_concept(data, "Payroll", [], broader="Payroll"))
        self.assertIsNone(data["concepts"][0]["broader"])


class _Review(unittest.TestCase):
    """An archive of the sites above, and a vertical with no concepts yet."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        archive = DirectoryArchive(os.path.join(self.tmp, "archive"))
        for i, o in enumerate(SITES):
            archive.put("registry/observations/%013d.json" % i, json.dumps(o).encode("utf-8"))
        verticals = os.path.join(self.tmp, "verticals")
        os.makedirs(verticals)
        with open(os.path.join(verticals, HR + ".json"), "w", encoding="utf-8") as f:
            json.dump({"vertical_id": HR, "display_name": "HR", "concepts": []}, f)
        env = patch.dict(os.environ, {"ONTOLEAP_VERTICALS_DIR": verticals})
        env.start()
        self.addCleanup(env.stop)
        self.published = []
        pub = patch("vertical_store.publish", side_effect=lambda vid, data: self.published.append((vid, data)))
        pub.start()
        self.addCleanup(pub.stop)
        self.registry = RegistryStore(archive=archive)
        self.store = VocabularyStore(archive=archive, registry_store=self.registry)
        self.profile = os.path.join(verticals, HR + ".json")

    def concepts(self):
        with open(self.profile, encoding="utf-8") as f:
            return {c["prefLabel"]: c for c in json.load(f)["concepts"]}


class ReviewTest(_Review):

    def test_approving_a_concept_writes_it_to_the_vertical_and_mirrors_it(self):
        key = variant_key("employer of record")
        result = self.store.decide(HR, key, "approve", definition="Hires on a client's behalf.")
        self.assertEqual(result["applied"]["concepts_written"], ["Employer of Record"])
        concept = self.concepts()["Employer of Record"]
        self.assertIn("EOR", concept["altLabels"])
        self.assertEqual(concept["definition"], "Hires on a client's behalf.")
        self.assertEqual(self.published[-1][0], HR)
        self.assertNotIn(key, {p["key"] for p in self.store.proposals(HR)["proposals"]})

    def test_a_concept_a_concurrent_write_dropped_comes_back(self):
        self.store.decide(HR, "payroll", "approve", label="Payroll")
        with open(self.profile, "w", encoding="utf-8") as f:
            json.dump({"vertical_id": HR, "concepts": []}, f)      # another writer won
        self.store.decide(HR, "hr", "approve", label="Human Resources")
        self.assertEqual(sorted(self.concepts()), ["Human Resources", "Payroll"])

    def test_the_reviewer_can_rename_and_reclassify(self):
        self.store.decide(HR, "hr", "approve", label="Human Resources", kind="concept")
        self.assertIn("HR", self.concepts()["Human Resources"]["altLabels"])

    def test_approving_a_name_creates_a_registry_entity(self):
        key = variant_key("sap successfactors")
        result = self.store.decide(HR, key, "approve", entity_kind="product")
        eid = result["applied"]["entity_created"]
        entity = self.registry.registry().entities[eid]
        self.assertEqual((entity.prefLabel, entity.kind, entity.status), ("SAP SuccessFactors", "product", "active"))
        self.assertEqual(self.concepts(), {}, "A name is not a concept")

    def test_reject_and_generic_are_remembered(self):
        self.store.decide(HR, "payroll", "reject")
        self.store.decide(HR, "hr", "generic")
        open_keys = {p["key"] for p in self.store.proposals(HR)["proposals"]}
        self.assertNotIn("payroll", open_keys)
        self.assertNotIn("hr", open_keys)
        self.assertEqual(self.concepts(), {})
        self.assertEqual(self.published, [])

    def test_a_concept_is_approved_under_its_parent(self):
        self.store.decide(HR, "payroll", "approve", label="Payroll")
        self.store.decide(HR, variant_key("employer of record"), "approve", broader="payroll")
        concepts = self.concepts()
        self.assertEqual(concepts["Employer of Record"]["broader"], concepts["Payroll"]["id"])

    def test_a_child_approved_before_its_parent_finds_it_later(self):
        result = self.store.decide(HR, variant_key("employer of record"), "approve", broader="Payroll")
        self.assertIsNone(self.concepts()["Employer of Record"]["broader"], "No parent yet: none invented")
        self.assertNotIn("Payroll", self.concepts())
        self.store.decide(HR, "payroll", "approve", label="Payroll")
        concepts = self.concepts()
        self.assertEqual(concepts["Employer of Record"]["broader"], concepts["Payroll"]["id"])
        self.assertEqual(result["applied"]["concepts_written"], ["Employer of Record"])

    def test_bad_decisions_are_refused(self):
        with self.assertRaises(ValueError):
            self.store.decide(HR, "payroll", "maybe")
        with self.assertRaises(ValueError):
            self.store.decide(HR, "not-a-proposal", "approve")
        with self.assertRaises(ValueError):
            self.store.decide(HR, variant_key("sap successfactors"), "approve", entity_kind="planet")
        with self.assertRaises(ValueError):
            self.store.decide("../etc", "payroll", "approve")
        self.assertEqual(self.store.decisions(), [])


class RoutesTest(_Review):
    """The same review, through the API router, without the service's auth middleware."""

    def setUp(self):
        super().setUp()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers import ontology_routes
        app = FastAPI()
        app.include_router(ontology_routes.router)
        self.client = TestClient(app)
        for p in (patch.object(ontology_routes, "default_vocabulary", return_value=self.store),
                  patch.object(ontology_routes, "_vertical_config_path",
                               side_effect=lambda vid: self.profile if vid == HR else None)):
            p.start()
            self.addCleanup(p.stop)

    def test_proposals_and_a_decision_over_http(self):
        listed = self.client.get("/api/ontology/vocabulary/proposals", params={"vertical_id": HR})
        self.assertEqual(listed.status_code, 200)
        keys = {p["key"] for p in listed.json()["proposals"]}
        self.assertIn("payroll", keys)
        decided = self.client.post("/api/ontology/vocabulary/decide", json={
            "vertical_id": HR, "key": "payroll", "decision": "approve", "label": "Payroll"})
        self.assertEqual(decided.status_code, 200, decided.text)
        self.assertEqual(decided.json()["applied"]["concepts_written"], ["Payroll"])

    def test_a_parent_can_be_given_over_http(self):
        body = {"vertical_id": HR, "decision": "approve"}
        self.client.post("/api/ontology/vocabulary/decide", json=dict(body, key="payroll", label="Payroll"))
        decided = self.client.post("/api/ontology/vocabulary/decide", json=dict(
            body, key=variant_key("employer of record"), broader="Payroll"))
        self.assertEqual(decided.status_code, 200, decided.text)
        self.assertEqual(decided.json()["decision"]["broader"], "Payroll")
        self.assertEqual(self.concepts()["Employer of Record"]["broader"], self.concepts()["Payroll"]["id"])

    def test_bad_requests_are_refused(self):
        get = self.client.get
        self.assertEqual(get("/api/ontology/vocabulary/proposals", params={"vertical_id": "nope"}).status_code, 404)
        self.assertEqual(get("/api/ontology/vocabulary/proposals", params={"vertical_id": "../x"}).status_code, 400)
        post = lambda body: self.client.post("/api/ontology/vocabulary/decide", json=body).status_code
        self.assertEqual(post({"vertical_id": HR, "key": "payroll", "decision": "maybe"}), 422)
        self.assertEqual(post({"vertical_id": HR, "key": "unknown", "decision": "reject"}), 400)


if __name__ == "__main__":
    unittest.main()
