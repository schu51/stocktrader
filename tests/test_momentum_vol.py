"""Round 7: buys scaled by the momentum factor's own volatility (backtest.momentum_factor_returns)."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import backtest as bt


def _trending(n=200, names=40, seed=0):
    """Names with persistent drifts: past winners keep winning, so WML is positive."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    drift = np.linspace(-0.002, 0.002, names)
    r = drift + rng.normal(0, 0.005, (n, names))
    return pd.DataFrame(100 * np.cumprod(1 + r, axis=0), index=idx, columns=[f"S{k}" for k in range(names)])


def test_factor_is_nan_until_the_lookback_is_filled():
    f = bt.momentum_factor_returns(_trending(), lookback=60)
    assert f.iloc[:61].isna().all()
    assert f.iloc[61:].notna().all()


def test_persistent_winners_give_a_positive_wml_and_winners_beat_wml_minus_losers():
    c = _trending()
    wml = bt.momentum_factor_returns(c, lookback=60)
    win = bt.momentum_factor_returns(c, lookback=60, kind="winners")
    assert wml.dropna().mean() > 0
    assert win.dropna().mean() > wml.dropna().mean() - 0.01     # winners leg alone is defined and sensible


def test_no_look_ahead_future_prices_do_not_change_past_factor_values():
    c = _trending()
    f1 = bt.momentum_factor_returns(c, lookback=60)
    shocked = c.copy()
    shocked.iloc[150:] *= np.linspace(0.5, 2.0, c.shape[1])       # rewrite the future
    f2 = bt.momentum_factor_returns(shocked, lookback=60)
    pd.testing.assert_series_equal(f1.iloc[:150], f2.iloc[:150])


def test_members_mask_limits_who_is_ranked():
    c = _trending()
    members = pd.DataFrame(True, index=c.index, columns=c.columns)
    members.iloc[:, :30] = False                                  # only 10 names left: below min_names
    f = bt.momentum_factor_returns(c, lookback=60, members=members)
    assert f.isna().all()


def test_bad_prints_are_clipped():
    c = _trending()
    c.iloc[120:, 0] *= 50                                         # an unadjusted split-like jump
    f = bt.momentum_factor_returns(c, lookback=60)
    assert f.abs().max() <= 1.5


def test_unknown_kind_is_refused():
    with pytest.raises(ValueError):
        bt.momentum_factor_returns(_trending(), kind="nope")


def test_vol_scaled_size_only_scales_down_and_has_a_floor():
    assert bt.vol_scaled_size(0.10, 0.20) == 1.0
    assert bt.vol_scaled_size(0.40, 0.20) == pytest.approx(0.5)
    assert bt.vol_scaled_size(4.00, 0.20) == bt.MOM_VOL_FLOOR
    assert bt.vol_scaled_size(float("nan"), 0.20) == 1.0           # unknown never blocks
    assert bt.vol_scaled_size(0.40, float("nan")) == 1.0


def _prices(n=520, syms=30, seed=5, wild_from=None):
    """Random walks with a cross-section; from `wild_from` on, returns get 4x as volatile."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n)
    cols = [f"S{k}" for k in range(syms)] + ["SPY"]
    scale = np.ones((n, 1))
    if wild_from is not None:
        scale[wild_from:] = 4.0
    r = rng.normal(0.0008, 0.012, (n, len(cols))) * scale
    c = pd.DataFrame(50 * np.cumprod(1 + r, axis=0), index=idx, columns=cols)
    return {"Close": c, "Open": c * 1.001, "High": c * 1.01, "Low": c * 0.99,
            "Volume": pd.DataFrame(2e6, index=idx, columns=cols)}


def test_scale_is_one_in_calm_times_and_cut_when_momentum_turns_wild():
    prices = _prices(wild_from=470)
    uni = {s: "technology" for s in prices["Close"].columns if s != "SPY"}
    b = bt.Backtest(uni, prices, str(prices["Close"].index[300].date()), mom_vol_target=1.0)
    assert b._mom_vol_scale(460) == pytest.approx(1.0, abs=0.15)  # usual volatility: about full size
    assert b._mom_vol_scale(519) < 0.6                            # four times as volatile: cut hard
    assert b._mom_vol_scale(10) == 1.0                            # no history yet: unknown, never blocks


def test_absolute_target_uses_the_number_as_annualised_volatility():
    prices = _prices()
    uni = {s: "technology" for s in prices["Close"].columns if s != "SPY"}
    lo = bt.Backtest(uni, prices, str(prices["Close"].index[300].date()), mom_vol_target=0.01,
                     mom_vol_relative=False)
    hi = bt.Backtest(uni, prices, str(prices["Close"].index[300].date()), mom_vol_target=5.0,
                     mom_vol_relative=False)
    assert lo._mom_vol_scale(500) == bt.MOM_VOL_FLOOR
    assert hi._mom_vol_scale(500) == 1.0


@pytest.mark.parametrize("args", [
    {"mom_vol_target": 1.0}, {"mom_vol_target": 1.0, "mom_vol_kind": "winners"},
    {"mom_vol_target": 1.0, "mom_vol_window": 42}, {"mom_vol_target": 1.0, "mom_vol_cap": True},
    {"mom_vol_target": 0.2, "mom_vol_relative": False}])
def test_every_round7_setting_replays(args):
    prices = _prices(wild_from=470)
    uni = {s: "technology" for s in prices["Close"].columns if s != "SPY"}
    b = bt.Backtest(uni, prices, str(prices["Close"].index[300].date()), **args)
    b.run(progress_every=0)
    assert len(b.equity) > 0
    out = b.results()
    assert out["variant"]["mom_vol_target"] == args["mom_vol_target"]
    assert out["average_mom_vol_scale"] is None or 0 < out["average_mom_vol_scale"] <= 1


def test_off_by_default_and_bad_settings_are_refused():
    prices = _prices(n=80, syms=3)
    b = bt.Backtest({"S0": "x"}, prices, "2023-03-01")
    assert b._mom_vol_scale(50) == 1.0 and b.mom_vol_target == 0.0
    with pytest.raises(ValueError):
        bt.Backtest({"S0": "x"}, prices, "2023-03-01", mom_vol_kind="nope")
    with pytest.raises(ValueError):
        bt.Backtest({"S0": "x"}, prices, "2023-03-01", mom_vol_window=5)


def test_round7_settings_are_lab_and_tournament_knobs():
    import daily_eval as de
    import picker_lab as lab
    for k in ("mom_vol_target", "mom_vol_relative", "mom_vol_kind", "mom_vol_window", "mom_vol_cap"):
        assert k in lab.rule_knobs()
        assert k in de.ALLOWED_KNOBS
    assert de.check_challenger({"mom_vol_target": 1.0}) is None
