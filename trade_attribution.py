"""
Trade attribution
=================
Why each closed trade won or lost, and how live trading compares with the
backtest of the same rules over the SAME period. Built to explain the gap the
agent-eval review found: live trades won 20% while the three-year backtest of
today's rules wins 60%.

Per closed trade, tags:
  exit        trend_break (closed below the 50-day average), stop, stop_gapped
              (filled 2%+ below its stop), hard_loss, reduce, stale, rotation
  quick_fail  closed within 3 trading days of entry
  earnings    a results filing (SEC 8-K 2.02) between entry and exit
  extended    bought 15%+ above its 50-day average (from the screen it was bought off)
  regime      the market regime of that screen (bull / neutral / bear)
  backtest    the backtest bought the same stock within 3 days: matched / unmatched

Summary:
  by tag      trades, win rate, average and total P&L
  same window the backtest's own win rate over the live trading window, for each
              backtest run on file: if it is as low as live, the gap is the period,
              not the execution
  matched     for trades both took: live vs backtest return on the same name

Inputs: docs/data/trades.json, research/backtest_trades_*.csv, earnings dates
from docs/data/big_movers_cache.json, and the screen each trade was bought off
from the point-in-time history (pit.py) when a checkout of it is given.

    python trade_attribution.py --history .history
"""

import argparse
import csv
import logging
import statistics as st
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional

import contracts

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
DOCS_DATA = ROOT / "docs" / "data"
OUT_FILE = DOCS_DATA / "trade_attribution.json"
BACKTESTS = {   # label -> file; the first is the rules most live trades ran under
    "rules live Jun-Nov (r11_live)": ROOT / "research" / "backtest_trades_r11_live.csv",
    "rules live since Nov 10 (r11_newlive)": ROOT / "research" / "backtest_trades_r11_newlive.csv",
}
MATCH_DAYS = 3
QUICK_DAYS = 3
EXTENDED = 0.15
GAP = 0.02


def _d(s) -> Optional[date]:
    try:
        return date.fromisoformat(str(s)[:10])
    except (TypeError, ValueError):
        return None


def exit_tag(t: Dict) -> str:
    r = str(t.get("exit_reason") or "").upper()
    if r == "BROKER_STOP" or r == "STOP":
        stop, px = t.get("stop_loss"), t.get("exit_price")
        if stop and px and px < stop * (1 - GAP):
            return "stop_gapped"
        return "stop"
    return {"PRICE_BELOW_50MA": "trend_break", "PRICE_BELOW_200MA": "trend_break", "DEATH_CROSS": "trend_break", "HARD_LOSS_STOP": "hard_loss", "REDUCE": "reduce",
            "STALE": "stale", "ROTATION": "rotation", "LOSS_CUT": "hard_loss"}.get(r, r.lower() or "unknown")


def entry_context(t: Dict, history) -> Dict:
    """The screen the trade was bought off: extension above the 50-day average, RS rank, regime."""
    if history is None:
        return {}
    try:
        screen = history.as_of("screener.json", t.get("entry_ts") or t["entry_date"])
    except Exception:
        return {}
    if not screen:
        return {}
    row = next((c for c in screen.get("candidates") or [] if c.get("symbol") == t["symbol"]), None)
    out = {"regime": (screen.get("market_regime") or {}).get("regime")}
    if row and row.get("sma50"):
        out["extension"] = round(row["price"] / row["sma50"] - 1, 4)
        out["rs_rank_at_screen"] = row.get("rs_rank")
    return out


def tag(trades: List[Dict], backtest: List[Dict], earnings: Dict[str, List[str]], history=None) -> List[Dict]:
    bt_by_symbol: Dict[str, List[Dict]] = {}
    for b in backtest:
        bt_by_symbol.setdefault(b["symbol"], []).append(b)
    out = []
    for t in trades:
        if t.get("status") != "CLOSED" or t.get("pnl_pct") is None:
            continue
        entry, exit_ = _d(t["entry_date"]), _d(t.get("exit_date"))
        ctx = entry_context(t, history)
        reports = [_d(x) for x in earnings.get(t["symbol"], [])]
        hit = next((b for b in bt_by_symbol.get(t["symbol"], [])
                    if entry and _d(b["entry_date"]) and abs((_d(b["entry_date"]) - entry).days) <= MATCH_DAYS + 2), None)
        tags = {
            "exit": exit_tag(t),
            "quick_fail": bool(t.get("hold_days") is not None and t["hold_days"] <= QUICK_DAYS),
            "earnings": bool(entry and exit_ and any(r and entry <= r <= exit_ for r in reports)),
            "extended": (ctx["extension"] >= EXTENDED) if "extension" in ctx else None,
            "regime": ctx.get("regime"),
            "backtest": "matched" if hit else "unmatched",
            "sleeve": bool(t.get("sleeve")),
        }
        out.append({"symbol": t["symbol"], "entry_date": t["entry_date"], "exit_date": t.get("exit_date"),
                    "pnl_pct": t["pnl_pct"], "pnl_usd": t.get("pnl_usd"), "hold_days": t.get("hold_days"),
                    "tags": tags, "context": ctx,
                    "backtest_pnl_pct": float(hit["pnl_pct"]) if hit and hit.get("pnl_pct") not in (None, "") else None})
    return out


def _stats(rows: Iterable[Dict]) -> Dict:
    rows = list(rows)
    if not rows:
        return {"trades": 0}
    p = [r["pnl_pct"] for r in rows]
    return {"trades": len(rows), "win_rate": round(sum(x > 0 for x in p) / len(p), 3),
            "avg_pnl_pct": round(st.mean(p), 2), "total_pnl_usd": round(sum(r.get("pnl_usd") or 0 for r in rows), 2)}


def summarize(tagged: List[Dict], backtests: Dict[str, List[Dict]]) -> Dict:
    out = {"all": _stats(tagged), "by_tag": {}}
    for key in ("exit", "quick_fail", "earnings", "extended", "regime", "backtest", "sleeve"):
        groups: Dict[str, List[Dict]] = {}
        for r in tagged:
            groups.setdefault(str(r["tags"][key]), []).append(r)
        out["by_tag"][key] = {k: _stats(v) for k, v in sorted(groups.items())}
    if tagged:
        lo, hi = min(r["entry_date"] for r in tagged), max(r["entry_date"] for r in tagged)
        out["window"] = {"from": lo, "to": hi}
        out["same_window_backtest"] = {}
        for label, rows in backtests.items():
            inside = [b for b in rows if lo <= b["entry_date"] <= hi and b.get("pnl_pct") not in (None, "")]
            if inside:
                p = [float(b["pnl_pct"]) for b in inside]
                out["same_window_backtest"][label] = {
                    "trades": len(p), "win_rate": round(sum(x > 0 for x in p) / len(p), 3),
                    "avg_pnl_pct": round(st.mean(p), 2),
                    "covers_to": max(b["entry_date"] for b in inside)}
    matched = [r for r in tagged if r["backtest_pnl_pct"] is not None]
    if matched:
        out["matched"] = {"trades": len(matched),
                          "live_avg_pnl_pct": round(st.mean(r["pnl_pct"] for r in matched), 2),
                          "backtest_avg_pnl_pct": round(st.mean(r["backtest_pnl_pct"] for r in matched), 2),
                          "live_win_rate": round(sum(r["pnl_pct"] > 0 for r in matched) / len(matched), 3),
                          "backtest_win_rate": round(sum(r["backtest_pnl_pct"] > 0 for r in matched) / len(matched), 3)}
    pnl = sum(r.get("pnl_usd") or 0 for r in tagged)
    losses = [r for r in tagged if (r.get("pnl_usd") or 0) < 0]
    by_exit = {}
    for r in losses:
        by_exit[r["tags"]["exit"]] = by_exit.get(r["tags"]["exit"], 0) + r["pnl_usd"]
    out["losses_by_exit"] = {k: round(v, 2) for k, v in sorted(by_exit.items(), key=lambda kv: kv[1])}
    out["worst"] = [{k: r[k] for k in ("symbol", "entry_date", "pnl_pct", "pnl_usd")} | {"tags": r["tags"]}
                    for r in sorted(tagged, key=lambda r: r.get("pnl_usd") or 0)[:8]]
    out["total_pnl_usd"] = round(pnl, 2)
    return out


def findings(s: Dict) -> List[str]:
    """Plain sentences from the summary, for the brief."""
    out = []
    a = s.get("all", {})
    for label, b in (s.get("same_window_backtest") or {}).items():
        out.append(f"Over the same window ({s['window']['from']} to {b['covers_to']}), the backtest of the {label} "
                   f"won {b['win_rate']:.0%} of {b['trades']} trades; live won {a.get('win_rate', 0):.0%} of {a.get('trades')}.")
    ex = s["by_tag"].get("exit", {})
    if ex:
        top = max(ex.items(), key=lambda kv: kv[1]["trades"])
        out.append(f"Most exits were {top[0].replace('_', ' ')}: {top[1]['trades']} trades, {top[1]['win_rate']:.0%} won, "
                   f"{top[1]['total_pnl_usd']:+,.0f} USD.")
    q = s["by_tag"].get("quick_fail", {}).get("True")
    if q and q["trades"]:
        out.append(f"{q['trades']} trades were closed within {QUICK_DAYS} days ({q['win_rate']:.0%} won, {q['total_pnl_usd']:+,.0f} USD).")
    e = s["by_tag"].get("extended", {}).get("True")
    n = s["by_tag"].get("extended", {}).get("False")
    if e and n and e["trades"] and n["trades"]:
        out.append(f"Bought 15%+ above the 50-day average: {e['win_rate']:.0%} won (avg {e['avg_pnl_pct']:+.1f}%) "
                   f"vs {n['win_rate']:.0%} (avg {n['avg_pnl_pct']:+.1f}%) otherwise.")
    m = s.get("matched")
    if m:
        out.append(f"On the {m['trades']} trades the backtest also took, live averaged {m['live_avg_pnl_pct']:+.1f}% "
                   f"vs the backtest's {m['backtest_avg_pnl_pct']:+.1f}%.")
    return out


def load_backtests() -> Dict[str, List[Dict]]:
    out = {}
    for label, path in BACKTESTS.items():
        if path.exists():
            with open(path) as f:
                out[label] = list(csv.DictReader(f))
    return out


def run(history_dir: Optional[str] = None, out_file: Path = OUT_FILE) -> Dict:
    trades = contracts.read(DOCS_DATA / "trades.json", default=[])
    earnings = {s: v.get("dates", []) for s, v in
                (contracts.read(DOCS_DATA / "big_movers_cache.json", default={}).get("earnings") or {}).items()}
    history = None
    if history_dir and Path(history_dir).exists():
        import pit
        history = pit.Store(history_dir)
    backtests = load_backtests()
    first = next(iter(backtests.values()), [])
    tagged = tag(trades, first, earnings, history)
    summary = summarize(tagged, backtests)
    report = {"generated_at": datetime.now().isoformat(timespec="seconds"),
              "findings": findings(summary), "summary": summary, "trades": tagged}
    contracts.write(out_file, report)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Why each closed trade won or lost, vs the backtest")
    ap.add_argument("--history", help="checkout of the history branch (pit.py)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    report = run(args.history)
    for f in report["findings"]:
        print("-", f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
