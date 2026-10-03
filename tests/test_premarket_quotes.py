import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

TODAY = date(2026, 10, 5)   # a Monday


def _bars(rows):
    """rows: {'2026-10-05 08:55': {'AMD': 640.0, ...}} in New York time."""
    idx = pd.DatetimeIndex(list(rows), tz="America/New_York").tz_convert("UTC")
    return pd.DataFrame(list(rows.values()), index=idx)


def test_uses_last_trade_before_the_open():
    from premarket_quotes import latest_premarket_prices
    bars = _bars({
        "2026-10-05 04:00": {"AMD": 630.0},
        "2026-10-05 08:55": {"AMD": 641.5},
        "2026-10-05 09:30": {"AMD": 650.0},   # regular session — not premarket
    })
    prices, as_of = latest_premarket_prices(bars, TODAY)
    assert prices == {"AMD": 641.5}
    assert as_of.strftime("%H:%M") == "08:55"


def test_ignores_previous_sessions():
    from premarket_quotes import latest_premarket_prices
    bars = _bars({
        "2026-10-02 08:00": {"AMD": 600.0},   # Friday premarket
        "2026-10-02 19:55": {"AMD": 610.0},   # Friday after-hours
    })
    prices, as_of = latest_premarket_prices(bars, TODAY)
    assert prices == {}
    assert as_of is None


def test_ticker_without_premarket_trades_is_omitted():
    from premarket_quotes import latest_premarket_prices
    bars = _bars({
        "2026-10-05 07:00": {"AMD": 640.0, "FF": float("nan")},
        "2026-10-05 08:00": {"AMD": 642.0, "FF": float("nan")},
    })
    prices, _ = latest_premarket_prices(bars, TODAY)
    assert prices == {"AMD": 642.0}


def test_carries_forward_last_trade_for_thinly_traded_ticker():
    from premarket_quotes import latest_premarket_prices
    bars = _bars({
        "2026-10-05 07:00": {"AMD": 640.0, "FF": 5.40},
        "2026-10-05 08:00": {"AMD": 642.0, "FF": float("nan")},
    })
    prices, _ = latest_premarket_prices(bars, TODAY)
    assert prices == {"AMD": 642.0, "FF": 5.40}


def test_empty_bars():
    from premarket_quotes import latest_premarket_prices
    assert latest_premarket_prices(pd.DataFrame(), TODAY) == ({}, None)
    assert latest_premarket_prices(None, TODAY) == ({}, None)


def test_completed_sessions_drops_todays_partial_bar():
    from premarket_quotes import completed_sessions
    daily = pd.Series([100.0, 101.0, 105.0],
                      index=pd.DatetimeIndex(["2026-10-01", "2026-10-02", "2026-10-05"]))
    assert list(completed_sessions(daily, TODAY)) == [100.0, 101.0]
    assert list(completed_sessions(daily.iloc[:2], TODAY)) == [100.0, 101.0]


def test_gap_reasons_gap_down_and_cross_below_50ma():
    from premarket_quotes import gap_reasons
    pct, reasons = gap_reasons(prev_close=100.0, premarket=95.0, ma50=98.0)
    assert round(pct, 4) == -0.05
    assert any("gap down" in r for r in reasons)
    assert any("crossed BELOW 50MA" in r for r in reasons)


def test_gap_reasons_gap_up_above_50ma():
    from premarket_quotes import gap_reasons
    pct, reasons = gap_reasons(prev_close=100.0, premarket=104.0, ma50=90.0)
    assert round(pct, 4) == 0.04
    assert reasons == ["gap UP 4.0% above 50MA — momentum alert"]


def test_gap_reasons_quiet_open():
    from premarket_quotes import gap_reasons
    assert gap_reasons(prev_close=100.0, premarket=101.0, ma50=90.0)[1] == []
    # gap up but still below the 50MA is not a momentum alert
    assert gap_reasons(prev_close=100.0, premarket=104.0, ma50=110.0)[1] == []
