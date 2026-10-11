"""The large-cap bot's rules must never act on a holding that belongs to the diamond sleeve."""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from tests.test_stop_placement import _Broker, _stop, make_position

SLEEVE_TRADE = {"symbol": "STAA", "status": "OPEN", "entry_date": "2026-10-26", "entry_price": 30.0, "shares": 96,
                "sleeve": "diamond", "stop_arm": "sleeve", "stop_dist": 0.25}
PLAIN_TRADE = {"symbol": "AME", "status": "OPEN", "entry_date": "2026-10-05", "entry_price": 254.01, "shares": 16}


def _long(symbol, qty, cost, price):
    return {**make_position(symbol, qty, cost, price, (price / cost - 1) * 100), "side": "long", "asset_class": "us_equity",
            "market_value": qty * price, "unrealized_pl": qty * (price - cost)}


def test_sleeve_symbols_are_open_trades_carrying_the_tag():
    from exit_logic import sleeve_symbols
    trades = [SLEEVE_TRADE, PLAIN_TRADE, dict(SLEEVE_TRADE, symbol="OLD", status="CLOSED"),
              dict(SLEEVE_TRADE, symbol="NOPE", status="CANCELLED")]
    assert sleeve_symbols(trades) == {"STAA"}
    assert sleeve_symbols([]) == set() and sleeve_symbols(None) == set()


def test_reading_the_sleeve_from_disk_says_unknown_rather_than_empty(tmp_path):
    from exit_logic import read_sleeve_symbols
    good = tmp_path / "trades.json"; good.write_text(json.dumps([SLEEVE_TRADE, PLAIN_TRADE]))
    assert read_sleeve_symbols(good) == {"STAA"}
    bad = tmp_path / "bad.json"; bad.write_text("{not json")
    assert read_sleeve_symbols(bad) is None                      # unreadable: unknown, not "no sleeve"
    assert read_sleeve_symbols(tmp_path / "missing.json") == set()   # no trade log at all: nothing is held by it


def test_without_sleeve_splits_positions():
    from exit_logic import without_sleeve
    a, b = _long("STAA", 96, 30.0, 25.0), _long("AME", 16, 254.01, 250.0)
    assert without_sleeve([a, b], {"STAA"}) == ([b], [a])
    assert without_sleeve([a, b], set()) == ([a, b], [])


def test_a_sleeve_position_gets_its_wide_stop_and_it_never_trails():
    from stop_placement import calculate_stop_for_position
    stop, tier = calculate_stop_for_position(_long("STAA", 96, 30.0, 29.0), SLEEVE_TRADE)
    assert stop == 22.5 and "sleeve" in tier
    stop, _ = calculate_stop_for_position(_long("STAA", 96, 30.0, 60.0), SLEEVE_TRADE)     # doubled
    assert stop == 22.5                                           # not break-even, not a trail: thesis exits only
    stop, _ = calculate_stop_for_position(_long("STAA", 96, 30.0, 29.0), dict(SLEEVE_TRADE, stop_dist="junk"))
    assert stop == 22.5                                           # a damaged record still gets the sleeve's stop
    stop, _ = calculate_stop_for_position(_long("AME", 16, 254.01, 250.0), PLAIN_TRADE)
    assert stop == round(254.01 * 0.92, 2)                        # large-cap positions are unchanged


def _intraday(monkeypatch, tmp_path, broker, sma, trades):
    import alpaca_broker, exit_logic, intraday_exit
    broker.market_clock = lambda: True
    (tmp_path / "trades.json").write_text(trades if isinstance(trades, str) else json.dumps(trades))
    monkeypatch.setattr(alpaca_broker, "AlpacaBroker", lambda: broker)
    monkeypatch.setattr(exit_logic, "get_sma50_map", lambda symbols: sma)
    monkeypatch.setattr(exit_logic, "TRADES_PATH", tmp_path / "trades.json")
    monkeypatch.setattr(intraday_exit, "DOCS_DATA", tmp_path)
    monkeypatch.setattr(intraday_exit, "TRADES_FILE", tmp_path / "trades.json")
    monkeypatch.setattr("stop_placement.load_trades", lambda: json.loads((tmp_path / "trades.json").read_text()))
    return intraday_exit


def test_intraday_check_leaves_a_sleeve_position_below_its_50_day_alone(monkeypatch, tmp_path):
    # 25.0 is far below the 50-day (40) and down 17%: the large-cap rules would
    # sell it. The fake broker has no way to sell, so any attempt fails the test.
    broker = _Broker([_long("STAA", 96, 30.0, 25.0)])
    mod = _intraday(monkeypatch, tmp_path, broker, {"STAA": 40.0}, [SLEEVE_TRADE])
    mod.main()
    out = json.loads((tmp_path / "intraday_exit.json").read_text())
    assert out["exits_triggered"] == [] and out["sleeve_positions"] == ["STAA"]
    assert [(o["symbol"], o["qty"], o["stop_price"]) for o in broker.placed] == [("STAA", 96, 22.5)]   # its own stop


def test_intraday_check_does_not_trail_a_winning_sleeve_position(monkeypatch, tmp_path):
    broker = _Broker([_long("STAA", 96, 30.0, 60.0)], orders=[_stop("STAA", 96, 22.5, "s1")])
    mod = _intraday(monkeypatch, tmp_path, broker, {"STAA": 40.0}, [SLEEVE_TRADE])
    mod.main()
    assert broker.placed == [] and broker.cancelled == []         # +100%: the tiers would trail it; the sleeve does not


def test_intraday_check_refuses_to_run_exits_when_it_cannot_tell_what_the_sleeve_holds(monkeypatch, tmp_path):
    broker = _Broker([_long("STAA", 96, 30.0, 25.0)], orders=[_stop("STAA", 96, 22.5, "s1")])
    mod = _intraday(monkeypatch, tmp_path, broker, {"STAA": 40.0}, "{not json")
    with pytest.raises(SystemExit):
        mod.main()
    out = json.loads((tmp_path / "intraday_exit.json").read_text())
    assert out["exits_triggered"] == [] and "sleeve" in json.dumps(out["stop_failures"])
    assert broker.cancelled == [] and broker.placed == []


def test_daily_exit_check_skips_sleeve_positions(monkeypatch, tmp_path):
    import exit_logic
    from run_daily_analysis import DailyRunner
    (tmp_path / "trades.json").write_text(json.dumps([SLEEVE_TRADE]))
    monkeypatch.setattr(exit_logic, "TRADES_PATH", tmp_path / "trades.json")
    broker = SimpleNamespace(get_positions=lambda: [_long("STAA", 96, 30.0, 25.0)], last_positions_ok=True)
    out = DailyRunner._evaluate_exits(SimpleNamespace(broker=broker), execute=True)
    assert out["sleeve"] == ["STAA"] and out["positions_checked"] == 0 and out["exits_triggered"] == []
    (tmp_path / "trades.json").write_text("{not json")
    out = DailyRunner._evaluate_exits(SimpleNamespace(broker=broker), execute=True)
    assert "sleeve" in out["error"] and out["exits_triggered"] == []


def test_daily_run_keeps_sleeve_holdings_out_of_its_caps_and_its_cash(monkeypatch, tmp_path):
    import exit_logic
    from run_daily_analysis import DailyRunner
    (tmp_path / "trades.json").write_text(json.dumps([SLEEVE_TRADE, PLAIN_TRADE]))
    monkeypatch.setattr(exit_logic, "TRADES_PATH", tmp_path / "trades.json")
    broker = _Broker([_long("STAA", 96, 30.0, 31.25), _long("AME", 16, 254.01, 250.0)])     # sleeve holds $3,000
    broker.get_account = lambda: {"portfolio_value": "100000", "cash": "40000", "buying_power": "80000", "long_market_value": "7000"}
    stub = SimpleNamespace(broker=broker, portfolio=SimpleNamespace(total_value=100000.0, cash=0.0, buying_power=0.0))
    state = DailyRunner._build_portfolio_state(stub)
    assert set(state.positions) == {"STAA", "AME"}                # still seen, so the trade log reconciles
    assert state.num_positions == 1 and stub._sleeve == {"STAA"}
    # The sleeve's budget is 20% = $20,000, of which $3,000 is invested: $17,000 of the cash is its own
    assert state.cash == 23000.0 and state.available_cash == 23000.0 * 0.95
    stub.portfolio = state
    assert "STAA" not in json.dumps(DailyRunner._get_sector_allocations(stub))


def test_the_large_cap_bot_never_buys_what_the_sleeve_holds():
    from exit_logic import drop_sleeve_candidates
    kept, dropped = drop_sleeve_candidates([{"symbol": "STAA"}, {"symbol": "AME"}], {"STAA"})
    assert kept == [{"symbol": "AME"}] and dropped == ["STAA"]


def test_sleeve_trades_stay_out_of_the_large_cap_learning():
    from learning_agent import closed_instrumented_trades
    done = {"status": "CLOSED", "weight_version": 3, "pnl_pct": 5.0, "rs_rank": 90, "thesis_score": 50}
    assert closed_instrumented_trades([done, dict(done, sleeve="diamond")]) == [done]
