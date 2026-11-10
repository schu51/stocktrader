import sys
from datetime import date, datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

POSITIONS = [{"symbol": "AAA", "qty": "10", "asset_class": "us_equity"},
             {"symbol": "SMOL", "qty": "50", "asset_class": "us_equity"},
             {"symbol": "OPT", "qty": "1", "asset_class": "us_option"}]
TRADES = [{"symbol": "SMOL", "status": "OPEN", "sleeve": "diamond"}, {"symbol": "AAA", "status": "OPEN"}]
SCREEN = {"candidates": [{"symbol": "AAA"}, {"symbol": "BRK-B"}, {"symbol": "CCC"}]}


def test_tonight_is_the_book_the_sleeve_and_top_unheld_candidates():
    from overnight_shadow import select
    assert select(POSITIONS, TRADES, SCREEN, n=2) == [
        {"symbol": "AAA", "group": "book"}, {"symbol": "SMOL", "group": "sleeve"},
        {"symbol": "BRK.B", "group": "candidates"}, {"symbol": "CCC", "group": "candidates"}]


def _bars(table):
    def bars(symbols, start, end):
        return {s: {d: v for d, v in table.get(s, {}).items() if start.isoformat() <= d <= end.isoformat()}
                for s in symbols if s in table}
    return bars


NO_SPREAD = lambda syms, a, b: {s: 4.0 for s in syms}
NO_REPORT = lambda s, d, n: s == "CCC"


def _evening(day, state, table):
    from overnight_shadow import run
    now = datetime(*map(int, day.split("-")), 21, 30, tzinfo=timezone.utc)      # 16:30 ET in November
    return run(now, POSITIONS, TRADES, SCREEN, state, bars=_bars(table), spreads=NO_SPREAD, reports=NO_REPORT)


TABLE = {"AAA": {"2026-11-10": {"o": 99, "c": 100}, "2026-11-11": {"o": 101, "c": 102}},
         "SMOL": {"2026-11-10": {"o": 20, "c": 20}, "2026-11-11": {"o": 19, "c": 19.5}},
         "BRK.B": {"2026-11-10": {"o": 500, "c": 500}, "2026-11-11": {"o": 500, "c": 501}},
         "CCC": {"2026-11-10": {"o": 10, "c": 10}, "2026-11-11": {"o": 11, "c": 11}}}


def test_an_evening_records_tonight_and_the_next_evening_settles_it_at_the_open():
    first = _evening("2026-11-10", {}, TABLE)
    assert first["pending"]["date"] == "2026-11-10" and len(first["pending"]["holdings"]) == 4
    assert first["nights"] == []
    second = _evening("2026-11-11", first, TABLE)
    got = {n["symbol"]: n["ret_bps"] for n in second["nights"]}
    assert got == {"AAA": 100.0, "SMOL": -500.0, "BRK.B": 0.0, "CCC": 1000.0}
    assert second["pending"]["date"] == "2026-11-11"                     # and tonight is recorded
    s = second["summary"]
    assert s["book"]["gross"]["mean_bps"] == 100.0 and s["sleeve"]["gross"]["mean_bps"] == -500.0
    assert s["candidates"]["gross"]["mean_bps"] == 500.0                 # equal weight of 0 and +1000
    assert s["candidates"]["gross_ex_earnings"]["mean_bps"] == 0.0       # CCC reported: dropped
    assert s["book"]["net"]["5bps_per_side"]["mean_bps"] == 90.0         # a night pays both sides
    assert s["book"]["median_half_spread_bps"] == 4.0
    assert s["book"]["net_at_measured_half_spread"]["mean_bps"] == 92.0


def test_a_rerun_the_same_evening_or_a_holiday_records_nothing_new():
    first = _evening("2026-11-10", {}, TABLE)
    again = _evening("2026-11-10", first, TABLE)
    assert again["pending"] == first["pending"] and again["nights"] == []
    holiday = _evening("2026-11-12", {}, TABLE)                           # no bars that day
    assert holiday["pending"] is None and "market closed" in holiday["notes"][0]


def test_a_missing_open_drops_that_stock_instead_of_guessing():
    from overnight_shadow import settle
    pending = {"date": "2026-11-10", "holdings": [{"symbol": "AAA", "group": "book"}, {"symbol": "ZZZ", "group": "book"}]}
    nights = settle(pending, {"AAA": 100.0, "ZZZ": 5.0}, {"AAA": 101.0}, {}, "2026-11-11")
    assert [n["symbol"] for n in nights] == ["AAA"]
