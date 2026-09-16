"""
Buyer prompts from the ontology: provenance, ranking, and the curated-vs-cold gap.

Offline. Builds its own verticals directory, so nothing here depends on which profiles
happen to be on the machine.

Run:  python test_prompt_generator.py
"""

import json
import os
import shutil
import tempfile
import unittest

import prompt_generator as pg

# A curated vertical in miniature: a typed, defined concept tree with a single root.
CURATED = {
    "vertical_id": "billing",
    "display_name": "Subscription Billing & Revenue",
    "core_seed_concepts": ["Subscription Billing"],
    "known_compliance": ["ASC 606"],
    "known_integrations": ["NetSuite"],
    "concepts": [
        {"id": "revenue-operations", "prefLabel": "Revenue Operations", "kind": "domain",
         "definition": "Running the money side of a subscription business."},
        {"id": "billing-automation", "prefLabel": "Billing Automation", "kind": "domain",
         "definition": "Issuing invoices without manual work.", "broader": "revenue-operations"},
        {"id": "cash-application", "prefLabel": "Cash Application", "kind": "process",
         "definition": "Matching payments to invoices.", "broader": "billing-automation"},
        {"id": "ach-payments", "prefLabel": "ACH Payments", "kind": "feature",
         "definition": "Taking payment by bank transfer.", "broader": "billing-automation",
         "altLabels": ["ACH"]},
        {"id": "compliance-standards", "prefLabel": "Compliance Standards", "kind": "domain",
         "definition": "Rules a product is measured against.", "broader": "revenue-operations"},
        {"id": "asc-606", "prefLabel": "ASC 606", "kind": "standard",
         "definition": "The US revenue recognition standard.", "broader": "compliance-standards",
         "altLabels": ["ASC 606 Compliance"]},
        {"id": "usage-pricing", "prefLabel": "Usage-Based Pricing", "kind": "pricing",
         "definition": "Charging in proportion to use.", "broader": "revenue-operations"},
    ],
}

# What discovery mints for a site it has never seen: seed strings, nothing typed.
COLD = {
    "vertical_id": "cold",
    "display_name": "Cold Vertical & Nothing Else",
    "core_seed_concepts": ["Expense Management", "Spend Control"],
    "known_compliance": ["SOC 2"],
    "known_integrations": ["Xero"],
}

PROFILE = {
    "domain": "acme.com",
    "vertical_id": "billing",
    "known_replaces": ["Spreadsheets", "QuickBooks invoicing"],
    "known_competitors": ["Zuora", "Recurly"],
    "known_segments": ["Vertical SaaS", "An equipment lifecycle software company serving construction"],
    "known_industries": ["Fintech"],
    "icp_evidence": {
        "known_replaces": {
            "Spreadsheets": {"source_url": "https://acme.com/why", "quote": "replaces spreadsheets"},
        },
        "known_competitors": {
            "Zuora": {"source_url": "https://acme.com/vs-zuora", "quote": "the best Zuora alternative"},
        },
        "known_industries": {"Fintech": {"source_url": "https://acme.com/x", "quote": "fintech"}},
    },
}


class _WithVerticals(unittest.TestCase):
    """A verticals directory holding one curated vertical, one cold one, one profile."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.tmp, "buyers"), exist_ok=True)
        for spec in (CURATED, COLD):
            with open(os.path.join(self.tmp, "%s.json" % spec["vertical_id"]), "w") as fh:
                json.dump(spec, fh)
        with open(os.path.join(self.tmp, "buyers", "acme.com.json"), "w") as fh:
            json.dump(PROFILE, fh)
        self.saved = os.environ.get("ONTOLEAP_VERTICALS_DIR")
        self.saved_mirror = os.environ.get("ONTOLEAP_VERTICALS_MIRROR")
        os.environ["ONTOLEAP_VERTICALS_DIR"] = self.tmp
        os.environ.pop("ONTOLEAP_VERTICALS_MIRROR", None)

    def tearDown(self):
        for key, value in (("ONTOLEAP_VERTICALS_DIR", self.saved),
                           ("ONTOLEAP_VERTICALS_MIRROR", self.saved_mirror)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.tmp, ignore_errors=True)

    def generate(self, vertical="billing", domain="acme.com", limit=0):
        return pg.generate_prompts(vertical, domain=domain, limit=limit, root=self.tmp)

    def texts(self, ps):
        return [p.text for p in ps.prompts]


class PromptGeneratorTest(_WithVerticals):
    # -- provenance ---------------------------------------------------------

    def test_every_prompt_names_the_thing_it_came_from(self):
        for p in self.generate().prompts:
            self.assertTrue(p.source_field, p.text)
            self.assertTrue(p.source_value, p.text)
            self.assertIn(p.grounding, pg.GROUNDINGS, p.text)
            self.assertIn(p.stage, pg.STAGES, p.text)

    def test_a_proven_claim_carries_its_quote_and_an_unproven_one_does_not(self):
        by_value = {}
        for p in self.generate().prompts:
            by_value.setdefault(p.source_value, p)
        proven = by_value["Spreadsheets"]
        self.assertEqual(proven.grounding, "evidenced")
        self.assertEqual(proven.source_url, "https://acme.com/why")
        self.assertIn("spreadsheets", proven.quote)
        # Proposed by the model, never found on a page discovery read.
        unproven = by_value["QuickBooks invoicing"]
        self.assertEqual(unproven.grounding, "proposed")
        self.assertEqual(unproven.source_url, "")

    def test_evidence_outranks_a_bare_proposal(self):
        # Ranking itself: the same stage, sorted by how well the claim is backed.
        rank = pg._rank
        proven = pg.Prompt("a", "problem", "known_replaces", "x", "evidenced")
        proposed = pg.Prompt("b", "problem", "known_replaces", "y", "proposed")
        from_vocab = pg.Prompt("c", "problem", "concept:process", "z", "ontology")
        self.assertLess(rank(proven), rank(proposed))
        self.assertLess(rank(proposed), rank(from_vocab))
        # And it shows: the first thing a caller sees is something a page proved.
        self.assertEqual(self.generate().prompts[0].grounding, "evidenced")

    def test_a_stage_leads_with_its_best_backed_subject(self):
        problem = self.generate().by_stage("problem")
        proven = [p for p in problem if p.grounding == "evidenced"]
        self.assertTrue(proven)
        # Spreadsheets is the only replaced value with a quote, so it opens the stage.
        self.assertEqual(problem[0].source_value, "Spreadsheets")

    # -- what a buyer would actually type -----------------------------------

    def test_the_buyers_own_words_survive(self):
        texts = self.texts(self.generate())
        # A brand keeps its spelling; an ordinary word does not keep its label's capital.
        self.assertIn("how to move off QuickBooks invoicing", texts)
        self.assertIn("how to move off spreadsheets", texts)
        self.assertIn("Zuora alternatives", texts)
        # An acronym is not a word to be lowercased.
        self.assertTrue(any("ACH" in t for t in texts))

    def test_a_template_never_says_the_subjects_own_word_back_to_it(self):
        """"ASC 606 Compliance" must not become "ASC 606 compliance compliant ..."."""
        for text in self.texts(self.generate()):
            words = [w.lower() for w in text.split()]
            for left, right in zip(words, words[1:]):
                self.assertFalse(left[:6] == right[:6] and len(left) > 3,
                                 "%r repeats a word" % text)

    def test_a_head_to_head_needs_two_named_competitors(self):
        self.assertIn("Zuora vs Recurly", self.texts(self.generate()))

    def test_only_a_segment_short_enough_to_be_a_name_qualifies(self):
        texts = self.texts(self.generate())
        self.assertTrue(any(t.endswith("for Vertical SaaS") for t in texts))
        self.assertFalse(any("equipment lifecycle" in t.lower() for t in texts),
                         "a sentence describing a buyer is not a query anyone types")

    def test_a_lookup_is_never_qualified_by_a_segment(self):
        for text in self.texts(self.generate()):
            if " for " in text:
                self.assertTrue(pg._IS_SHOPPING.search(text),
                                "%r qualifies a prompt that is not shopping" % text)

    # -- ranking ------------------------------------------------------------

    def test_a_limit_spans_the_search_rather_than_filling_up_on_one_stage(self):
        ps = self.generate(limit=12)
        self.assertEqual(len(ps.prompts), 12)
        for stage in pg.STAGES:
            self.assertTrue(ps.by_stage(stage), "%s stage was crowded out" % stage)

    def test_consecutive_prompts_in_a_stage_are_about_different_things(self):
        ps = self.generate(limit=16)
        for stage in pg.STAGES:
            values = [p.source_value for p in ps.by_stage(stage)]
            for left, right in zip(values, values[1:]):
                self.assertNotEqual(left, right, "%s repeats %r" % (stage, left))

    def test_the_most_committed_stage_leads(self):
        self.assertEqual(self.generate(limit=4).prompts[0].stage, "problem")

    # -- degrading on a cold vertical ---------------------------------------

    def test_a_cold_vertical_still_produces_prompts(self):
        ps = pg.generate_prompts("cold", limit=0, root=self.tmp)
        self.assertTrue(ps.prompts)
        self.assertTrue(any("SOC 2" in p.text for p in ps.prompts))
        self.assertTrue(any("expense management" in p.text.lower() for p in ps.prompts))
        # With no concept layer the category noun falls back to the display name's topic.
        self.assertTrue(any("cold vertical" in p.text.lower() for p in ps.prompts))

    def test_a_cold_vertical_says_what_it_is_missing(self):
        missing = " ".join(pg.generate_prompts("cold", limit=0, root=self.tmp).coverage["missing"])
        self.assertIn("no concept layer", missing)
        self.assertIn("No competitors known", missing)
        self.assertIn("No domain given", missing)

    def test_the_gap_between_curated_and_cold_is_reported_not_hidden(self):
        curated = self.generate().coverage
        cold = pg.generate_prompts("cold", limit=0, root=self.tmp).coverage
        self.assertGreater(curated["generated"], cold["generated"])
        self.assertGreater(curated["ontology_depth"]["concepts"], 0)
        self.assertEqual(cold["ontology_depth"]["concepts"], 0)
        self.assertGreater(curated["by_grounding"]["evidenced"], 0)
        self.assertEqual(cold["by_grounding"]["evidenced"], 0)
        # A curated vertical with a full buyer profile has nothing to complain about.
        self.assertEqual(curated["missing"], [])

    def test_a_known_vertical_with_no_buyer_profile_says_so(self):
        ps = pg.generate_prompts("billing", domain="nobody.example", limit=0, root=self.tmp)
        self.assertFalse(ps.by_stage("comparison"), "no profile means no competitors")
        self.assertIn("No buyer profile stored for nobody.example",
                      " ".join(ps.coverage["missing"]))
        self.assertTrue(ps.prompts, "the vertical's own vocabulary still generates prompts")

    # -- shape --------------------------------------------------------------

    def test_a_grouping_concept_is_not_sold_as_a_product(self):
        """Billing Automation has children, so "best billing automation software" is wrong."""
        texts = self.texts(self.generate())
        self.assertIn("what is billing automation", texts)
        self.assertNotIn("best billing automation software", texts)

    def test_a_standard_asks_about_compliance_not_about_buying_one(self):
        texts = self.texts(self.generate())
        self.assertIn("ASC 606 compliant revenue operations software", texts)
        self.assertNotIn("best ASC 606 software", texts)

    def test_the_category_noun_comes_from_the_tree_not_the_display_name(self):
        self.assertTrue(any("revenue operations software" in t for t in self.texts(self.generate())))

    def test_it_serialises(self):
        payload = json.loads(json.dumps(self.generate(limit=5).to_dict()))
        self.assertEqual(len(payload["prompts"]), 5)
        self.assertIn("missing", payload["coverage"])
        self.assertIn("text", payload["prompts"][0])

    def test_nothing_is_generated_twice(self):
        texts = self.texts(self.generate())
        self.assertEqual(len(texts), len(set(t.lower() for t in texts)))


class BuyerPromptsRouteTest(_WithVerticals):
    """The same generator over HTTP, against the same fixtures.

    The route is mounted on a bare app rather than on api.app: this is about the endpoint,
    not about the middleware stack, and api.app would drag a GLiNER load in with it.
    """

    def client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from routers.system_routes import router
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_a_domain_alone_is_enough(self):
        """The profile records the vertical, so a caller need not know it."""
        r = self.client().get("/api/buyer-prompts", params={"domain": "https://www.acme.com/pricing"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["domain"], "acme.com")
        self.assertEqual(body["vertical_id"], "billing")
        self.assertTrue(body["prompts"])

    def test_a_vertical_alone_answers_without_the_buyer_half(self):
        r = self.client().get("/api/buyer-prompts", params={"vertical_id": "cold", "limit": 5})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["domain"], "")
        self.assertTrue(body["prompts"])
        self.assertTrue(body["coverage"]["missing"])

    def test_one_stage_returns_a_full_limit_of_that_stage(self):
        r = self.client().get("/api/buyer-prompts",
                              params={"domain": "acme.com", "stage": "comparison", "limit": 4})
        self.assertEqual(r.status_code, 200)
        prompts = r.json()["prompts"]
        self.assertEqual(len(prompts), 4, "a stage filter must not cost three quarters of the limit")
        self.assertTrue(all(p["stage"] == "comparison" for p in prompts))
        # Still spread, so the stage opens on different competitors rather than four
        # wordings of the first one.
        self.assertGreater(len({p["source_value"] for p in prompts}), 1)

    def test_a_prompt_arrives_with_its_evidence(self):
        r = self.client().get("/api/buyer-prompts", params={"domain": "acme.com", "stage": "problem"})
        proven = next(p for p in r.json()["prompts"] if p["grounding"] == "evidenced")
        self.assertTrue(proven["source_url"])
        self.assertTrue(proven["quote"])
        self.assertTrue(proven["source_field"])

    def test_the_arguments_it_refuses(self):
        client = self.client()
        for label, params in (
            ("neither a domain nor a vertical", {}),
            ("a stage that does not exist", {"domain": "acme.com", "stage": "nonsense"}),
            ("a vertical that does not exist", {"vertical_id": "no_such_vertical"}),
            ("a vertical_id reaching out of the directory", {"vertical_id": "../../etc/passwd"}),
            ("a domain with no host", {"domain": "http://"}),
            ("a site with no profile to name a vertical", {"domain": "never-seen.example"}),
        ):
            self.assertEqual(client.get("/api/buyer-prompts", params=params).status_code, 400, label)
        # Out of the declared range, so the model layer refuses it before the route runs.
        self.assertEqual(client.get("/api/buyer-prompts",
                                    params={"vertical_id": "billing", "limit": 9999}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
