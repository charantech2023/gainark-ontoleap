"""
GainARK OntoLeap — Compounding Truth Ledger Recorder
Appends version-controlled, timestamped audit events to truth_ledger/log.md.
"""

import os
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

logger = logging.getLogger("gainark.truth_ledger")

LEDGER_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(LEDGER_DIR, "log.md")


def record_audit_to_ledger(
    brand: str,
    domain: str,
    total_claims: int,
    total_tech: int,
    mgi: float,
    verified_claims: List[str],
    drift_alerts: List[str],
    unmarketed_caps: List[str]
) -> bool:
    """
    Append a Product Truth audit entry to log.md.
    """
    try:
        now_utc = datetime.now(timezone.utc)
        date_str = now_utc.strftime("%Y-%m-%d")
        time_str = now_utc.strftime("%H:%M:%S UTC")

        verified_preview = ", ".join(verified_claims[:5]) if verified_claims else "None verified"
        if len(verified_claims) > 5:
            verified_preview += f" (+{len(verified_claims) - 5} more)"

        drift_preview = f"{len(drift_alerts)} critical drift alerts" if drift_alerts else "0 critical regulatory drifts"
        gold_preview = f"{len(unmarketed_caps)} capabilities" if unmarketed_caps else "0 unmarketed capabilities"

        entry_lines = [
            f"\n### {date_str} {time_str} — [PRODUCT TRUTH AUDIT]",
            f"- **Target**: `{brand}` ({domain})",
            f"- **Grounding Index**: **{mgi:.1f}%** | Marketing Claims: {total_claims} | Technical Capabilities: {total_tech}",
            f"- **Verified Truth**: {verified_preview}",
            f"- **Drift Findings**: {drift_preview}",
            f"- **Unmarketed Engineering Gold**: {gold_preview}\n"
        ]

        entry_text = "\n".join(entry_lines)

        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(entry_text)

        logger.info("[TruthLedger] Appended audit entry for %s to %s", brand, LOG_FILE)
        return True
    except Exception as ex:
        logger.warning("[TruthLedger] Failed to append audit to ledger: %s", ex)
        return False


def record_alignment_to_ledger(
    company_name: str,
    competitors: List[str],
    vertical: str,
    advantages_count: int,
    vulnerabilities_count: int,
    table_stakes_count: int,
    briefs_count: int
) -> bool:
    """
    Append a Tri-Ontology Competitive Alignment entry to log.md.
    """
    try:
        now_utc = datetime.now(timezone.utc)
        date_str = now_utc.strftime("%Y-%m-%d")
        time_str = now_utc.strftime("%H:%M:%S UTC")

        comp_str = ", ".join(competitors)

        entry_lines = [
            f"\n### {date_str} {time_str} — [TRI-ONTOLOGY COMPETITIVE ALIGNMENT]",
            f"- **Matchup**: `{company_name}` vs. `{comp_str}` ({vertical})",
            f"- **Company Advantages**: **{advantages_count}** verified vectors",
            f"- **Competitor Fluff Gaps**: **{vulnerabilities_count}** unbacked competitor claims to exploit",
            f"- **Table Stakes**: {table_stakes_count} shared requirements",
            f"- **Synthesized Battlecards**: {briefs_count} counter-positioning angles generated\n"
        ]

        entry_text = "\n".join(entry_lines)

        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(entry_text)

        logger.info("[TruthLedger] Appended alignment entry for %s to %s", company_name, LOG_FILE)
        return True
    except Exception as ex:
        logger.warning("[TruthLedger] Failed to append alignment to ledger: %s", ex)
        return False
