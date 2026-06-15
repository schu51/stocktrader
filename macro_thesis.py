"""
Macro Thesis — pure logic
=========================
Validation gates, conviction scoring, liveness/retirement, and the screener
multiplier for macro theses. No I/O, no LLM — fully unit-testable.

A thesis tilts the screener's ranking only when it is live, source-grounded,
second-order, and falsifiable. See docs/superpowers/specs/2026-06-14-macro-thesis-agent-design.md
"""

from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

CONVICTION_FLOOR = 0.35
MULTIPLIER_SPAN  = 0.25     # conviction 1.0 -> +25%
MULTIPLIER_FLOOR = 0.90     # crowded-leader dampening
STALE_DAYS       = 14

# Reddit / retail domains are supplementary only — never count as primary evidence
_SUPPLEMENTARY_DOMAINS = ("reddit.com", "stocktwits.com")

_VALID_SECTORS = {
    "technology", "healthcare", "financials", "consumer_cyclical", "industrials",
    "communication_services", "consumer_defensive", "energy", "basic_materials",
    "real_estate", "utilities",
}


def compute_conviction(breakdown: Dict[str, float]) -> float:
    """Conviction is the equal-weighted mean of the four sub-scores."""
    keys = ("source_corroboration", "causal_directness", "non_consensus", "invalidation_clarity")
    vals = [float(breakdown.get(k, 0.0)) for k in keys]
    return sum(vals) / len(keys)


def _has_primary_source(sources: List[str]) -> bool:
    """True if at least one source is NOT a supplementary (retail) domain."""
    for s in sources or []:
        low = s.lower()
        if not any(dom in low for dom in _SUPPLEMENTARY_DOMAINS):
            return True
    return False


def validate_thesis(t: Dict) -> Tuple[bool, str]:
    """
    Gate a thesis. Returns (accepted, reason). Rejects unless ALL hold:
      - >=1 primary-tier source (not Reddit-only, not empty)
      - non-empty invalidation_condition AND a future horizon
      - second_order is True AND consensus_names_excluded is non-empty
      - every beneficiary_sectors entry is a valid SECTOR_MAP key
      - conviction (mean of sub-scores) >= CONVICTION_FLOOR
    """
    sources = t.get("sources") or []
    if not sources or not _has_primary_source(sources):
        return False, "needs at least one primary-tier source (Reddit-only/empty rejected)"

    if not (t.get("invalidation_condition") or "").strip():
        return False, "missing invalidation_condition"

    horizon = t.get("horizon")
    try:
        if not horizon or date.fromisoformat(str(horizon)) <= date.today():
            return False, "horizon missing or not in the future"
    except Exception:
        return False, "horizon not a valid ISO date"

    if not t.get("second_order") or not (t.get("consensus_names_excluded") or []):
        return False, "not second-order: must name consensus leaders excluded"

    sectors = t.get("beneficiary_sectors") or []
    if not sectors or any(s not in _VALID_SECTORS for s in sectors):
        return False, "beneficiary_sectors must all be valid SECTOR_MAP keys"

    if compute_conviction(t.get("conviction_breakdown", {})) < CONVICTION_FLOOR:
        return False, f"conviction below floor {CONVICTION_FLOOR}"

    return True, "ok"
