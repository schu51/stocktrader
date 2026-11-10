#!/usr/bin/env python3
"""
Overnight split (research, 2026-11-10)
======================================
Where does the strategy's return come from: while the market is closed, or
while it is open? Replays the live rules over three years (the same replay as
daily_eval.py), then splits every holding's path into legs:

  night   prior close -> next open      (what an overnight-only trader would hold)
  day     open -> close                 (what a day trader would hold)
          the entry day runs from the fill to the close, the exit day from the
          open to the exit fill, so the strategy's own slippage sits in the day legs

Each session's book return is value-weighted across the holdings in it. The
overnight-only strategy holds the same stocks the live rules pick, but only
from close to open, buying in the closing auction and selling in the opening
auction: it pays AUCTION_COSTS_BPS per side on every night it holds anything.

Earnings: a report dated D gaps either at D's open (before the open) or at
D+1's open (after the close), and the dates carry no time. "ex_earnings" drops
both nights around each report, which is what an overnight trader would skip.

Caveat: the universe is today's index members replayed back three years, so
absolute returns are flattered; both legs share that bias, so the comparison
between them is the point.

Places no orders. Writes research/overnight_split.json.

Usage:
    python overnight_split.py
"""

import json
import logging
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.resolve()
OUT = ROOT / "research" / "overnight_split.json"
YEARS = 3
AUCTION_COSTS_BPS = (0.0, 2.5, 5.0, 10.0)     # per side; an overnight round trip pays twice

logger = logging.getLogger(__name__)


def _holdings(bt) -> List[Dict]:
    """Every holding the replay had: closed trades plus positions still open at the end."""
    out = []
    for t in bt.trades:
        out.append({"symbol": t["symbol"], "shares": float(t["shares"]),
                    "entry_date": date.fromisoformat(str(t["entry_date"])[:10]), "entry_price": float(t["entry_price"]),
                    "exit_date": date.fromisoformat(str(t["exit_date"])[:10]), "exit_price": float(t["exit_price"])})
    for sym, p in (bt.positions or {}).items():
        out.append({"symbol": sym, "shares": float(p["shares"]), "entry_date": p["entry_date"],
                    "entry_price": float(p["avg_cost"]), "exit_date": None, "exit_price": None})
    return out


def _near_report(reports: Optional[Iterable], night_from: date, night_to: date) -> bool:
    """True if a report dated on either side of this night could gap it."""
    for d in reports or []:
        d = d if isinstance(d, date) else date.fromisoformat(str(d)[:10])
        if d in (night_from, night_to):
            return True
    return False


def session_legs(bt, earnings: Optional[Dict[str, List]] = None) -> pd.DataFrame:
    """
    One row per holding per session: date (the session's trading day), symbol,
    leg ("night" ends at this date's open, "day" is this date's trading hours),
    value at the start of the leg, pnl over it, and near_report for nights.
    """
    index = {ts.date(): i for i, ts in enumerate(bt.dates)}
    last = len(bt.dates) - 1
    rows = []
    for h in _holdings(bt):
        sym, n = h["symbol"], h["shares"]
        i0 = index.get(h["entry_date"])
        i1 = index.get(h["exit_date"]) if h["exit_date"] else last
        if i0 is None or i1 is None or sym not in bt.close.columns:
            continue
        o, c = bt.open[sym], bt.close[sym]
        for j in range(i0, i1 + 1):
            day = bt.dates[j].date()
            oj, cj = float(o.iloc[j]), float(c.iloc[j])
            if j > i0:                                            # the night before this session
                prev = float(c.iloc[j - 1])
                if prev == prev and oj == oj and prev > 0:
                    rows.append({"date": day, "symbol": sym, "leg": "night", "value": n * prev,
                                 "pnl": n * (oj - prev),
                                 "near_report": _near_report((earnings or {}).get(sym), bt.dates[j - 1].date(), day)})
            start = h["entry_price"] if j == i0 else oj
            end = h["exit_price"] if (h["exit_date"] and j == i1) else cj
            if start == start and end == end and start > 0:
                rows.append({"date": day, "symbol": sym, "leg": "day", "value": n * start,
                             "pnl": n * (end - start), "near_report": False})
    return pd.DataFrame(rows, columns=["date", "symbol", "leg", "value", "pnl", "near_report"])


def book_returns(legs: pd.DataFrame, leg: str, ex_earnings: bool = False) -> pd.Series:
    """Value-weighted return of the book over each session of one leg."""
    x = legs[legs["leg"] == leg]
    if ex_earnings:
        x = x[~x["near_report"].astype(bool)]
    if x.empty:
        return pd.Series(dtype=float)
    g = x.groupby("date")[["pnl", "value"]].sum()
    g = g[g["value"] > 0]
    return (g["pnl"] / g["value"]).sort_index()


def describe(r: pd.Series, cost_per_session: float = 0.0) -> Dict:
    """Per-session statistics and the compounded path, net of a cost charged every session."""
    if r.empty:
        return {"sessions": 0}
    net = r - cost_per_session
    eq = (1 + net).cumprod()
    years = max((pd.Timestamp(r.index[-1]) - pd.Timestamp(r.index[0])).days / 365.25, 1e-9)
    sd = float(net.std(ddof=1)) if len(net) > 1 else float("nan")
    by_year = {str(y): round(float((1 + v).prod() - 1), 4) for y, v in net.groupby(pd.to_datetime(net.index).year)}
    return {
        "sessions": int(len(net)),
        "mean_bps": round(float(net.mean()) * 1e4, 2),
        "median_bps": round(float(net.median()) * 1e4, 2),
        "t": round(float(net.mean()) / (sd / math.sqrt(len(net))), 2) if sd and sd == sd else None,
        "share_positive": round(float((net > 0).mean()), 3),
        "total_return": round(float(eq.iloc[-1] - 1), 4),
        "cagr": round(float(eq.iloc[-1] ** (1 / years) - 1), 4) if years >= 0.25 and eq.iloc[-1] > 0 else None,
        "max_drawdown": round(float((eq / eq.cummax() - 1).min()), 4),
        "worst_sessions_bps": [round(float(v) * 1e4, 1) for v in net.nsmallest(5)],
        "by_year": by_year,
    }


def split_report(bt, earnings: Optional[Dict[str, List]] = None, costs_bps=AUCTION_COSTS_BPS) -> Dict:
    legs = session_legs(bt, earnings)
    night, day = book_returns(legs, "night"), book_returns(legs, "day")
    night_ex = book_returns(legs, "night", ex_earnings=True)
    nights = legs[legs["leg"] == "night"]
    report_share = float(nights["near_report"].mean()) if len(nights) else None
    return {
        "night": describe(night),
        "day": describe(day),
        "night_ex_earnings": describe(night_ex),
        "overnight_only": {f"{c:g}bps_per_side": {"all_nights": describe(night, 2 * c / 1e4),
                                                  "ex_earnings": describe(night_ex, 2 * c / 1e4)}
                           for c in costs_bps},
        "night_legs_near_a_report": round(report_share, 4) if report_share is not None else None,
        "pnl_usd": {"night": round(float(nights["pnl"].sum()), 2),
                    "night_near_report": round(float(nights.loc[nights["near_report"].astype(bool), "pnl"].sum()), 2),
                    "day": round(float(legs.loc[legs["leg"] == "day", "pnl"].sum()), 2)},
    }


def print_report(r: Dict) -> None:
    def line(name, d):
        if not d.get("sessions"):
            print(f"  {name:<46} no sessions")
            return
        print(f"  {name:<46} {d['sessions']:>5}  mean {d['mean_bps']:>6.1f}bp  median {d['median_bps']:>6.1f}bp  "
              f"t {d['t'] if d['t'] is not None else '-':>5}  +{d['share_positive']:.0%}  "
              f"total {d['total_return']:>7.1%}  CAGR {(d['cagr'] or 0):>6.1%}  maxDD {d['max_drawdown']:>6.1%}")
    print(f"\nOvernight split, {r['start']} to {r['end']} (live rules: {r['rules']})")
    line("held book, nights (close->open)", r["night"])
    line("held book, days (open->close)", r["day"])
    line("held book, nights ex-earnings", r["night_ex_earnings"])
    for cost, v in r["overnight_only"].items():
        line(f"overnight-only, {cost}", v["all_nights"])
        line(f"overnight-only, {cost}, ex-earnings", v["ex_earnings"])
    print(f"  night legs near a report: {r['night_legs_near_a_report']:.1%}   P&L $: {r['pnl_usd']}")


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)s  %(message)s")
    import backtest as bt
    from daily_eval import live_rules
    today = date.today()
    start = (pd.Timestamp(today) - pd.DateOffset(years=YEARS)).date().isoformat()
    end = (today + timedelta(days=1)).isoformat()
    universe = bt.load_universe()
    prices = bt.load_prices(list(universe), start, end)
    universe = {s: v for s, v in universe.items() if s in prices["Close"].columns}
    earnings = bt.load_earnings(list(universe))
    rules = live_rules()
    b = bt.Backtest(universe, prices, start, None, earnings=earnings, **rules)
    b.run(progress_every=0)
    report = {"generated_at": datetime.now().isoformat(), "start": start, "end": b.dates[b.last].date().isoformat(),
              "rules": rules, "universe": len(universe), "caveat": "today's index members replayed back: "
              "absolute returns are flattered; compare the legs", **split_report(b, earnings)}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str))
    print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
