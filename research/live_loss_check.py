"""
Live loss check (research, 2026-11-10)
======================================
For every closed live trade in docs/data/trades.json: did an earnings report
fall inside the holding period, was one within the 5-day entry blackout when
the bot bought (so the blackout failed open), and for stop exits, did the
stock gap through the stop at the open?

    python research/live_loss_check.py   ->  research/live_loss_check.json
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    trades = [t for t in json.loads((ROOT / "docs/data/trades.json").read_text()) if t.get("status") == "CLOSED"]
    syms = sorted({t["symbol"] for t in trades})
    earnings = {}
    for s in syms:
        try:
            f = yf.Ticker(s).get_earnings_dates(limit=12)
            earnings[s] = sorted({ts.date() for ts in f.index}) if f is not None else []
        except Exception as e:
            print(f"{s}: earnings failed {e}")
            earnings[s] = []
    start = min(date.fromisoformat(t["entry_date"]) for t in trades) - timedelta(days=5)
    bars = yf.download(syms, start=start.isoformat(), auto_adjust=False, progress=False, threads=True)

    rows = []
    for t in trades:
        s, e0, e1 = t["symbol"], date.fromisoformat(t["entry_date"]), date.fromisoformat(t["exit_date"])
        ed = earnings.get(s, [])
        held_through = [d.isoformat() for d in ed if e0 < d <= e1]
        in_blackout = [d.isoformat() for d in ed if e0 <= d <= e0 + timedelta(days=5)]
        gap = None
        if t["exit_reason"] == "BROKER_STOP" and t.get("stop_loss"):
            try:
                o = float(bars["Open"][s].loc[pd.Timestamp(e1)])
                gap = {"exit_open": round(o, 2), "stop": t["stop_loss"], "gapped_through": o < t["stop_loss"],
                       "fill_vs_stop_pct": round((t["exit_price"] / t["stop_loss"] - 1) * 100, 2)}
            except Exception:
                gap = {"stop": t["stop_loss"], "fill_vs_stop_pct": round((t["exit_price"] / t["stop_loss"] - 1) * 100, 2)}
        rows.append({"symbol": s, "entry": t["entry_date"], "exit": t["exit_date"], "reason": t["exit_reason"],
                     "pnl_pct": t["pnl_pct"], "pnl_usd": t["pnl_usd"], "earnings_in_hold": held_through,
                     "earnings_within_5d_of_entry": in_blackout, "stop_exit": gap})

    df = pd.DataFrame(rows)
    df["earn"] = df["earnings_in_hold"].str.len() > 0
    summary = {
        "closed": len(df),
        "held_through_earnings": int(df["earn"].sum()),
        "pnl_usd_held_through_earnings": float(df.loc[df["earn"], "pnl_usd"].sum()),
        "pnl_usd_no_earnings": float(df.loc[~df["earn"], "pnl_usd"].sum()),
        "win_rate_held_through_earnings": float((df.loc[df["earn"], "pnl_pct"] > 0).mean()) if df["earn"].any() else None,
        "entries_inside_5d_blackout": int((df["earnings_within_5d_of_entry"].str.len() > 0).sum()),
        "stop_exits": int((df["reason"] == "BROKER_STOP").sum()),
        "stop_exits_gapped_through": int(sum(1 for g in df["stop_exit"] if g and g.get("gapped_through"))),
        "stop_exits_with_earnings_in_hold": int(((df["reason"] == "BROKER_STOP") & df["earn"]).sum()),
    }
    out = {"summary": summary, "trades": rows}
    (ROOT / "research/live_loss_check.json").write_text(json.dumps(out, indent=1, default=str))
    print(json.dumps(summary, indent=1))
    for r in sorted(rows, key=lambda r: r["pnl_pct"])[:15]:
        print(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
