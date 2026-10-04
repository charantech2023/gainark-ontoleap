"""
competitor_set.py - which companies a customer competes with, at which domains.

Phase 1 of COMPETITOR_PROFILES_DESIGN.md (P3). A buyer profile names competitors -
"Recurly", "SaaSOptics (Maxio)", "Zuora" - and a crawl needs a domain. Each name is
resolved by the cheapest rung that can answer, most certain first:

    1  registry  a form of the name is an active registry entity that has a domain
    2  link      a page that proved the competitor links to a domain that is the name
                 (recurly.com for Recurly); anchor text alone never counts - on 3 Oct
                 2026 the Zuora page's only "Zuora" links went to techcrunch.com and
                 cnbc.com
    3  proposal  <name>.com, followed through its redirects, whose homepage title or site
                 name carries the name; saasoptics.com landing on maxio.com is the rebrand

Rungs 1 and 2 apply automatically and say so. A rung-3 proposal is pending until a
reviewer confirms it (design §11.4). Confirming writes the domain to the registry - a
domain_added event, or a new entity when the registry has none - so the next customer
with the same competitor resolves at rung 1 with no lookup.

The set is stored per customer site as buyers/<site>.competitors.json beside the buyer
profile, through vertical_store.update, so two writers cannot lose each other's change.
A rebuild keeps every reviewer decision: a confirmed domain, a removed competitor, a
competitor a reviewer added.
"""

import logging
import re
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

logger = logging.getLogger("gainark.competitor_set")

STATUSES = ("automatic", "pending", "confirmed", "unresolved", "removed")
_UA = {"User-Agent": "Mozilla/5.0 (compatible; GainARK-OntoLeap/1.0; +https://gainark.com)"}

# (final url, html) for a url, following redirects. Injected in tests.
Fetch = Callable[[str], Tuple[str, str]]


def _fetch(url: str) -> Tuple[str, str]:
    with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=20) as r:
        return r.geturl(), r.read(400_000).decode("utf-8", "replace")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Names and domains
# ---------------------------------------------------------------------------

def name_forms(name: str) -> List[str]:
    """The ways a competitor is named. "SaaSOptics (Maxio)" is SaaSOptics and Maxio - a
    rebrand written as one name, which buyer profiles do (PR #9)."""
    name = (name or "").strip()
    inner = re.findall(r"\(([^)]+)\)", name)
    outer = re.sub(r"\s*\([^)]*\)", "", name).strip()
    forms = [f for f in [outer] + [i.strip() for i in inner] if f]
    return list(dict.fromkeys(forms))


def _label(form: str) -> str:
    """A name as a domain label would write it: "Sage Intacct" -> "sageintacct"."""
    return re.sub(r"[^a-z0-9]", "", form.lower())


def host_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def domain_names(domain: str, forms: List[str]) -> bool:
    """Whether a domain is one of the forms: its first label, hyphens dropped, equals the
    form's label. recurly.com is Recurly; recurly.zendesk.com is not."""
    first = host_of("https://" + domain).split(".")[0].replace("-", "")
    return any(first == _label(f) for f in forms if _label(f))


def _title(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html or "", re.S | re.I)
    site = re.search(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)',
                     html or "", re.I)
    return " ".join(x.group(1).strip() for x in (m, site) if x)


# ---------------------------------------------------------------------------
# The three rungs
# ---------------------------------------------------------------------------

def by_registry(forms: List[str], registry: Any) -> Optional[Dict[str, Any]]:
    from entity_resolver import normalise_key
    for form in forms:
        hits = [e for e in registry.lookup(normalise_key(form)) if e.domains]
        if len(hits) == 1:
            return {"domain": hits[0].domains[0], "entity_id": hits[0].id,
                    "method": "registry", "status": "automatic",
                    "evidence": "registry entity %s" % hits[0].id}
    return None


def by_link(forms: List[str], evidence_urls: List[str], site: str,
            fetch: Fetch) -> Optional[Dict[str, Any]]:
    """A single linked domain, other than the site's own, that is the name."""
    found = {}
    for url in evidence_urls:
        try:
            _, html = fetch(url)
        except Exception as err:
            logger.info("[Competitors] Could not read %s: %s", url, err)
            continue
        for href in re.findall(r'href=["\'](https?://[^"\']+)', html or "", re.I):
            host = host_of(href)
            if host and site not in host and domain_names(host, forms):
                found.setdefault(host, url)
    if len(found) == 1:
        host, page = next(iter(found.items()))
        return {"domain": host, "method": "link", "status": "automatic",
                "evidence": "linked from %s" % page}
    return None


def by_proposal(forms: List[str], fetch: Fetch) -> Optional[Dict[str, Any]]:
    """<label>.com for each form, kept when the page it lands on names the competitor."""
    for form in forms:
        label = _label(form)
        if not label:
            continue
        guess = "https://%s.com" % label
        try:
            final, html = fetch(guess)
        except urllib.error.HTTPError as err:
            if err.code in (401, 403, 429):
                return _unverified("%s.com" % label, form, "answered HTTP %d" % err.code)
            logger.info("[Competitors] %s did not answer: %s", guess, err)
            continue
        except Exception as err:
            logger.info("[Competitors] %s did not answer: %s", guess, err)
            continue
        from scraper import challenge_vendor
        vendor = challenge_vendor(html)
        if vendor:
            # recurly.com, 3 Oct 2026. A bot check is not something to get past: the
            # domain is still the name, so it is proposed, and the reviewer is told the
            # homepage could not be read.
            return _unverified(host_of(final), form, "is behind a %s bot check" % vendor)
        title = _title(html)
        named = [f for f in forms if f.lower() in title.lower()]
        if not named:
            continue
        host = host_of(final)
        moved = "" if host == "%s.com" % label else " (redirected from %s.com)" % label
        return {"domain": host, "method": "proposal", "status": "pending",
                "evidence": "homepage title %r names %s%s" % (title[:120], named[0], moved)}
    return None


def _unverified(domain: str, form: str, why: str) -> Dict[str, Any]:
    return {"domain": domain, "method": "proposal", "status": "pending", "verified": False,
            "evidence": "the domain is the name %r, but its homepage %s, so its title could "
                        "not be read" % (form, why)}


def resolve(name: str, registry: Any, evidence_urls: List[str], site: str,
            fetch: Fetch = _fetch) -> Dict[str, Any]:
    forms = name_forms(name)
    hit = (by_registry(forms, registry)
           or by_link(forms, evidence_urls, site, fetch)
           or by_proposal(forms, fetch))
    entry = {"name": name, "forms": forms, "domain": None, "entity_id": None,
             "method": None, "status": "unresolved", "evidence": "", "resolved_at": _now()}
    if hit:
        entry.update(hit)
    return entry


# ---------------------------------------------------------------------------
# A site's set
# ---------------------------------------------------------------------------

def _store_id(site: str) -> str:
    return "buyers/%s.competitors" % site


def load(site: str) -> Dict[str, Any]:
    import os
    import vertical_store
    from security import verticals_dir
    store = vertical_store.mirror()
    if store is not None:
        payload = store.get(_store_id(site) + ".json")
        if payload:
            import json
            return json.loads(payload.decode("utf-8"))
    local = vertical_store.read_local(os.path.join(verticals_dir(), _store_id(site) + ".json"))
    return local or {"site": site, "competitors": []}


def _save(site: str, mutate: Callable[[Dict[str, Any]], Optional[Dict[str, Any]]]) -> Dict[str, Any]:
    import vertical_store

    def apply(current):
        return mutate(current or {"site": site, "competitors": []})

    return vertical_store.update(_store_id(site), apply) or load(site)


def build(site: str, profile: Optional[Dict[str, Any]] = None, registry: Any = None,
          fetch: Fetch = _fetch, extra: Optional[List[str]] = None) -> Dict[str, Any]:
    """Resolve every competitor the buyer profile names, plus `extra` names a reviewer
    added, and store the set. Entries a reviewer confirmed or removed are kept as they are."""
    if profile is None:
        import buyer_profiles
        profile = buyer_profiles.load(site) or {}
    if registry is None:
        from entity_registry import default_store
        registry = default_store().registry()
    evidence = (profile.get("icp_evidence") or {}).get("known_competitors") or {}
    names = list(dict.fromkeys([c for c in profile.get("known_competitors") or [] if c]
                               + list(extra or [])))
    current = {c["name"].lower(): c for c in load(site).get("competitors") or []}
    resolved = []
    for name in names:
        kept = current.get(name.lower())
        if kept and kept.get("status") in ("confirmed", "removed"):
            continue
        urls = [evidence[name]["source_url"]] if isinstance(evidence.get(name), dict) else []
        entry = resolve(name, registry, urls, site, fetch)
        if kept and kept.get("source"):
            entry["source"] = kept["source"]
        else:
            entry["source"] = "reviewer" if name in (extra or []) else "buyer_profile"
        resolved.append(entry)

    def merge(doc):
        by = {c["name"].lower(): c for c in doc.get("competitors") or []}
        for e in resolved:
            prior = by.get(e["name"].lower())
            if prior and prior.get("status") in ("confirmed", "removed"):
                continue
            by[e["name"].lower()] = e
        doc["competitors"] = sorted(by.values(), key=lambda c: c["name"].lower())
        doc["site"], doc["updated_at"] = site, _now()
        return doc

    return _save(site, merge)


def confirm(site: str, name: str, domain: Optional[str] = None, store: Any = None) -> Dict[str, Any]:
    """A reviewer confirms a competitor's domain (the proposed one, or `domain`), and the
    registry learns it: domain_added on the entity a form names, or a new entity."""
    from entity_registry import ACTOR_REVIEWER, default_store, make_event
    from entity_resolver import normalise_key, slug

    doc = load(site)
    entry = next((c for c in doc.get("competitors") or [] if c["name"].lower() == name.lower()), None)
    if entry is None:
        raise ValueError("%s is not in %s's competitor set" % (name, site))
    domain = (domain or entry.get("domain") or "").strip().lower()
    if not domain:
        raise ValueError("No domain to confirm for %s" % name)

    store = store or default_store()
    registry = store.registry(force=True)
    ent = next((hits[0] for f in entry["forms"]
                for hits in [registry.lookup(normalise_key(f))] if len(hits) == 1), None)
    if ent is None:
        # The form the domain is named after is the company's current name: saasoptics.com
        # lands on maxio.com, so the entity is Maxio and SaaSOptics an alias.
        forms = entry["forms"]
        primary = next((f for f in forms if domain_names(domain, [f])), forms[0])
        eid = slug(primary)
        store.append(make_event("entity_created", ACTOR_REVIEWER, entity={
            "id": eid, "kind": "organization", "prefLabel": primary,
            "aliases": [{"form": f, "source": ACTOR_REVIEWER} for f in forms if f != primary],
            "domains": [domain]}))
    else:
        eid = ent.id
        if domain not in ent.domains:
            store.append(make_event("domain_added", ACTOR_REVIEWER, entity_id=eid, domain=domain))
        for f in entry["forms"]:
            if all(normalise_key(f) != normalise_key(x) for x in ent.forms()):
                store.append(make_event("alias_added", ACTOR_REVIEWER, entity_id=eid, form=f))

    def mark(d):
        for c in d.get("competitors") or []:
            if c["name"].lower() == name.lower():
                c.update(domain=domain, entity_id=eid, status="confirmed", confirmed_at=_now())
        return d

    return _save(site, mark)


def remove(site: str, name: str) -> Dict[str, Any]:
    """A reviewer says this is not a competitor. Kept as a decision, so a rebuild does not
    bring it back."""
    def mark(d):
        hit = False
        for c in d.get("competitors") or []:
            if c["name"].lower() == name.lower():
                c.update(status="removed", removed_at=_now())
                hit = True
        return d if hit else None
    return _save(site, mark)


def crawlable(site: str) -> List[Dict[str, Any]]:
    """Competitors with a domain a crawl may use: confirmed, or resolved automatically."""
    return [c for c in load(site).get("competitors") or []
            if c.get("domain") and c.get("status") in ("confirmed", "automatic")]


if __name__ == "__main__":
    import json
    import sys
    logging.basicConfig(level=logging.INFO)
    print(json.dumps(build(sys.argv[1], extra=sys.argv[2:]), indent=1, ensure_ascii=False))
