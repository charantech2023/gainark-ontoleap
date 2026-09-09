"""
Entity grounding: the curated Q-IDs stay authoritative, the rest get asked about.

constants.WIKIDATA_KB holds 40 hand-verified Q-IDs. That is good coverage of compliance
standards, which are a closed set, and poor coverage of the small private B2B companies
this platform actually audits - so most phrases came back ungrounded, indistinguishable
from phrases that genuinely have no external identity.

entity_grounding keeps the dict as the fast path and adds live resolution behind it. The
constraint that shapes the whole module is latency: the call sites are tight loops inside
a synchronous graph build, and a network call per miss would turn one build into minutes
of sequential timeouts. So lookups never touch the network and prefetch() is the only
thing that does.

These tests hold that line. They cover precedence, the pure-lookup promise, negative
caching, the budget, and the guarantee that an unreachable Wikidata costs sameAs links
rather than the audit.

Offline: a fake resolver throughout. No network, no GLiNER.
"""

import asyncio

import entity_grounding as eg
from constants import WIKIDATA_KB


# ---------------------------------------------------------------------------
# Doubles
# ---------------------------------------------------------------------------

class FakeResolver:
    """Stands in for remediation.resolve_wikidata, and counts what it was asked."""

    def __init__(self, answers=None, fail=False):
        self.answers = answers or {}
        self.fail = fail
        self.asked = []

    async def __call__(self, phrase):
        self.asked.append(phrase)
        if self.fail:
            raise IOError("wikidata unreachable")
        qid = self.answers.get(phrase)
        if not qid:
            return None
        return {"id": qid, "name": phrase, "description": "billing software",
                "sameAs": "https://www.wikidata.org/wiki/%s" % qid,
                "concepturi": "http://www.wikidata.org/entity/%s" % qid}


def clear_cache():
    eg._live_cache.clear()


# These tests are about the module's logic, not its configuration, so they pin the
# live-lookup switch on regardless of the environment they run in. Test [8] owns the
# disabled case and sets it explicitly.
eg.LIVE_ENABLED = True


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_curated_entries_answer_without_asking():
    print("\n[1] The static KB answers on its own ...")
    clear_cache()
    resolver = FakeResolver({"soc 2": "Q999"})

    assert eg.ground_url("SOC 2") == WIKIDATA_KB["soc 2"]
    assert eg.ground_id("SOC 2") == "Q105822363"
    assert eg.is_grounded("SOC 2")
    stats = eg.prefetch(["SOC 2", "GDPR", "Stripe"], resolver=resolver)

    print("    resolver asked: %s | prefetch: %s" % (resolver.asked, stats))
    assert resolver.asked == [], "A curated phrase was sent to the network."
    assert stats["requested"] == 0, stats
    print("  PASS")


def test_a_curated_answer_is_never_overridden():
    print("\n[2] A fetched answer does not overrule a hand-verified one ...")
    clear_cache()
    # The resolver would happily return something else for a phrase the KB already
    # places. Those 40 entries were verified against the Q-ID by hand, which is a
    # stronger claim than the resolver's description heuristics can make.
    eg._live_cache["soc 2"] = "https://www.wikidata.org/wiki/Q000000"

    print("    curated %s | cache holds %s"
          % (eg.ground_id("SOC 2"), eg._live_cache["soc 2"].rsplit("/", 1)[-1]))
    assert eg.ground_url("SOC 2") == WIKIDATA_KB["soc 2"], "Curated Q-ID was overridden."
    clear_cache()
    print("  PASS")


def test_unknown_phrase_is_grounded_after_prefetch():
    print("\n[3] A phrase outside the KB gets an identity ...")
    clear_cache()
    resolver = FakeResolver({"ordway": "Q42424242"})

    assert eg.ground_url("Ordway") is None, "Grounded before anyone asked."
    stats = eg.prefetch(["Ordway"], resolver=resolver)

    print("    asked %s | resolved %d | now -> %s"
          % (resolver.asked, stats["resolved"], eg.ground_id("Ordway")))
    assert resolver.asked == ["ordway"], resolver.asked
    assert eg.ground_id("Ordway") == "Q42424242", "Live result was not cached."
    assert eg.is_grounded("ordway  "), "Normalisation is not applied on read."
    clear_cache()
    print("  PASS")


def test_lookups_never_touch_the_network():
    print("\n[4] Lookups are pure; only prefetch reaches out ...")
    clear_cache()
    resolver = FakeResolver({"ordway": "Q1"})

    # The real call sites are loops inside a synchronous graph build. If a miss could
    # reach the network, one build would cost a 4s timeout per unknown phrase.
    for _ in range(50):
        eg.ground_url("Never Heard Of It")
        eg.ground_id("Nor This")
        eg.is_grounded("Nor This Either")

    print("    150 lookups on unknown phrases | resolver calls: %d" % len(resolver.asked))
    assert resolver.asked == [], "A lookup reached the network."
    clear_cache()
    print("  PASS")


def test_unresolvable_phrases_are_asked_about_once():
    print("\n[5] A phrase Wikidata cannot ground is asked once per process ...")
    clear_cache()
    resolver = FakeResolver({})  # resolves nothing

    eg.prefetch(["Fictional Vendor"], resolver=resolver)
    eg.prefetch(["Fictional Vendor"], resolver=resolver)
    stats = eg.prefetch(["Fictional Vendor"], resolver=resolver)

    print("    three prefetches -> %d resolver call(s) | last: %s"
          % (len(resolver.asked), stats))
    assert len(resolver.asked) == 1, (
        "The negative was not cached; every graph build would re-ask.")
    assert eg.ground_url("Fictional Vendor") is None
    assert stats["requested"] == 0, stats
    clear_cache()
    print("  PASS")


def test_budget_bounds_one_run():
    print("\n[6] A run cannot spend unbounded time grounding ...")
    clear_cache()
    resolver = FakeResolver({})
    saved = eg.LIVE_BUDGET
    eg.LIVE_BUDGET = 5
    try:
        stats = eg.prefetch(["vendor %d" % i for i in range(20)], resolver=resolver)
    finally:
        eg.LIVE_BUDGET = saved

    print("    20 unknown phrases, budget 5 -> asked %d, skipped %d"
          % (len(resolver.asked), stats["skipped_over_budget"]))
    assert len(resolver.asked) == 5, resolver.asked
    assert stats["skipped_over_budget"] == 15, stats
    clear_cache()
    print("  PASS")


def test_unreachable_wikidata_costs_links_not_the_audit():
    print("\n[7] A failing resolver degrades to the static KB ...")
    clear_cache()
    resolver = FakeResolver({}, fail=True)

    stats = eg.prefetch(["Ordway", "SOC 2"], resolver=resolver)

    print("    prefetch -> %s | curated still %s | unknown %s"
          % (stats, eg.ground_id("SOC 2"), eg.ground_url("Ordway")))
    assert stats["resolved"] == 0, stats
    assert eg.ground_url("SOC 2") == WIKIDATA_KB["soc 2"], "The fast path broke too."
    assert eg.ground_url("Ordway") is None
    # A raised exception is not evidence the phrase is ungroundable, so it must not be
    # cached as a negative - the next run gets to try again.
    assert "ordway" not in eg._live_cache, "A transport failure was cached as a verdict."
    clear_cache()
    print("  PASS")


def test_disabled_live_lookup_is_the_old_behaviour():
    print("\n[8] With live lookup off, the module is the dict it replaced ...")
    clear_cache()
    resolver = FakeResolver({"ordway": "Q1"})
    saved = eg.LIVE_ENABLED
    eg.LIVE_ENABLED = False
    try:
        stats = eg.prefetch(["Ordway"], resolver=resolver)
    finally:
        eg.LIVE_ENABLED = saved

    print("    asked %s | curated %s | unknown %s"
          % (resolver.asked, eg.ground_id("GDPR"), eg.ground_url("Ordway")))
    assert resolver.asked == [], "Live lookup ran while disabled."
    assert stats["requested"] == 0, stats
    assert eg.ground_url("GDPR") == WIKIDATA_KB["gdpr"]
    assert eg.ground_url("Ordway") is None
    clear_cache()
    print("  PASS")


def test_prefetch_works_inside_a_running_loop():
    print("\n[9] Prefetch works from inside an event loop ...")
    clear_cache()
    resolver = FakeResolver({"ordway": "Q7"})

    # FastAPI request handlers call the graph builders from inside a running loop, and
    # asyncio.run() cannot nest. Getting this wrong fails only in the deployed service.
    async def in_a_handler():
        return eg.prefetch(["Ordway"], resolver=resolver)

    stats = asyncio.run(in_a_handler())
    print("    resolved %d -> %s" % (stats["resolved"], eg.ground_id("Ordway")))
    assert eg.ground_id("Ordway") == "Q7", "Prefetch failed under a running loop."
    clear_cache()
    print("  PASS")


def test_graph_build_grounds_a_phrase_the_kb_never_had():
    print("\n[10] A built graph carries a live-resolved sameAs ...")
    import knowledge_graph as kg
    from models import SemanticTriple
    from rdflib import URIRef

    clear_cache()
    resolver = FakeResolver({"ordway": "Q42424242"})
    eg.prefetch(["Ordway"], resolver=resolver)  # warm as a real run's prefetch would

    triples = [SemanticTriple(subject="Ordway", predicate="integratesWith",
                              object="Ordway", evidence_sentence="x")]
    g = kg.build_rdf_graph("ordway.example", triples, {}, [])

    same_as = URIRef("https://schema.org/sameAs")
    grounded = [str(o) for _s, _p, o in g.triples((None, same_as, None))]
    print("    sameAs in graph: %s" % grounded)
    assert any("Q42424242" in u for u in grounded), (
        "The graph did not carry the live-resolved identity: %s" % grounded)
    clear_cache()
    print("  PASS")


if __name__ == "__main__":
    print("=" * 78)
    print("ENTITY GROUNDING")
    print("=" * 78)
    try:
        test_curated_entries_answer_without_asking()
        test_a_curated_answer_is_never_overridden()
        test_unknown_phrase_is_grounded_after_prefetch()
        test_lookups_never_touch_the_network()
        test_unresolvable_phrases_are_asked_about_once()
        test_budget_bounds_one_run()
        test_unreachable_wikidata_costs_links_not_the_audit()
        test_disabled_live_lookup_is_the_old_behaviour()
        test_prefetch_works_inside_a_running_loop()
        test_graph_build_grounds_a_phrase_the_kb_never_had()
    finally:
        clear_cache()
    print("\n" + "=" * 78)
    print("ALL ENTITY GROUNDING TESTS PASSED")
    print("Curated Q-IDs still win; everything else finally gets asked about.")
    print("=" * 78)
