# diamond_screen.py
"""
Diamond screen: gates and score
===============================
Decides which companies show a financial inflection, and ranks them.

The pattern being looked for (owner, 2026-10-08, with Palantir before it
became a household name as the example): losses shrinking quarter after
quarter until they cross into profit, revenue still growing, and margins
that suggest a product customers pay up for.

A company must pass every gate. Those that do are scored 0-100 so they can
be ranked. Every threshold below is a starting value: the test of whether
they are right is whether the screen, run on early-2023 data, finds Palantir
(diamond_run.py --as-of).

Pure functions on the metrics dict from diamond_data.company_metrics. An
unknown figure fails the gate that needs it; nothing here raises.
"""

from typing import Dict, List, Optional

MIN_TTM_REVENUE = 50e6          # a real business, not a concept
MIN_REVENUE_GROWTH = 0.15       # trailing twelve months against the twelve before
MIN_MARGIN_CHANGE = 0.05        # operating margin up at least five points on the year
MIN_OP_MARGIN = -0.15           # near or past break-even now
MAX_PRIOR_OP_MARGIN = 0.05      # was not already comfortably profitable a year ago
MIN_GROSS_MARGIN = 0.40         # edge proxy...
MIN_GROSS_MARGIN_RISE = 0.03    # ...or a gross margin clearly rising
MIN_RUNWAY_YEARS = 2.0          # a loss-maker must have this much cash at its current loss
MAX_SHARE_GROWTH = 0.10         # dilution

GATES = ("revenue", "growth", "turning", "break_even", "already_rich", "edge", "runway", "dilution")


def _known(*values) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and v == v for v in values)


def gate_failures(m: Dict) -> List[str]:
    """Names of the gates this company fails, in GATES order. Empty means it passes."""
    m = m or {}
    failed = []

    def check(name: str, ok: bool):
        if not ok:
            failed.append(name)

    check("revenue", _known(m.get("ttm_revenue")) and m["ttm_revenue"] >= MIN_TTM_REVENUE)
    check("growth", _known(m.get("revenue_growth")) and m["revenue_growth"] >= MIN_REVENUE_GROWTH)
    check("turning", _known(m.get("margin_change")) and m["margin_change"] >= MIN_MARGIN_CHANGE)
    check("break_even", _known(m.get("ttm_op_margin")) and m["ttm_op_margin"] >= MIN_OP_MARGIN)
    check("already_rich", _known(m.get("prior_ttm_op_margin")) and m["prior_ttm_op_margin"] <= MAX_PRIOR_OP_MARGIN)
    gm, rise = m.get("ttm_gross_margin"), m.get("gross_margin_change")
    check("edge", (_known(gm) and gm >= MIN_GROSS_MARGIN) or (_known(rise) and rise >= MIN_GROSS_MARGIN_RISE))
    profitable = _known(m.get("ttm_op_income")) and m["ttm_op_income"] >= 0
    check("runway", profitable or (_known(m.get("runway_years")) and m["runway_years"] >= MIN_RUNWAY_YEARS))
    check("dilution", _known(m.get("shares_growth")) and m["shares_growth"] <= MAX_SHARE_GROWTH)
    return failed


def _part(value: Optional[float], zero: float, full: float, points: float) -> float:
    """0 at `zero`, `points` at `full`, straight line between, clamped. Unknown scores 0."""
    if not _known(value) or full == zero:
        return 0.0
    return max(0.0, min(1.0, (value - zero) / (full - zero))) * points


def score(m: Dict) -> Dict:
    """Rank score for a company that passed the gates."""
    m = m or {}
    inflection = _part(m.get("margin_change"), 0.0, 0.20, 20)                    # 20 points of margin = full
    inflection += (m.get("seq_improvements") or 0) / 3 * 10                      # each quarter better than the last
    if m.get("latest_q_profitable") and m.get("year_ago_q_profitable") is False:
        inflection += 10                                                         # crossed into profit this year

    revenue = _part(m.get("revenue_growth"), MIN_REVENUE_GROWTH, 0.50, 20)
    if _known(m.get("q_growth_now"), m.get("q_growth_2q_ago")) and m["q_growth_now"] > m["q_growth_2q_ago"]:
        revenue += 5                                                             # growth is speeding up

    edge = _part(m.get("ttm_gross_margin"), MIN_GROSS_MARGIN, 0.80, 10) + _part(m.get("gross_margin_change"), 0.0, 0.05, 5)

    # Demand: backlog and prepaid revenue growing faster than revenue itself.
    # Scored only on what the company reports; nothing reported spreads the weight.
    growth = m.get("revenue_growth") if _known(m.get("revenue_growth")) else 0.0
    reported = [g for g in (m.get("backlog_growth"), m.get("prepaid_growth")) if _known(g)]
    demand = (sum(_part(g - growth, -0.10, 0.10, 10) for g in reported) / len(reported)) if reported else None

    safety = 5.0 if _known(m.get("ttm_op_income")) and m["ttm_op_income"] >= 0 \
        else _part(m.get("runway_years"), MIN_RUNWAY_YEARS, 4.0, 5)
    safety += 5 - _part(m.get("shares_growth"), 0.0, MAX_SHARE_GROWTH, 5) if _known(m.get("shares_growth")) else 0.0

    parts = {"inflection": round(min(inflection, 40.0), 1), "revenue": round(min(revenue, 25.0), 1),
             "edge": round(min(edge, 15.0), 1), "demand": None if demand is None else round(demand, 1),
             "safety": round(min(safety, 10.0), 1)}
    rest = parts["inflection"] + parts["revenue"] + parts["edge"] + parts["safety"]
    parts["total"] = round(rest / 90 * 100, 1) if demand is None else round(rest + parts["demand"], 1)
    return parts
