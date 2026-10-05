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
        def is_market_open(self): return True
        def get_positions(self):
            return [make_position("CTLP", 281, 10.64, 11.2, 5.26), make_position("AMD", 7, 506.44, 621.86, 22.79)]
        def get_orders(self, status=None): return []
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
