"""
Buyer situations in the graph (BUYER_SITUATIONS_DESIGN.md phase 0).

A story's parts become facets that name concepts, registry entities and controlled
values. Names are found by exact key, never by similarity; a situation round-trips
through the store; and situations stay out of the crawl runs list_runs reports. No
network: a small vertical and registry stand in for the real ones.
"""

import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

import buyer_situations as bs
import graph_store
from entity_resolver import normalise_key


def concept(cid, label, *alts):
    return SimpleNamespace(id=cid, pref_label=label, alt_labels=list(alts))


CONCEPTS = [
    concept("proration", "Proration"),
    concept("revenue-recognition", "Revenue Recognition", "rev rec", "ASC 606 revenue"),
    concept("metered-billing", "Metered Billing", "usage-based billing"),
    concept("parent-customer", "Parent Customer", "parent-child billing"),
    # Two concepts sharing a form: the form must name neither.
    concept("invoicing", "Invoicing", "billing run"),
    concept("bill-run", "Bill Run", "billing run"),
]


def entity(eid, label, kind="product"):
    return SimpleNamespace(id=eid, prefLabel=label, kind=kind, uri="https://gainark.com/entity/" + eid)


class FakeRegistry:
    def __init__(self, *entities):
        self.index = {normalise_key(e.prefLabel): [e] for e in entities}

    def lookup(self, key):
        return self.index.get(key, [])


REGISTRY = FakeRegistry(entity("quickbooks", "QuickBooks"), entity("netsuite", "NetSuite"))


def vocab():
    return bs.Vocabulary("b2b_saas_fintech", CONCEPTS, REGISTRY)


def story(quotes=None, **parts):
    """A story whose quotes repeat its text, unless `quotes` gives a part another one."""
    quotes = quotes or {}
    return {"source_url": "https://ordway.example/case/acme", "voice": "customer",
            "parts": {k: {"text": v, "quote": quotes.get(k, "We said: %s." % v)}
                      for k, v in parts.items()}}


class ClassifyTest(unittest.TestCase):

    def test_roles_are_function_and_level(self):
        # Ordway's buyers (3 Oct 2026) and bamboohr.com's (4 Oct), one vocabulary for both.
        cases = {
            "David Sheen, CFO, Vestwell": "finance/c-level",
            "Fractional CFO for CaliberMind": "finance/c-level",
            "Liz Hanson, CMA Director of Accounting at HappyCo": "accounting/director",
            "Kari Lemke Controller, Yardstik": "accounting/director",
            "Conor O'Donoghue Head of Finance at Ocrolus": "finance/head",
            "Revenue Operations Specialist": "revops/staff",
            "Lisa Scian, VP of People and Culture at ProntoForms": "people/vp",
            "Josie Keucke, People and Culture Manager at Civtec": "people/manager",
            "an HR leader": "people/unspecified",
            "billing specialist": "unspecified/staff",
        }
        for text, expected in cases.items():
            self.assertEqual(bs.classify_role(text), expected, text)
        self.assertIsNone(bs.classify_role("the team"))

    def test_the_sign_off_beats_a_shortened_summary(self):
        s = bs.from_story(story(role="people", quotes={
            "role": "Josie Keucke, People and Culture Manager at Civtec"}), "acme.example", vocab())
        self.assertEqual([n.id for n in s.facets[0].names], ["people/manager"])

    def test_triggers(self):
        self.assertEqual(bs.classify_trigger("expanded into B2B and B2B2C"), "new-business-model")
        self.assertEqual(bs.classify_trigger("was migrating to NetSuite"), "erp-change")
        self.assertEqual(bs.classify_trigger("volume of changes to customer contracts"), "new-deal-types")
        self.assertEqual(bs.classify_trigger("adding more customers and revenue"), "growth-in-volume")
        self.assertEqual(bs.classify_trigger(
            "Today, Civtec has more than quadrupled its workforce and added branches."),
            "growth-in-volume")


class PainTypeTest(unittest.TestCase):
    """Quotes from Ordway's case studies, 3 Oct 2026."""

    def test_pains_by_what_they_cost(self):
        cases = {
            "We were tracking everything in Excel, which became unwieldy as we grew.":
                ["cannot-scale", "manual-effort"],
            "Imagine no longer needing two to three weeks to complete the monthly bill run.":
                ["slow-cycle"],
            "With Zuora, we found that it was hard to get support from them.":
                ["vendor-support-fit"],
            "The previous billing system was not able to automatically perform proration "
            "calculations for mid-period changes, resulting in lost revenue.":
                ["revenue-leakage", "missing-capability"],
            "Billing was slow and error-prone because so much of it was manual.":
                ["errors-accuracy", "slow-cycle", "manual-effort"],
            "Processes such as subscription management, monthly billing, and revenue "
            "recognition became more complicated in a B2B environment.":
                ["cannot-scale"],
            "Processes such as subscription management, monthly billing, and revenue "
            "recognition have all gotten more complicated in a B2B environment.":
                ["cannot-scale"],
            # bamboohr.com, 4 Oct 2026: an HR team's pain, in an HR team's words.
            "That was something I had to spend an entire whole day a month on, sorting "
            "through paper files.":
                ["slow-cycle", "manual-effort"],
        }
        for quote, expected in cases.items():
            self.assertEqual(bs.classify_pain(quote), expected, quote)

    def test_competitor_stories_in_enterprise_wording(self):
        # Customer stories on zuora.com, hibob.com and maxio.com, read 4 Oct 2026.
        cases = {
            "It simply wasn't sustainable. We couldn't hire finance staff fast enough and our "
            "customers demanded a better way.": ["cannot-scale"],
            "These are effective for one-time charges but are not capable of managing recurring "
            "revenue models.": ["missing-capability"],
            "With their old legacy billing system, it was a challenge to access customer info.":
                ["missing-capability"],
            "CTS's finance team needed to manage high-volume, repetitive billing work.":
                ["manual-effort"],
            "VaynerMedia managed their performance reviews using Google Docs, which was neither "
            "efficient nor effective.": ["manual-effort"],
            "Auror's People Team hadn't had positive experiences with HRIS in the past, finding "
            "them clunky and not user-friendly.": ["vendor-support-fit"],
            "Sendspark's 180 degree flip from not being able to charge customers to immediate "
            "revenue.": ["missing-capability"],
            "The existing system was unscalable.": ["cannot-scale"],
        }
        for quote, expected in cases.items():
            self.assertEqual(bs.classify_pain(quote), expected, quote)

    def test_a_pain_facet_carries_its_types_from_the_quote(self):
        s = bs.from_story(story(pain="manual effort for billing",
                                quotes={"pain": "The process required a lot of manual effort."}),
                          "ordway.example", vocab())
        self.assertEqual([(n.kind, n.id) for n in s.facets[0].names],
                         [("pain", "manual-effort"), ("legacy", "manual-process")])
        self.assertEqual(bs.report([s])["pains_unclassified"], [])


class ProposalTest(unittest.TestCase):

    def test_spans_are_terms_not_fragments(self):
        spans = bs.term_spans("a flexible billing foundation that can handle evolving pricing models",
                              "a flexible billing foundation that can handle evolving pricing models")
        self.assertIn("billing foundation", spans)
        self.assertIn("evolving pricing models", spans)
        self.assertNotIn("billing foundation that can", spans)
        self.assertFalse([s for s in spans if " " not in s], "single words are not terms")

    def test_a_span_must_be_in_the_quote(self):
        self.assertEqual(bs.term_spans("usage-based billing engine", "a system for current needs"), [])

    def test_proposals_go_to_the_queue_with_their_proof(self):
        from graph_archive import DirectoryArchive
        from synonym_queue import SynonymQueue
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, True)
        queue = SynonymQueue(DirectoryArchive(tmp))
        s = bs.from_story(story(need="a system to support evolving pricing models",
                                pain="tracking everything in Excel"), "ordway.example", vocab())
        calls = []

        def find(spans, concepts):
            calls.append(list(spans))
            hits = [SimpleNamespace(surface_form=sp, concept="Monetization Model", score=0.73,
                                    margin=0.098, runner_up="Dynamic Pricing")
                    for sp in spans if sp == "pricing models"]
            return hits

        proposals = bs.propose_concepts([s], "b2b_saas_fintech", CONCEPTS, queue=queue, find=find)
        self.assertEqual([(p["surface_form"], p["generated_canonical"]) for p in proposals],
                         [("pricing models", "Monetization Model")])
        queued = queue.load(force=True)
        self.assertEqual(len(queued), 1)
        entry = next(iter(queued.values()))
        self.assertEqual((entry["status"], entry["vertical_id"], entry["method"]),
                         ("pending", "b2b_saas_fintech", "situation"))
        self.assertEqual(entry["quote"], "We said: a system to support evolving pricing models.")

    def test_only_a_missing_capability_pain_is_proposed(self):
        grew = bs.from_story(story(pain="growth made billing hard", quotes={
            "pain": "As the company grew and added customers, billing was hard to scale."}),
            "acme.example", vocab())
        lacked = bs.from_story(story(pain="could not handle parent accounts", quotes={
            "pain": "The old system could not handle parent accounts."}), "acme.example", vocab())
        tried = []
        bs.propose_concepts([grew, lacked], "b2b_saas_fintech", CONCEPTS,
                            find=lambda spans, c: tried.extend(spans) or [])
        self.assertIn("parent accounts", tried)
        self.assertFalse([t for t in tried if "customers" in t], "a growth pain proposed a concept")

    def test_terms_come_from_the_quote_too(self):
        # Civtec, bamboohr.com, 4 Oct 2026: the summary said "time tracking", the quote
        # said "clock in and clock out".
        s = bs.from_story(story(
            need="a system for leave and time tracking",
            quotes={"need": "She wanted a system staff could use to apply for leave and "
                            "clock in and clock out from a single portal."}),
            "acme.example", vocab())
        tried = []
        bs.propose_concepts([s], "hr_payroll_benefits", CONCEPTS,
                            find=lambda spans, c: tried.extend(spans) or [])
        self.assertIn("clock out", tried)
        self.assertNotIn("time tracking", tried, "the quote never says it")

    def test_a_facet_already_naming_a_concept_is_not_proposed(self):
        s = bs.from_story(story(need="rev rec automation"), "ordway.example", vocab())
        self.assertEqual(bs.propose_concepts([s], "b2b_saas_fintech", CONCEPTS,
                                             find=lambda spans, c: 1 / 0), [])


class NamesTest(unittest.TestCase):

    def test_names_inside_a_phrase(self):
        names = vocab().names_in("lost revenue from delayed proration calculations")
        self.assertEqual([(n.kind, n.id) for n in names], [("concept", "proration")])

    def test_an_alt_label_names_its_concept(self):
        names = vocab().names_in("a flexible billing system for usage-based billing")
        self.assertEqual([n.id for n in names], ["metered-billing"])

    def test_the_longest_run_wins_and_runs_do_not_overlap(self):
        names = vocab().names_in("we needed rev rec and ASC 606 revenue reporting")
        self.assertEqual([n.id for n in names], ["revenue-recognition"])

    def test_a_shared_form_names_neither_concept(self):
        self.assertEqual(vocab().names_in("our monthly billing run took weeks"), [])

    def test_no_similarity(self):
        self.assertEqual(vocab().names_in("parent-child account hierarchies"), [])

    def test_registry_entities(self):
        names = vocab().names_in("spreadsheets and QuickBooks invoicing")
        self.assertIn(("entity", "quickbooks"), [(n.kind, n.id) for n in names])

    def test_a_longer_name_of_a_known_thing_is_not_a_candidate(self):
        v = vocab()
        text = "used QuickBooks Online and Recurly"
        self.assertEqual(bs.unresolved_names(text, v.names_in(text)), ["Recurly"])

    def test_a_function_acronym_is_not_a_company(self):
        text = "spreadsheets and paper for HR processes, then ADP for payroll"
        self.assertEqual(bs.unresolved_names(text, []), ["ADP"])
        self.assertEqual(bs.unresolved_names("a PLG motion and MS Word templates", []), ["MS Word"])
        s = bs.from_story(story(before="paper files and HR spreadsheets"), "acme.example", vocab())
        self.assertEqual({n.id for n in s.facets[0].names}, {"paper", "spreadsheets"})


class FromStoryTest(unittest.TestCase):

    def test_parts_become_facets(self):
        s = bs.from_story(story(
            company="a background-check platform", role="Controller",
            before="several spreadsheets and QuickBooks", trigger="growth and evolving pricing models",
            pain="tracking everything in Excel became unwieldy",
            need="a flexible billing system for usage-based billing"), "ordway.example", vocab())
        by = {f.predicate: f for f in s.facets}
        self.assertEqual(set(by), {"buyerIs", "buyerRole", "usedBefore", "triggeredBy", "sufferedFrom", "needed"})
        self.assertEqual([n.id for n in by["buyerRole"].names], ["accounting/director"])
        self.assertEqual({n.id for n in by["usedBefore"].names}, {"quickbooks", "spreadsheets"})
        self.assertEqual([n.id for n in by["triggeredBy"].names], ["new-business-model"])
        self.assertEqual({(n.kind, n.id) for n in by["sufferedFrom"].names},
                         {("pain", "cannot-scale"), ("pain", "manual-effort"), ("legacy", "spreadsheets")})
        self.assertEqual([n.id for n in by["needed"].names], ["metered-billing"])
        self.assertEqual(by["needed"].quote, "We said: a flexible billing system for usage-based billing.")

    def test_a_name_the_quote_does_not_say_is_not_linked(self):
        # Yardstik, 3 Oct 2026: the summary said usage-based billing, the quote did not.
        s = bs.from_story(story(
            need="a flexible billing system for usage-based billing",
            quotes={"need": "They wanted a system that met the current needs of the business."}),
            "ordway.example", vocab())
        self.assertEqual(s.facets[0].names, [])

    def test_the_trigger_is_read_from_the_quote(self):
        s = bs.from_story(story(
            trigger="growth and evolving pricing models",
            quotes={"trigger": "The process was not going to scale as the company grew."}),
            "ordway.example", vocab())
        self.assertEqual([n.id for n in s.facets[0].names], ["growth-in-volume"])

    def test_a_product_named_in_the_change_is_a_constraint(self):
        s = bs.from_story(story(before="QuickBooks Online", trigger="was migrating to NetSuite"),
                          "ordway.example", vocab())
        constraints = [f for f in s.facets if f.predicate == "constrainedBy"]
        self.assertEqual([n.id for f in constraints for n in f.names], ["netsuite"])

    def test_a_tool_already_used_is_not_a_constraint(self):
        s = bs.from_story(story(before="QuickBooks", pain="QuickBooks could not handle contracts"),
                          "ordway.example", vocab())
        self.assertFalse([f for f in s.facets if f.predicate == "constrainedBy"])

    def test_the_report_lists_what_did_not_resolve(self):
        s = bs.from_story(story(before="used Recurly", pain="support was slow",
                                need="parent-child account hierarchies"), "ordway.example", vocab())
        r = bs.report([s])
        self.assertEqual(r["registry_candidates"], ["Recurly"])
        self.assertEqual(r["pains_without_concept"], ["support was slow"])
        self.assertEqual(r["needs_without_concept"], ["parent-child account hierarchies"])


class StoreTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "g.sqlite")

    def situation(self):
        return bs.from_story(story(role="CFO", before="QuickBooks and Recurly",
                                   pain="proration was manual", need="rev rec"),
                             "ordway.example", vocab())

    def test_round_trip(self):
        original = self.situation()
        back = bs.from_graph(bs.to_graph([original]))
        self.assertEqual(len(back), 1)
        def shape(s):
            return sorted((f.predicate, f.text, f.quote, tuple(sorted((n.kind, n.id) for n in f.names)))
                          for f in s.facets)
        self.assertEqual(shape(back[0]), shape(original))

    def test_each_facet_carries_its_proof(self):
        g = bs.to_graph([self.situation()])
        from rdflib.namespace import PROV
        quotes = set(str(o) for o in g.objects(None, PROV.value))
        self.assertIn("We said: rev rec.", quotes)

    def test_stored_situations_are_not_crawl_runs(self):
        gid = bs.persist([self.situation()], "ordway.example", "b2b_saas_fintech", path=self.db)
        self.assertNotIn(gid, [r["graph_id"] for r in graph_store.list_runs(path=self.db)])
        self.assertEqual(len(bs.latest("ordway.example", path=self.db)), 1)
        self.assertEqual(bs.latest("other.example", path=self.db), [])


if __name__ == "__main__":
    unittest.main()
