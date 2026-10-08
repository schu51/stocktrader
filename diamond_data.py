# diamond_data.py
"""
Diamond screen: financial data
==============================
Turns SEC XBRL "frames" into per-company figures the inflection screen can
judge (diamond_screen.py). A frame is one figure for every company for one
period, so a few dozen requests cover the whole market. No key is needed;
the SEC requires a contact address in the User-Agent.

Two traps this module exists to handle:

  Fourth quarters   A company reports the full year in its 10-K, not Q4 on
                    its own. Q4 is the year minus the three reported
                    quarters. If one of the three is missing, Q4 is unknown.
  Latest quarter    Companies file at different times. Each is judged on its
                    own latest quarter; one more than STALE_DAYS old is dropped.

Unknown is never zero. A figure that cannot be established is None, and a
company without the figures the gates need gets no metrics at all.
"""

from datetime import date, timedelta
from typing import Dict, List, Optional

QUARTER_DAYS = (80, 100)       # a fiscal quarter, allowing 12- and 14-week quarters
YEAR_DAYS = (350, 380)         # a fiscal year, allowing 52- and 53-week years
QUARTER_GAP_DAYS = (80, 100)   # between one quarter end and the next
STALE_DAYS = 160               # latest quarter older than this at the cutoff: dropped


def _d(text) -> Optional[date]:
    try:
        return date.fromisoformat(str(text)[:10])
    except (TypeError, ValueError):
        return None


def _num(value) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    return float(value)


def _periods(rows: List[dict]) -> List[tuple]:
    """(start, end, value) for rows that parse, in a fixed order."""
    out = []
    for r in rows or []:
        start, end, val = _d(r.get("start")), _d(r.get("end")), _num(r.get("val"))
        if start and end and val is not None and end > start:
            out.append((start, end, val))
    # Largest absolute value last, so a period reported twice resolves the same way every run
    return sorted(out, key=lambda p: (p[1], p[0], abs(p[2])))


def quarterly_series(rows: List[dict]) -> Dict[date, float]:
    """Quarter end -> value, with fourth quarters derived from the full year where needed."""
    quarters: Dict[date, tuple] = {}
    years = []
    for start, end, val in _periods(rows):
        days = (end - start).days + 1
        if QUARTER_DAYS[0] <= days <= QUARTER_DAYS[1]:
            quarters[end] = (start, val)
        elif YEAR_DAYS[0] <= days <= YEAR_DAYS[1]:
            years.append((start, end, val))
    series = {end: val for end, (_, val) in quarters.items()}
    for y_start, y_end, y_val in years:
        inside = [(end, val) for end, (start, val) in quarters.items()
                  if start >= y_start - timedelta(days=5) and end <= y_end + timedelta(days=5)]
        reported_last = any(abs((end - y_end).days) <= 5 for end, _ in inside)
        if len(inside) == 3 and not reported_last:
            series[y_end] = y_val - sum(val for _, val in inside)
    return series


def last_quarters(series: Dict[date, float], cutoff: date, n: int = 8) -> Optional[List[float]]:
    """The n latest consecutive quarters ending on or before cutoff, newest first. None if there is a gap."""
    ends = sorted((e for e in series if e <= cutoff), reverse=True)[:n]
    if len(ends) < n:
        return None
    for newer, older in zip(ends, ends[1:]):
        if not QUARTER_GAP_DAYS[0] <= (newer - older).days <= QUARTER_GAP_DAYS[1]:
            return None
    return [series[e] for e in ends]


def instant_at(rows: List[dict], when: date, tolerance_days: int = 20) -> Optional[float]:
    """A point-in-time figure (cash, backlog) reported nearest to `when`, within the tolerance."""
    best = None
    for r in rows or []:
        end, val = _d(r.get("end")), _num(r.get("val"))
        if end is None or val is None or abs((end - when).days) > tolerance_days:
            continue
        key = (abs((end - when).days), -abs(val))
        if best is None or key < best[0]:
            best = (key, val)
    return best[1] if best else None


def _growth(now: Optional[float], before: Optional[float]) -> Optional[float]:
    if now is None or before is None or before <= 0:
        return None
    return now / before - 1


def company_metrics(fin: dict, cutoff: date) -> Optional[dict]:
    """Everything the screen needs for one company as of `cutoff`, or None if it cannot be established."""
    fin = fin or {}
    revenue_series = quarterly_series(fin.get("revenue"))
    op_series = quarterly_series(fin.get("op_income"))
    revenue = last_quarters(revenue_series, cutoff)
    if revenue is None or min(revenue) <= 0:
        return None
    latest_end = max(e for e in revenue_series if e <= cutoff)
    if (cutoff - latest_end).days > STALE_DAYS:
        return None
    op = last_quarters(op_series, latest_end)
    if op is None or max(e for e in op_series if e <= latest_end) != latest_end:
        return None

    ttm_rev, prior_rev = sum(revenue[:4]), sum(revenue[4:])
    ttm_op, prior_op = sum(op[:4]), sum(op[4:])
    margins = [o / r for o, r in zip(op[:4], revenue[:4])]

    gross = last_quarters(quarterly_series(fin.get("gross_profit")), latest_end)
    if gross is None:
        cost = last_quarters(quarterly_series(fin.get("cost_of_revenue")), latest_end)
        gross = [r - c for r, c in zip(revenue, cost)] if cost else None
    gm = sum(gross[:4]) / ttm_rev if gross else None
    prior_gm = sum(gross[4:]) / prior_rev if gross else None

    shares = last_quarters(quarterly_series(fin.get("shares")), latest_end)
    rd = last_quarters(quarterly_series(fin.get("rd")), latest_end, n=4)

    year_ago = latest_end - timedelta(days=365)
    cash = instant_at(fin.get("cash"), latest_end)
    investments = instant_at(fin.get("investments"), latest_end)
    if cash is not None and investments is not None:
        cash += investments

    return {
        "latest_quarter_end": latest_end.isoformat(),
        "ttm_revenue": ttm_rev, "prior_ttm_revenue": prior_rev,
        "revenue_growth": ttm_rev / prior_rev - 1,
        "q_growth_now": revenue[0] / revenue[4] - 1,
        "q_growth_2q_ago": revenue[2] / revenue[6] - 1,
        "ttm_op_income": ttm_op,
        "ttm_op_margin": ttm_op / ttm_rev, "prior_ttm_op_margin": prior_op / prior_rev,
        "margin_change": ttm_op / ttm_rev - prior_op / prior_rev,
        "seq_improvements": sum(1 for newer, older in zip(margins, margins[1:]) if newer > older),
        "latest_q_profitable": op[0] > 0, "year_ago_q_profitable": op[4] > 0,
        "ttm_gross_margin": gm,
        "gross_margin_change": gm - prior_gm if gm is not None else None,
        "cash": cash,
        "runway_years": cash / -ttm_op if cash is not None and ttm_op < 0 else None,
        "shares": shares[0] if shares else None,
        "shares_growth": _growth(shares[0], shares[4]) if shares else None,
        "backlog_growth": _growth(instant_at(fin.get("backlog"), latest_end), instant_at(fin.get("backlog"), year_ago)),
        "prepaid_growth": _growth(instant_at(fin.get("prepaid"), latest_end), instant_at(fin.get("prepaid"), year_ago)),
        "rd_share": sum(rd) / ttm_rev if rd else None,
    }
