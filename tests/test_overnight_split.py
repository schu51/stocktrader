import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import pytest


def _bt(trades=(), positions=None):
    dates = pd.bdate_range("2026-01-05", periods=5)
    opens = pd.DataFrame({"AAA": [100, 102, 99, 104, 106]}, index=dates, dtype=float)
    closes = pd.DataFrame({"AAA": [101, 100, 103, 105, 107]}, index=dates, dtype=float)
    return SimpleNamespace(dates=dates, open=opens, close=closes, trades=list(trades), positions=positions or {})


TRADE = {"symbol": "AAA", "shares": 10, "entry_date": "2026-01-05", "entry_price": 100.0,
         "exit_date": "2026-01-08", "exit_price": 104.5}


def test_a_holding_splits_into_nights_close_to_open_and_days_open_to_close():
    from overnight_split import session_legs
    legs = session_legs(_bt([TRADE]))
    nights = legs[legs["leg"] == "night"]
    days = legs[legs["leg"] == "day"]
    # nights end at the opens of Jan 6, 7, 8: 101->102, 100->99, 103->104
    assert list(nights["pnl"]) == [10.0, -10.0, 10.0]
    # entry day from the fill, middle days open->close, exit day open->exit fill
    assert list(days["pnl"]) == [10.0, -20.0, 40.0, 5.0]
    # the legs add up to the trade's whole move
    assert legs["pnl"].sum() == pytest.approx(10 * (104.5 - 100.0))


def test_a_position_still_open_runs_to_the_last_session():
    from overnight_split import session_legs
    legs = session_legs(_bt(positions={"AAA": {"shares": 10, "avg_cost": 101.0, "entry_date": date(2026, 1, 8)}}))
    assert list(legs["leg"]) == ["day", "night", "day"]
    assert legs["pnl"].sum() == pytest.approx(10 * (107 - 101.0))


def test_nights_on_either_side_of_a_report_are_flagged():
    from overnight_split import session_legs
    legs = session_legs(_bt([TRADE]), earnings={"AAA": ["2026-01-07"]})
    nights = legs[legs["leg"] == "night"]
    assert list(nights["near_report"]) == [False, True, True]   # 6th->7th and 7th->8th could carry the gap


def test_overnight_costs_are_charged_every_night_and_earnings_nights_can_be_dropped():
    from overnight_split import book_returns, describe, session_legs, split_report
    bt = _bt([TRADE])
    r = split_report(bt, earnings={"AAA": ["2026-01-07"]}, costs_bps=(0.0, 5.0))
    assert r["night"]["sessions"] == 3 and r["night_ex_earnings"]["sessions"] == 1
    free, paid = r["overnight_only"]["0bps_per_side"]["all_nights"], r["overnight_only"]["5bps_per_side"]["all_nights"]
    assert paid["mean_bps"] == pytest.approx(free["mean_bps"] - 10.0)
    night = book_returns(session_legs(bt), "night")
    assert describe(night)["total_return"] == pytest.approx((1 + 1/101) * (1 - 1/100) * (1 + 1/103) - 1, abs=1e-4)
    assert r["pnl_usd"] == {"night": 10.0, "night_near_report": 0.0, "day": 35.0}
