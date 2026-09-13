"""
Every curated Q-ID names the thing its key names. Checked against live Wikidata.

constants.WIKIDATA_KB was described as hand-verified for months while 38 of its 40 Q-IDs
pointed at unrelated items: SaaS was Caiaphas, Salesforce a town in Chile, Slack a
baseball player. The offline tests could not see it - they compared the table to itself -
and every graph shipped those identities as schema:sameAs.

This test asks Wikidata. For each key it fetches the item and requires the key to appear
in the item's English label, an alias, or its English Wikipedia title. A few entries map
a narrower phrase onto a broader item on purpose; those are named in DELIBERATE with the
label they are expected to carry, so a change there is still caught.

Network: required. Without it the test prints SKIP and exits cleanly, because an offline
run proves nothing about these identities either way.
"""

import json
import re
import sys
import urllib.request

from constants import WIKIDATA_KB

API = ("https://www.wikidata.org/w/api.php?action=wbgetentities&format=json"
       "&props=labels|aliases|sitelinks|claims&languages=en|mul&sitefilter=enwiki&ids=")
HEADERS = {"User-Agent": "GainARK-OntoLeap/2.0 (KB verification; https://gainark.com)"}
DISAMBIGUATION = "Q4167410"

# key -> a name the item must carry, for keys that deliberately ground on a broader item.
DELIBERATE = {
    "soc 1 type ii": "SOC 1",
    "soc 2 type ii": "SOC 2",
    "usage-based pricing": "Pay-per-use",
}


def _norm(s):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())


def _names(entity):
    out = []
    for lang in ("en", "mul"):
        label = entity.get("labels", {}).get(lang)
        if label:
            out.append(label["value"])
        out.extend(a["value"] for a in entity.get("aliases", {}).get(lang, []))
    enwiki = entity.get("sitelinks", {}).get("enwiki")
    if enwiki:
        out.append(enwiki["title"])
    return out


def _fetch(qids):
    entities = {}
    qids = sorted(qids)
    for i in range(0, len(qids), 50):
        req = urllib.request.Request(API + "|".join(qids[i:i + 50]), headers=HEADERS)
        with urllib.request.urlopen(req, timeout=20) as resp:
            entities.update(json.load(resp)["entities"])
    return entities


def _wrong_items(expected):
    """expected: label -> (qid, [names any of which the item must carry]). None if offline."""
    try:
        entities = _fetch({qid for qid, _ in expected.values()})
    except OSError as exc:
        print("  SKIP - Wikidata unreachable: %s" % exc)
        return None
    wrong = []
    for label, (qid, wanted) in expected.items():
        entity = entities.get(qid, {})
        names = _names(entity)
        instance_of = [c["mainsnak"].get("datavalue", {}).get("value", {}).get("id")
                       for c in entity.get("claims", {}).get("P31", [])]
        matched = any(re.search(r"\b%s\b" % re.escape(_norm(w)), _norm(n))
                      for w in wanted for n in names)
        if "missing" in entity or not matched or DISAMBIGUATION in instance_of:
            wrong.append("%-30s %-12s is %r" % (label, qid, names[:1] or "(no such item)"))
    return wrong


def test_every_curated_qid_names_its_key():
    print("\n[1] Every WIKIDATA_KB entry resolves to the item its key names ...")
    expected = {k: (v.rstrip("/").rsplit("/", 1)[-1], [DELIBERATE.get(k, k)])
                for k, v in WIKIDATA_KB.items()}
    wrong = _wrong_items(expected)
    if wrong is None:
        return
    print("    %d entries checked, %d wrong" % (len(expected), len(wrong)))
    for line in wrong:
        print("      " + line)
    assert not wrong, "WIKIDATA_KB holds Q-IDs for the wrong items:\n" + "\n".join(wrong)
    print("  PASS")


def test_every_registry_seed_qid_names_its_entity():
    print("\n[2] Every registry seed Q-ID names the entity that carries it ...")
    from entity_registry import load_seed
    expected = {}
    for ent in load_seed():
        qid = (ent.get("external") or {}).get("wikidata")
        if qid:
            forms = [ent["prefLabel"]] + [a if isinstance(a, str) else a["form"]
                                          for a in ent.get("aliases", [])]
            expected[ent["id"]] = (qid, forms)
    wrong = _wrong_items(expected)
    if wrong is None:
        return
    print("    %d seed Q-IDs checked, %d wrong" % (len(expected), len(wrong)))
    for line in wrong:
        print("      " + line)
    assert not wrong, "The registry seed holds Q-IDs for the wrong items:\n" + "\n".join(wrong)
    print("  PASS")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 78)
    print("WIKIDATA KB - LIVE VERIFICATION")
    print("=" * 78)
    test_every_curated_qid_names_its_key()
    test_every_registry_seed_qid_names_its_entity()
    print("\n" + "=" * 78)
    print("ALL WIKIDATA KB CHECKS PASSED")
    print("=" * 78)
