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

GOOGLE_KG_API_KEY = os.environ.get(
    "GOOGLE_KG_API_KEY",
    "AIzaSyDLQurSsWq87g_9ia0lZLWIM6FGRyLrZYc"  # Project: robotic-catwalk-463901-h0
)
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
        req = urllib.request.Request(encoded_url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            items = data.get("itemListElement", [])
            if not items:
                return {
                    "query": clean_query,
                    "is_recognized": False,
                    "status": "Absent from Google Knowledge Graph",
                    "entity_name": None,
                    "google_mid": None,
                    "google_kg_url": None,
                    "score": 0.0,
                    "types": [],
                    "description": "Entity not indexed in Google's Knowledge Graph.",
                    "ai_overview_risk": "High Omission Risk (Google AI does not have a canonical entity card for this brand)"
                }

            # Filter for organization / company / software if possible
            best_item = items[0]
            for item in items:
                types = item.get("result", {}).get("@type", [])
                if any(t in types for t in ["Organization", "Corporation", "SoftwareApplication", "Product"]):
                    best_item = item
                    break

            res = best_item.get("result", {})
            types = res.get("@type", [])
            score = round(best_item.get("resultScore", 0.0), 2)
            mid = res.get("@id", "").replace("kg:", "")
            desc = res.get("description", "")
            detailed_desc = res.get("detailedDescription", {}).get("articleBody", desc)
            wiki_url = res.get("detailedDescription", {}).get("url")

            is_business_or_tech = any(
                t in types for t in ["Organization", "Corporation", "SoftwareApplication", "Product", "Service"]
            )

            kg_url = f"https://www.google.com/search?kgmid={mid}" if mid else None

            return {
                "query": clean_query,
                "is_recognized": True,
                "status": "Recognized in Google Knowledge Graph" if is_business_or_tech else "Ambiguous / Non-Business Entity",
                "entity_name": res.get("name", clean_query),
                "google_mid": mid,
                "google_kg_url": kg_url,
                "score": score,
                "types": types,
                "description": detailed_desc or desc or "Recognized Google Knowledge Graph Entity",
                "wikipedia_url": wiki_url,
                "is_business_or_tech": is_business_or_tech,
                "ai_overview_risk": (
                    "Low Risk (Indexed in Google KG with high authority)" if score > 500 else
                    "Moderate Risk (Recognized in Google KG, low authority score)" if score > 100 else
                    "High Risk (Ambiguous or low entity presence)"
                )
            }
    except Exception as e:
        logger.error(f"Google Knowledge Graph Search API failed for '{query}': {e}")
        return None
