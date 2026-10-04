"""
The competitor matrix: concepts by companies, each cell a claim with its proof, a
mention, or an honest absence. Built from run graphs written the way site_graph writes
them, in a temporary store. No network.
"""

import json
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from rdflib import RDF, Graph, Literal, URIRef
from rdflib.namespace import PROV

import competitor_matrix as cm
import graph_store

V = "b2b_saas_fintech"
BASE = "https://gainark.com/ontoleap/ontology/%s/concept/" % V
EX = "https://gainark.com/kg/"
CONCEPTS = [SimpleNamespace(id=i, pref_label=l) for i, l in
            (("dunning", "Dunning"), ("proration", "Proration"), ("revenue-recognition", "Revenue Recognition"),
             ("multi-entity", "Multi-Entity"))]


def run_graph(domain, claims, mentions=()):
    """A run graph shaped like site_graph._build_site_turtle's: claims reified with their
    sentence and page; a mention is a concept in the graph no claim of the brand names."""
    g = Graph()
    brand = URIRef("https://gainark.com/ontoleap/entity/%s" % domain.split(".")[0])
    g.add((brand, RDF.type, URIRef("http://schema.org/SoftwareApplication")))
    for i, (concept, predicate, quote) in enumerate(claims):
        obj = URIRef(BASE + concept)
        g.add((brand, URIRef(EX + predicate), obj))
        st = URIRef("https://%s/claim/%d" % (domain, i))
        g.add((st, RDF.type, RDF.Statement))
        g.add((st, RDF.subject, brand))
        g.add((st, RDF.predicate, URIRef(EX + predicate)))
        g.add((st, RDF.object, obj))
        g.add((st, PROV.value, Literal(quote)))
        g.add((st, PROV.wasDerivedFrom, URIRef("https://%s/page" % domain)))
    other = URIRef("https://gainark.com/ontoleap/entity/stripe")
    for concept in mentions:
        g.add((other, URIRef(EX + "hasFeature"), URIRef(BASE + concept)))
    return g


class MatrixTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "g.sqlite")
        for patch in (mock.patch("industry_ontology.load_industry_ontology",
                                 return_value=SimpleNamespace(concepts=CONCEPTS)),
                      mock.patch.object(cm, "_company_columns", return_value=[
                          {"name": "ordway.example", "domain": "ordway.example", "role": "customer"},
                          {"name": "Zuora", "domain": "zuora.example", "role": "competitor"},
                          {"name": "Recurly", "domain": "recurly.example", "role": "competitor"}])):
            patch.start()
            self.addCleanup(patch.stop)
        self.store("ordway.example", run_graph("ordway.example", [
            ("dunning", "automates", "Ordway automates dunning."),
            ("proration", "hasFeature", "Ordway prorates mid-cycle changes.")]))
        self.store("zuora.example", run_graph("zuora.example", [
            ("dunning", "automates", "Zuora runs dunning."),
            ("revenue-recognition", "hasFeature", "Zuora Revenue recognises revenue.")],
            mentions=["multi-entity"]))
        # recurly.example: no run at all.

    def store(self, domain, g):
        graph_store.persist_graph(g, "https://%s/audit/20261004T000000Z" % domain, kind="run",
                                  domain=domain, vertical_id=V, path=self.db, archive_write=False,
                                  metadata=json.dumps({"pages_crawled": 25}))

    def rows(self):
        m = cm.matrix("ordway.example", V, path=self.db)
        return m, {r["label"]: r for r in m["rows"]}

    def test_each_cell_is_its_strongest_state(self):
        m, rows = self.rows()
        self.assertEqual(rows["Dunning"]["cells"]["ordway.example"]["state"], "claimed")
        self.assertEqual(rows["Dunning"]["cells"]["ordway.example"]["quote"], "Ordway automates dunning.")
        self.assertEqual(rows["Multi-Entity"]["cells"]["zuora.example"]["state"], "mentioned")
        self.assertEqual(rows["Proration"]["cells"]["zuora.example"],
                         {"state": "not_found", "pages_read": 25,
                          "run": "https://zuora.example/audit/20261004T000000Z"})

    def test_an_unread_competitor_is_not_read_never_not_found(self):
        m, rows = self.rows()
        self.assertEqual({r["cells"]["recurly.example"]["state"] for r in rows.values()}, {"not_read"})
        self.assertEqual(m["coverage"]["recurly.example"]["not_read"], 4)
        self.assertEqual(m["coverage"]["recurly.example"]["determinate_share"], 0.0)

    def test_verdicts(self):
        m, rows = self.rows()
        self.assertEqual({k: r["verdict"] for k, r in rows.items()},
                         {"Dunning": "both", "Proration": "customer_only",
                          "Revenue Recognition": "competitor_only", "Multi-Entity": "neither"})
        self.assertEqual(m["verdicts"], {"customer_only": 1, "competitor_only": 1, "both": 1, "neither": 1})

    def test_determinate_share(self):
        m, _ = self.rows()
        self.assertEqual(m["coverage"]["ordway.example"]["determinate_share"], 0.5)
        self.assertEqual(m["coverage"]["zuora.example"]["determinate_share"], 0.75)

    def test_one_concept(self):
        m = cm.matrix("ordway.example", V, concept="proration", path=self.db)
        self.assertEqual([r["label"] for r in m["rows"]], ["Proration"])

    def test_a_claim_by_someone_else_is_not_the_companys(self):
        g = run_graph("zuora.example", [], mentions=["dunning"])
        self.assertEqual(cm.read_run(g)["claims"], {})
        self.assertEqual(cm.read_run(g)["mentioned"], {BASE + "dunning"})


class RunChoiceTest(unittest.TestCase):
    """Production, 4 Oct 2026: a 5-page alignment run newer than Ordway's full crawl became
    the matrix's Ordway column, and the coverage date was the instance's startup."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "g.sqlite")

    def store(self, stamp, pages, claims):
        graph_store.persist_graph(
            run_graph("ordway.example", claims), "https://ordway.example/audit/%s" % stamp,
            kind="run", domain="ordway.example", vertical_id=V, path=self.db, archive_write=False,
            metadata=json.dumps({"pages_crawled": pages}))

    def test_a_newer_small_run_does_not_replace_a_full_crawl(self):
        self.store("20261002T090000Z", 25, [("dunning", "automates", "Full crawl.")])
        self.store("20261003T143203Z", 5, [("proration", "hasFeature", "Small run.")])
        run = cm.usable_run("ordway.example", path=self.db)
        self.assertEqual((run["graph_id"].rsplit("/", 1)[-1], run["below_budget"]),
                         ("20261002T090000Z", False))

    def test_with_only_small_runs_the_newest_is_used_and_marked(self):
        self.store("20261002T090000Z", 4, [("dunning", "automates", "Older.")])
        self.store("20261003T143203Z", 5, [("proration", "hasFeature", "Newer.")])
        run = cm.usable_run("ordway.example", path=self.db)
        self.assertEqual((run["graph_id"].rsplit("/", 1)[-1], run["below_budget"]),
                         ("20261003T143203Z", True))

    def test_coverage_reports_when_the_run_was_crawled(self):
        self.store("20261003T143203Z", 25, [("dunning", "automates", "Crawl.")])
        with mock.patch("industry_ontology.load_industry_ontology",
                        return_value=SimpleNamespace(concepts=CONCEPTS)), \
                mock.patch.object(cm, "_company_columns", return_value=[
                    {"name": "ordway.example", "domain": "ordway.example", "role": "customer"}]):
            cov = cm.matrix("ordway.example", V, path=self.db)["coverage"]["ordway.example"]
        self.assertEqual(cov["crawled_at"], "2026-10-03T14:32:03Z")
        self.assertEqual((cov["pages_read"], cov["below_budget"]), (25, False))
        self.assertNotIn("created_at", cov)

    def test_crawled_at(self):
        self.assertEqual(cm.crawled_at("https://maxio.com/audit/20261004T115523Z"), "2026-10-04T11:55:23Z")
        self.assertIsNone(cm.crawled_at("https://maxio.com/situations/x"))


class RouteTest(unittest.TestCase):

    def test_a_site_with_no_vertical_is_told_so(self):
        from fastapi.testclient import TestClient
        from api import app
        with mock.patch("buyer_profiles.load", return_value=None):
            r = TestClient(app).get("/api/competitor-matrix", params={"domain": "nosuch.example"})
        self.assertEqual(r.status_code, 422)
        self.assertIn("run discovery", r.json()["detail"])

    def test_a_domain_must_be_a_host(self):
        from fastapi.testclient import TestClient
        from api import app
        r = TestClient(app).get("/api/competitor-matrix", params={"domain": "not a host!"})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
