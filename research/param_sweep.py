"""
Parameter sweep on the survivorship-free whole-market universe (temporary research script).
Each variant changes one or a few settings from the live rules; scored on both halves.
"""
import json, logging, sys
from datetime import date, timedelta
import numpy as np, pandas as pd
sys.path.insert(0, ".")
logging.basicConfig(level=logging.INFO)
import backtest as bt
import exit_logic
from daily_eval import live_rules
from run_daily_analysis import DailyRunner

today = date.today()
start = (pd.Timestamp(today) - pd.DateOffset(years=3)).date().isoformat()
end = (today + timedelta(days=1)).isoformat()
universe, prices, members = bt.load_backtest_data(start, end, "market")
earnings = bt.load_earnings(list(universe))
LIVE = live_rules()
print("LIVE", json.dumps(LIVE), flush=True)
_orig_bb = DailyRunner._check_bb_at_entry

V = {  # name: (backtest kwargs override, rsi block, bb gate on, breakeven_at_pct)
    "live":            ({}, 78, True, 5.0),
    "spy_filter_off":  ({"min_spy_above_50ma": 0.0}, 78, True, 5.0),
    "spy_above_50ma":  ({"min_spy_above_50ma": 0.0001}, 78, True, 5.0),
    "rsi_85":          ({}, 85, True, 5.0),
    "rsi_off":         ({}, 101, True, 5.0),
    "bb_gate_off":     ({}, 78, False, 5.0),
    "room_gate_off":   ({"min_above_50ma": 0.0}, 78, True, 5.0),
    "breakeven_10":    ({}, 78, True, 10.0),
    "breakeven_15":    ({}, 78, True, 15.0),
    "breakeven_25":    ({}, 78, True, 24.99),
    "atr_stop_off":    ({"atr_stop": 0.0}, 78, True, 5.0),
    "atr_stop_3.5":    ({"atr_stop": 3.5}, 78, True, 5.0),
    "entry_gates_off": ({"min_above_50ma": 0.0}, 101, False, 5.0),
    "gates_off_be15":  ({"min_above_50ma": 0.0}, 101, False, 15.0),
    "gates_off_be15_spy0": ({"min_above_50ma": 0.0, "min_spy_above_50ma": 0.0001}, 101, False, 15.0),
    "gates_off_be15_trail": ({"min_above_50ma": 0.0, "trail_at": 30.0, "trail_pct": 0.15}, 101, False, 15.0),
}
only = sys.argv[1:] or list(V)

def half_stats(equity: pd.Series, trades: pd.DataFrame):
    mid = equity.index[len(equity) // 2]
    out = {}
    for name, eq, tr in (("h1", equity.loc[:mid], trades[trades.exit_date <= str(mid.date())] if len(trades) else trades),
                         ("h2", equity.loc[mid:], trades[trades.exit_date > str(mid.date())] if len(trades) else trades)):
        gains, losses = tr.pnl_usd[tr.pnl_usd > 0].sum(), -tr.pnl_usd[tr.pnl_usd < 0].sum()
        out[name] = {"cagr": round(bt.cagr(eq) * 100, 1), "pf": round(gains / losses, 2) if losses else None,
                     "dd": round(bt.max_drawdown(eq) * 100, 1)}
    return out

for name in only:
    kw, rsi, bb_on, be = V[name]
    exit_logic.BREAKEVEN_AT_PCT = be
    bt.BREAKEVEN_AT_PCT = be
    DailyRunner._check_bb_at_entry = _orig_bb if bb_on else (lambda self, bars: (False, ""))
    b = bt.Backtest(universe, prices, start, None, earnings=earnings, members=members, **{**LIVE, **kw})
    b.engine.config.entry_rules.rsi_overbought_block = rsi
    b.run(progress_every=0)
    r = b.results(); t = r["closed_trades"]; s = r["strategy"]
    tr = pd.DataFrame(b.trades)
    eq = pd.Series(b.equity)
    row = {"cagr": round(s["cagr"] * 100, 1), "dd": round(s["max_drawdown"] * 100, 1),
           "pf": round(t.get("profit_factor") or 0, 2), "win": round((t.get("win_rate") or 0) * 100),
           "trades": t.get("trades"), "avg_win": round(t.get("avg_win_pct") or 0, 1),
           "avg_loss": round(t.get("avg_loss_pct") or 0, 1), "hold": t.get("median_hold_days"),
           "invested": round(r["average_invested_share"] * 100),
           "spy": round(r["spy_buy_and_hold"]["cagr"] * 100, 1), **half_stats(eq, tr),
           "gates": {k: r["funnel"].get(k) for k in ("gate_rsi", "gate_bb", "gate_50ma_room", "days_market_weak")}}
    print("RESULT", name, json.dumps(row), flush=True)
