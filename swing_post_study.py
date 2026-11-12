"""
Swing Post Study
================
One-off research run (2026-11-12): how the rules from a r/swingtrading
write-up ("How Trading Really Works", part 3, u/30RITUALS) do against the
live momentum rules, replayed by backtest.py over the last three years on the
same universe, prices and costs the daily eval uses.

Each variant changes the live rules ("champion", daily_eval.live_rules) in one
idea from the post; two combined variants put most of the post together:

  tight15 / tight30    rank boost for a tight base (narrow range, volume drying up, near the high)
  adr4_8 / adr3        only names that move 4-8% (or at least 3%) a day on average
  ext3                 no entry more than 3 ATRs above the 20-day EMA
  swing_exit(_half)    sell a third (half) at +1R, stop to break-even, trail the rest on the 20-day EMA
  qqq20 / vix15 / vix20 / breadth50   market gates: QQQ above its 20-day EMA, VIX under 15 or 20,
                       half the universe above its 50-day average
  post_all             every idea at the post's numbers, plus buying only 20-day breakouts on volume
  post_lite            tight30 + ext3 + swing_exit + qqq20

Places no orders and changes no rules. Writes research/swing_post_study.json
and research/swing_post_study.md.

    python swing_post_study.py               # everything (about an hour on 4 cores)
    python swing_post_study.py --only ext3   # one variant against the champion
"""

import argparse
import json
import logging
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd

import backtest as bt
from daily_eval import YEARS, gates, live_rules, summarize_run

ROOT = Path(__file__).parent.resolve()
OUT_JSON = ROOT / "research" / "swing_post_study.json"
OUT_MD = ROOT / "research" / "swing_post_study.md"

VARIANTS: Dict[str, Dict] = {
    "tight15": {"tight_boost": 0.15},
    "tight30": {"tight_boost": 0.30},
    "adr4_8": {"min_adr": 4.0, "max_adr": 8.0},
    "adr3": {"min_adr": 3.0},
    "ext3": {"max_ext_atr20": 3.0},
    "swing_exit": {"swing_exit": True},
    "swing_exit_half": {"swing_exit": True, "partial_size": 0.5},
    "qqq20": {"qqq_above_ema20": True},
    "vix15": {"max_vix": 15.0},
    "vix20": {"max_vix": 20.0},
    "breadth50": {"min_breadth": 0.5},
    "post_all": {"entry_trigger": "breakout", "tight_boost": 0.30, "min_adr": 4.0, "max_adr": 8.0,
                 "max_ext_atr20": 3.0, "swing_exit": True, "qqq_above_ema20": True,
                 "max_vix": 15.0, "min_breadth": 0.5},
    "post_lite": {"tight_boost": 0.30, "max_ext_atr20": 3.0, "swing_exit": True, "qqq_above_ema20": True},
}

_DATA: Dict = {}


def position_stats(trades: pd.DataFrame) -> Dict:
    """
    Win rate and profit factor per position, not per sale: a partial sale and
    the rest of the same position count once, so the swing exit's extra
    'trades' do not flatter its win rate.
    """
    if not len(trades):
        return {"positions": 0}
    g = trades.assign(cost=trades["shares"] * trades["entry_price"]).groupby(["symbol", "entry_date"])
    pos = pd.DataFrame({"pnl_usd": g["pnl_usd"].sum(), "cost": g["cost"].sum()})
    pos["pnl_pct"] = pos["pnl_usd"] / pos["cost"] * 100
    wins, losses = pos[pos["pnl_usd"] > 0], pos[pos["pnl_usd"] <= 0]
    gross_loss = -losses["pnl_usd"].sum()
    return {"positions": int(len(pos)), "win_rate": round(float(len(wins) / len(pos)), 4),
            "profit_factor": round(float(wins["pnl_usd"].sum() / gross_loss), 3) if gross_loss > 0 else None,
            "avg_win_pct": round(float(wins["pnl_pct"].mean()), 2) if len(wins) else None,
            "avg_loss_pct": round(float(losses["pnl_pct"].mean()), 2) if len(losses) else None}


def replay(job):
    name, args = job
    d = _DATA
    b = bt.Backtest(d["universe"], d["prices"], d["start"], None, earnings=d["earnings"], **args)
    b.run(progress_every=0)
    eq, tr = pd.Series(b.equity), pd.DataFrame(b.trades)
    r = summarize_run(eq, tr, d["today"])
    yearly = eq.resample("YE").last()
    first = eq.iloc[0]
    yr = {}
    prev = first
    for ts, v in yearly.items():
        yr[str(ts.year)] = round(float(v / prev - 1), 4)
        prev = v
    return name, {
        **r, "positions": position_stats(tr), "yearly": yr,
        "invested_share": round(float(1 - np.mean(b.cash_share)), 3) if b.cash_share else None,
        "exit_reasons": tr["exit_reason"].value_counts().to_dict() if len(tr) else {},
        "funnel": {k: v for k, v in b.counters.items() if v},
        "median_hold_days": float(tr["hold_days"].median()) if len(tr) else None,
    }


def pct(x, d=1):
    return "–" if x is None else f"{x * 100:+.{d}f}%"


def to_markdown(out: Dict) -> str:
    champ = out["champion"]
    lines = [f"# Swing post study — {out['generated']}", "",
             f"{YEARS}-year replay from {out['start']} on {out['universe']} tickers, same costs as the daily eval. "
             "Each row changes the live rules as shown. Position stats count a partial sale and the rest once.", "",
             "| variant | annual | max DD | worst month | last 6 mo | positions | pos win | pos PF | invested | gates |",
             "|---|---|---|---|---|---|---|---|---|---|"]

    def row(name, r, g=None):
        p = r["positions"]
        passed = "–" if g is None else f"{sum(g.values())}/{len(g)}"
        lines.append(f"| {name} | {pct(r['cagr'])} | {pct(r['max_drawdown'])} | {pct(r['worst_month'])} | "
                     f"{pct(r['recent_return'])} | {p.get('positions', 0)} | {pct(p.get('win_rate'), 0)} | "
                     f"{p.get('profit_factor') or '–'} | {round((r.get('invested_share') or 0) * 100)}% | {passed} |")

    row("**live (champion)**", champ)
    for name, r in out["variants"].items():
        if "error" in r:
            lines.append(f"| {name} | error: {r['error']} |||||||||")
        else:
            row(name, r, r["gates"])
    lines += ["", "Gates are the daily eval's promotion gates against the champion (all must pass, 5 evals in a row, "
              "before a rule change is proposed).", "", "## Variant settings", ""]
    for name, change in VARIANTS.items():
        lines.append(f"- **{name}**: `{json.dumps(change)}`")
    lines += ["", f"Live rules: `{json.dumps(out['champion_rules'])}`"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args(argv)

    today = date.today()
    start = (pd.Timestamp(today) - pd.DateOffset(years=YEARS)).date().isoformat()
    end = (today + timedelta(days=1)).isoformat()
    universe = bt.load_universe()
    prices = bt.load_prices(list(universe), start, end)
    missing = [t for t in bt.MARKET_TICKERS if t not in prices["Close"].columns]
    if missing:
        print(f"WARNING: no data for {missing}; their gates will not block buys", flush=True)
    universe = {s: v for s, v in universe.items() if s in prices["Close"].columns}
    earnings = bt.load_earnings(list(universe))
    _DATA.update(universe=universe, prices=prices, start=start, earnings=earnings, today=today)

    champ_args = live_rules()
    variants = {k: v for k, v in VARIANTS.items() if not args.only or k in args.only}
    jobs = [("champion", champ_args)] + [(n, {**champ_args, **c}) for n, c in variants.items()]
    print(f"Replaying {len(jobs)} rule sets from {start} on {len(universe)} tickers", flush=True)

    results = {}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:   # fork: workers share the loaded prices
        for name, r in pool.map(replay, jobs):
            results[name] = r
            print(f"  {name:16} annual {pct(r['cagr'])}  max DD {pct(r['max_drawdown'])}  "
                  f"positions {r['positions'].get('positions')}  PF {r['positions'].get('profit_factor')}", flush=True)

    champion = results.pop("champion")
    out = {"generated": today.isoformat(), "start": start, "universe": len(universe),
           "market_data_missing": missing, "champion_rules": champ_args, "champion": champion,
           "variants": {n: {"change": variants[n], **r, "gates": gates(r, champion)} for n, r in results.items()}}
    OUT_JSON.write_text(json.dumps(out, indent=2, default=str))
    OUT_MD.write_text(to_markdown(out))
    print(to_markdown(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
