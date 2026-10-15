"""The large-cap bot's rules must never act on a holding that belongs to the diamond sleeve."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
import sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from tests.test_stop_placement import _Broker, _stop, make_position

ET = ZoneInfo("America/New_York")

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
    broker = SimpleNamespace(get_positions=lambda: [_long("STAA", 96, 30.0, 25.0)], last_positions_ok=True,
                             get_orders=lambda **kw: [], last_orders_ok=True)
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


# ── when the trade log cannot be read, nothing may guess ─────────────────────

def test_daily_run_does_not_count_its_positions_verified_when_the_sleeve_is_unknown(monkeypatch, tmp_path):
    # An unreadable trade log read as "the sleeve holds nothing" would let
    # rotation and the stale exit sell the sleeve's holdings as their own.
    import exit_logic
    from run_daily_analysis import DailyRunner
    (tmp_path / "trades.json").write_text("{not json")
    monkeypatch.setattr(exit_logic, "TRADES_PATH", tmp_path / "trades.json")
    broker = _Broker([_long("STAA", 96, 30.0, 31.25)])
    broker.get_account = lambda: {"portfolio_value": "100000", "cash": "40000", "buying_power": "80000"}
    broker.last_positions_ok = True
    stub = SimpleNamespace(broker=broker, portfolio=SimpleNamespace(total_value=100000.0, cash=0.0, buying_power=0.0))
    DailyRunner._build_portfolio_state(stub)
    assert stub._sleeve_known is False
    assert DailyRunner._positions_verified(stub) is False          # gates rotation, the stale exit, reconciliation and buys
    (tmp_path / "trades.json").write_text(json.dumps([SLEEVE_TRADE]))
    DailyRunner._build_portfolio_state(stub)
    assert stub._sleeve_known is True and DailyRunner._positions_verified(stub) is True


def test_no_stop_is_placed_on_a_guess_when_the_trade_log_is_unreadable(monkeypatch, tmp_path):
    # Without the log a sleeve holding would get the large-cap bot's tight stop
    # and be sold on an ordinary dip. Nothing is placed; the run reports why.
    import stop_placement
    bad = tmp_path / "trades.json"; bad.write_text("{not json")
    monkeypatch.setattr(stop_placement, "ROOT", tmp_path)
    (tmp_path / "docs" / "data").mkdir(parents=True)
    (tmp_path / "docs" / "data" / "trades.json").write_text("{not json")
    assert stop_placement.load_trades() is None
    broker = _Broker([_long("STAA", 96, 30.0, 29.0)])
    out = stop_placement.place_missing_stops(broker)
    assert broker.placed == [] and "trade log" in out["error"]
    (tmp_path / "docs" / "data" / "trades.json").unlink()
    assert stop_placement.load_trades() == []                       # no log at all is not "unreadable"


# ── ownership that survives a lost or late trade-log write ───────────────────

def test_a_position_bought_by_the_sleeve_is_known_from_the_broker_even_without_a_log_entry():
    # The log can be behind (another job's checkout) or lose an entry in a git
    # merge. Every sleeve buy also carries a client order id the broker keeps.
    from exit_logic import SLEEVE_ORDER_PREFIX, broker_sleeve_symbols
    class B:
        last_orders_ok = True
        def get_orders(self, status=None, limit=50, symbols=None):
            return [
                {"symbol": "STAA", "side": "buy", "status": "filled", "client_order_id": SLEEVE_ORDER_PREFIX + "STAA-1",
                 "filled_at": "2026-10-26T14:31:00Z"},
                {"symbol": "AME", "side": "buy", "status": "filled", "client_order_id": "abc", "filled_at": "2026-10-26T14:01:00Z"},
                # sold by the sleeve and later bought by the large-cap run: the latest buy decides
                {"symbol": "KLIC", "side": "buy", "status": "filled", "client_order_id": SLEEVE_ORDER_PREFIX + "KLIC-1",
                 "filled_at": "2026-09-01T14:31:00Z"},
                {"symbol": "KLIC", "side": "buy", "status": "filled", "client_order_id": "x", "filled_at": "2026-10-20T14:01:00Z"},
                {"symbol": "ETON", "side": "buy", "status": "partially_filled", "client_order_id": SLEEVE_ORDER_PREFIX + "ETON-1",
                 "filled_at": "2026-10-26T14:31:00Z"},
            ]
    assert broker_sleeve_symbols(B(), {"STAA", "AME", "KLIC", "ETON"}) == {"STAA", "ETON"}
    down = B(); down.last_orders_ok = False
    assert broker_sleeve_symbols(down, {"STAA"}) is None                 # unknown, not "none"


def test_the_intraday_monitor_leaves_alone_a_sleeve_fill_its_checkout_has_not_seen(monkeypatch, tmp_path):
    from exit_logic import SLEEVE_ORDER_PREFIX
    broker = _Broker([_long("STAA", 96, 30.0, 25.0)])                    # far below its 50-day
    broker.get_orders = lambda status=None, limit=50, symbols=None: (
        [{"symbol": "STAA", "side": "buy", "status": "filled", "client_order_id": SLEEVE_ORDER_PREFIX + "STAA-1",
          "filled_at": "2026-10-26T14:31:00Z"}] if status == "closed" else [])
    mod = _intraday(monkeypatch, tmp_path, broker, {"STAA": 40.0}, [])   # the log has no entry for it at all
    mod.main()
    out = json.loads((tmp_path / "intraday_exit.json").read_text())
    assert out["exits_triggered"] == [] and out["sleeve_positions"] == ["STAA"]
    assert [(o["symbol"], o["stop_price"]) for o in broker.placed] == [("STAA", 22.5)]   # the sleeve's stop, not 8%


def test_the_dispatcher_never_starts_the_sleeve_beside_a_job_that_writes_the_trade_log():
    from dispatcher import run_due
    started = []
    # 10:45 after a missed 10:30: the intraday check is due, the sleeve is a catch-up
    run_due(datetime(2026, 10, 26, 10, 45, 5, tzinfo=ET), "main",
            already_started=lambda wf, since: wf not in ("diamond_sleeve.yml", "intraday_exit.yml"),
            start=lambda wf, ref: started.append(wf) or True)
    assert "diamond_sleeve.yml" not in started and "intraday_exit.yml" in started
    started.clear()
    # 11:00: nothing else that writes the log is due, so the sleeve is caught up now
    run_due(datetime(2026, 10, 26, 11, 0, 5, tzinfo=ET), "main", already_started=lambda wf, since: wf != "diamond_sleeve.yml",
            start=lambda wf, ref: started.append(wf) or True)
    assert "diamond_sleeve.yml" in started


def test_daily_exits_on_a_mixed_portfolio_sell_only_the_large_cap_holding(monkeypatch, tmp_path):
    # Both holdings are far below their 50-day average; only the large-cap one may be sold.
    import exit_logic, pandas as pd, yfinance
    from run_daily_analysis import DailyRunner
    (tmp_path / "trades.json").write_text(json.dumps([SLEEVE_TRADE, PLAIN_TRADE]))
    monkeypatch.setattr(exit_logic, "TRADES_PATH", tmp_path / "trades.json")
    monkeypatch.setattr(exit_logic, "CANCEL_POLL_SECONDS", 0)
    closes = pd.DataFrame({"Close": [300.0] * 230 + [200.0] * 20})
    monkeypatch.setattr(yfinance, "Ticker", lambda sym: SimpleNamespace(history=lambda period="1y": closes))
    sold = []
    broker = SimpleNamespace(
        get_positions=lambda: [_long("STAA", 96, 30.0, 25.0), _long("AME", 16, 254.01, 200.0)], last_positions_ok=True,
        get_orders=lambda **kw: [], last_orders_ok=True, get_order=lambda oid: {"status": "canceled"},
        cancel_order=lambda oid: {"success": True}, place_order=lambda **kw: {"id": "x"},
        close_position=lambda sym, qty=None: sold.append(sym) or {"id": f"sale-{sym}"})
    runner = SimpleNamespace(broker=broker, _log_trade=lambda *a, **k: None)
    out = DailyRunner._evaluate_exits(runner, execute=True)
    assert sold == ["AME"] and out["sleeve"] == ["STAA"]


def test_the_large_cap_buy_loop_drops_sleeve_holdings_before_evaluating_candidates():
    import inspect
    from run_daily_analysis import DailyRunner
    source = inspect.getsource(DailyRunner)
    assert source.index("drop_sleeve_candidates(candidates") < source.index("for candidate in candidates:")
    assert source.count('| getattr(self, "_sleeve", set())') == 2          # rotation and the stale exit
