"""Edge lab: portfolio mechanics, no lookahead, and the edge statistics (synthetic data)."""
import numpy as np
import pandas as pd
import pytest

import edge_lab as el


def _closes(n=400, seed=0, symbols=("A", "B", "C", "D")):
    idx = pd.bdate_range("2020-01-01", periods=n)
    rng = np.random.default_rng(seed)
    data = {"SPY": 100 * np.cumprod(1 + rng.normal(0.0004, 0.01, n))}
    for k, s in enumerate(symbols):
        data[s] = 50 * np.cumprod(1 + rng.normal(0.0002 * k, 0.02, n))
    return pd.DataFrame(data, index=idx)


def test_month_end_dates_are_last_trading_days():
    idx = pd.bdate_range("2021-01-01", "2021-03-31")
    ends = el.month_end_dates(idx)
    assert [d.strftime("%Y-%m-%d") for d in ends] == ["2021-01-29", "2021-02-26", "2021-03-31"]


def test_simulate_buy_and_hold_matches_price_path_net_of_entry_cost():
    c = _closes()
    rets = el.daily_returns(c)
    start = c.index[10]
    eq = el.simulate(rets, {start: {"SPY": 1.0}}, start, cost_bps=10)
    expected = 100_000 * (1 - 10 / 1e4) * c["SPY"].iloc[-1] / c["SPY"].loc[start]
    assert eq.iloc[-1] == pytest.approx(expected, rel=1e-9)


def test_simulate_costs_reduce_equity_with_turnover():
    c = _closes()
    rets = el.daily_returns(c)
    start = c.index[0]
    flips = {d: ({"A": 1.0} if k % 2 else {"B": 1.0}) for k, d in enumerate(el.month_end_dates(c.index))}
    flips[start] = {"A": 1.0}
    cheap = el.simulate(rets, flips, start, cost_bps=0)
    dear = el.simulate(rets, flips, start, cost_bps=50)
    assert dear.iloc[-1] < cheap.iloc[-1]


def test_delisted_stock_holds_its_last_value():
    c = _closes()
    c.loc[c.index[200]:, "A"] = np.nan
    rets = el.daily_returns(c)
    start = c.index[100]
    eq = el.simulate(rets, {start: {"A": 1.0}}, start, cost_bps=0)
    assert eq.iloc[-1] == pytest.approx(eq.loc[c.index[199]])
    assert not eq.isna().any()


def test_momentum_uses_12_1_window_and_no_future_data():
    c = _closes(n=300)
    i = 280
    s1 = el.momentum_scores(c, i, None)
    c2 = c.copy()
    c2.iloc[i + 1:] = c2.iloc[i + 1:] * 10            # future prices change
    c2.iloc[i - el.MOM_SKIP + 1:i + 1, :] *= 3          # the skipped month changes
    s2 = el.momentum_scores(c2, i, None)
    pd.testing.assert_series_equal(s1, s2)
    assert "SPY" not in s1.index
    assert s1["A"] == pytest.approx(c["A"].iloc[i - 21] / c["A"].iloc[i - 252] - 1)


def test_momentum_respects_membership_and_picks_the_top():
    c = _closes(n=300)
    i = 280
    members = pd.Series({"A": True, "B": True, "C": False, "D": True, "SPY": False})
    s = el.momentum_scores(c, i, members)
    assert set(s.index) == {"A", "B", "D"}
    members_df = pd.DataFrame(True, index=c.index, columns=c.columns)
    t = el.momentum_targets(c, members_df, c.index[260], top_frac=None, top_n=2)
    d = max(t)
    full = el.momentum_scores(c, list(c.index).index(d), members_df.loc[d])
    assert set(t[d]) == set(full.nlargest(2).index)
    assert sum(t[d].values()) == pytest.approx(1.0)


def test_trend_filter_goes_defensive_below_the_200_day():
    idx = pd.bdate_range("2020-01-01", periods=400)
    spy = np.concatenate([np.linspace(100, 200, 300), np.linspace(200, 120, 100)])
    c = pd.DataFrame({"SPY": spy, "IEF": 100.0}, index=idx)
    t = el.trend_targets(c, idx[250], "IEF")
    ends = sorted(t)
    assert t[ends[0]] == {"SPY": 1.0}
    assert t[ends[-1]] == {"IEF": 1.0}
    assert el.trend_targets(c, idx[250], None)[ends[-1]] == {}


def test_newey_west_recovers_alpha_and_beta():
    rng = np.random.default_rng(1)
    x = rng.normal(0.008, 0.04, 240)
    y = 0.005 + 1.3 * x + rng.normal(0, 0.01, 240)
    fit = el.newey_west_ols(y, x)
    assert fit["beta"] == pytest.approx(1.3, abs=0.05)
    assert fit["alpha"] == pytest.approx(0.005, abs=0.002)
    assert fit["alpha_t"] > 3


def test_leveraged_spy_has_beta_not_alpha():
    # Beating SPY by taking more market risk is not an edge
    c = _closes(n=1500, seed=3)
    spy_eq = 100_000 * c["SPY"] / c["SPY"].iloc[0]
    lev = 100_000 * (1 + 1.5 * c["SPY"].pct_change().fillna(0)).cumprod()
    s = el.stats(lev, spy_eq)
    assert s["beta"] == pytest.approx(1.5, abs=0.05)
    assert abs(s["alpha"]) < 0.02
    assert el.verdict(s, s, el.stats(spy_eq, spy_eq))["edge"] is False


def test_verdict_requires_every_check():
    spy = {"max_drawdown": -0.20, "sharpe": 0.8}
    good = {"alpha": 0.06, "alpha_t": 3.5, "positive_years": 0.8, "max_drawdown": -0.22, "sharpe": 1.1}
    stress = {"alpha": 0.04, "alpha_t": 2.5}
    assert el.verdict(good, stress, spy)["edge"] is True
    for key, bad in [("alpha", 0.03), ("alpha_t", 2.5), ("positive_years", 0.5),
                     ("max_drawdown", -0.30), ("sharpe", 0.9)]:
        v = el.verdict({**good, key: bad}, stress, spy)
        assert v["edge"] is False
    assert el.verdict(good, {"alpha": 0.01, "alpha_t": 2.5}, spy)["edge"] is False


def test_evaluate_end_to_end_on_synthetic_market():
    c = _closes(n=1000, seed=5, symbols=tuple("ABCDEFGHIJ"))
    c["IEF"] = 100 * np.cumprod(np.full(len(c), 1.0001))
    members = pd.DataFrame(True, index=c.index, columns=c.columns)
    out = el.evaluate(c, members, c.index[300], defensive="IEF",
                      extra={"champion": 100_000 * c["A"] / c["A"].iloc[0]},
                      extra_stress={"champion": 99_000 * c["A"] / c["A"].iloc[0]})
    assert out["champion"]["at_2x_costs"]["alpha"] is not None
    assert set(out) == {"spy", "ew_universe", "trend_spy", "mom_decile", "mom_top50", "mom_decile_trend",
                        "champion"}
    assert "verdict" not in out["spy"] and out["spy"]["beta"] == pytest.approx(1.0)
    assert "verdict" not in out["ew_universe"]
    for name in ("trend_spy", "mom_decile", "champion"):
        assert set(out[name]["verdict"]["checks"]) == {"alpha", "alpha_t", "consistency", "survives_2x_costs",
                                                       "drawdown", "sharpe"}
    md = el.report(out, {"date": "2026-11-21", "start": "2021-02-01", "end": "2023-11-01",
                         "cost_bps": 10, "defensive": "IEF"})
    assert "| mom_decile |" in md and "benchmark" in md


def test_picking_from_a_rising_universe_is_not_alpha():
    # Random, unrelated stocks with positive drift: any equal-weight basket looks like
    # zero-beta "alpha" against SPY, but nothing beats the equal-weight universe itself.
    n, k = 1500, 400
    idx = pd.bdate_range("2016-01-01", periods=n)
    rng = np.random.default_rng(0)
    c = pd.DataFrame(100 * np.cumprod(1 + rng.normal(0.0006, 0.02, (n, k)), axis=0), index=idx,
                     columns=[f"S{i}" for i in range(k)])
    c["SPY"] = 100 * np.cumprod(1 + rng.normal(0.0004, 0.011, n))
    members = pd.DataFrame(True, index=idx, columns=c.columns)
    out = el.evaluate(c, members, idx[300])
    mom = out["mom_decile"]
    assert mom["capm_alpha"] > 0.03                     # looks great against SPY alone...
    assert mom["verdict"]["edge"] is False             # ...but is not an edge
    assert mom["verdict"]["checks"]["alpha_t"] is False


def test_stats_two_factor_alpha_matches_construction():
    rng = np.random.default_rng(2)
    idx = pd.bdate_range("2015-01-01", periods=2520)
    spy = 100 * np.cumprod(1 + rng.normal(0.0004, 0.01, len(idx)))
    ew = 100 * np.cumprod(1 + rng.normal(0.0004, 0.012, len(idx)))
    spy_eq, ew_eq = pd.Series(spy, idx), pd.Series(ew, idx)
    m_spy, m_ew = el.monthly_returns(spy_eq), el.monthly_returns(ew_eq)
    strat_m = 0.006 + 0.5 * m_spy + 0.6 * m_ew + rng.normal(0, 0.005, len(m_spy))
    # Rebuild a daily curve whose month-ends carry these monthly returns
    ends = el.month_end_dates(idx)
    eq = pd.Series(np.nan, idx)
    eq.loc[ends[0]] = 100.0
    level = 100.0
    for d, r in zip(ends[1:], strat_m.values):
        level *= 1 + r
        eq.loc[d] = level
    eq = eq.ffill().bfill()
    s = el.stats(eq, spy_eq, ew_eq)
    assert s["alpha"] == pytest.approx(0.072, abs=0.015)
    assert s["loadings"]["spy"] == pytest.approx(0.5, abs=0.1)
    assert s["loadings"]["universe"] == pytest.approx(0.6, abs=0.1)
