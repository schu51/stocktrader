import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import backtest as bt
import fundamentals_pit as fp


def test_high_ratio_is_one_at_the_high_and_below_after_a_drop():
    c = np.r_[np.linspace(50, 100, 200), 90.0]
    assert bt.high_ratio(c[:-1]) == pytest.approx(1.0)
    assert bt.high_ratio(c) == pytest.approx(0.9)


def test_smoothness_prefers_a_steady_climb_over_a_jumpy_one():
    steady = 100 * np.cumprod(np.full(260, 1.002))
    rng = np.random.default_rng(0)
    jumpy = 100 * np.cumprod(1 + np.where(rng.random(260) < 0.1, 0.05, -0.003))
    assert bt.smoothness(steady) > 0.9
    assert bt.smoothness(jumpy) < 0
    assert np.isnan(bt.smoothness(steady[:100]))


def test_report_reaction_is_the_three_day_move_over_spy():
    c = np.array([100, 100, 110, 112, 112.0])
    spy = np.array([100, 100, 101, 102, 102.0])
    assert bt.report_reaction(c, spy, 2) == pytest.approx(0.12 - 0.02)
    assert np.isnan(bt.report_reaction(c, spy, 4))          # the next session is not there yet


def _ohlcv(n=80):
    c = np.full(n, 50.0); o = c.copy(); l = c * 0.99; v = np.full(n, 1e6)
    return o, l, c, v


def test_gap_entry_needs_a_gap_heavy_volume_and_the_gap_held():
    o, l, c, v = _ohlcv()
    k = 70
    o[k], c[k:], l[k], v[k] = 54.0, 55.0, 53.0, 3e6
    assert bt.gap_entry_ok(o, l, c, v, k)
    v2 = v.copy(); v2[k] = 1.5e6
    assert not bt.gap_entry_ok(o, l, c, v2, k)              # volume too light
    c2 = c.copy(); c2[-1] = 52.0
    assert not bt.gap_entry_ok(o, l, c2, v, k)              # gave the gap back
    o3 = o.copy(); o3[k] = 51.0
    assert not bt.gap_entry_ok(o3, l, c, v, k)              # no real gap


def test_vcp_needs_a_tight_base_then_a_pivot_break_on_volume():
    base = np.r_[np.linspace(50, 100, 60), 100 + np.sin(np.arange(15)) * 0.4]
    vol = np.r_[np.full(60, 2e6), np.full(15, 8e5)]
    c = np.r_[base, 102.0]; v = np.r_[vol, 2e6]
    assert bt.vcp_ok(c, v)
    assert not bt.vcp_ok(np.r_[base, 100.1], np.r_[vol, 2e6])     # no break above the pivot
    loose = np.r_[np.linspace(50, 100, 60), 100 + np.sin(np.arange(15)) * 15]
    assert not bt.vcp_ok(np.r_[loose, 120.0], v)


def test_margin_change_from_gross_profit():
    rows, y, q = [], 2023, 1
    for k in range(9):
        end = (pd.Timestamp(year=y, month=3 * q, day=1) + pd.offsets.MonthEnd(0)).date()
        gp = 40.0 if k < 8 else 50.0
        rows.append([end.isoformat(), (end + timedelta(days=30)).isoformat(), 1.0, 100.0, gp])
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    f = fp.factor_asof(rows, date(2026, 1, 1))
    assert f["margin_chg"] == pytest.approx(0.10)
    old = [r[:4] for r in rows]                          # rows from a version-1 cache
    assert fp.factor_asof(old, date(2026, 1, 1))["margin_chg"] is None


def _prices(n=320, syms=25, seed=4):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    cols = [f"S{k}" for k in range(syms)] + ["SPY"]
    c = pd.DataFrame({s: 50 * np.cumprod(1 + rng.normal(0.001, 0.012, n)) for s in cols}, index=idx)
    return {"Close": c, "Open": c * 1.001, "High": c * 1.01, "Low": c * 0.99,
            "Volume": pd.DataFrame(2e6, index=idx, columns=cols)}


@pytest.mark.parametrize("args", [
    {"entry_trigger": "vcp"}, {"entry_trigger": "ep"}, {"min_ear": 0.0}, {"min_high_ratio": 0.9},
    {"w_high": 0.3}, {"min_smooth": 0.05}, {"vol_target": 0.10}, {"fill_mode": "prior_close"}])
def test_every_round6_setting_replays(args):
    prices = _prices()
    uni = {s: "technology" for s in prices["Close"].columns if s != "SPY"}
    earnings = {s: [prices["Close"].index[200].date(), prices["Close"].index[300].date()] for s in uni}
    b = bt.Backtest(uni, prices, str(prices["Close"].index[260].date()), earnings=earnings, **args)
    b.run(progress_every=0)
    assert len(b.equity) > 0


def test_bad_trigger_or_fill_mode_is_refused():
    prices = _prices(n=80, syms=3)
    with pytest.raises(ValueError):
        bt.Backtest({"S0": "x"}, prices, "2024-03-01", entry_trigger="nope")
    with pytest.raises(ValueError):
        bt.Backtest({"S0": "x"}, prices, "2024-03-01", fill_mode="nope")


def test_vol_scale_only_scales_down():
    b = bt.Backtest.__new__(bt.Backtest)
    b.vol_target = 0.15
    calm = 100 * np.cumprod(np.full(30, 1.0005))
    wild = 100 * np.cumprod(1 + np.tile([0.03, -0.03], 15))
    assert b._vol_scale(calm) == 1.0
    assert 0.25 <= b._vol_scale(wild) < 1.0
