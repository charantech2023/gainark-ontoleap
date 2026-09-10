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
from typing import Dict, Any, Optional

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
