"""
buyer_profiles.py - who buys, stored per site.

The buyer half of discovery - segments, industries, competitors, what customers replace,
and the quotes that prove each - describes one company. It used to be written onto the
vertical, which many sites share, so every site routed to a vertical showed the buyer
profile of whichever site was discovered there first. On 14 Sep 2026 ordwaylabs.com's
Who Buys tab showed nineteen claims, every one quoted from chargebee.com.

A profile is keyed by the site's host without "www.", written to `buyers/` inside the
verticals directory and copied to the verticals mirror under the same prefix, so it
survives an instance that does not. Vertical listings read only top-level files, so the
subdirectory is never mistaken for a vertical.
"""

import json
import logging
import os
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from security import verticals_dir

logger = logging.getLogger("gainark.buyer_profiles")

MIRROR_PREFIX = "buyers/"


def buyer_profiles_dir(root: Optional[str] = None) -> str:
    return os.path.join(root or verticals_dir(), "buyers")

BUYER_FIELDS = ("known_segments", "known_industries", "known_competitors", "known_replaces")


def domain_key(url_or_domain: str) -> str:
    """"https://www.ordwaylabs.com/x" and "ordwaylabs.com" both give "ordwaylabs.com"."""
    raw = (url_or_domain or "").strip().lower()
    host = urlparse(raw if "://" in raw else "https://" + raw).hostname or ""
    if host.startswith("www."):
        host = host[4:]
    # Used as a file name: nothing but a host's own characters.
    return host if re.fullmatch(r"[a-z0-9.-]{1,253}", host) and ".." not in host else ""


def _path(key: str, root: Optional[str] = None) -> str:
    return os.path.join(buyer_profiles_dir(root), "%s.json" % key)


def save(url_or_domain: str, profile: Dict[str, Any], root: Optional[str] = None) -> Optional[str]:
    """Write one site's buyer profile, replacing the last one. Returns the local path.

    `root` is the verticals directory to write under; the default is the configured one.
    """
    key = domain_key(url_or_domain)
    if not key:
        logger.warning("Not saving a buyer profile for unusable domain %r", url_or_domain)
        return None
    payload = dict(profile, domain=key)
    os.makedirs(buyer_profiles_dir(root), exist_ok=True)
    path = _path(key, root)
    tmp = path + ".partial"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)

    try:
        import vertical_store
        store = vertical_store.mirror()
        if store is not None:
            store.put(MIRROR_PREFIX + "%s.json" % key,
                      json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8"))
    except Exception as err:
        # The local copy is written; a mirror failure costs durability, not the profile.
        logger.error("Could not mirror buyer profile for %s: %s", key, err)
    return path


def load(url_or_domain: str, root: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The stored buyer profile for a site, or None when it was never discovered."""
    key = domain_key(url_or_domain)
    if not key:
        return None
    path = _path(key, root)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as err:
            logger.error("Could not read buyer profile %s: %s", path, err)
    try:
        import vertical_store
        store = vertical_store.mirror()
        payload = store.get(MIRROR_PREFIX + "%s.json" % key) if store is not None else None
        if payload:
            data = json.loads(payload.decode("utf-8"))
            return data if isinstance(data, dict) else None
    except Exception as err:
        logger.error("Could not read mirrored buyer profile for %s: %s", key, err)
    return None
