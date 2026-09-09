"""
GainARK OntoLeap - 4-Tier Drift Alert Severity System
======================================================
Grok's recommendation: not all drift alerts carry equal commercial risk.
A false compliance claim (ASC 606) is a legal liability; a missing integration
mention is a missed opportunity. Classifying by severity makes the MGI report
actionable for different audiences (legal review vs. content team).

Tiers
-----
  CRITICAL  — Regulatory / compliance false claim. Legal + trust risk.
              Predicate: compliesWith. Standard body standards (ASC 606, IFRS 15, SOC 2, etc.)
  HIGH      — Core capability gap. Product does not demonstrably do what marketing claims.
              Predicate: automates, enables, powers, processes.
  MEDIUM    — Integration claim not proven in tech docs.
              Predicate: integratesWith, connects, syncs.
  LOW       — Weak signal drift — marginal confidence, generic phrasing, or minor omission.
              Everything else that passes the gate but isn't substantive.

Deduplication
-------------
  Executable Assertions (e.g. [Assert_Subscription_Pricing_Engine]) may fire
  against dozens of marketing phrasings of the same underlying concept.
  Only the first occurrence per assertion ID is kept; the rest are suppressed.

  Generic integration terms ("finance", "API calls", etc.) and self-referencing
  integration claims (brand integrates with itself) are also suppressed.
"""

import re
from typing import List, Optional, Tuple

# ── Tier constants ──────────────────────────────────────────────────────────
CRITICAL = "CRITICAL"
HIGH     = "HIGH"
MEDIUM   = "MEDIUM"
LOW      = "LOW"

_TIER_ORDER = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3}

# ── Pattern sets ────────────────────────────────────────────────────────────
_COMPLIANCE_STANDARDS = {
    "asc 606", "ifrs 15", "us gaap", "gaap", "sox", "soc 2", "soc2",
    "pci dss", "pci", "hipaa", "gdpr", "iso 27001", "iso27001",
    "hitech", "ccpa", "glba", "fca", "mifid",
}

_CAPABILITY_PREDICATES = {
    "automates", "enables", "powers", "processes", "generates",
    "calculates", "schedules", "manages", "performs", "executes",
}

_INTEGRATION_PREDICATES = {
    "integrateswith", "connects", "syncs", "integrates with",
    "syncs with", "connects to", "interfaces with",
}

_REGULATORY_KEYWORDS = {
    "compli", "certif", "regulat", "standard", "requirement",
    "assertion", "audit", "framework", "law", "act",
}

# Generic terms too vague to constitute a real integration claim.
# "brand integrates with <term>" is noise when term is one of these.
_GENERIC_INTEGRATION_TERMS = {
    "finance", "api calls", "auditors", "erp", "erp systems", "erp vendors",
    "payment processors", "billing", "accounting", "data", "platform",
    "software", "system", "service", "tool", "vendor", "provider",
    "solution", "cloud", "third-party", "partners",
}

# Regex to pull the assertion ID out of an Executable Assertion alert
_ASSERT_ID_RE = re.compile(r"\[Assert_([A-Za-z0-9_]+)\]")

# Regex to pull the integration target out of an Integration Drift alert
# e.g. "Marketing claims integration with 'QuickBooks', but..."
_INTEGRATION_TARGET_RE = re.compile(
    "integration with [\"'](.*?)[\"']", re.IGNORECASE
)


def classify_alert(alert_text: str) -> Tuple[str, str]:
    """
    Classify a drift alert string into a severity tier.
    Returns (tier, emoji_label) e.g. ("CRITICAL", "[CRITICAL]")
    """
    lower = alert_text.lower()

    # --- CRITICAL: compliance/regulatory false claim ---
    if any(std in lower for std in _COMPLIANCE_STANDARDS):
        return CRITICAL, "[CRITICAL]"

    if any(kw in lower for kw in _REGULATORY_KEYWORDS):
        if "complieswith" in lower or "certif" in lower or "regulat" in lower:
            return CRITICAL, "[CRITICAL]"

    # --- HIGH: core capability gap ---
    if any(pred in lower for pred in _CAPABILITY_PREDICATES):
        return HIGH, "[HIGH]"


    # --- MEDIUM: unproven integration ---
    if any(pred in lower for pred in _INTEGRATION_PREDICATES):
        return MEDIUM, "[MEDIUM]"

    if "integrates" in lower or "integration" in lower or "connects" in lower:
        return MEDIUM, "[MEDIUM]"

    # --- LOW: everything else ---
    return LOW, "[LOW]"


def tier_sort_key(alert_text: str) -> int:
    """Return sort key so CRITICAL sorts first."""
    tier, _ = classify_alert(alert_text)
    return _TIER_ORDER.get(tier, 9)


def prefix_severity(alert_text: str) -> str:
    """Prepend the severity emoji+tier label to an alert string."""
    tier, label = classify_alert(alert_text)
    return f"{label} {alert_text}"


def deduplicate_executable_assertions(alerts: List[str]) -> List[str]:
    """
    Collapse Executable Assertion alerts with the same assertion ID to a
    single representative alert (the first one seen).

    E.g. [Assert_Subscription_Pricing_Engine] firing 16 times for
    "subscription", "flat-rate subscriptions", "Subscription Billing", ...
    collapses to ONE alert showing the clearest marketing claim.
    """
    seen_assert_ids: set = set()
    deduped: List[str] = []
    for alert in alerts:
        m = _ASSERT_ID_RE.search(alert)
        if m:
            assert_id = m.group(1)
            if assert_id in seen_assert_ids:
                continue  # suppress duplicate
            seen_assert_ids.add(assert_id)
        deduped.append(alert)
    return deduped


def suppress_generic_integrations(
    alerts: List[str], brand: Optional[str] = None
) -> List[str]:
    """
    Remove Integration Drift alerts whose target is:
      (a) a generic term too vague to be a real integration claim, or
      (b) the brand itself (GLiNER self-reference false positive).
    """
    brand_lower = brand.lower() if brand else None
    filtered: List[str] = []
    for alert in alerts:
        # Only examine Integration Drift alerts
        if "integration drift" not in alert.lower():
            filtered.append(alert)
            continue
        m = _INTEGRATION_TARGET_RE.search(alert)
        if not m:
            filtered.append(alert)
            continue
        target = m.group(1).lower().strip()
        # Suppress if target is generic
        if target in _GENERIC_INTEGRATION_TERMS:
            continue
        # Suppress self-reference: brand integrates with brand
        if brand_lower and target == brand_lower:
            continue
        filtered.append(alert)
    return filtered


def apply_severity_to_alerts(
    alerts: List[str], brand: Optional[str] = None
) -> List[str]:
    """
    Takes a list of drift alert strings.
    1. Deduplicates Executable Assertions (same ID → keep first only).
    2. Suppresses generic / self-referencing integration claims.
    3. Prefixes each alert with its severity tier label.
    4. Sorts CRITICAL → HIGH → MEDIUM → LOW.
    """
    if not alerts:
        return alerts

    alerts = deduplicate_executable_assertions(alerts)
    alerts = suppress_generic_integrations(alerts, brand=brand)

    prefixed = [prefix_severity(a) for a in alerts]
    prefixed.sort(key=lambda a: _TIER_ORDER.get(classify_alert(a)[0], 9))
    return prefixed


def severity_breakdown(alerts: List[str]) -> dict:
    """Returns {CRITICAL: n, HIGH: n, MEDIUM: n, LOW: n} count of alerts."""
    counts = {CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0}
    for a in alerts:
        tier, _ = classify_alert(a)
        counts[tier] = counts.get(tier, 0) + 1
    return counts


def extract_drift_terms(alerts: List[str]) -> List[str]:
    """
    Pull the key quoted terms out of drift alert strings.

    E.g. "Marketing claims integration with 'QuickBooks', but ..."
    → ["QuickBooks"]

    Returns a deduplicated list preserving order, for use as search queries.
    """
    seen: set = set()
    terms: List[str] = []
    for alert in alerts:
        for m in re.findall(r"'([^']{2,80})'", alert):
            key = m.strip()
            if key and key.lower() not in seen:
                seen.add(key.lower())
                terms.append(key)
    return terms
