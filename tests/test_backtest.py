import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd


def _pos(**over):
    p = {"symbol": "AAA", "shares": 10, "avg_cost": 100.0, "stop": 92.0,
         "entry_date": date(2026, 1, 5), "stop_active": True}
    p.update(over)
    return p


def _bar(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


# ── exits ────────────────────────────────────────────────────────────────────

def test_stop_hit_intraday_fills_at_stop_price():
    from backtest import evaluate_exit
    price, reason = evaluate_exit(_pos(), _bar(100, 101, 91, 95), sma50=80.0)
    assert (price, reason) == (92.0, "STOP")


def test_gap_below_stop_fills_at_the_open_not_the_stop():
    from backtest import evaluate_exit
    price, reason = evaluate_exit(_pos(), _bar(88, 90, 85, 89), sma50=80.0)
    assert (price, reason) == (88.0, "STOP")


def test_stop_not_active_on_entry_day():
    # Live: the stop order is only placed the morning after the buy
    from backtest import evaluate_exit
    price, reason = evaluate_exit(_pos(stop_active=False), _bar(100, 101, 91, 99), sma50=80.0)
    assert (price, reason) == (None, "")


def test_close_below_50ma_exits_at_close():
    from backtest import evaluate_exit
    price, reason = evaluate_exit(_pos(), _bar(100, 101, 97, 98), sma50=99.0)
    assert (price, reason) == (98.0, "PRICE_BELOW_50MA")


def test_hard_loss_exits_at_close_when_no_stop_protects():
    from backtest import evaluate_exit
    price, reason = evaluate_exit(_pos(stop_active=False), _bar(100, 100, 80, 84), sma50=50.0)
    assert (price, reason) == (84.0, "HARD_LOSS_STOP")


def test_no_exit_holds():
    from backtest import evaluate_exit
    assert evaluate_exit(_pos(), _bar(100, 105, 99, 104), sma50=90.0) == (None, "")


def test_stop_only_ratchets_up_through_the_tiers():
    from backtest import next_stop
    assert next_stop(_pos(), close=110.0) == 92.0                 # <25% gain: 8% below entry
    assert next_stop(_pos(), close=130.0) == 101.5                # 25%+: break-even + 1.5%
    assert next_stop(_pos(stop=101.5), close=160.0) == 144.0      # 50%+: 10% trail
    assert next_stop(_pos(stop=144.0), close=150.0) == 144.0      # price falls back: stop never lowers


# ── regime ───────────────────────────────────────────────────────────────────

def test_regime_threshold_matches_screener_rules():
    from backtest import regime_threshold
    up = np.linspace(100, 200, 260)                     # price > 50MA > 200MA
    assert regime_threshold(up) == ("bull", 70)
    dip = np.concatenate([np.linspace(100, 200, 250), np.full(10, 170.0)])   # above 200MA, below 50MA
    assert regime_threshold(dip) == ("neutral", 80)
    down = np.linspace(200, 100, 260)
    assert regime_threshold(down) == ("bear", 90)


# ── statistics ───────────────────────────────────────────────────────────────

def test_max_drawdown():
    from backtest import max_drawdown
    assert round(max_drawdown(pd.Series([100, 120, 90, 110, 130])), 6) == round(90 / 120 - 1, 6)
    assert max_drawdown(pd.Series([100, 110, 120])) == 0.0


def test_summarize_trades_counts_costs_and_concentration():
    from backtest import summarize_trades
    trades = pd.DataFrame({
        "pnl_usd": [500.0, -100.0, -100.0, -100.0, 50.0],
        "pnl_pct": [50.0, -8.0, -8.0, -8.0, 4.0],
        "hold_days": [60, 5, 7, 3, 20],
    })
    s = summarize_trades(trades)
    assert s["trades"] == 5
    assert s["win_rate"] == 0.4
    assert s["total_pnl_usd"] == 250.0
    assert round(s["profit_factor"], 6) == round(550 / 300, 6)
    assert s["pnl_without_best_trade_usd"] == -250.0     # one winner carries the result
    assert s["avg_win_pct"] == 27.0 and s["avg_loss_pct"] == -8.0


def test_summarize_trades_empty():
    from backtest import summarize_trades
    assert summarize_trades(pd.DataFrame(columns=["pnl_usd", "pnl_pct", "hold_days"]))["trades"] == 0


def test_cagr():
    from backtest import cagr
    idx = pd.bdate_range("2024-01-01", periods=505)       # ~2 years of sessions
    assert round(cagr(pd.Series(np.linspace(100, 121, 505), index=idx)), 2) == 0.10
