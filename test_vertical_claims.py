"""
Claims read in the vertical's own vocabulary, and stored with their proof.

The cases come from the 18 Sep 2026 crawls of gusto.com, rippling.com and deel.com: 378
HR pages read for the billing lists in constants.py, which found "Overage Pricing" on
Gusto and not one claim about payroll. Offline.

Run:  python test_vertical_claims.py
"""

import os
import tempfile
import unittest
from unittest.mock import patch

from rdflib import Graph, Literal, Namespace, RDF, URIRef
from rdflib.namespace import PROV

import graph_store as gs
import page_graph
import site_graph
from concept_roles import claim_vocabulary
from entity_registry import fold, load_seed
from entity_resolver import normalise_key, resolve_site
from models import IndustryConcept, IndustryOntologyModel, KGEdge, KGNode
from ontology_schema import concept_uri

VERTICAL = "test_vertical"


def hr(concepts=(), compliance=(), integrations=()):
    """The reviewed HR tree in miniature: a root, a branch, and what sits under them."""
    return IndustryOntologyModel(
        vertical_id=VERTICAL, display_name="HR",
        known_compliance=list(compliance), known_integrations=list(integrations),
        concepts=[
            IndustryConcept(id="hr-software", pref_label="HR software",
                            alt_labels=["HR platform"]),
            IndustryConcept(id="payroll", pref_label="Payroll", broader="hr-software",
                            alt_labels=["payroll processing"]),
            IndustryConcept(id="tax-filing", pref_label="Tax filing", broader="payroll",
                            alt_labels=["automated tax filings"]),
            IndustryConcept(id="hr-compliance", pref_label="HR compliance",
                            broader="hr-software"),
            IndustryConcept(id="worker-misclassification", pref_label="Worker misclassification",
                            broader="hr-compliance"),
        ] + list(concepts))


def edges(text, onto, url="https://gusto.com/product/payroll", nodes=()):
    with patch.object(page_graph, "load_industry_ontology", return_value=onto):
        found = page_graph._extract_semantic_edges(text, "Gusto", list(nodes), url, VERTICAL)
    return {(e.predicate, e.target) for e in found}


class VerticalClaimsTest(unittest.TestCase):

    def test_an_hr_site_is_read_for_hr_claims(self):
        found = edges("Gusto runs payroll processing for small businesses, "
                      "with automated tax filings in every state.", hr())
        self.assertIn(("hasFeature", "Payroll"), found)
        self.assertIn(("hasFeature", "Tax filing"), found)

    def test_the_billing_lists_are_not_read_on_a_vertical_with_concepts(self):
        # Gusto's plans page, as the 18 Sep crawl read it.
        found = edges("Gusto pricing uses tiered plans, with overage pricing for "
                      "contractors and usage-based pricing on add-ons.", hr())
        self.assertFalse([f for f in found if f[0] == "supportsPricingModel"], found)
        # The same page read for payroll still finds it.
        self.assertIn(("hasFeature", "Payroll"),
                      edges("Gusto pricing includes full-service payroll.", hr()))

    def test_the_root_and_a_risk_are_not_claims(self):
        found = edges("Gusto is HR software that protects you from worker "
                      "misclassification.", hr())
        self.assertNotIn(("hasFeature", "HR software"), found)
        self.assertFalse([f for f in found if f[1] == "Worker misclassification"], found)
        # A real claim in the same shape is still found, so the test is not vacuous.
        self.assertIn(("hasFeature", "Payroll"),
                      edges("Gusto is HR software that runs payroll.", hr()))

    def test_a_seed_written_as_a_description_is_read_as_the_name(self):
        onto = hr(compliance=["FLSA (Fair Labor Standards Act)"],
                  integrations=["Accounting Software (e.g., QuickBooks, Xero)",
                                "401(k) Providers"])
        found = edges("Gusto keeps overtime compliant with the Fair Labor Standards Act.", onto)
        self.assertIn(("compliesWith", "FLSA"), found)
        found = edges("Gusto integrates with QuickBooks, Xero and your 401(k) provider.", onto)
        for target in ("QuickBooks", "Xero", "401(k) Provider"):
            self.assertIn(("integratesWith", target), found)

    def test_naming_a_rule_is_not_complying_with_it(self):
        onto = hr(compliance=["FLSA (Fair Labor Standards Act)"])
        self.assertFalse([f for f in edges("Gusto explains the FLSA overtime rules for "
                                           "salaried staff.", onto) if f[0] == "compliesWith"])
        self.assertIn(("compliesWith", "FLSA"),
                      edges("Gusto helps you comply with FLSA overtime rules.", onto))

    def test_a_question_claims_nothing(self):
        # gusto.com's EOR page FAQ, 19 Sep 2026.
        onto = hr(concepts=[IndustryConcept(id="peo", pref_label="PEO", broader="hr-software")])
        self.assertFalse(edges("FAQs – What's the difference between an EOR and PEO?", onto))
        self.assertIn(("hasFeature", "PEO"),
                      edges("Get HR, payroll and benefits with Gusto's PEO services.", onto))

    def test_a_form_two_concepts_share_proves_neither(self):
        onto = hr(concepts=[
            IndustryConcept(id="benefits-admin", pref_label="Benefits administration",
                            broader="hr-software", alt_labels=["benefits"]),
            IndustryConcept(id="health-benefits", pref_label="Health benefits",
                            broader="hr-software", alt_labels=["benefits"])])
        found = edges("Gusto offers benefits to every employee.", onto)
        self.assertFalse([f for f in found if f[0] == "hasFeature"], found)
        self.assertIn(("hasFeature", "Benefits administration"),
                      edges("Gusto handles benefits administration.", onto))

    def test_a_claim_lands_on_the_concept_uri(self):
        onto = hr()
        with patch.object(page_graph, "load_industry_ontology", return_value=onto):
            raw = page_graph._extract_semantic_edges(
                "Gusto runs payroll for small businesses.", "Gusto", [],
                "https://gusto.com/product/payroll", VERTICAL)
        res = resolve_site([], raw, "gusto.com", VERTICAL,
                           fold(load_seed(), [], normalise_key), onto)
        payroll = [e for e in res.edges if e.predicate == "hasFeature"]
        self.assertEqual([e.target_id for e in payroll], [concept_uri(VERTICAL, "payroll")])

    def test_the_vocabulary_holds_one_target_per_law(self):
        vocab = claim_vocabulary(hr(compliance=["FLSA (Fair Labor Standards Act)"]))
        flsa = [forms for target, forms in vocab["compliesWith"] if target == "FLSA"]
        self.assertEqual(flsa, [["Fair Labor Standards Act", "FLSA"]])
        self.assertNotIn("Fair Labor Standards Act",
                         [target for target, _ in vocab["compliesWith"]])


EX = Namespace("https://gainark.com/kg/")


def _claim(sentence, url, target="Payroll", target_id=None):
    return KGEdge(id="e", source="Gusto", target=target, predicate="hasFeature",
                  source_id="https://gusto.com/brand",
                  target_id=target_id or concept_uri(VERTICAL, "payroll"),
                  provenance_sentence=sentence, source_url=url)


BRAND = KGNode(id="https://gusto.com/brand", canonical_name="Gusto", entity_type="Organization")


def _stored(edge):
    g = Graph()
    g.parse(data=site_graph._build_site_turtle("gusto.com", BRAND, [BRAND], [edge]),
            format="turtle")
    return g


class ClaimProofTest(unittest.TestCase):

    def test_a_stored_claim_keeps_its_sentence_and_page(self):
        g = _stored(_claim("Gusto runs payroll in all 50 states.", "https://gusto.com/payroll"))
        claims = list(g.subjects(RDF.type, RDF.Statement))
        self.assertEqual(len(claims), 1)
        claim = claims[0]
        self.assertEqual(g.value(claim, RDF.subject), URIRef("https://gusto.com/brand"))
        self.assertEqual(g.value(claim, RDF.predicate), EX.hasFeature)
        self.assertEqual(g.value(claim, RDF.object), URIRef(concept_uri(VERTICAL, "payroll")))
        self.assertEqual(g.value(claim, PROV.value), Literal("Gusto runs payroll in all 50 states."))
        self.assertEqual(g.value(claim, PROV.wasDerivedFrom), URIRef("https://gusto.com/payroll"))
        # The claim itself is still there as a plain triple, for every existing query.
        self.assertIn((URIRef("https://gusto.com/brand"), EX.hasFeature,
                       URIRef(concept_uri(VERTICAL, "payroll"))), g)

    def test_the_same_claim_from_the_same_page_is_one_node_in_every_run(self):
        first = _stored(_claim("Gusto runs payroll.", "https://gusto.com/payroll"))
        reworded = _stored(_claim("Gusto runs payroll, fast.", "https://gusto.com/payroll"))
        claims = set(first.subjects(RDF.type, RDF.Statement))
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims, set(reworded.subjects(RDF.type, RDF.Statement)))

    def test_proof_moving_to_another_page_is_not_a_changed_claim(self):
        fd, path = tempfile.mkstemp(suffix=".sqlite")
        os.close(fd)
        os.remove(path)
        try:
            ids = []
            for n, url in enumerate(("https://gusto.com/payroll", "https://gusto.com/features")):
                gid = "urn:run:%d" % n
                stored = _stored(_claim("Gusto runs payroll.", url))
                # Two different claim nodes, so the diff has something to leave out.
                self.assertEqual(len(list(stored.subjects(RDF.type, RDF.Statement))), 1)
                gs.persist_graph(stored, gid, kind="run", domain="gusto.com", path=path)
                ids.append(gid)
            d = gs.diff_runs(ids[0], ids[1], path=path)
            self.assertEqual(d, {"added": [], "removed": []})
        finally:
            if os.path.exists(path):
                os.remove(path)

    def test_a_quote_stays_with_its_own_page_when_edges_merge(self):
        raw = [KGEdge(id="1", source="Gusto", target="Stripe", predicate="integratesWith",
                      provenance_sentence="Gusto syncs with Stripe.",
                      source_url="https://gusto.com/a"),
               KGEdge(id="2", source="Gusto", target="Stripe", predicate="integratesWith",
                      provenance_sentence="Gusto syncs payouts with Stripe every night.",
                      source_url="https://gusto.com/b")]
        res = resolve_site([], raw, "gusto.com", VERTICAL, fold(load_seed(), [], normalise_key))
        (edge,) = [e for e in res.edges if e.predicate == "integratesWith"]
        self.assertEqual((edge.provenance_sentence, edge.source_url),
                         ("Gusto syncs payouts with Stripe every night.", "https://gusto.com/b"))


if __name__ == "__main__":
    unittest.main()
