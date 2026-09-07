"""
GainARK OntoLeap - Google Gemini Integration Layer
Connects to Google Gemini 2.5 Flash for:
1. Live Perplexity & SearchGPT Search Simulator (grounded in verified KG triples)
2. Product Truth Content Brief Generator (structured briefs for writers)
3. Draft Alignment & Claim Verification (LLM-as-Judge for draft text)
"""

import os
import json
import time
import random
import logging
import urllib.request
import urllib.error
from typing import List, Dict, Any, Optional

logger = logging.getLogger("gainark.gemini")

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

# Default to environment variable
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

# Transient upstream failures worth retrying: rate limit and the 5xx family.
_RETRYABLE_STATUS = (429, 500, 502, 503, 504)
_MAX_ATTEMPTS = int(os.environ.get("GEMINI_MAX_ATTEMPTS", "3") or 3)


def is_available() -> bool:
    """Check if Gemini API key is configured."""
    return bool(GEMINI_API_KEY)


# ---------------------------------------------------------------------------
# Model output coercion
# ---------------------------------------------------------------------------
# A language model JSON response is untrusted input: a key may be absent, a value may be
# a string where a list was asked for, or a nested object. Coercing here keeps a
# malformed generation from surfacing to the caller as a 500 out of the response model.

def _parse_json_object(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """Extract the first JSON object from a model response, or None."""
    if not raw:
        return None
    import re
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        logger.warning("Gemini response contained no JSON object; using deterministic fallback.")
        return None
    try:
        parsed = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        logger.warning("Gemini response was not valid JSON; using deterministic fallback.")
        return None
    return parsed if isinstance(parsed, dict) else None


def _as_str_list(value: Any, limit: int = 50) -> List[str]:
    """Coerce a model-supplied value into a bounded list of strings."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value[:1000]]
    if isinstance(value, (list, tuple)):
        return [str(v)[:1000] for v in value[:limit] if v is not None]
    return [str(value)[:1000]]


def _as_int(value: Any, default: int) -> int:
    """Coerce a model-supplied value into an int, falling back on anything unusable."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _call_gemini(
    prompt: str,
    system_instruction: Optional[str] = None,
    temperature: float = 0.2,
    max_output_tokens: int = 4096,
    thinking_budget: int = 256,
    timeout: int = 30
) -> Optional[str]:
    """Call Gemini 2.5 Flash via REST API with configurable timeout and error handling."""
    if not GEMINI_API_KEY:
        logger.warning("Gemini API key is not configured.")
        return None

    # The key travels in a header, never in the query string: a URL is logged by every
    # proxy in the path and lands in exception text, access logs and crash reports.
    url = f"{BASE_URL}/models/{GEMINI_MODEL}:generateContent"
    headers = {
        "Content-Type": "application/json",
        "x-goog-api-key": GEMINI_API_KEY,
    }

    gen_config: Dict[str, Any] = {
        "temperature": temperature,
        "maxOutputTokens": max_output_tokens,
    }
    if thinking_budget is not None:
        gen_config["thinkingConfig"] = {"thinkingBudget": thinking_budget}

    payload: Dict[str, Any] = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": gen_config
    }

    if system_instruction:
        payload["systemInstruction"] = {
            "parts": [{"text": system_instruction}]
        }

    data_bytes = json.dumps(payload).encode("utf-8")

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            req = urllib.request.Request(url, data=data_bytes, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                res_json = json.loads(resp.read().decode("utf-8"))
                candidates = res_json.get("candidates", [])
                if candidates:
                    parts = candidates[0].get("content", {}).get("parts", [])
                    if parts:
                        return parts[0].get("text", "").strip()
            return None
        except urllib.error.HTTPError as http_err:
            # Log status and class only. Upstream error bodies can echo request material
            # back, and must never be written to the log alongside credentials.
            if http_err.code in _RETRYABLE_STATUS and attempt < _MAX_ATTEMPTS:
                backoff = min(8.0, 0.5 * (2 ** (attempt - 1))) + random.uniform(0, 0.3)
                logger.warning(
                    "Gemini API returned HTTP %d (attempt %d/%d); retrying in %.1fs",
                    http_err.code, attempt, _MAX_ATTEMPTS, backoff,
                )
                time.sleep(backoff)
                continue
            logger.error("Gemini API request failed with HTTP %d", http_err.code)
            return None
        except Exception as e:
            if attempt < _MAX_ATTEMPTS:
                backoff = min(8.0, 0.5 * (2 ** (attempt - 1))) + random.uniform(0, 0.3)
                logger.warning(
                    "Gemini API request failed (%s, attempt %d/%d); retrying in %.1fs",
                    type(e).__name__, attempt, _MAX_ATTEMPTS, backoff,
                )
                time.sleep(backoff)
                continue
            logger.error("Gemini API request failed: %s", type(e).__name__)
            return None

    return None


def generate_search_answer(
    query: str,
    triples: List[Dict[str, Any]],
    brand_name: str = "The Platform",
    site_url: str = "https://example.com"
) -> Dict[str, Any]:
    """
    Simulate a live AI Search Engine (Perplexity / SearchGPT / Google AI Overview).
    Synthesizes an authoritative answer strictly grounded in the verified triples,
    embedding bracketed citations [1], [2] referencing canonical sources.
    """
    # Format triples for grounding
    facts_str = ""
    for idx, t in enumerate(triples[:12], 1):
        subj = t.get("subject", brand_name)
        pred = t.get("predicate", "relatesTo")
        obj = t.get("object", "")
        facts_str += f"- Fact [{idx}]: {subj} --{pred}--> {obj}\n"

    system_prompt = (
        "You are an enterprise AI Answer Engine (like Perplexity or Google AI Overviews). "
        "Your task is to provide a concise, high-authority, direct answer to the user's B2B research query. "
        "Strict rules:\n"
        "1. Base your answer PRIMARILY on the verified facts provided.\n"
        "2. Include explicit footnote markers like [1], [2] immediately following claims supported by the facts.\n"
        "3. Highlight specific compliance standards, native integrations, and pricing capabilities.\n"
        "4. Keep the answer between 60 and 120 words. Professional, executive B2B tone."
    )

    user_prompt = (
        f"User Search Query: \"{query}\"\n\n"
        f"Brand Under Evaluation: {brand_name} ({site_url})\n\n"
        f"Verified Product Knowledge Graph Triples:\n{facts_str or 'No verified triples provided.'}\n\n"
        "Provide the grounded answer with citations:"
    )

    gemini_resp = _call_gemini(user_prompt, system_instruction=system_prompt, temperature=0.1)

    citations = []
    for idx, t in enumerate(triples[:12], 1):
        citations.append({
            "citation_id": f"[{idx}]",
            "source": brand_name,
            "url": site_url,
            "fact": f"{t.get('subject', brand_name)} {t.get('predicate')} {t.get('object')}"
        })

    if gemini_resp:
        return {
            "query": query,
            "answer": gemini_resp,
            "engine": f"Google Gemini 2.5 Flash (Grounding: {brand_name} KG)",
            "status": "live_ai_generated",
            "citations": citations,
            "grounded_triples_count": len(triples)
        }

    # Fallback to deterministic template if Gemini fails or is unreachable
    return {
        "query": query,
        "answer": (
            f"Based on verified knowledge graph data, {brand_name} provides enterprise solutions "
            f"with verified capabilities [1]. For compliance and integrations [2], the platform maintains "
            f"structured schema entities for automated AI engine retrieval."
        ),
        "engine": "Rule-Based Deterministic Fallback",
        "status": "fallback",
        "citations": citations[:3],
        "grounded_triples_count": len(triples)
    }


def generate_product_brief(
    topic: str,
    brand_name: str,
    triples: List[Dict[str, Any]],
    gaps: List[str],
    vertical_name: str = "B2B SaaS"
) -> Dict[str, Any]:
    """
    Generate a Product Truth Content Brief for human writers and AI copywriters.
    Constrains content generation to verified facts and explicitly flags prohibited claims.
    """
    triples_summary = "\n".join([
        f"- {t.get('subject')} --{t.get('predicate')}--> {t.get('object')}"
        for t in triples[:15]
    ])
    gaps_summary = "\n".join([f"- Missing/Competitor Gap: {g}" for g in gaps[:5]])

    system_prompt = (
        "You are an enterprise Product Truth & Content Strategy Architect for GainARK OntoLeap. "
        "Generate a structured, high-value Content Brief for content writers and AI generation pipelines. "
        "Ensure content adheres 100% to verified product capabilities and avoids hallucinated drift."
    )

    user_prompt = (
        f"Topic / Content Goal: \"{topic}\"\n"
        f"Brand: {brand_name}\n"
        f"Industry Vertical: {vertical_name}\n\n"
        f"Verified Product Knowledge Graph (Canonical Truth):\n{triples_summary or 'None specified'}\n\n"
        f"Identified Content & Entity Gaps:\n{gaps_summary or 'None'}\n\n"
        "Generate a structured JSON response with exactly these keys:\n"
        "{\n"
        '  "target_alignment_score": 90,\n'
        '  "must_include_entities": ["list", "of", "required", "entities"],\n'
        '  "required_relational_triples": ["list of explicit Subject-Predicate-Object triples"],\n'
        '  "prohibited_claims": ["claims to strictly avoid that lack product backing"],\n'
        '  "suggested_outline": ["Section 1 Title", "Section 2 Title", ...],\n'
        '  "differentiation_angles": ["How to stand out against competitors in AI answers"]\n'
        "}\n"
        "Return ONLY the JSON object, nothing else."
    )

    raw_resp = _call_gemini(user_prompt, system_instruction=system_prompt, temperature=0.2)
    parsed = _parse_json_object(raw_resp)
    if parsed is not None:
        return {
            "target_alignment_score": _as_int(parsed.get("target_alignment_score"), 90),
            "must_include_entities": _as_str_list(parsed.get("must_include_entities")),
            "required_relational_triples": _as_str_list(parsed.get("required_relational_triples")),
            "prohibited_claims": _as_str_list(parsed.get("prohibited_claims")),
            "suggested_outline": _as_str_list(parsed.get("suggested_outline")),
            "differentiation_angles": _as_str_list(parsed.get("differentiation_angles")),
        }

    # Deterministic fallback brief
    return {
        "target_alignment_score": 85,
        "must_include_entities": [t.get("object", "") for t in triples[:5]],
        "required_relational_triples": [
            f"{t.get('subject')} --{t.get('predicate')}--> {t.get('object')}"
            for t in triples[:4]
        ],
        "prohibited_claims": [
            "Do not claim unsupported enterprise ERP capabilities",
            "Avoid claiming integrations not present in canonical Knowledge Graph",
            "Refrain from generic superlatives without verified metrics"
        ],
        "suggested_outline": [
            f"1. Executive Overview: Solving {topic}",
            "2. Architecture & Core Platform Capabilities",
            "3. Certified Compliance & Security Standards",
            "4. Supported Ecosystem & Native Integrations",
            "5. Verifiable ROI & Deployment Next Steps"
        ],
        "differentiation_angles": [
            "Explicitly anchor compliance certifications to Wikidata Q-IDs",
            "Highlight native integrations verified in schema.org structured data"
        ]
    }


def check_draft_alignment(
    draft_text: str,
    brand_name: str,
    triples: List[Dict[str, Any]],
    entities: List[str]
) -> Dict[str, Any]:
    """
    LLM-as-Judge draft analysis:
    Checks if a drafted blog post, landing page, or PR statement aligns with the Knowledge Graph.
    Identifies ungrounded claims, contradiction risks, and provides concrete rewrite advice.
    """
    triples_summary = "\n".join([
        f"- {t.get('subject')} --{t.get('predicate')}--> {t.get('object')}"
        for t in triples[:15]
    ])

    system_prompt = (
        "You are an AI Product Truth Auditor. Analyze the draft text against the canonical "
        "Product Knowledge Graph. Detect ungrounded claims, buzzword fluff, and factual contradictions."
    )

    user_prompt = (
        f"Brand: {brand_name}\n\n"
        f"Canonical Product Knowledge Graph:\n{triples_summary or 'No verified triples'}\n\n"
        f"Draft Text Under Review:\n\"\"\"\n{draft_text[:2500]}\n\"\"\"\n\n"
        "Evaluate the draft and return a JSON object with these keys:\n"
        "{\n"
        '  "grounded_claims": ["list of statements in draft that match product truth"],\n'
        '  "ungrounded_claims": ["statements making unverified or risky claims"],\n'
        '  "contradictions": ["claims that conflict with known triples or capabilities"],\n'
        '  "fluff_phrases": ["generic marketing buzzwords that lack concrete entities"],\n'
        '  "executive_verdict": "2-sentence executive summary of fidelity",\n'
        '  "rewrite_recommendations": ["Actionable step 1", "Actionable step 2"]\n'
        "}\n"
        "Return ONLY the JSON object, nothing else."
    )

    raw_resp = _call_gemini(user_prompt, system_instruction=system_prompt, temperature=0.1)
    parsed = _parse_json_object(raw_resp)
    if parsed is not None:
        return {
            "grounded_claims": _as_str_list(parsed.get("grounded_claims")),
            "ungrounded_claims": _as_str_list(parsed.get("ungrounded_claims")),
            "contradictions": _as_str_list(parsed.get("contradictions")),
            "fluff_phrases": _as_str_list(parsed.get("fluff_phrases")),
            "executive_verdict": str(parsed.get("executive_verdict") or "")[:2000],
            "rewrite_recommendations": _as_str_list(parsed.get("rewrite_recommendations")),
        }

    # Fallback heuristic judge
    return {
        "grounded_claims": [f"Mentions core brand {brand_name}"],
        "ungrounded_claims": ["Review draft against manual product specifications"],
        "contradictions": [],
        "fluff_phrases": ["all-in-one platform", "seamless synergy"],
        "executive_verdict": "Draft analyzed via heuristic fallback. Recommend adding explicit schema entities.",
        "rewrite_recommendations": [
            "Reference verified integration partners explicitly.",
            "Add verifiable compliance standard identifiers (e.g. SOC 2, ASC 606)."
        ]
    }
