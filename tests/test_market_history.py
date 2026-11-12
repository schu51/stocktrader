import numpy as np
import pandas as pd

import market_history as mh
import market_universe as mu


def test_delisted_names_are_kept_unless_their_ticker_was_reused():
    active = [{"symbol": "AAA", "exchange": "NYSE", "name": "Aaa Inc", "tradable": True, "status": "active"},
              {"symbol": "REUSE", "exchange": "NASDAQ", "name": "New Co", "tradable": True, "status": "active"}]
    inactive = [{"symbol": "GONE", "exchange": "NASDAQ", "name": "Gone Corp", "tradable": False, "status": "inactive"},
                {"symbol": "REUSE", "exchange": "NYSE", "name": "Old Co", "tradable": False, "status": "inactive"},
                {"symbol": "GONEW", "exchange": "NASDAQ", "name": "Gone Corp Warrants", "status": "inactive"},
                {"symbol": "OTCD", "exchange": "OTC", "name": "Otc Dead", "status": "inactive"}]
    assert mh.symbols_for_history(active, inactive) == ["AAA", "GONE", "REUSE"]


def test_membership_matches_the_weekly_rules_day_by_day():
    idx = pd.bdate_range("2024-01-01", periods=320)
    rng = np.random.default_rng(3)
    closes = pd.DataFrame({s: 10 + rng.random(len(idx)) * 20 for s in "ABCDE"}, index=idx)
    vols = pd.DataFrame({s: rng.random(len(idx)) * 300_000 for s in "ABCDE"}, index=idx)
    closes.iloc[:100, 0] = np.nan            # A lists late
    closes.iloc[250:, 1] = np.nan            # B delists
    m = mh.membership(closes, vols)
    for day in (idx[210], idx[260], idx[-1]):
        expect = mu.liquid(closes.loc[:day].tail(252), vols.loc[:day].tail(252))
        assert sorted(m.columns[m.loc[day].values]) == expect, day
    assert not m["B"].iloc[260:].any()
    assert not m["A"].iloc[:299].any()       # needs 200 closes first
