"""
Point-in-time fundamentals for the backtest
===========================================
Quarterly diluted EPS and revenue for every SEC filer, each stamped with the
date it became public, so a replay only ever sees what was known that day.
Built from the SEC's XBRL "frames" (free, no key), the same source and
parsing as the diamond screen (diamond_data.py).

When a quarter became known: the company's first earnings report on or after
the quarter's end (8-K item 2.02 dates from the big-mover cache, or yfinance,
via backtest.load_earnings), within 100 days. Without one, the quarter end plus
KNOWN_AFTER_DAYS: later than almost every 10-Q/10-K deadline, so it errs late.

Signals as of a date (factor_asof), from the quarters known by then:
  sue          standardized unexpected earnings: this quarter's EPS change on
               the same quarter a year ago, over the spread of that change in
               the previous eight quarters (a seasonal random-walk surprise)
  eps_yoy      EPS growth on the same quarter a year ago
  rev_yoy      revenue growth on the same quarter a year ago
  rev_accel    rev_yoy now minus rev_yoy a quarter earlier
  days_since   calendar days since the latest quarter became known
  score        0-100 blend for ranking (50 = no information)

The build runs where the SEC is reachable (GitHub Actions); the result is
cached in trading_data/backtest_cache/ (gitignored).
"""

import json
import logging
import statistics
from bisect import bisect_right
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
CACHE = ROOT / "trading_data" / "backtest_cache" / "fundamentals_pit.json"
FIRST_YEAR = 2021               # three-year replays plus a year of history for year-on-year and spreads
KNOWN_AFTER_DAYS = 60           # no report date found: assume public this long after the quarter's end
REPORT_WINDOW_DAYS = 100        # a report date further than this after the quarter end is not its report
MIN_SPREAD_QUARTERS = 4         # year-on-year EPS changes needed before a surprise is measured

FIGURES = {
    "eps": {"taxonomy": "us-gaap", "unit": "USD-per-shares", "tags": ["EarningsPerShareDiluted", "EarningsPerShareBasic"]},
    "revenue": {"taxonomy": "us-gaap", "unit": "USD",
                "tags": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"]},
}


def _as_date(x) -> Optional[date]:
    if isinstance(x, date):
        return x
    try:
        return date.fromisoformat(str(x)[:10])
    except ValueError:
        return None


def known_from(quarter_end: date, report_dates: List[date]) -> date:
    """The first report on or after the quarter's end (within REPORT_WINDOW_DAYS), else the conservative default."""
    for d in report_dates:
        if quarter_end <= d <= quarter_end + timedelta(days=REPORT_WINDOW_DAYS):
            return d
    return quarter_end + timedelta(days=KNOWN_AFTER_DAYS)


def periods(today: date) -> List[str]:
    """Calendar-quarter and calendar-year frame names from FIRST_YEAR to today."""
    out = []
    for y in range(FIRST_YEAR, today.year + 1):
        out += [f"CY{y}Q{q}" for q in range(1, 5) if date(y, 3 * q, 1) <= today]
        out.append(f"CY{y}")
    return out


def build(today: date, earnings: Dict[str, List], fetch=None, listings: Optional[Dict[int, Dict]] = None) -> Dict:
    """{ticker: [[quarter_end, known_from, eps, revenue], ...]} oldest first."""
    import diamond_data as dd
    fetch = fetch or dd.fetch_frame
    if listings is None:
        import diamond_universe as du
        listings = du.listed_companies(du.fetch_listings())
    rows: Dict[str, Dict[int, List[dict]]] = {}
    for name, spec in FIGURES.items():
        by_tag: List[Dict[int, List[dict]]] = []
        for tag in spec["tags"]:
            per_cik: Dict[int, List[dict]] = {}
            for period in periods(today):
                for r in fetch(spec["taxonomy"], tag, spec["unit"], period):
                    if isinstance(r.get("cik"), int):
                        per_cik.setdefault(r["cik"], []).append({k: r[k] for k in ("start", "end", "val") if k in r})
            by_tag.append(per_cik)
        # One tag per company for the whole series (as diamond_data: never splice two definitions)
        rows[name] = {cik: max(by_tag, key=lambda t: len(t.get(cik, ())))[cik] for cik in set().union(*by_tag)}
    out = {}
    for cik, listing in listings.items():
        ticker = listing["ticker"]
        eps = dd.quarterly_series(rows["eps"].get(cik, []))
        rev = dd.quarterly_series(rows["revenue"].get(cik, []))
        ends = sorted(set(eps) | set(rev))
        if not ends:
            continue
        reports = sorted(d for d in (_as_date(x) for x in earnings.get(ticker, []) or []) if d)
        out[ticker] = [[e.isoformat(), known_from(e, reports).isoformat(), eps.get(e), rev.get(e)] for e in ends]
    return out


def load_or_build(today: date, earnings: Dict[str, List], max_age_days: int = 7) -> Dict:
    """The cached table if it is recent enough, else a fresh build (SEC access needed)."""
    if CACHE.exists():
        import contracts
        data = contracts.read(CACHE)
        built = _as_date(data.get("built"))
        if built and (today - built).days <= max_age_days:
            return data["tickers"]
    tickers = build(today, earnings)
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"built": today.isoformat(), "tickers": tickers}))
    logger.info(f"fundamentals: {len(tickers)} tickers built from SEC frames")
    return tickers


def _yoy(now: Optional[float], before: Optional[float]) -> Optional[float]:
    if now is None or before is None or abs(before) < 1e-9:
        return None
    return (now - before) / abs(before)


def factor_asof(history: List[list], asof: date) -> Optional[Dict]:
    """Signals from the quarters public by `asof`; None if fewer than five are known."""
    known = [q for q in history if _as_date(q[1]) <= asof]
    if len(known) < 5:
        return None
    ends = [_as_date(q[0]) for q in known]
    by_end = dict(zip(ends, known))

    def year_ago(e: date):
        """The quarter that ended about a year before `e` (fiscal calendars drift a few days)."""
        for d in ends:
            if 350 <= (e - d).days <= 380:
                return by_end[d]
        return None

    latest = known[-1]
    prior = year_ago(ends[-1])
    eps_now, rev_now = latest[2], latest[3]
    eps_then, rev_then = (prior[2], prior[3]) if prior else (None, None)
    # Surprise: this quarter's year-on-year EPS change against the spread of earlier ones
    changes = []
    for e in ends[-9:-1]:
        q, p = by_end[e], year_ago(e)
        if p and q[2] is not None and p[2] is not None:
            changes.append(q[2] - p[2])
    sue = None
    if eps_now is not None and eps_then is not None and len(changes) >= MIN_SPREAD_QUARTERS:
        spread = statistics.pstdev(changes)
        if spread > 1e-6:
            sue = (eps_now - eps_then) / spread
    rev_yoy = _yoy(rev_now, rev_then)
    prev = known[-2]
    prev_prior = year_ago(ends[-2])
    rev_yoy_prev = _yoy(prev[3], prev_prior[3]) if prev_prior else None
    accel = rev_yoy - rev_yoy_prev if rev_yoy is not None and rev_yoy_prev is not None else None
    return {"sue": sue, "eps_yoy": _yoy(eps_now, eps_then), "rev_yoy": rev_yoy, "rev_accel": accel,
            "days_since": (asof - _as_date(latest[1])).days, "score": score(sue, rev_yoy, accel)}


def score(sue: Optional[float], rev_yoy: Optional[float], accel: Optional[float]) -> float:
    """0-100: surprise, growth and acceleration, each capped so no one figure dominates. 50 = nothing known."""
    clip = lambda x, lo, hi: max(lo, min(hi, x))
    s = 50.0
    if sue is not None:
        s += 8.0 * clip(sue, -3.0, 3.0)
    if rev_yoy is not None:
        s += 40.0 * clip(rev_yoy, -0.5, 0.5)
    if accel is not None:
        s += 40.0 * clip(accel, -0.25, 0.25)
    return round(clip(s, 0.0, 100.0), 1)


class Lookup:
    """factor_asof for many symbols on many days, memoised per (symbol, latest known quarter)."""

    def __init__(self, table: Dict[str, List[list]]):
        self.table = table or {}
        self._known = {t: [_as_date(q[1]) for q in rows] for t, rows in self.table.items()}
        self._memo: Dict[tuple, Optional[Dict]] = {}

    def __call__(self, sym: str, asof: date) -> Optional[Dict]:
        rows = self.table.get(sym)
        if not rows:
            return None
        k = bisect_right(sorted(self._known[sym]), asof)
        key = (sym, k)
        if key not in self._memo:
            self._memo[key] = factor_asof(rows, asof)
        f = self._memo[key]
        if f is None:
            return None
        known = [d for d in self._known[sym] if d <= asof]
        return dict(f, days_since=(asof - max(known)).days)
