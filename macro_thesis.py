"""
Macro Thesis — pure logic
=========================
Validation gates, conviction scoring, liveness/retirement, and the screener
multiplier for macro theses. No I/O, no LLM — fully unit-testable.

A thesis tilts the screener's ranking only when it is live, source-grounded,
second-order, and falsifiable. See docs/superpowers/specs/2026-06-14-macro-thesis-agent-design.md
"""

import re
import contracts
import unicodedata
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

CONVICTION_FLOOR = 0.35
MULTIPLIER_SPAN  = 0.25     # conviction 1.0 -> +25%
MULTIPLIER_FLOOR = 0.90     # crowded-leader dampening
STALE_DAYS       = 14
MAX_LIVE_THESES  = 5        # a new thesis displaces the weakest beyond this

# Reddit / retail domains are supplementary only — never count as primary evidence
_SUPPLEMENTARY_DOMAINS = ("reddit.com", "stocktwits.com")

_VALID_SECTORS = {
    "technology", "healthcare", "financials", "consumer_cyclical", "industrials",
    "communication_services", "consumer_defensive", "energy", "basic_materials",
    "real_estate", "utilities",
}


TEXT_LIMITS = {"theme": 240, "invalidation_condition": 400}
_TEXT_LIMITS = TEXT_LIMITS

# Text that is stored and later replayed into prompts or reports is held to an
# ALLOWLIST of plain characters. A denylist of "dangerous" Unicode is never
# complete (invisible fillers, variation selectors, tag characters, ...).
_ALLOWED_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    " .,;:!?'\"()-+/%$&="
)
# Common typographic characters the model writes, mapped to plain equivalents
_PLAIN_EQUIVALENTS = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
    "\u2013": "-", "\u2014": "-", "\u2212": "-", "\u2026": "...",
})


def is_safe_text(value, limit: int, extra: str = "") -> bool:
    """True for a string of at most `limit` allowlisted characters (plus `extra`)."""
    if not isinstance(value, str) or len(value) > limit:
        return False
    allowed = _ALLOWED_CHARS | set(extra)
    return all(c in allowed for c in value)


def clean_text(value, limit: int, extra: str = "") -> str:
    """
    Coerce any value to one line of at most `limit` allowlisted characters.
    Typographic quotes and dashes become plain ones; every other character
    outside the allowlist becomes a space.
    """
    if value is None:
        return ""
    allowed = _ALLOWED_CHARS | set(extra)
    text = unicodedata.normalize("NFKC", str(value)).translate(_PLAIN_EQUIVALENTS)
    return " ".join("".join(c if c in allowed else " " for c in text).split())[:limit]


_TICKER_RE = re.compile(r"^[A-Z]{1,5}([.-][A-Z]{1,2})?$")


def is_ticker(name) -> bool:
    """True for a US ticker symbol like XOM or BRK.B — not a company name."""
    return isinstance(name, str) and bool(_TICKER_RE.match(name.strip().upper())) and " " not in name.strip()


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
      - every consensus_names_excluded entry is a ticker symbol
      - every beneficiary_sectors entry is a valid SECTOR_MAP key
      - conviction (mean of sub-scores) >= CONVICTION_FLOOR
    """
    # Free text is stored and replayed into later model prompts: keep it short
    # and single-line so it cannot carry a block of instructions.
    # Fail closed: a missing, empty or non-string value is rejected, not waved through.
    for field, limit in _TEXT_LIMITS.items():
        value = t.get(field)
        if not is_safe_text(value, limit) or not value.strip():
            return False, f"{field} must be a non-empty plain single line of at most {limit} characters"

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

    if not all(is_ticker(n) for n in t["consensus_names_excluded"]):
        return False, "consensus_names_excluded must be ticker symbols (e.g. XOM), not company names"

    sectors = t.get("beneficiary_sectors") or []
    if not sectors or any(s not in _VALID_SECTORS for s in sectors):
        return False, "beneficiary_sectors must all be valid SECTOR_MAP keys"

    if compute_conviction(t.get("conviction_breakdown", {})) < CONVICTION_FLOOR:
        return False, f"conviction below floor {CONVICTION_FLOOR}"

    return True, "ok"


def find_duplicate(candidate: Dict, live_theses: List[Dict]) -> Optional[str]:
    """
    Id of a live thesis this candidate repeats, or None. Two theses are the
    same bet when they favour the same sectors and avoid mostly the same
    crowded leaders (at least half of the smaller list).
    """
    sectors = set(candidate.get("beneficiary_sectors") or [])
    names = {str(n).strip().upper() for n in (candidate.get("consensus_names_excluded") or [])}
    for t in live_theses:
        if set(t.get("beneficiary_sectors") or []) != sectors:
            continue
        other = {str(n).strip().upper() for n in (t.get("consensus_names_excluded") or [])}
        smaller = min(len(names), len(other))
        if smaller and len(names & other) / smaller >= 0.5:
            return t.get("id")
    return None


def is_thesis_live(t: Dict, today: Optional[date] = None) -> bool:
    """
    A thesis tilts the screener only when live:
      status == "active", horizon in the future, last_validated within STALE_DAYS.
    """
    today = today or date.today()
    if t.get("status") != "active":
        return False
    try:
        if date.fromisoformat(str(t.get("horizon"))) <= today:
            return False
    except Exception:
        return False
    lv = t.get("last_validated")
    if lv:
        try:
            if (today - date.fromisoformat(str(lv))).days > STALE_DAYS:
                return False
        except Exception:
            return False
    return True


def retire_expired(theses: List[Dict], today: Optional[date] = None) -> List[Dict]:
    """Mark theses past their horizon as status='retired' (in place) and return the list."""
    today = today or date.today()
    for t in theses:
        try:
            if date.fromisoformat(str(t.get("horizon"))) <= today:
                t["status"] = "retired"
        except Exception:
            t["status"] = "retired"
    return theses


def enforce_cap(theses: List[Dict], today: Optional[date] = None,
                cap: int = MAX_LIVE_THESES) -> List[Dict]:
    """
    Keep at most `cap` live theses. The weakest beyond the cap are retired (in
    place) with retired_reason="displaced": lowest conviction first, ties going
    to the one validated longest ago, then the oldest. Returns the displaced theses.

    Without a cap the register only grows (no thesis had ever been invalidated
    by 2026-11-21) and the sector tilt spreads over nearly every sector.
    """
    today = today or date.today()
    live = [t for t in theses if is_thesis_live(t, today)]
    if len(live) <= cap:
        return []
    ranked = sorted(live, key=lambda t: (float(t.get("conviction", 0.0)),
                                         str(t.get("last_validated", "")),
                                         str(t.get("created_at", ""))), reverse=True)
    displaced = ranked[cap:]
    for t in displaced:
        t["status"] = "retired"
        t["retired_reason"] = "displaced"
        t["retired_at"] = today.isoformat()
    return displaced


def _sector_boost(sector: str, live_theses: List[Dict]) -> float:
    """Strongest matching conviction * MULTIPLIER_SPAN, or 0 when no live thesis names the sector."""
    convictions = [float(t.get("conviction", 0.0)) for t in live_theses
                   if sector in (t.get("beneficiary_sectors") or [])]
    return max(convictions) * MULTIPLIER_SPAN if convictions else 0.0


def macro_multiplier(symbol: str, sector: str, live_theses: List[Dict]) -> float:
    """
    Bounded, RELATIVE ranking tilt for one candidate.
      1. symbol in any live thesis's consensus_names_excluded -> MULTIPLIER_FLOOR (0.90)
      2. otherwise 1 + (this sector's boost - the mean boost over all sectors),
         where a sector's boost is its strongest live conviction * MULTIPLIER_SPAN.
    The tilts net to zero across sectors, so a register that names every sector
    is neutral rather than a uniform +15% (it was: 9 of 11 sectors boosted on
    2026-11-21). Sectors no thesis names now sit slightly below 1.0.
    Result is hard-capped to [MULTIPLIER_FLOOR, 1 + MULTIPLIER_SPAN].
    """
    symbol = symbol.strip().upper()
    for t in live_theses:
        excluded = {str(n).strip().upper() for n in (t.get("consensus_names_excluded") or [])}
        if symbol in excluded:
            return MULTIPLIER_FLOOR

    if not live_theses:
        return 1.0
    mean_boost = sum(_sector_boost(s, live_theses) for s in _VALID_SECTORS) / len(_VALID_SECTORS)
    mult = 1.0 + _sector_boost(sector, live_theses) - mean_boost
    return max(MULTIPLIER_FLOOR, min(mult, 1.0 + MULTIPLIER_SPAN))


def load_live_theses(path) -> List[Dict]:
    """
    Read theses.json and return only the live theses. Any error (absent,
    malformed) returns [] so the screener degrades to neutral (all 1.0).
    """
    import json
    from pathlib import Path
    try:
        p = Path(path)
        if not p.exists():
            return []
        data = contracts.read(p)
        theses = data.get("theses", []) if isinstance(data, dict) else []
        return [t for t in theses if is_thesis_live(t)]
    except Exception:
        return []
