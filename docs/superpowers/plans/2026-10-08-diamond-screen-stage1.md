# Diamond Screen Stage 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A weekly, numbers-only watchlist of about 40 small and mid-sized companies whose finances are turning from loss to profit, with no trading.

**Architecture:** Four small modules. `diamond_data.py` turns SEC XBRL "frames" (one request = one figure for every company for one period) into per-company metrics. `diamond_screen.py` applies gates and a 0-100 score to those metrics and is pure. `diamond_universe.py` holds the listing, size, volume and industry filters. `diamond_run.py` wires them together, writes the output files and guards against bad data. Financial gates run first, so prices and industry codes are only fetched for the few hundred companies that survive them.

**Tech Stack:** Python 3.12, `requests`, `pandas`/`numpy`, `yfinance` (all already in `requirements.txt`), pytest, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-08-diamond-screen-stage1-design.md`

## Global Constraints

- No orders, no broker calls, no change to any large-cap trading rule.
- Unknown is never zero: a missing figure fails the gate that needs it, or is skipped if optional. No exception may escape a per-company calculation.
- Size $300M to $10B; median 60-session dollar volume at least $5M; price at least $5; SIC 6000-6799 excluded.
- TTM = trailing twelve months = the latest four quarters. "Prior TTM" = the four quarters before those.
- SEC requests: at most 5 a second, `User-Agent` from `config.SEC_EDGAR_USER_AGENT`, retried with backoff. HTTP 404 on a frame means "no data for that period" and is empty; any other failure fails the run.
- Fail closed: fewer than 2,500 companies with revenue in the reference quarter, or prices missing for more than 20% of the companies asked for, writes nothing and exits non-zero.
- Every threshold is a named constant at the top of `diamond_screen.py` or `diamond_universe.py`.
- Tests inject fetchers; no test touches the network.
- Match the repo: module docstring explaining the why, plain-English comments, atomic JSON writes (`tmp` then `os.replace`).

## Review Focus

1. A company that changed its fiscal year end has quarters that do not follow one another: it must come out unknown, not be summed across a gap. (Task 1)
2. Revenue of zero or a negative figure in a quarter must not divide by zero or produce an absurd margin: the company is unknown. (Task 1)
3. A period reported twice in the pooled rows (restatement, or the same quarter appearing in two frames) must resolve the same way every run. (Task 1)
4. A company with two listed share classes must appear once. (Task 4)
5. A 404 for the newest quarter is normal; a 403 or 500 is not and must stop the run rather than shrink the list silently. (Task 3)

---

### Task 1: Period math and metrics

**Files:**
- Create: `diamond_data.py`
- Test: `tests/test_diamond_data.py`

**Interfaces:**
- Produces:
  - `quarterly_series(rows: list[dict]) -> dict[date, float]` — rows are `{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "val": number}` for one company and one figure, quarters and full years mixed. Returns quarter end date -> value, including fourth quarters derived from the full year.
  - `last_quarters(series: dict[date, float], cutoff: date, n: int = 8) -> Optional[list[float]]` — the `n` latest consecutive quarters ending on or before `cutoff`, newest first, or `None`.
  - `instant_at(rows: list[dict], when: date, tolerance_days: int = 20) -> Optional[float]`
  - `company_metrics(fin: dict, cutoff: date) -> Optional[dict]` — `fin` is `{"revenue": rows, "op_income": rows, "gross_profit": rows, "cost_of_revenue": rows, "rd": rows, "shares": rows, "cash": rows, "investments": rows, "backlog": rows, "prepaid": rows}`. Returns the metrics dict below or `None`.
  - Metrics keys: `latest_quarter_end` (ISO str), `ttm_revenue`, `prior_ttm_revenue`, `revenue_growth`, `q_growth_now`, `q_growth_2q_ago`, `ttm_op_income`, `ttm_op_margin`, `prior_ttm_op_margin`, `margin_change`, `seq_improvements` (0-3), `latest_q_profitable`, `year_ago_q_profitable`, `ttm_gross_margin`, `gross_margin_change`, `cash`, `runway_years`, `shares`, `shares_growth`, `backlog_growth`, `prepaid_growth`, `rd_share`. Any of the last eleven may be `None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_diamond_data.py
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from diamond_data import company_metrics, instant_at, last_quarters, quarterly_series


def q(start, end, val):
    return {"start": start, "end": end, "val": val}


CAL_2025 = [q("2025-01-01", "2025-03-31", 10), q("2025-04-01", "2025-06-30", 20),
            q("2025-07-01", "2025-09-30", 30), q("2025-01-01", "2025-12-31", 100)]


def test_fourth_quarter_is_the_year_minus_the_three_reported():
    s = quarterly_series(CAL_2025)
    assert s[date(2025, 12, 31)] == 40
    assert len(s) == 4


def test_fourth_quarter_is_unknown_when_a_quarter_is_missing():
    s = quarterly_series(CAL_2025[1:])           # Q1 never reported
    assert date(2025, 12, 31) not in s


def test_a_reported_fourth_quarter_is_not_overwritten():
    s = quarterly_series(CAL_2025 + [q("2025-10-01", "2025-12-31", 41)])
    assert s[date(2025, 12, 31)] == 41


def test_fiscal_year_not_ending_in_december():
    rows = [q("2024-10-01", "2024-12-31", 5), q("2025-01-01", "2025-03-31", 6),
            q("2025-04-01", "2025-06-30", 7), q("2024-10-01", "2025-09-30", 30)]
    assert quarterly_series(rows)[date(2025, 9, 30)] == 12


def test_duplicate_period_resolves_to_the_larger_absolute_value_every_time():
    rows = [q("2025-01-01", "2025-03-31", 10), q("2025-01-01", "2025-03-31", 12)]
    assert quarterly_series(rows) == quarterly_series(list(reversed(rows))) == {date(2025, 3, 31): 12}


def _eight(vals, last_end=date(2026, 6, 30)):
    """Eight calendar quarters ending at last_end, newest value first in `vals`."""
    ends = [date(2026, 6, 30), date(2026, 3, 31), date(2025, 12, 31), date(2025, 9, 30),
            date(2025, 6, 30), date(2025, 3, 31), date(2024, 12, 31), date(2024, 9, 30)]
    return dict(zip(ends, vals))


def test_last_quarters_returns_newest_first_and_respects_the_cutoff():
    s = _eight([8, 7, 6, 5, 4, 3, 2, 1])
    assert last_quarters(s, date(2026, 7, 15)) == [8, 7, 6, 5, 4, 3, 2, 1]
    assert last_quarters(s, date(2026, 5, 1)) is None            # only seven quarters by then


def test_a_gap_between_quarters_is_unknown_not_summed():
    s = _eight([8, 7, 6, 5, 4, 3, 2, 1])
    del s[date(2025, 9, 30)]
    s[date(2024, 6, 30)] = 0                                      # eight values, but not consecutive
    assert last_quarters(s, date(2026, 7, 15)) is None


def test_instant_at_takes_the_nearest_within_tolerance():
    rows = [{"end": "2026-06-30", "val": 50}, {"end": "2026-03-31", "val": 40}]
    assert instant_at(rows, date(2026, 6, 28)) == 50
    assert instant_at(rows, date(2026, 1, 15)) is None


def _rows(series):
    """{end: val} -> frame rows with a 91-day quarter."""
    from datetime import timedelta
    return [q((end - timedelta(days=90)).isoformat(), end.isoformat(), val) for end, val in series.items()]


def _fin(**over):
    fin = {
        "revenue":   _rows(_eight([130, 125, 120, 115, 100, 96, 92, 88])),
        "op_income": _rows(_eight([6, 2, -1, -3, -8, -10, -12, -14])),
        "gross_profit": _rows(_eight([91, 87, 84, 80, 68, 65, 62, 59])),
        "cost_of_revenue": [], "rd": _rows(_eight([20] * 8)),
        "shares":    _rows(_eight([105, 105, 104, 104, 100, 100, 100, 100])),
        "cash": [{"end": "2026-06-30", "val": 300}], "investments": [],
        "backlog": [{"end": "2026-06-30", "val": 260}, {"end": "2025-06-30", "val": 200}],
        "prepaid": [],
    }
    fin.update(over)
    return fin


def test_metrics_for_a_company_turning_profitable():
    m = company_metrics(_fin(), date(2026, 8, 15))
    assert m["latest_quarter_end"] == "2026-06-30"
    assert (m["ttm_revenue"], m["prior_ttm_revenue"]) == (490, 376)
    assert round(m["revenue_growth"], 4) == round(490 / 376 - 1, 4)
    assert round(m["ttm_op_margin"], 4) == round(4 / 490, 4)
    assert round(m["prior_ttm_op_margin"], 4) == round(-44 / 376, 4)
    assert m["seq_improvements"] == 3
    assert m["latest_q_profitable"] is True and m["year_ago_q_profitable"] is False
    assert round(m["ttm_gross_margin"], 4) == round(342 / 490, 4)
    assert m["runway_years"] is None                 # profitable over the year: runway does not apply
    assert round(m["shares_growth"], 4) == 0.05
    assert round(m["backlog_growth"], 4) == 0.30 and m["prepaid_growth"] is None
    assert round(m["rd_share"], 4) == round(80 / 490, 4)


def test_runway_is_cash_over_the_yearly_operating_loss():
    m = company_metrics(_fin(op_income=_rows(_eight([-10, -15, -20, -25, -40, -40, -40, -40]))), date(2026, 8, 15))
    assert round(m["runway_years"], 3) == round(300 / 70, 3)


def test_gross_profit_falls_back_to_revenue_minus_cost():
    m = company_metrics(_fin(gross_profit=[], cost_of_revenue=_rows(_eight([39, 38, 36, 35, 32, 31, 30, 29]))),
                        date(2026, 8, 15))
    assert round(m["ttm_gross_margin"], 4) == round((490 - 148) / 490, 4)


def test_unknown_inputs_give_none_never_an_exception():
    assert company_metrics(_fin(revenue=[]), date(2026, 8, 15)) is None
    assert company_metrics(_fin(op_income=_rows(_eight([1, 1, 1]))), date(2026, 8, 15)) is None
    zero = _fin(revenue=_rows(_eight([130, 125, 120, 115, 0, 96, 92, 88])))
    assert company_metrics(zero, date(2026, 8, 15)) is None      # a quarter with no revenue: unknown
    assert company_metrics({}, date(2026, 8, 15)) is None
    m = company_metrics(_fin(shares=[], cash=[], gross_profit=[]), date(2026, 8, 15))
    assert m["shares"] is None and m["cash"] is None and m["ttm_gross_margin"] is None


def test_stale_company_is_dropped():
    assert company_metrics(_fin(), date(2027, 1, 15)) is None    # latest quarter is more than 160 days old
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_diamond_data.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'diamond_data'`

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests/test_diamond_data.py -q`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add diamond_data.py tests/test_diamond_data.py
git commit -m "Diamond screen: quarter math and per-company metrics from SEC figures"
```

---

### Task 2: Gates and score

**Files:**
- Create: `diamond_screen.py`
- Test: `tests/test_diamond_screen.py`

**Interfaces:**
- Consumes: the metrics dict from `diamond_data.company_metrics`.
- Produces:
  - `gate_failures(m: dict) -> list[str]` — names of failed gates, in the fixed order `revenue, growth, turning, break_even, already_rich, edge, runway, dilution`; empty means it passes.
  - `score(m: dict) -> dict` — `{"total", "inflection", "revenue", "edge", "demand", "safety"}`; `demand` is `None` when neither backlog nor prepaid revenue is reported, and `total` is then rescaled to 100.
  - `GATES: tuple[str, ...]` — the gate names.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_diamond_screen.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from diamond_screen import GATES, gate_failures, score


def _m(**over):
    m = {"ttm_revenue": 490e6, "revenue_growth": 0.30, "q_growth_now": 0.30, "q_growth_2q_ago": 0.25,
         "ttm_op_income": 4e6, "ttm_op_margin": 0.01, "prior_ttm_op_margin": -0.12, "margin_change": 0.13,
         "seq_improvements": 3, "latest_q_profitable": True, "year_ago_q_profitable": False,
         "ttm_gross_margin": 0.70, "gross_margin_change": 0.02, "cash": 300e6, "runway_years": None,
         "shares_growth": 0.05, "backlog_growth": 0.30, "prepaid_growth": None}
    m.update(over)
    return m


def test_a_company_turning_profitable_passes_every_gate():
    assert gate_failures(_m()) == []


def test_each_gate_at_its_boundary():
    assert gate_failures(_m(ttm_revenue=49.9e6)) == ["revenue"]
    assert gate_failures(_m(ttm_revenue=50e6)) == []
    assert gate_failures(_m(revenue_growth=0.149)) == ["growth"]
    assert gate_failures(_m(margin_change=0.049)) == ["turning"]
    assert gate_failures(_m(ttm_op_margin=-0.151)) == ["break_even"]
    assert gate_failures(_m(prior_ttm_op_margin=0.051)) == ["already_rich"]
    assert gate_failures(_m(ttm_gross_margin=0.39, gross_margin_change=0.029)) == ["edge"]
    assert gate_failures(_m(ttm_gross_margin=0.39, gross_margin_change=0.03)) == []      # low but rising
    assert gate_failures(_m(shares_growth=0.101)) == ["dilution"]


def test_runway_gate_only_binds_a_loss_maker():
    losing = dict(ttm_op_income=-50e6, ttm_op_margin=-0.10)
    assert gate_failures(_m(**losing, runway_years=1.9)) == ["runway"]
    assert gate_failures(_m(**losing, runway_years=2.0)) == []
    assert gate_failures(_m(**losing, runway_years=None)) == ["runway"]     # losing money, cash unknown


def test_unknown_fails_the_gate_that_needs_it_without_error():
    assert gate_failures(_m(ttm_gross_margin=None, gross_margin_change=None)) == ["edge"]
    assert gate_failures(_m(shares_growth=None)) == ["dilution"]
    assert set(gate_failures({})) == set(GATES)


def test_score_parts_and_total():
    s = score(_m())
    assert s["inflection"] == 33.0          # 13 of 20 margin points -> 13, three improvements -> 10, crossing -> 10
    assert s["revenue"] == round((0.30 - 0.15) / 0.35 * 20 + 5, 1)
    assert s["edge"] == round((0.70 - 0.40) / 0.40 * 10 + 0.02 / 0.05 * 5, 1)
    assert s["demand"] == 5.0               # backlog grew exactly as fast as revenue: half marks, on the full 10
    assert s["safety"] == 7.5               # profitable -> 5; 5% dilution -> 2.5
    assert s["total"] == round(s["inflection"] + s["revenue"] + s["edge"] + s["demand"] + s["safety"], 1)


def test_demand_weight_is_spread_when_nothing_is_reported():
    s = score(_m(backlog_growth=None))
    assert s["demand"] is None
    rest = s["inflection"] + s["revenue"] + s["edge"] + s["safety"]
    assert s["total"] == round(rest / 90 * 100, 1)


def test_scores_are_clamped():
    s = score(_m(margin_change=0.9, revenue_growth=5.0, ttm_gross_margin=0.99, gross_margin_change=0.5,
                 shares_growth=-0.2, backlog_growth=9.0, prepaid_growth=9.0))
    assert (s["inflection"], s["revenue"], s["edge"], s["demand"], s["safety"]) == (40.0, 25.0, 15.0, 10.0, 10.0)
    assert s["total"] == 100.0
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_diamond_screen.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'diamond_screen'`

- [ ] **Step 3: Implement**

```python
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
```

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests/test_diamond_screen.py -q`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add diamond_screen.py tests/test_diamond_screen.py
git commit -m "Diamond screen: inflection gates and rank score"
```

---

### Task 3: Fetching SEC frames

**Files:**
- Modify: `diamond_data.py` (append)
- Test: `tests/test_diamond_data.py` (append)

**Interfaces:**
- Consumes: `company_metrics`.
- Produces:
  - `FIGURES: dict[str, dict]` — figure name -> `{"taxonomy", "tags": [...], "unit", "kind": "duration" | "instant"}`.
  - `class FrameError(Exception)`
  - `fetch_frame(taxonomy: str, tag: str, unit: str, period: str, session=None) -> list[dict]` — rows for one frame; `[]` on HTTP 404; raises `FrameError` otherwise.
  - `periods_for(cutoff: date) -> dict` — `{"quarters": [...], "years": [...], "instants": [...]}` of frame period names (`CY2026Q2`, `CY2025`, `CY2026Q2I`).
  - `load_financials(cutoff: date, fetch=fetch_frame) -> tuple[dict[int, dict], dict]` — `{cik: fin}` in the shape `company_metrics` takes, and `{"reference_quarter": str, "companies_with_revenue": int}`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_diamond_data.py`)

```python
def test_periods_cover_eleven_quarters_three_years_and_four_instants():
    from diamond_data import periods_for
    p = periods_for(date(2026, 8, 15))
    assert p["quarters"][0] == "CY2026Q3" and p["quarters"][-1] == "CY2024Q1" and len(p["quarters"]) == 11
    assert p["years"] == ["CY2026", "CY2025", "CY2024"]
    assert p["instants"] == ["CY2026Q3I", "CY2026Q2I", "CY2025Q3I", "CY2025Q2I"]


def test_load_financials_pools_tags_by_company_and_reports_coverage():
    from diamond_data import load_financials
    calls = []

    def fetch(taxonomy, tag, unit, period, session=None):
        calls.append((tag, period))
        if tag == "RevenueFromContractWithCustomerExcludingAssessedTax" and period == "CY2026Q2":
            return [{"cik": 1, "start": "2026-04-01", "end": "2026-06-30", "val": 130}]
        if tag == "Revenues" and period == "CY2026Q2":
            return [{"cik": 2, "start": "2026-04-01", "end": "2026-06-30", "val": 70},
                    {"cik": 1, "start": "2026-04-01", "end": "2026-06-30", "val": 999}]   # second tag for cik 1
        if tag == "CashAndCashEquivalentsAtCarryingValue" and period == "CY2026Q2I":
            return [{"cik": 1, "end": "2026-06-30", "val": 300}]
        return []

    fins, coverage = load_financials(date(2026, 8, 15), fetch=fetch)
    assert [r["val"] for r in fins[1]["revenue"]] == [130]            # first tag wins for a company
    assert [r["val"] for r in fins[2]["revenue"]] == [70]
    assert fins[1]["cash"] == [{"end": "2026-06-30", "val": 300}]
    assert coverage == {"reference_quarter": "CY2026Q2", "companies_with_revenue": 2}
    assert ("OperatingIncomeLoss", "CY2025") in calls                  # full years are fetched for Q4


def test_fetch_frame_treats_404_as_empty_and_anything_else_as_failure():
    import pytest
    from diamond_data import FrameError, fetch_frame

    class Resp:
        def __init__(self, status, payload=None):
            self.status_code, self._payload = status, payload
        def json(self):
            return self._payload

    class Session:
        def __init__(self, *responses):
            self.responses, self.headers_seen = list(responses), []
        def get(self, url, headers=None, timeout=None):
            self.headers_seen.append(headers)
            return self.responses.pop(0)

    ok = Session(Resp(200, {"data": [{"cik": 1, "val": 5}]}))
    assert fetch_frame("us-gaap", "Revenues", "USD", "CY2026Q2", session=ok, pause=0) == [{"cik": 1, "val": 5}]
    assert "User-Agent" in ok.headers_seen[0]
    assert fetch_frame("us-gaap", "Revenues", "USD", "CY2026Q3", session=Session(Resp(404)), pause=0) == []
    with pytest.raises(FrameError):
        fetch_frame("us-gaap", "Revenues", "USD", "CY2026Q2", session=Session(Resp(403), Resp(403), Resp(403)), pause=0)
    retried = Session(Resp(500), Resp(200, {"data": []}))
    assert fetch_frame("us-gaap", "Revenues", "USD", "CY2026Q2", session=retried, pause=0) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_diamond_data.py -q`
Expected: 3 failed (`ImportError: cannot import name 'periods_for'`), 13 passed

- [ ] **Step 3: Implement** (append to `diamond_data.py`; add `import time` and `from typing import Tuple` to its imports)

```python
FRAMES_URL = "https://data.sec.gov/api/xbrl/frames"
REQUEST_PAUSE = 0.2            # five requests a second; the SEC allows ten
FETCH_ATTEMPTS = 3

# Figure -> where to find it. Tags are tried in order; the first that has a
# company's figure for a period is used for that company.
FIGURES = {
    "revenue":   {"taxonomy": "us-gaap", "unit": "USD", "kind": "duration",
                  "tags": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"]},
    "op_income": {"taxonomy": "us-gaap", "unit": "USD", "kind": "duration", "tags": ["OperatingIncomeLoss"]},
    "gross_profit": {"taxonomy": "us-gaap", "unit": "USD", "kind": "duration", "tags": ["GrossProfit"]},
    "cost_of_revenue": {"taxonomy": "us-gaap", "unit": "USD", "kind": "duration", "tags": ["CostOfRevenue"]},
    "rd":        {"taxonomy": "us-gaap", "unit": "USD", "kind": "duration", "tags": ["ResearchAndDevelopmentExpense"]},
    # Weighted average, not the cover-page count: it is reported for the same
    # period as the income statement, by nearly every company, in one figure.
    "shares":    {"taxonomy": "us-gaap", "unit": "shares", "kind": "duration",
                  "tags": ["WeightedAverageNumberOfSharesOutstandingBasic"]},
    "cash":      {"taxonomy": "us-gaap", "unit": "USD", "kind": "instant",
                  "tags": ["CashAndCashEquivalentsAtCarryingValue"]},
    "investments": {"taxonomy": "us-gaap", "unit": "USD", "kind": "instant",
                    "tags": ["ShortTermInvestments", "MarketableSecuritiesCurrent",
                             "AvailableForSaleSecuritiesDebtSecuritiesCurrent"]},
    "backlog":   {"taxonomy": "us-gaap", "unit": "USD", "kind": "instant",
                  "tags": ["RevenueRemainingPerformanceObligation"]},
    "prepaid":   {"taxonomy": "us-gaap", "unit": "USD", "kind": "instant",
                  "tags": ["ContractWithCustomerLiability", "ContractWithCustomerLiabilityCurrent",
                           "DeferredRevenueCurrent"]},
}


class FrameError(Exception):
    """A frame could not be fetched, so the data would be partial."""


def fetch_frame(taxonomy: str, tag: str, unit: str, period: str, session=None, pause: float = REQUEST_PAUSE) -> List[dict]:
    """One figure for every company for one period. [] if the SEC has none (404); FrameError otherwise."""
    import requests
    from config import SEC_EDGAR_USER_AGENT
    session = session or requests
    url = f"{FRAMES_URL}/{taxonomy}/{tag}/{unit}/{period}.json"
    last = None
    for attempt in range(FETCH_ATTEMPTS):
        try:
            resp = session.get(url, headers={"User-Agent": SEC_EDGAR_USER_AGENT}, timeout=40)
            time.sleep(pause)
            if resp.status_code == 404:
                return []
            if resp.status_code == 200:
                data = resp.json().get("data")
                if isinstance(data, list):
                    return data
                last = "no data list in the response"
            else:
                last = f"HTTP {resp.status_code}"
        except Exception as exc:                      # network error, bad JSON
            last = str(exc)
        time.sleep(pause * 5 * (attempt + 1))
    raise FrameError(f"{tag} {period}: {last}")


def _quarter_name(year: int, quarter: int) -> str:
    return f"CY{year}Q{quarter}"


def periods_for(cutoff: date) -> Dict[str, List[str]]:
    """Frame periods needed to give every company eight quarters ending by `cutoff`."""
    year, quarter = cutoff.year, (cutoff.month - 1) // 3 + 1
    quarters = []
    for _ in range(11):                               # the current quarter and the ten before it
        quarters.append((year, quarter))
        year, quarter = (year, quarter - 1) if quarter > 1 else (year - 1, 4)
    names = [_quarter_name(y, q) for y, q in quarters]
    years = sorted({y for y, _ in quarters}, reverse=True)
    instants = [names[0] + "I", names[1] + "I", names[4] + "I", names[5] + "I"]   # now, and a year before
    return {"quarters": names, "years": [f"CY{y}" for y in years], "instants": instants}


def load_financials(cutoff: date, fetch=fetch_frame) -> Tuple[Dict[int, dict], dict]:
    """
    {cik: figures} for every company the SEC has data for, and a coverage
    note. The reference quarter is the last complete calendar quarter before
    `cutoff`; how many companies report revenue for it tells the caller
    whether the data is whole.
    """
    periods = periods_for(cutoff)
    reference = periods["quarters"][1]
    reference_count = 0
    fins: Dict[int, dict] = {}
    for name, spec in FIGURES.items():
        wanted = periods["instants"] if spec["kind"] == "instant" else periods["quarters"] + periods["years"]
        for period in wanted:
            taken = set()                             # companies already given this period by an earlier tag
            for tag in spec["tags"]:
                got = set()
                for row in fetch(spec["taxonomy"], tag, spec["unit"], period):
                    cik = row.get("cik")
                    if not isinstance(cik, int) or cik in taken:
                        continue
                    fins.setdefault(cik, {k: [] for k in FIGURES})[name].append(
                        {k: row[k] for k in ("start", "end", "val") if k in row})
                    got.add(cik)
                taken |= got
            if name == "revenue" and period == reference:
                reference_count = len(taken)
    return fins, {"reference_quarter": reference, "companies_with_revenue": reference_count}
```

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests/test_diamond_data.py -q`
Expected: 16 passed

- [ ] **Step 5: Check against the real SEC once**

Run:
```bash
SEC_EDGAR_USER_AGENT="stocktrader diamond screen (aschumacherdesign@gmail.com)" python3 -c "
from datetime import date
from diamond_data import load_financials, company_metrics
fins, cov = load_financials(date.today())
print(cov, len(fins))
m = company_metrics(fins[1321655], date.today())
print({k: m[k] for k in ('latest_quarter_end', 'ttm_revenue', 'revenue_growth', 'ttm_op_margin', 'margin_change')})"
```
Expected: `companies_with_revenue` above 2,500; Palantir (CIK 1321655) prints a latest quarter in 2026, trailing revenue in the billions and a positive operating margin. If Palantir's figures are `None` or its latest quarter is a year old, the period or tag handling is wrong: stop and fix before going on.

- [ ] **Step 6: Commit**

```bash
git add diamond_data.py tests/test_diamond_data.py
git commit -m "Diamond screen: load every company's figures from SEC frames"
```

---

### Task 4: Universe filters

**Files:**
- Create: `diamond_universe.py`
- Test: `tests/test_diamond_universe.py`

**Interfaces:**
- Produces:
  - `listed_companies(rows: list[list]) -> dict[int, dict]` — from the SEC `company_tickers_exchange.json` `data` rows `[cik, name, ticker, exchange]`; returns `{cik: {"ticker", "name", "exchange"}}`, NYSE and Nasdaq common shares only, one ticker per company.
  - `is_common_share(ticker: str, name: str) -> bool`
  - `size_and_volume(shares: float, closes: list[float], volumes: list[float]) -> Optional[dict]` — `{"price", "market_value", "dollar_volume"}` or `None` if it cannot be worked out.
  - `size_failures(s: Optional[dict]) -> list[str]` — any of `price_unknown, too_small, too_big, illiquid, penny`.
  - `excluded_sic(sic) -> bool` — `True` for 6000-6799; `False` for unknown.
  - `fetch_listings(session=None) -> list[list]`, `fetch_sic(cik: int, session=None) -> Optional[int]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_diamond_universe.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from diamond_universe import excluded_sic, is_common_share, listed_companies, size_and_volume, size_failures


def test_only_common_shares_on_the_two_main_exchanges():
    rows = [[1, "Alpha Inc", "ALPH", "Nasdaq"], [2, "Beta Corp", "BETA", "NYSE"],
            [3, "Gamma Ltd", "GAMM", "OTC"], [4, "Delta Acquisition Corp Warrant", "DLTAW", "Nasdaq"],
            [5, "Epsilon Units", "EPS-UN", "NYSE"], [6, "Zeta Inc 5% Preferred", "ZETA-PA", "NYSE"],
            [7, "Eta Inc", None, "NYSE"]]
    assert sorted(listed_companies(rows)) == [1, 2]


def test_two_share_classes_give_one_company_with_its_first_ticker():
    rows = [[9, "Dual Class Inc", "DUAL", "Nasdaq"], [9, "Dual Class Inc", "DUALB", "Nasdaq"]]
    assert listed_companies(rows) == {9: {"ticker": "DUAL", "name": "Dual Class Inc", "exchange": "Nasdaq"}}


def test_share_class_suffixes():
    assert is_common_share("BRK-B", "Berkshire Hathaway Inc")          # a class of common stock
    assert not is_common_share("ABCDW", "Abcd Corp Warrants")
    assert not is_common_share("ABCD-WT", "Abcd Corp")
    assert not is_common_share("ABCDU", "Abcd Acquisition Corp Unit")
    assert not is_common_share("ABCDR", "Abcd Corp Rights")
    assert is_common_share("UBER", "Uber Technologies Inc")             # ends in R, is not a right


def test_size_and_volume():
    closes, volumes = [20.0] * 60, [400_000.0] * 60
    s = size_and_volume(100e6, closes, volumes)
    assert s == {"price": 20.0, "market_value": 2e9, "dollar_volume": 8e6}
    assert size_failures(s) == []
    assert size_failures(size_and_volume(10e6, closes, volumes)) == ["too_small"]       # $200M
    assert size_failures(size_and_volume(600e6, closes, volumes)) == ["too_big"]        # $12B
    assert size_failures(size_and_volume(100e6, closes, [100_000.0] * 60)) == ["illiquid"]
    assert size_failures(size_and_volume(1e9, [4.0] * 60, [5e6] * 60)) == ["penny"]


def test_unknown_price_or_shares_is_a_failure_not_a_crash():
    assert size_and_volume(None, [20.0] * 60, [1e6] * 60) is None
    assert size_and_volume(100e6, [], []) is None
    assert size_and_volume(100e6, [float("nan")] * 60, [1e6] * 60) is None
    assert size_and_volume(100e6, [20.0] * 10, [1e6] * 10) is None      # too little history to judge volume
    assert size_failures(None) == ["price_unknown"]


def test_financial_industries_are_excluded_and_unknown_is_kept():
    assert excluded_sic(6021) and excluded_sic("6798") and excluded_sic(6000) and excluded_sic(6799)
    assert not excluded_sic(7372) and not excluded_sic(5999) and not excluded_sic(6800)
    assert not excluded_sic(None) and not excluded_sic("")
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_diamond_universe.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'diamond_universe'`

- [ ] **Step 3: Implement**

```python
# diamond_universe.py
"""
Diamond screen: which companies are eligible
============================================
Listing, size, liquidity and industry filters for the diamond screen.

These run AFTER the financial gates (diamond_screen.py), on the few hundred
companies that pass them, so prices and industry codes are fetched for
hundreds of companies rather than thousands.

  Listed    NYSE or Nasdaq common shares; one ticker per company
  Size      $300M to $10B: small enough that the market can still be wrong,
            large enough to trade and to have several years of filings
  Liquid    median daily dollar volume of $5M over 60 sessions, price >= $5
  Industry  not SIC 6000-6799 (banks, insurers, property trusts, investment
            vehicles): "losses turning to profit" means something else there
"""

import re
import time
from statistics import median
from typing import Dict, List, Optional

MIN_MARKET_VALUE = 300e6
MAX_MARKET_VALUE = 10e9
MIN_DOLLAR_VOLUME = 5e6
MIN_PRICE = 5.0
VOLUME_SESSIONS = 60
EXCHANGES = ("NYSE", "Nasdaq")
EXCLUDED_SIC = (6000, 6799)

LISTINGS_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# Not common stock: a dash suffix for warrants, units, rights or a preferred
# series; or a five-letter Nasdaq ticker whose fifth letter marks the same,
# confirmed by the security's name so that UBER is not taken for a right.
_DASH_SUFFIX = re.compile(r"-(W|WT|WS|U|UN|R|RT|P[A-Z]?)$")
_FIFTH_LETTER = {"W": ("warrant",), "U": ("unit",), "R": ("right",)}


def is_common_share(ticker: str, name: str) -> bool:
    ticker, name = (ticker or "").upper(), (name or "").lower()
    if not ticker or _DASH_SUFFIX.search(ticker) or "preferred" in name:
        return False
    if len(ticker) == 5 and ticker[-1] in _FIFTH_LETTER:
        return not any(word in name for word in _FIFTH_LETTER[ticker[-1]])
    return True


def listed_companies(rows: List[list]) -> Dict[int, Dict]:
    """{cik: listing} from the SEC's ticker file. The first ticker listed for a company is kept."""
    out: Dict[int, Dict] = {}
    for row in rows or []:
        try:
            cik, name, ticker, exchange = row[0], row[1], row[2], row[3]
        except (IndexError, TypeError):
            continue
        if not isinstance(cik, int) or exchange not in EXCHANGES or cik in out:
            continue
        if is_common_share(ticker, name):
            out[cik] = {"ticker": ticker.upper(), "name": name, "exchange": exchange}
    return out


def size_and_volume(shares: Optional[float], closes: List[float], volumes: List[float]) -> Optional[Dict]:
    """Price, market value and median dollar volume, or None if any of them cannot be worked out."""
    try:
        pairs = [(float(c), float(v)) for c, v in zip(closes or [], volumes or [])
                 if c == c and v == v and c > 0][-VOLUME_SESSIONS:]
        if len(pairs) < VOLUME_SESSIONS or not shares or shares != shares or shares <= 0:
            return None
        price = pairs[-1][0]
        return {"price": price, "market_value": float(shares) * price,
                "dollar_volume": median(c * v for c, v in pairs)}
    except (TypeError, ValueError):
        return None


def size_failures(s: Optional[Dict]) -> List[str]:
    if not s:
        return ["price_unknown"]
    failed = []
    if s["market_value"] < MIN_MARKET_VALUE:
        failed.append("too_small")
    if s["market_value"] > MAX_MARKET_VALUE:
        failed.append("too_big")
    if s["dollar_volume"] < MIN_DOLLAR_VOLUME:
        failed.append("illiquid")
    if s["price"] < MIN_PRICE:
        failed.append("penny")
    return failed


def excluded_sic(sic) -> bool:
    """True for the financial industries. An unknown code is not excluded."""
    try:
        return EXCLUDED_SIC[0] <= int(sic) <= EXCLUDED_SIC[1]
    except (TypeError, ValueError):
        return False


def _get(url: str, session=None):
    import requests
    from config import SEC_EDGAR_USER_AGENT
    resp = (session or requests).get(url, headers={"User-Agent": SEC_EDGAR_USER_AGENT}, timeout=40)
    time.sleep(0.2)
    resp.raise_for_status()
    return resp.json()


def fetch_listings(session=None) -> List[list]:
    return _get(LISTINGS_URL, session).get("data") or []


def fetch_sic(cik: int, session=None) -> Optional[int]:
    """The company's industry code from its SEC record. None if it cannot be read."""
    try:
        return int(_get(SUBMISSIONS_URL.format(cik=cik), session).get("sic"))
    except Exception:
        return None
```

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests/test_diamond_universe.py -q`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add diamond_universe.py tests/test_diamond_universe.py
git commit -m "Diamond screen: listing, size, liquidity and industry filters"
```

---

### Task 5: The run, its output and its guard

**Files:**
- Create: `diamond_run.py`
- Test: `tests/test_diamond_run.py`

**Interfaces:**
- Consumes: `diamond_data.load_financials`, `diamond_data.company_metrics`, `diamond_screen.gate_failures`, `diamond_screen.score`, `diamond_screen.GATES`, everything `diamond_universe` produces.
- Produces:
  - `class CoverageError(Exception)`
  - `build_watchlist(cutoff: date, fins: dict, coverage: dict, listings: dict, prices: callable, sic: callable, top: int = 40) -> dict` — `prices(tickers: list[str]) -> {ticker: {"closes": [...], "volumes": [...]}}`; `sic(cik: int) -> Optional[int]`. Returns the `diamonds.json` document. Raises `CoverageError`.
  - `update_history(history: list, doc: dict) -> list`
  - `main(argv=None) -> int`
  - Output `docs/data/diamonds.json`: `{"generated_at", "cutoff", "reference_quarter", "coverage": {"companies_with_data", "companies_with_revenue", "listed", "with_metrics", "passed_financial_gates", "priced", "passed_size", "excluded_industry", "watchlist"}, "gate_failures": {gate: count}, "size_failures": {name: count}, "watchlist": [row, ...]}`; each row is `{"rank", "ticker", "name", "cik", "sic", "score": {...}, "market_value", "price", "dollar_volume", **metrics}`.
  - Output `docs/data/diamond_history.json`: list of `{"date", "ticker", "rank", "total"}`, at most 104 distinct dates.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_diamond_run.py
import json
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from diamond_run import CoverageError, build_watchlist, update_history

CUTOFF = date(2026, 8, 15)
ENDS = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31", "2024-12-31", "2024-09-30"]
STARTS = ["2026-04-01", "2026-01-01", "2025-10-01", "2025-07-01", "2025-04-01", "2025-01-01", "2024-10-01", "2024-07-01"]


def _rows(vals, scale=1e6):
    return [{"start": s, "end": e, "val": v * scale} for s, e, v in zip(STARTS, ENDS, vals)]


def _fin(op=(6, 2, -1, -3, -8, -10, -12, -14)):
    return {"revenue": _rows([130, 125, 120, 115, 100, 96, 92, 88]), "op_income": _rows(op),
            "gross_profit": _rows([91, 87, 84, 80, 68, 65, 62, 59]), "cost_of_revenue": [], "rd": [],
            "shares": _rows([105, 105, 104, 104, 100, 100, 100, 100]),
            "cash": [{"end": "2026-06-30", "val": 300e6}], "investments": [], "backlog": [], "prepaid": []}


FINS = {1: _fin(), 2: _fin(op=(6, 2, 1, 0, -4, -5, -6, -7)),            # 2 improves, but less
        3: _fin(op=(-40, -40, -40, -40, -40, -40, -40, -40)),          # not turning
        4: _fin(), 5: _fin()}
LISTINGS = {1: {"ticker": "AAA", "name": "A Inc", "exchange": "Nasdaq"},
            2: {"ticker": "BBB", "name": "B Inc", "exchange": "NYSE"},
            3: {"ticker": "CCC", "name": "C Inc", "exchange": "NYSE"},
            4: {"ticker": "BANK", "name": "Bank Inc", "exchange": "NYSE"},
            5: {"ticker": "TINY", "name": "Tiny Inc", "exchange": "Nasdaq"}}
COVERAGE = {"reference_quarter": "CY2026Q2", "companies_with_revenue": 3000}


def _prices(tickers):
    out = {t: {"closes": [20.0] * 60, "volumes": [400_000.0] * 60} for t in tickers}
    if "TINY" in out:
        out["TINY"] = {"closes": [1.0] * 60, "volumes": [400_000.0] * 60}     # $105M: too small, and a penny stock
    return out


def _sic(cik):
    return 6021 if cik == 4 else 7372


def test_watchlist_ranks_survivors_and_counts_every_drop():
    doc = build_watchlist(CUTOFF, FINS, COVERAGE, LISTINGS, _prices, _sic)
    assert [(r["rank"], r["ticker"]) for r in doc["watchlist"]] == [(1, "AAA"), (2, "BBB")]
    assert doc["watchlist"][0]["score"]["total"] > doc["watchlist"][1]["score"]["total"]
    assert doc["watchlist"][0]["market_value"] == 105e6 * 20.0
    assert doc["gate_failures"]["turning"] == 1
    assert doc["size_failures"] == {"too_small": 1, "penny": 1}
    c = doc["coverage"]
    assert (c["listed"], c["with_metrics"], c["passed_financial_gates"], c["passed_size"],
            c["excluded_industry"], c["watchlist"]) == (5, 5, 4, 3, 1, 2)
    assert doc["cutoff"] == "2026-08-15" and doc["reference_quarter"] == "CY2026Q2"


def test_prices_are_only_asked_for_companies_that_passed_the_financial_gates():
    asked = []
    build_watchlist(CUTOFF, FINS, COVERAGE, LISTINGS, lambda t: asked.extend(t) or _prices(t), _sic)
    assert sorted(asked) == ["AAA", "BANK", "BBB", "TINY"]


def test_top_n_and_stable_order_on_a_tie():
    doc = build_watchlist(CUTOFF, {1: _fin(), 2: _fin()}, COVERAGE,
                          {1: LISTINGS[2], 2: LISTINGS[1]}, _prices, _sic, top=1)
    assert [r["ticker"] for r in doc["watchlist"]] == ["AAA"]            # equal scores: alphabetical


def test_too_few_companies_reporting_stops_the_run():
    with pytest.raises(CoverageError, match="2499"):
        build_watchlist(CUTOFF, FINS, dict(COVERAGE, companies_with_revenue=2499), LISTINGS, _prices, _sic)


def test_missing_prices_for_too_many_stops_the_run():
    with pytest.raises(CoverageError, match="price"):
        build_watchlist(CUTOFF, FINS, COVERAGE, LISTINGS, lambda t: {"AAA": _prices(["AAA"])["AAA"]}, _sic)


def test_history_adds_a_week_replaces_a_rerun_and_keeps_104_dates():
    doc = {"generated_at": "2026-10-10T07:31:00", "watchlist": [
        {"rank": 1, "ticker": "AAA", "score": {"total": 80.0}}, {"rank": 2, "ticker": "BBB", "score": {"total": 70.0}}]}
    old = [{"date": "2026-10-03", "ticker": "AAA", "rank": 3, "total": 60.0},
           {"date": "2026-10-10", "ticker": "ZZZ", "rank": 1, "total": 99.0}]      # an earlier run today
    h = update_history(old, doc)
    assert h == [{"date": "2026-10-03", "ticker": "AAA", "rank": 3, "total": 60.0},
                 {"date": "2026-10-10", "ticker": "AAA", "rank": 1, "total": 80.0},
                 {"date": "2026-10-10", "ticker": "BBB", "rank": 2, "total": 70.0}]
    many = [{"date": f"2024-{m:02d}-{d:02d}", "ticker": "AAA", "rank": 1, "total": 1.0}
            for m in range(1, 13) for d in range(1, 11)]                           # 120 dates
    assert len({r["date"] for r in update_history(many, doc)}) == 104


def test_a_failed_run_leaves_last_weeks_file_alone(tmp_path, monkeypatch):
    import diamond_run
    out = tmp_path / "diamonds.json"
    out.write_text(json.dumps({"generated_at": "last week"}))
    monkeypatch.setattr(diamond_run, "OUT_FILE", out)
    monkeypatch.setattr(diamond_run, "HISTORY_FILE", tmp_path / "diamond_history.json")
    monkeypatch.setattr(diamond_run, "load_financials",
                        lambda cutoff: (FINS, dict(COVERAGE, companies_with_revenue=10)))
    monkeypatch.setattr(diamond_run, "fetch_listings", lambda: [[c, v["name"], v["ticker"], v["exchange"]]
                                                                 for c, v in LISTINGS.items()])
    assert diamond_run.main([]) == 1
    assert json.loads(out.read_text()) == {"generated_at": "last week"}
    assert not (tmp_path / "diamond_history.json").exists()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_diamond_run.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'diamond_run'`

- [ ] **Step 3: Implement**

```python
# diamond_run.py
"""
Diamond screen: weekly run
==========================
Builds the watchlist of small and mid-sized companies whose finances are
turning from loss to profit. Nothing here trades.

Order of work, cheapest first:
  1. every company's figures from the SEC (diamond_data)      ~130 requests
  2. financial gates and score (diamond_screen)               no network
  3. price, size and liquidity for the survivors only         one download
  4. industry code for what is left                           one request each

Fails closed: if the SEC data or the prices look incomplete, nothing is
written and last week's list stays in place.

Usage:
    python diamond_run.py                # build this week's list
    python diamond_run.py --top 60
    python diamond_run.py --dry-run      # print the list, write nothing
"""

import argparse
import json
import logging
import os
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from diamond_data import company_metrics, load_financials
from diamond_screen import GATES, gate_failures, score
from diamond_universe import (excluded_sic, fetch_listings, fetch_sic, listed_companies,
                              size_and_volume, size_failures)

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
OUT_FILE = ROOT / "docs" / "data" / "diamonds.json"
HISTORY_FILE = ROOT / "docs" / "data" / "diamond_history.json"

MIN_COMPANIES_WITH_REVENUE = 2500   # fewer than this for the reference quarter: the SEC data is incomplete
MAX_MISSING_PRICE_SHARE = 0.20
HISTORY_WEEKS = 104
TOP = 40


class CoverageError(Exception):
    """The inputs look incomplete; a list built from them could not be trusted."""


def build_watchlist(cutoff: date, fins: Dict[int, dict], coverage: dict, listings: Dict[int, dict],
                    prices: Callable[[List[str]], Dict[str, dict]], sic: Callable[[int], Optional[int]],
                    top: int = TOP) -> dict:
    """The diamonds.json document. Raises CoverageError rather than return a list from partial data."""
    reporting = coverage.get("companies_with_revenue") or 0
    if reporting < MIN_COMPANIES_WITH_REVENUE:
        raise CoverageError(f"only {reporting} companies report revenue for {coverage.get('reference_quarter')} "
                            f"(need {MIN_COMPANIES_WITH_REVENUE}): the SEC data looks incomplete")

    gate_counts, with_metrics, passed = Counter(), 0, []
    for cik, listing in listings.items():
        try:
            m = company_metrics(fins.get(cik), cutoff)
        except Exception as exc:                       # one company's odd data must not stop the list
            logger.warning(f"{listing['ticker']}: metrics failed ({exc})")
            m = None
        if m is None:
            continue
        with_metrics += 1
        failed = gate_failures(m)
        gate_counts.update(failed)
        if not failed:
            passed.append((cik, listing, m))

    tickers = [listing["ticker"] for _, listing, _ in passed]
    quotes = prices(tickers) if tickers else {}
    missing = [t for t in tickers if t not in quotes]
    if tickers and len(missing) / len(tickers) > MAX_MISSING_PRICE_SHARE:
        raise CoverageError(f"no price data for {len(missing)} of {len(tickers)} companies: "
                            f"the price download looks incomplete")

    size_counts, sized = Counter(), []
    for cik, listing, m in passed:
        quote = quotes.get(listing["ticker"]) or {}
        s = size_and_volume(m.get("shares"), quote.get("closes"), quote.get("volumes"))
        failed = size_failures(s)
        size_counts.update(failed)
        if not failed:
            sized.append((cik, listing, m, s))

    rows, excluded = [], 0
    for cik, listing, m, s in sized:
        code = sic(cik)
        if excluded_sic(code):
            excluded += 1
            continue
        rows.append({"ticker": listing["ticker"], "name": listing["name"], "cik": cik, "sic": code,
                     "score": score(m), **s, **m})
    rows.sort(key=lambda r: (-r["score"]["total"], r["ticker"]))
    rows = rows[:top]
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank

    return {
        "generated_at": datetime.now().isoformat(),
        "cutoff": cutoff.isoformat(),
        "reference_quarter": coverage.get("reference_quarter"),
        "coverage": {"companies_with_data": len(fins), "companies_with_revenue": reporting,
                     "listed": len(listings), "with_metrics": with_metrics,
                     "passed_financial_gates": len(passed), "priced": len(tickers) - len(missing),
                     "passed_size": len(sized), "excluded_industry": excluded, "watchlist": len(rows)},
        "gate_failures": {g: gate_counts.get(g, 0) for g in GATES},
        "size_failures": dict(size_counts),
        "watchlist": rows,
    }


def update_history(history: list, doc: dict) -> list:
    """One row per company per run date, so its trend can be read. A re-run on the same date replaces that date."""
    day = str(doc.get("generated_at") or "")[:10]
    kept = [h for h in (history or []) if isinstance(h, dict) and h.get("date") != day]
    kept += [{"date": day, "ticker": r["ticker"], "rank": r["rank"], "total": r["score"]["total"]}
             for r in doc.get("watchlist") or []]
    dates = sorted({h["date"] for h in kept})[-HISTORY_WEEKS:]
    return sorted((h for h in kept if h["date"] in dates), key=lambda h: (h["date"], h["rank"]))


def fetch_prices(tickers: List[str]) -> Dict[str, dict]:
    """Six months of daily closes and volumes for each ticker that has them."""
    import yfinance as yf
    if not tickers:
        return {}
    raw = yf.download(sorted(set(tickers)), period="6mo", auto_adjust=True, progress=False, threads=True,
                      group_by="column")
    out = {}
    for t in tickers:
        try:
            closes = raw["Close"][t].dropna() if len(tickers) > 1 else raw["Close"].dropna().squeeze()
            volumes = raw["Volume"][t].reindex(closes.index) if len(tickers) > 1 \
                else raw["Volume"].reindex(closes.index).squeeze()
            if len(closes):
                out[t] = {"closes": [float(x) for x in closes.values], "volumes": [float(x) for x in volumes.values]}
        except Exception:
            continue
    return out


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    os.replace(tmp, path)


def print_list(doc: dict) -> None:
    c = doc["coverage"]
    print(f"\nDiamond watchlist as of {doc['cutoff']}: {c['listed']} listed, {c['with_metrics']} with eight "
          f"quarters, {c['passed_financial_gates']} turning, {c['passed_size']} the right size, {c['watchlist']} listed below")
    print(f"gate failures: {doc['gate_failures']}  size: {doc['size_failures']}")
    print(f"{'#':>3} {'ticker':7}{'score':>6}{'value $M':>10}{'rev gr':>8}{'margin':>8}{'was':>8}{'gross':>7}  name")
    for r in doc["watchlist"]:
        gross = f"{r['ttm_gross_margin'] * 100:5.0f}%" if r.get("ttm_gross_margin") is not None else "    --"
        print(f"{r['rank']:>3} {r['ticker']:7}{r['score']['total']:>6.1f}{r['market_value'] / 1e6:>10,.0f}"
              f"{r['revenue_growth'] * 100:>7.0f}%{r['ttm_op_margin'] * 100:>7.1f}%{r['prior_ttm_op_margin'] * 100:>7.1f}%"
              f"{gross:>7}  {r['name'][:34]}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build the diamond watchlist (no trades)")
    parser.add_argument("--top", type=int, default=TOP)
    parser.add_argument("--dry-run", action="store_true", help="print the list, write nothing")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")

    cutoff = date.today()
    try:
        fins, coverage = load_financials(cutoff)
        listings = listed_companies(fetch_listings())
        sic_cache: Dict[int, Optional[int]] = {}
        doc = build_watchlist(cutoff, fins, coverage, listings, fetch_prices,
                              lambda cik: sic_cache.setdefault(cik, fetch_sic(cik)), top=args.top)
    except Exception as exc:
        # CoverageError, a frame that would not load, the ticker file missing:
        # in every case last week's list is better than a partial one.
        print(f"::error::diamond screen did not run: {exc}")
        logger.error(f"Diamond screen failed, last week's list left in place: {exc}")
        return 1

    print_list(doc)
    if args.dry_run:
        return 0
    try:
        history = json.loads(HISTORY_FILE.read_text()) if HISTORY_FILE.exists() else []
    except Exception:
        history = []
    _write(OUT_FILE, doc)
    _write(HISTORY_FILE, update_history(history, doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests/test_diamond_run.py tests/test_diamond_data.py tests/test_diamond_screen.py tests/test_diamond_universe.py -q`
Expected: 36 passed

- [ ] **Step 5: First real list (writes nothing)**

Run: `SEC_EDGAR_USER_AGENT="stocktrader diamond screen (aschumacherdesign@gmail.com)" python3 diamond_run.py --dry-run`
Expected: a table of up to 40 companies in under 10 minutes. Sanity checks before going on: `with eight quarters` is in the thousands; `turning` is in the low hundreds or below; every listed company's market value is between 300 and 10,000 ($M); no bank or insurer appears. Paste the table into the session for the owner.

- [ ] **Step 6: Commit**

```bash
git add diamond_run.py tests/test_diamond_run.py
git commit -m "Diamond screen: weekly run, watchlist files and coverage guard"
```

---

### Task 6: Historical check (`--as-of`) and the Palantir test

**Files:**
- Modify: `diamond_run.py`
- Test: `tests/test_diamond_run.py` (append)

**Interfaces:**
- Consumes: `build_watchlist`.
- Produces:
  - `as_of_cutoff(text: str) -> date` — `"2023Q1"` -> the date 60 days after that quarter ends (`date(2023, 5, 30)`), by which its filings are public.
  - `forward_returns(tickers: list[str], start: date, months: int = 12, download=None) -> dict[str, float]` — total return from the first close on or after `start` to the last close on or before `start + months`.
  - CLI: `--as-of 2023Q1`, `--no-size-cap`, `--find TICKER` (repeatable; prints that company's gate failures and score even if it is not on the list). `--as-of` never writes files.

- [ ] **Step 1: Write the failing tests** (append)

```python
def test_as_of_cutoff_is_sixty_days_after_the_quarter_ends():
    from diamond_run import as_of_cutoff
    assert as_of_cutoff("2023Q1") == date(2023, 5, 30)
    assert as_of_cutoff("2022Q4") == date(2023, 3, 1)
    with pytest.raises(ValueError):
        as_of_cutoff("2023")


def test_forward_returns_from_first_close_after_start_to_last_before_the_end():
    import pandas as pd
    from diamond_run import forward_returns
    idx = pd.to_datetime(["2023-05-30", "2023-05-31", "2024-05-29", "2024-05-31"])
    frame = pd.DataFrame({"AAA": [10.0, 11.0, 20.0, 99.0], "BBB": [5.0, 5.0, float("nan"), 4.0]}, index=idx)
    r = forward_returns(["AAA", "BBB", "GONE"], date(2023, 5, 30), download=lambda tickers, start, end: frame)
    assert r == {"AAA": 1.0}        # BBB has no close at the end of the window; GONE has no data


def test_explain_reports_why_a_company_is_or_is_not_listed():
    from diamond_run import explain
    text = explain("CCC", CUTOFF, FINS, LISTINGS)
    assert "CCC" in text and "turning" in text
    assert "no listing" in explain("NOPE", CUTOFF, FINS, LISTINGS)
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_diamond_run.py -q`
Expected: 3 failed (`ImportError: cannot import name 'as_of_cutoff'`), 7 passed

- [ ] **Step 3: Implement** (add to `diamond_run.py`; add `from datetime import timedelta` and `import calendar`)

```python
FILING_LAG_DAYS = 60          # by then nearly every company has filed for the quarter
BENCHMARK = "IWM"             # Russell 2000 ETF


def as_of_cutoff(text: str) -> date:
    """'2023Q1' -> the date by which that quarter's filings are public."""
    try:
        year, quarter = int(text[:4]), int(text[5])
        if text[4].upper() != "Q" or not 1 <= quarter <= 4 or len(text) != 6:
            raise ValueError
    except (ValueError, IndexError):
        raise ValueError(f"--as-of wants a quarter such as 2023Q1, not {text!r}")
    month = quarter * 3
    return date(year, month, calendar.monthrange(year, month)[1]) + timedelta(days=FILING_LAG_DAYS)


def _download_closes(tickers: List[str], start: date, end: date):
    import yfinance as yf
    raw = yf.download(sorted(set(tickers)), start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),
                      auto_adjust=True, progress=False, threads=True)
    return raw["Close"]


def forward_returns(tickers: List[str], start: date, months: int = 12, download=None) -> Dict[str, float]:
    """Total return over the window for each ticker with a close at both ends."""
    import pandas as pd
    end = (pd.Timestamp(start) + pd.DateOffset(months=months)).date()
    closes = (download or _download_closes)(tickers, start, end)
    out = {}
    for t in tickers:
        if t not in getattr(closes, "columns", []):
            continue
        series = closes[t].loc[pd.Timestamp(start):pd.Timestamp(end)]
        if len(series) < 2 or series.iloc[[0, -1]].isna().any() or series.iloc[0] <= 0:
            continue
        out[t] = round(float(series.iloc[-1] / series.iloc[0] - 1), 4)
    return out


def explain(ticker: str, cutoff: date, fins: Dict[int, dict], listings: Dict[int, dict]) -> str:
    """Why one company is, or is not, a candidate: its gate failures and score."""
    cik = next((c for c, v in listings.items() if v["ticker"] == ticker.upper()), None)
    if cik is None:
        return f"{ticker}: no listing on NYSE or Nasdaq as common stock"
    m = company_metrics(fins.get(cik), cutoff)
    if m is None:
        return f"{ticker}: eight consecutive quarters of revenue and operating income could not be established"
    failed = gate_failures(m)
    shown = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in m.items()}
    return (f"{ticker}: {'FAILS ' + ', '.join(failed) if failed else 'passes every gate'} | "
            f"score {score(m)} | {shown}")
```

In `main`, add the arguments and replace the cutoff and write logic:

```python
    parser.add_argument("--as-of", help="run on the data filed by a past quarter, e.g. 2023Q1; writes nothing")
    parser.add_argument("--no-size-cap", action="store_true", help="historical checks: lift the $10B ceiling")
    parser.add_argument("--find", action="append", default=[], help="explain one ticker (repeatable)")
```

```python
    cutoff = as_of_cutoff(args.as_of) if args.as_of else date.today()
    if args.no_size_cap:
        import diamond_universe
        diamond_universe.MAX_MARKET_VALUE = float("inf")
```

For `--as-of`, prices must be those of the cutoff, not today's. Pass a price function bound to the cutoff:

```python
    def prices_as_of(tickers):
        if not args.as_of:
            return fetch_prices(tickers)
        import yfinance as yf
        raw = yf.download(sorted(set(tickers)), start=(cutoff - timedelta(days=200)).isoformat(),
                          end=(cutoff + timedelta(days=1)).isoformat(), auto_adjust=False, progress=False, threads=True)
        out = {}
        for t in tickers:
            try:
                closes = raw["Close"][t].dropna()
                out[t] = {"closes": [float(x) for x in closes.values],
                          "volumes": [float(x) for x in raw["Volume"][t].reindex(closes.index).values]}
            except Exception:
                continue
        return out
```

`auto_adjust=False` matters here: the market value on that date needs the price as it was, and share counts from the filings are as reported then.

After `print_list(doc)`:

```python
    for ticker in args.find:
        print(explain(ticker, cutoff, fins, listings))
    if args.as_of:
        picks = [r["ticker"] for r in doc["watchlist"]]
        returns = forward_returns(picks + [BENCHMARK], cutoff)
        got = [returns[t] for t in picks if t in returns]
        if got:
            got.sort()
            print(f"\n12 months after {cutoff}: {len(got)} of {len(picks)} priced | "
                  f"average {sum(got) / len(got):+.0%} | median {got[len(got) // 2]:+.0%} | "
                  f"best {got[-1]:+.0%} | worst {got[0]:+.0%} | {BENCHMARK} {returns.get(BENCHMARK, float('nan')):+.0%}")
        print("Limits: today's ticker list (companies since delisted or acquired are missing, which flatters "
              "the result), and the SEC figures include later restatements.")
        return 0
```

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests/test_diamond_run.py -q`
Expected: 10 passed

- [ ] **Step 5: The Palantir test (owner checkpoint)**

Run each and keep the output:
```bash
export SEC_EDGAR_USER_AGENT="stocktrader diamond screen (aschumacherdesign@gmail.com)"
python3 diamond_run.py --as-of 2023Q1 --no-size-cap --top 60 --find PLTR
python3 diamond_run.py --as-of 2023Q2 --no-size-cap --top 60 --find PLTR
for q in 2022Q2 2022Q4 2023Q2 2023Q4 2024Q2 2024Q4; do python3 diamond_run.py --as-of $q --top 40; done
```
Expected: `PLTR: passes every gate` in at least one of the two 2023 runs, ranked in the top 60. If it fails a gate, that gate's threshold does not describe the pattern the owner named: report which gate and by how much, propose the changed threshold, and re-run all eight commands with it. Do not tune on the six hit-rate runs alone; Palantir is the stated target.

Report to the owner: Palantir's rank and score in each 2023 run; for each of the six quarters, the average and median 12-month return of the list against IWM; and the two limits printed by the tool. This is the stopping point for owner review before the screen is scheduled.

- [ ] **Step 6: Commit**

```bash
git add diamond_run.py diamond_screen.py tests/test_diamond_run.py
git commit -m "Diamond screen: as-of runs, forward returns and per-company explanation"
```

---

### Task 7: Schedule it and show it in the weekly review

**Files:**
- Create: `.github/workflows/diamond_screen.yml`
- Modify: `dispatcher.py` (the `TIMETABLE` Saturday block)
- Modify: `weekly_review.py` (inputs and a new section)
- Test: `tests/test_dispatcher.py`, `tests/test_weekly_review.py` (append)

**Interfaces:**
- Consumes: `docs/data/diamonds.json`, `docs/data/diamond_history.json` as written by Task 5.
- Produces: `weekly_review.diamond_section(doc: Optional[dict], history: Optional[list], today: date) -> list[str]` (markdown lines).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_dispatcher.py`:

```python
def test_diamond_screen_runs_saturday_before_the_rest_of_the_chain():
    assert _due(2026, 10, 3, 7, 30) == ["diamond_screen.yml"]
    assert "diamond_screen.yml" not in _due(2026, 10, 5, 7, 30)          # not on a weekday
    assert ("diamond_screen.yml", "07:30") in _catch_up(2026, 10, 3, 8, 0)
```

Append to `tests/test_weekly_review.py`:

```python
def _diamond_doc(day="2026-10-10"):
    row = lambda rank, t, total: {"rank": rank, "ticker": t, "name": f"{t} Inc", "score": {"total": total},
                                  "market_value": 2.1e9, "revenue_growth": 0.31, "margin_change": 0.12,
                                  "ttm_op_margin": 0.01}
    return {"generated_at": f"{day}T07:31:00", "coverage": {"with_metrics": 3100, "passed_financial_gates": 140,
                                                           "watchlist": 2},
            "watchlist": [row(1, "AAA", 81.5), row(2, "BBB", 70.0)]}


def test_diamond_section_lists_the_top_names_with_rank_change():
    from datetime import date
    from weekly_review import diamond_section
    history = [{"date": "2026-10-03", "ticker": "AAA", "rank": 4, "total": 70.0},
               {"date": "2026-10-10", "ticker": "AAA", "rank": 1, "total": 81.5},
               {"date": "2026-10-10", "ticker": "BBB", "rank": 2, "total": 70.0}]
    text = "\n".join(diamond_section(_diamond_doc(), history, date(2026, 10, 10)))
    assert "## Diamond watchlist" in text
    assert "| 1 | AAA | 81.5 | up 3 |" in text and "| 2 | BBB | 70.0 | new |" in text
    assert "+31%" in text and "+12 pts" in text and "$2.1B" in text
    assert "3,100" in text and "140" in text


def test_diamond_section_without_a_list_or_with_an_old_one_says_so():
    from datetime import date
    from weekly_review import diamond_section
    assert "no list" in "\n".join(diamond_section(None, None, date(2026, 10, 10)))
    old = "\n".join(diamond_section(_diamond_doc("2026-09-26"), [], date(2026, 10, 10)))
    assert "14 days old" in old


def test_diamond_section_escapes_company_names():
    from datetime import date
    from weekly_review import diamond_section
    doc = _diamond_doc()
    doc["watchlist"][0]["name"] = "Evil | [link](http://x) Inc"
    text = "\n".join(diamond_section(doc, [], date(2026, 10, 10)))
    assert "[link](http://x)" not in text
```

- [ ] **Step 2: Run to verify they fail**

Run: `python3 -m pytest tests/test_dispatcher.py tests/test_weekly_review.py -q`
Expected: 4 failed, the rest pass

- [ ] **Step 3: Implement**

`dispatcher.py`, first line of the Saturday block in `TIMETABLE`:

```python
    ("diamond_screen.yml",     [SATURDAY], "07:30", "07:30", None),  # small/mid-cap inflection watchlist (no trades)
```

`weekly_review.py`: read the two files wherever the other `docs/data` inputs are read (the function that builds `data`), under the keys `diamonds` and `diamond_history`; add this function; and call `out += diamond_section(data.get("diamonds"), data.get("diamond_history"), today)` in `build_review` after the experiments section. Company names come from SEC data and go into a GitHub issue, so they pass through the module's existing `_md` escaper like every other interpolated string.

```python
DIAMOND_TOP = 15


def diamond_section(doc: Optional[Dict], history: Optional[list], today: date) -> List[str]:
    """The diamond watchlist (diamond_run.py): top names, and how each moved since the week before."""
    out = ["## Diamond watchlist"]
    rows = (doc or {}).get("watchlist") if isinstance(doc, dict) else None
    if not rows:
        return out + ["- There is no list yet."]
    day = str(doc.get("generated_at") or "")[:10]
    try:
        age = (today - date.fromisoformat(day)).days
    except ValueError:
        age = None
    if age is None or age > 8:
        out.append(f"- **The list is {age if age is not None else 'an unknown number of'} days old**: "
                   f"this week's screen did not produce one.")
    cov = doc.get("coverage") if isinstance(doc.get("coverage"), dict) else {}
    out.append(f"- {int(_num(cov.get('with_metrics'))):,} companies with eight quarters of figures; "
               f"{int(_num(cov.get('passed_financial_gates'))):,} turning from loss to profit; no trades are placed from this list.")
    earlier = sorted({h.get("date") for h in history or [] if isinstance(h, dict) and str(h.get("date")) < day})
    before = {h["ticker"]: h["rank"] for h in history or []
              if isinstance(h, dict) and earlier and h.get("date") == earlier[-1]}
    out += ["", "| # | Ticker | Score | Since last week | Revenue | Margin change | Value | Company |",
            "|---|---|---|---|---|---|---|---|"]
    for r in rows[:DIAMOND_TOP]:
        if not isinstance(r, dict):
            continue
        ticker, rank = _md(r.get("ticker"), 8), int(_num(r.get("rank")))
        was = before.get(r.get("ticker"))
        move = "new" if was is None else "same" if was == rank else f"up {was - rank}" if was > rank else f"down {rank - was}"
        value = _num(r.get("market_value"))
        size = f"${value / 1e9:.1f}B" if value >= 1e9 else f"${value / 1e6:.0f}M"
        out.append(f"| {rank} | {ticker} | {_num((r.get('score') or {}).get('total')):.1f} | {move} | "
                   f"{_num(r.get('revenue_growth')):+.0%} | {_num(r.get('margin_change')) * 100:+.0f} pts | "
                   f"{size} | {_md(r.get('name'), 40)} |")
    return out
```

`.github/workflows/diamond_screen.yml`:

```yaml
name: Diamond Screen

# Weekly: small and mid-sized companies whose finances are turning from loss
# to profit (diamond_run.py). Builds a watchlist only; nothing is traded.
# Started by dispatcher.yml (Saturday, before the rest of the chain).

on:
  workflow_dispatch:

jobs:
  screen:
    runs-on: ubuntu-latest
    timeout-minutes: 25
    permissions:
      contents: write
      issues: write          # to open a failure issue

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v4
        with:
          python-version: '3.12'
          cache: pip

      - run: pip install -r requirements.txt

      - name: Build the watchlist
        env:
          # The SEC requires a contact address on every request
          SEC_EDGAR_USER_AGENT: "stocktrader diamond screen (aschumacherdesign@gmail.com)"
        run: python diamond_run.py

      - name: Commit the watchlist
        if: always()
        run: |
          git config user.email "aschumacherdesign@gmail.com"
          git config user.name "schu51"
          git add docs/data/diamonds.json docs/data/diamond_history.json 2>/dev/null || true
          git diff --staged --quiet || git commit -m "Diamond watchlist update [skip ci]"
          pushed=0
          for attempt in 1 2 3 4 5; do
            if git pull --rebase -X theirs origin main && git push; then pushed=1; break; fi
            echo "push attempt $attempt failed, retrying..."
            git rebase --abort 2>/dev/null || true
            sleep $((RANDOM % 4 + 2))
          done
          [ "$pushed" = "1" ] || { echo "::error::push failed after 5 attempts"; exit 1; }

      # In-workflow alert: runs started by the dispatcher belong to the Actions
      # bot, which neither emails anyone nor triggers a workflow_run listener.
      - name: Open issue on failure
        if: failure() || cancelled()
        env:
          GH_TOKEN: ${{ github.token }}
          RUN_URL: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: |
          gh issue create \
            --title "🚨 ${GITHUB_WORKFLOW} run failed $(date -u +%Y-%m-%d)" \
            --body "The ${GITHUB_WORKFLOW} workflow failed. Run log: ${RUN_URL}"
```

- [ ] **Step 4: Run to verify they pass**

Run: `python3 -m pytest tests -q`
Expected: every test passes (the count before this plan, 364, plus the new ones)

- [ ] **Step 5: Commit, push and run it once for real**

```bash
git add .github/workflows/diamond_screen.yml dispatcher.py weekly_review.py tests/test_dispatcher.py tests/test_weekly_review.py
git commit -m "Diamond screen: Saturday schedule and weekly review section"
git pull --rebase origin main && git push origin main
```

Then start the workflow once by hand from the Actions tab (or wait for Saturday 07:30 ET) and confirm: the run is green, `docs/data/diamonds.json` and `docs/data/diamond_history.json` are committed, and `python3 weekly_review.py | sed -n '/Diamond watchlist/,/^## /p'` shows the table.
