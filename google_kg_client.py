"""
GainARK OntoLeap — Google Knowledge Graph Search Client
Connects to Google Cloud Knowledge Graph Search API (kgsearch.googleapis.com)
to verify if brands, products, and features are recognized in Google's official Knowledge Graph.
"""

import os
import json
import logging
import urllib.request
import urllib.parse
from typing import Dict, Any, Optional, List

logger = logging.getLogger("gainark.google_kg")

# Auto-load .env file if present
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    try:
        with open(_env_path, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    _k, _v = _k.strip(), _v.strip().strip("'\"")
                    if _k and _k not in os.environ:
                        os.environ[_k] = _v
    except Exception:
        pass

GOOGLE_KG_API_KEY = os.environ.get("GOOGLE_KG_API_KEY", "")
BASE_URL = "https://kgsearch.googleapis.com/v1/entities:search"


def is_available() -> bool:
    """Check if Google Knowledge Graph API key is configured."""
    return bool(GOOGLE_KG_API_KEY)


def search_entity(query: str, limit: int = 3) -> Optional[Dict[str, Any]]:
    """
    Query Google's Knowledge Graph for a given brand, company, or concept.
    Returns the top matching entity with its Google Machine ID (MID),
    entity types, description, and Google's internal salience/prominence score.
    """
    if not GOOGLE_KG_API_KEY or not query or not query.strip():
        return None

    clean_query = query.strip()
    params = {
        "query": clean_query,
        "key": GOOGLE_KG_API_KEY,
        "limit": limit,
        "indent": "True"
    }
    encoded_url = f"{BASE_URL}?{urllib.parse.urlencode(params)}"

    try:
        BUSINESS_TYPES = {"Organization", "Corporation", "SoftwareApplication", "Product", "Service", "Company"}

        def _fetch_kg_items(q: str):
            p = {"query": q, "key": GOOGLE_KG_API_KEY, "limit": limit, "indent": "True"}
            u = f"{BASE_URL}?{urllib.parse.urlencode(p)}"
            r = urllib.request.Request(u)
            with urllib.request.urlopen(r, timeout=10) as response:
                d = json.loads(response.read().decode("utf-8"))
                return d.get("itemListElement", [])

        items = _fetch_kg_items(clean_query)

        # 1. Look for business / tech entity in direct results
        best_item = None
        for item in items:
            types = set(item.get("result", {}).get("@type", []))
            if types & BUSINESS_TYPES:
                best_item = item
                break

        # 2. If ambiguous or non-business (e.g. Person like Melissa Ordway), retry with disambiguation terms
        if not best_item:
            for suffix in ["software", "company", "labs", "platform"]:
                retry_items = _fetch_kg_items(f"{clean_query} {suffix}")
                for item in retry_items:
                    types = set(item.get("result", {}).get("@type", []))
                    if types & BUSINESS_TYPES:
                        best_item = item
                        break
                if best_item:
                    break

        # 3. If still no business/tech entity found, explicitly mark as not recognized
        if not best_item:
            first_types = items[0].get("result", {}).get("@type", []) if items else []
            first_name = items[0].get("result", {}).get("name") if items else None
            return {
                "query": clean_query,
                "is_recognized": False,
                "status": f"Excluded Non-Business Entity ({first_name}: {', '.join(first_types) or 'Unknown'})" if items else "Absent from Google Knowledge Graph",
                "entity_name": None,
                "google_mid": None,
                "google_kg_url": None,
                "score": 0.0,
                "types": first_types,
                "description": f"Entity matched non-business types ({', '.join(first_types)}). Excluded to prevent incorrect sameAs attribution." if items else "Entity not indexed in Google's Knowledge Graph.",
                "wikipedia_url": None,
                "is_business_or_tech": False,
                "ai_overview_risk": "High Omission Risk (No verified business/organization entity in Google KG)"
            }

        res = best_item.get("result", {})
        types = res.get("@type", [])
        score = round(best_item.get("resultScore", 0.0), 2)
        mid = res.get("@id", "").replace("kg:", "")
        desc = res.get("description", "")
        detailed_desc = res.get("detailedDescription", {}).get("articleBody", desc)
        wiki_url = res.get("detailedDescription", {}).get("url")

        # Double-check that wikipedia_url does not point to a person/film/actor
        if wiki_url:
            w_lower = wiki_url.lower()
            if any(bad in w_lower for bad in ["actor", "actress", "model", "singer", "film", "album", "athlete"]):
                wiki_url = None

        kg_url = f"https://www.google.com/search?kgmid={mid}" if mid else None

        return {
            "query": clean_query,
            "is_recognized": True,
            "status": "Recognized in Google Knowledge Graph",
            "entity_name": res.get("name", clean_query),
            "google_mid": mid,
            "google_kg_url": kg_url,
            "score": score,
            "types": types,
            "description": detailed_desc or desc or "Recognized Google Knowledge Graph Entity",
            "wikipedia_url": wiki_url,
            "is_business_or_tech": True,
            "ai_overview_risk": (
                "Low Risk (Indexed in Google KG with high authority)" if score > 500 else
                "Moderate Risk (Recognized in Google KG, low authority score)" if score > 100 else
                "High Risk (Ambiguous or low entity presence)"
            )
        }
    except Exception as e:
        logger.error(f"Google Knowledge Graph Search API failed for '{query}': {e}")
        return None
