import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def make_position(symbol, qty, avg_cost, current_price, pnl_pct):
    return {
        "symbol": symbol,
        "qty": qty,
        "avg_entry_price": avg_cost,
        "current_price": current_price,
        "unrealized_plpc": pnl_pct / 100,  # Alpaca returns decimal
    }


def test_build_protected_set():
    from stop_placement import build_protected_set
    orders = [
        {"side": "sell", "type": "stop",  "symbol": "AAPL"},
        {"side": "sell", "type": "limit", "symbol": "MSFT"},  # not a stop — skip
        {"side": "buy",  "type": "stop",  "symbol": "TSLA"},  # buy side — skip
    ]
    protected = build_protected_set(orders)
    assert "AAPL" in protected
    assert "MSFT" not in protected
    assert "TSLA" not in protected


def test_build_protected_set_stop_limit():
    from stop_placement import build_protected_set
    orders = [{"side": "sell", "type": "stop_limit", "symbol": "NVDA"}]
    protected = build_protected_set(orders)
    assert "NVDA" in protected


def test_build_protected_set_empty():
    from stop_placement import build_protected_set
    assert build_protected_set([]) == set()


def test_positions_needing_stops():
    from stop_placement import positions_needing_stops
    positions = [
        make_position("AAPL", 10, 100, 120, 20),
        make_position("MSFT", 5, 200, 190, -5),
    ]
    protected = {"AAPL"}
    result = positions_needing_stops(positions, protected)
    assert len(result) == 1
    assert result[0]["symbol"] == "MSFT"


def test_positions_needing_stops_all_protected():
    from stop_placement import positions_needing_stops
    positions = [make_position("AAPL", 10, 100, 120, 20)]
    result = positions_needing_stops(positions, {"AAPL"})
    assert result == []


def test_positions_needing_stops_none_protected():
    from stop_placement import positions_needing_stops
    positions = [
        make_position("AAPL", 10, 100, 120, 20),
        make_position("MSFT", 5, 200, 190, -5),
    ]
    result = positions_needing_stops(positions, set())
    assert len(result) == 2


def test_calculate_stop_for_position_winner():
    from stop_placement import calculate_stop_for_position
    pos = make_position("COHU", 17, 24.29, 56.77, 133.7)
    stop, tier = calculate_stop_for_position(pos)
    assert stop == round(56.77 * 0.92, 2)   # 100%+ → 8% trail
    assert "8%" in tier


def test_calculate_stop_for_position_loser():
    from stop_placement import calculate_stop_for_position
    pos = make_position("AMD", 6, 540.69, 491.66, -9.1)
    stop, tier = calculate_stop_for_position(pos)
    assert stop == round(540.69 * 0.92, 2)  # default → 8% below entry
    assert "entry" in tier.lower() or "default" in tier.lower()


def _alpaca_pos(current, avg, plpc):
    return {"symbol": "AAA", "current_price": str(current), "avg_entry_price": str(avg), "unrealized_plpc": str(plpc)}


def test_atr_arm_uses_its_own_entry_stop():
    from stop_placement import calculate_stop_for_position
    trade = {"symbol": "AAA", "status": "OPEN", "stop_arm": "atr", "stop_dist": 0.12}
    stop, tier = calculate_stop_for_position(_alpaca_pos(102, 100, 0.02), trade)
    assert stop == 88.0 and "ATR" in tier


def test_fixed_arm_and_untagged_positions_keep_the_8pct_stop():
    from stop_placement import calculate_stop_for_position
    for trade in (None, {"stop_arm": "fixed", "stop_dist": None}, {"stop_arm": "atr", "stop_dist": None}, {}):
        assert calculate_stop_for_position(_alpaca_pos(102, 100, 0.02), trade)[0] == 92.0


def test_atr_arm_does_not_touch_the_profit_tiers():
    from stop_placement import calculate_stop_for_position
    trade = {"stop_arm": "atr", "stop_dist": 0.12}
    assert calculate_stop_for_position(_alpaca_pos(130, 100, 0.30), trade)[0] == 101.5     # break-even + 1.5%
    assert calculate_stop_for_position(_alpaca_pos(160, 100, 0.60), trade)[0] == 144.0     # 10% trail


def test_out_of_range_or_garbage_stop_distance_falls_back_to_default():
    from stop_placement import calculate_stop_for_position
    for bad in (0.9, 0.0001, -0.1, "wide", float("nan")):
        assert calculate_stop_for_position(_alpaca_pos(102, 100, 0.02), {"stop_arm": "atr", "stop_dist": bad})[0] == 92.0


def test_open_trade_for_picks_the_latest_open_entry():
    from stop_placement import open_trade_for
    trades = [{"symbol": "AAA", "status": "CLOSED", "stop_arm": "atr"},
              {"symbol": "AAA", "status": "OPEN", "stop_arm": "fixed"},
              {"symbol": "BBB", "status": "OPEN", "stop_arm": "atr"}]
    assert open_trade_for("AAA", trades)["stop_arm"] == "fixed"
    assert open_trade_for("ZZZ", trades) is None and open_trade_for("AAA", None) is None


def test_no_sell_stop_is_placed_on_a_short_or_an_option():
    # A sell stop on a short would add to it; options do not take stop orders
    from stop_placement import positions_needing_stops
    positions = [
        {"symbol": "AMD", "qty": 7.0, "side": "long", "asset_class": "us_equity"},
        {"symbol": "XOM", "qty": -10.0, "side": "short", "asset_class": "us_equity"},
        {"symbol": "AMD261218P00400000", "qty": 2.0, "side": "long", "asset_class": "us_option"},
    ]
    assert [p["symbol"] for p in positions_needing_stops(positions, set())] == ["AMD"]


def test_delisted_holding_is_untradable_not_a_failed_stop():
    # CTLP: acquired and delisted, still in the account, rejects every order
    from stop_placement import untradable_reason
    assert "inactive" in untradable_reason({"symbol": "CTLP", "status": "inactive", "tradable": False})
    assert untradable_reason({"symbol": "HALT", "status": "active", "tradable": False}) == "asset is not tradable on Alpaca"
    assert untradable_reason({"symbol": "AMD", "status": "active", "tradable": True}) is None


def test_failed_asset_lookup_still_attempts_the_stop():
    from stop_placement import untradable_reason
    assert untradable_reason({"error": "timeout"}) is None
    assert untradable_reason(None) is None


def test_main_reports_a_delisted_holding_separately(monkeypatch, tmp_path):
    import stop_placement, alpaca_broker, json

    class Broker:
        orders = []
        def market_clock(self): return True
        def get_positions(self):
            return [make_position("CTLP", 281, 10.64, 11.2, 5.26), make_position("AMD", 7, 506.44, 621.86, 22.79)]
        def get_orders(self, status=None, limit=50): return []
        def get_asset(self, symbol):
            return {"status": "inactive", "tradable": False} if symbol == "CTLP" else {"status": "active", "tradable": True}
        def place_order(self, **kw):
            self.orders.append(kw["symbol"]); return {"id": "1"}

    monkeypatch.setattr(alpaca_broker, "AlpacaBroker", Broker)
    monkeypatch.setattr(stop_placement, "ROOT", tmp_path)
    stop_placement.main()
    out = json.loads((tmp_path / "docs" / "data" / "stop_placement.json").read_text())
    assert Broker.orders == ["AMD"]
    assert (out["stops_placed"], out["stops_failed"], out["untradable"]) == (1, 0, 1)
    assert [r["status"] for r in out["results"]] == ["untradable", "placed"]


# ── blanket rule: every long position carries a stop for all its shares ──────

class _Broker:
    """Minimal Alpaca stand-in: positions, open orders, and a record of what was sent."""
    def __init__(self, positions, orders=(), inactive=()):
        self.positions, self.orders, self.inactive = list(positions), list(orders), set(inactive)
        self.placed, self.cancelled = [], []
    def get_positions(self): return self.positions
    def get_orders(self, status=None, limit=50): return [o for o in self.orders if o["id"] not in self.cancelled]
    def get_asset(self, symbol):
        return {"status": "inactive", "tradable": False} if symbol in self.inactive else {"status": "active", "tradable": True}
    def cancel_order(self, order_id): self.cancelled.append(order_id); return {"success": True}
    def place_order(self, **kw):
        self.placed.append(kw)
        self.orders.append({"id": f"new{len(self.placed)}", "symbol": kw["symbol"], "side": "sell", "type": "stop",
                            "qty": str(kw["qty"]), "stop_price": str(kw["stop_price"])})
        return {"id": f"new{len(self.placed)}"}


def _stop(symbol, qty, price, oid):
    return {"id": oid, "symbol": symbol, "side": "sell", "type": "stop", "qty": str(qty), "stop_price": str(price)}


def test_a_position_bought_today_gets_a_stop_for_all_its_shares():
    from stop_placement import place_missing_stops
    broker = _Broker([make_position("AME", 16, 254.01, 253.90, -0.04), make_position("AMD", 7, 506.44, 630.18, 24.4)],
                     orders=[_stop("AMD", 7, 514.04, "s1")])
    out = place_missing_stops(broker, trades=[])
    assert [(o["symbol"], o["qty"], o["side"], o["order_type"], o["time_in_force"]) for o in broker.placed] == \
        [("AME", 16, "sell", "stop", "gtc")]
    assert broker.placed[0]["stop_price"] == round(254.01 * 0.92, 2)       # default tier: 8% below entry
    assert (out["stops_placed"], out["stops_failed"], out["already_protected"]) == (1, 0, 1)


def test_a_stop_covering_only_part_of_the_shares_is_topped_up_never_lowered():
    # Bought in two fills: the stop from the first fill covers 6 of 9 shares
    from stop_placement import place_missing_stops, uncovered_positions
    pos = make_position("PSX", 9, 266.80, 266.63, -0.06)
    broker = _Broker([pos], orders=[_stop("PSX", 6, 250.00, "old")])        # 250.00 is above the 8% tier (245.46)
    assert [p["symbol"] for p in uncovered_positions([pos], broker.get_orders())] == ["PSX"]
    place_missing_stops(broker, trades=[])
    assert broker.cancelled == []                                          # the existing stop is never removed
    assert (broker.placed[0]["qty"], broker.placed[0]["stop_price"]) == (3, 250.00)
    assert uncovered_positions([pos], broker.get_orders()) == []           # and now the rule holds


def test_a_failed_account_read_places_nothing_and_says_so():
    # [] from a failed call must not be read as "no positions" or "no stops"
    from stop_placement import place_missing_stops
    for flag in ("last_positions_ok", "last_orders_ok"):
        broker = _Broker([make_position("AME", 16, 254.01, 253.90, -0.04)])
        setattr(broker, flag, False)
        out = place_missing_stops(broker, trades=[])
        assert broker.placed == [] and broker.cancelled == []
        assert "could not read" in out["error"]


def test_stop_placement_run_fails_loudly_when_a_stop_cannot_be_placed(monkeypatch, tmp_path):
    import pytest
    import alpaca_broker, stop_placement
    broker = _Broker([make_position("AME", 16, 254.01, 253.90, -0.04)])
    broker.market_clock = lambda: True
    broker.place_order = lambda **kw: {"error": "rejected"}
    monkeypatch.setattr(alpaca_broker, "AlpacaBroker", lambda: broker)
    monkeypatch.setattr(stop_placement, "ROOT", tmp_path)
    with pytest.raises(SystemExit):
        stop_placement.main()
    assert (tmp_path / "docs" / "data" / "stop_placement.json").exists()    # the result is still recorded


def test_fully_covered_positions_and_positions_being_sold_are_left_alone():
    from stop_placement import place_missing_stops
    broker = _Broker([make_position("AMD", 7, 506.44, 630.18, 24.4), make_position("VRT", 5, 100.0, 80.0, -20.0)],
                     orders=[_stop("AMD", 7, 514.04, "s1")])
    out = place_missing_stops(broker, trades=[], skip={"VRT"})              # VRT's exit was just sent
    assert broker.placed == [] and broker.cancelled == []
    assert out["stops_placed"] == 0


def test_stop_coverage_adds_up_several_orders_and_ignores_buys_and_limits():
    from stop_placement import stop_coverage
    cover = stop_coverage([_stop("GEV", 1, 900.0, "a"), _stop("GEV", 1, 910.0, "b"),
                           {"id": "c", "symbol": "GEV", "side": "sell", "type": "limit", "qty": "2"},
                           {"id": "d", "symbol": "GEV", "side": "buy", "type": "stop", "qty": "2"},
                           {"id": "e", "symbol": "VLO", "side": "sell", "type": "trailing_stop", "qty": "5"}])
    assert cover["GEV"] == {"qty": 2, "stop": 910.0, "ids": ["a", "b"]}
    assert cover["VLO"]["qty"] == 5


def test_daily_run_places_stops_on_what_it_just_bought():
    from types import SimpleNamespace
    from run_daily_analysis import DailyRunner
    broker = _Broker([make_position("AME", 16, 254.01, 253.90, -0.04), make_position("AMD", 7, 506.44, 630.18, 24.4)],
                     orders=[_stop("AMD", 7, 514.04, "s1")])
    broker.get_order = lambda oid: {"status": "filled"}
    execution = {"details": [{"symbol": "AME", "status": "submitted", "order_id": "o1"},
                             {"symbol": "CAT", "status": "skipped"}]}
    out = DailyRunner._protect_new_buys(SimpleNamespace(broker=broker), execution, wait_seconds=0)
    assert [o["symbol"] for o in broker.placed] == ["AME"]
    assert (out["stops_placed"], out["pending"], out["unprotected"]) == (1, [], [])


def test_daily_run_reports_a_buy_still_filling_and_a_buy_left_without_a_stop():
    from types import SimpleNamespace
    from run_daily_analysis import DailyRunner
    broker = _Broker([make_position("AME", 16, 254.01, 253.90, -0.04)])
    broker.get_order = lambda oid: {"status": "new" if oid == "o2" else "filled"}
    broker.place_order = lambda **kw: {"error": "insufficient qty available"}       # the stop is rejected
    execution = {"details": [{"symbol": "AME", "status": "submitted", "order_id": "o1"},
                             {"symbol": "EXPD", "status": "submitted", "order_id": "o2"}]}
    out = DailyRunner._protect_new_buys(SimpleNamespace(broker=broker), execution, wait_seconds=0)
    assert out["pending"] == ["EXPD"]            # not filled yet: nothing to protect, not an alert
    assert out["unprotected"] == ["AME"]         # held without a stop: this is the alert
    assert out["stops_failed"] == 1


def test_daily_run_survives_a_broker_failure_while_placing_stops():
    from types import SimpleNamespace
    from run_daily_analysis import DailyRunner

    class Down:
        def get_order(self, oid): raise RuntimeError("alpaca down")
    out = DailyRunner._protect_new_buys(
        SimpleNamespace(broker=Down()), {"details": [{"symbol": "AME", "status": "submitted", "order_id": "o1"}]},
        wait_seconds=0)
    assert out["unprotected"] == ["AME"] and "alpaca down" in out["error"]


def test_intraday_check_sweeps_for_positions_without_a_stop(monkeypatch, tmp_path):
    import json
    import alpaca_broker, exit_logic, intraday_exit
    pos = {**make_position("EXPD", 13, 192.39, 192.38, -0.01), "side": "long", "asset_class": "us_equity"}
    broker = _Broker([pos])
    broker.market_clock = lambda: True
    monkeypatch.setattr(alpaca_broker, "AlpacaBroker", lambda: broker)
    monkeypatch.setattr(exit_logic, "get_sma50_map", lambda symbols: {"EXPD": 150.0})
    monkeypatch.setattr(intraday_exit, "DOCS_DATA", tmp_path)
    intraday_exit.main()
    assert [(o["symbol"], o["qty"]) for o in broker.placed] == [("EXPD", 13)]
    out = json.loads((tmp_path / "intraday_exit.json").read_text())
    assert out["exits_triggered"] == [] and [r["symbol"] for r in out["stops_placed"]] == ["EXPD"]


def test_daily_run_treats_an_unreadable_order_as_still_filling_and_an_unreadable_account_as_unprotected():
    from types import SimpleNamespace
    from run_daily_analysis import DailyRunner
    broker = _Broker([make_position("AME", 16, 254.01, 253.90, -0.04)])
    broker.get_order = lambda oid: {"error": "timeout"}
    execution = {"details": [{"symbol": "AME", "status": "submitted", "order_id": "o1"}]}
    out = DailyRunner._protect_new_buys(SimpleNamespace(broker=broker), execution, wait_seconds=0)
    assert out["pending"] == ["AME"] and out["stops_placed"] == 1          # still gets its stop: it is held
    broker.last_orders_ok = False
    out = DailyRunner._protect_new_buys(SimpleNamespace(broker=broker), execution, wait_seconds=0)
    assert out["unprotected"] == ["AME"] and "could not read" in out["error"]


def _intraday(monkeypatch, tmp_path, broker, sma):
    import alpaca_broker, exit_logic, intraday_exit
    broker.market_clock = lambda: True
    monkeypatch.setattr(alpaca_broker, "AlpacaBroker", lambda: broker)
    monkeypatch.setattr(exit_logic, "get_sma50_map", lambda symbols: sma)
    monkeypatch.setattr(intraday_exit, "DOCS_DATA", tmp_path)
    return intraday_exit


def test_intraday_check_fails_the_run_when_a_stop_cannot_be_placed(monkeypatch, tmp_path):
    import json, pytest
    pos = {**make_position("EXPD", 13, 192.39, 192.38, -0.01), "side": "long", "asset_class": "us_equity"}
    broker = _Broker([pos])
    broker.place_order = lambda **kw: {"error": "rejected"}
    with pytest.raises(SystemExit):
        _intraday(monkeypatch, tmp_path, broker, {"EXPD": 150.0}).main()
    out = json.loads((tmp_path / "intraday_exit.json").read_text())
    assert [f["symbol"] for f in out["stop_failures"]] == ["EXPD"] and out["stops_placed"] == []


def test_a_failed_stop_raise_puts_the_old_stop_back(monkeypatch):
    import intraday_exit
    pos = make_position("VLO", 5, 267.28, 412.25, 54.24)                   # 50%+ tier: trail 10% below price
    broker = _Broker([pos], orders=[_stop("VLO", 5, 360.00, "old")])
    broker.get_orders = lambda status=None, limit=50, symbols=None: [o for o in broker.orders if o["id"] not in broker.cancelled]
    sent = []

    def place_order(**kw):
        sent.append(kw["stop_price"])
        return {"error": "rejected"} if len(sent) == 1 else {"id": "restored"}
    broker.place_order = place_order
    out = intraday_exit._update_stop_if_better(broker, pos)
    assert sent == [round(412.25 * 0.90, 2), 360.00]                       # tried the raise, then restored the old one
    assert out["status"] == "failed" and out["restored"] is True


def test_stop_is_left_alone_when_open_orders_cannot_be_read():
    import intraday_exit
    pos = make_position("VLO", 5, 267.28, 412.25, 54.24)
    broker = _Broker([pos], orders=[_stop("VLO", 5, 360.00, "old")])
    broker.get_orders = lambda status=None, limit=50, symbols=None: []
    broker.last_orders_ok = False
    out = intraday_exit._update_stop_if_better(broker, pos)
    assert broker.cancelled == [] and broker.placed == [] and out["status"] == "failed"


def test_a_failed_exit_gets_its_stop_back_and_fails_the_run(monkeypatch, tmp_path):
    # The exit cancels the stop, then the sell is rejected: the position is still held
    import json, pytest
    pos = {**make_position("VRT", 5, 100.0, 80.0, -20.0), "side": "long", "asset_class": "us_equity"}
    broker = _Broker([pos], orders=[_stop("VRT", 5, 92.0, "old")])
    broker.get_orders = lambda status=None, limit=50, symbols=None: [o for o in broker.orders if o["id"] not in broker.cancelled]
    broker.close_position = lambda sym: {"error": "rejected"}
    with pytest.raises(SystemExit):
        _intraday(monkeypatch, tmp_path, broker, {"VRT": 90.0}).main()
    assert broker.cancelled == ["old"]
    assert [(o["symbol"], o["qty"]) for o in broker.placed] == [("VRT", 5)]      # protected again
    out = json.loads((tmp_path / "intraday_exit.json").read_text())
    assert out["stop_failures"][0]["status"] == "exit_failed"


def test_jobs_fail_instead_of_skipping_when_the_market_clock_cannot_be_read(monkeypatch, tmp_path):
    import pytest
    import alpaca_broker, stop_placement
    broker = _Broker([make_position("AME", 16, 254.01, 253.90, -0.04)])
    broker.market_clock = lambda: None
    monkeypatch.setattr(alpaca_broker, "AlpacaBroker", lambda: broker)
    monkeypatch.setattr(stop_placement, "ROOT", tmp_path)
    with pytest.raises(SystemExit):
        stop_placement.main()
    intraday = _intraday(monkeypatch, tmp_path, broker, {})
    broker.market_clock = lambda: None
    with pytest.raises(SystemExit):
        intraday.main()
    broker.market_clock = lambda: False                       # a real "closed" is a quiet no-op
    stop_placement.main()
    assert broker.placed == []


def test_daily_run_confirms_stops_against_the_account_not_the_sweeps_own_report():
    from types import SimpleNamespace
    from run_daily_analysis import DailyRunner
    broker = _Broker([make_position("AME", 16, 254.01, 253.90, -0.04)])
    broker.get_order = lambda oid: {"status": "filled"}
    broker.place_order = lambda **kw: {"id": "accepted-but-never-live"}        # reports success, no order appears
    execution = {"details": [{"symbol": "AME", "status": "submitted", "order_id": "o1"}]}
    out = DailyRunner._protect_new_buys(SimpleNamespace(broker=broker), execution, wait_seconds=0)
    assert out["stops_placed"] == 1 and out["unprotected"] == ["AME"]


def test_an_exit_that_is_accepted_but_never_fills_is_not_treated_as_sold(monkeypatch, tmp_path):
    import json, pytest
    pos = {**make_position("VRT", 5, 100.0, 80.0, -20.0), "side": "long", "asset_class": "us_equity"}
    broker = _Broker([pos], orders=[_stop("VRT", 5, 92.0, "old")])
    broker.get_orders = lambda status=None, limit=50, symbols=None: [o for o in broker.orders if o["id"] not in broker.cancelled]
    broker.close_position = lambda sym: {"id": "sell1"}
    broker.get_order = lambda oid: {"status": "accepted"}
    intraday = _intraday(monkeypatch, tmp_path, broker, {"VRT": 90.0})
    monkeypatch.setattr(intraday, "EXIT_CONFIRM_SECONDS", 0)
    with pytest.raises(SystemExit):
        intraday.main()
    assert [(o["symbol"], o["qty"]) for o in broker.placed] == [("VRT", 5)]      # not skipped: stop goes back on
    out = json.loads((tmp_path / "intraday_exit.json").read_text())
    assert out["exits_triggered"][0]["confirmed"] is False
    assert out["stop_failures"][0]["status"] == "exit_unconfirmed"


def test_a_filled_exit_is_left_alone_by_the_sweep(monkeypatch, tmp_path):
    import json
    pos = {**make_position("VRT", 5, 100.0, 80.0, -20.0), "side": "long", "asset_class": "us_equity"}
    broker = _Broker([pos], orders=[_stop("VRT", 5, 92.0, "old")])
    broker.get_orders = lambda status=None, limit=50, symbols=None: [o for o in broker.orders if o["id"] not in broker.cancelled]
    broker.close_position = lambda sym: {"id": "sell1"}
    broker.get_order = lambda oid: {"status": "filled"}
    monkeypatch.setattr("intraday_exit._log_exit", lambda *a, **k: None)
    _intraday(monkeypatch, tmp_path, broker, {"VRT": 90.0}).main()               # no SystemExit
    assert broker.placed == []
    out = json.loads((tmp_path / "intraday_exit.json").read_text())
    assert out["exits_triggered"][0]["confirmed"] is True and out["stop_failures"] == []
