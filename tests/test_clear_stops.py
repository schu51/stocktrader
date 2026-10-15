"""
A stop order holds the shares it protects. Selling them while it is still
being cancelled fails at the broker with "insufficient qty available"
(2026-10-15: NET, PANW and SNOW, whose stops were cancelled and never put
back). Every sale must wait until its stops are confirmed cancelled.
"""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from tests.test_stop_placement import make_position


class SlowCancelBroker:
    """Alpaca-like: a cancelled stop is 'pending_cancel' for a few reads, and a sale fails while any stop holds the shares."""
    def __init__(self, positions, stops, reads_until_cancelled=2, cancel_never=False, stop_fills=False):
        self.positions = {p["symbol"]: p for p in positions}
        self.orders = {s["id"]: dict(s, status="new") for s in stops}
        self.reads, self.lag, self.never, self.stop_fills = {}, reads_until_cancelled, cancel_never, stop_fills
        self.closed, self.placed = [], []
        self.last_orders_ok = self.last_positions_ok = True
    def market_clock(self): return True
    def get_positions(self): return list(self.positions.values())
    def get_asset(self, symbol): return {"status": "active", "tradable": True}
    def get_orders(self, status=None, limit=50, symbols=None):
        return [dict(o) for o in self.orders.values() if o["status"] in ("new", "accepted", "pending_cancel")
                and (not symbols or o["symbol"] in symbols)]
    def cancel_order(self, oid):
        self.orders[oid]["status"] = "pending_cancel"
        self.reads[oid] = 0
        return {"success": True}
    def get_order(self, oid):
        o = self.orders[oid]
        if o["status"] == "pending_cancel":
            self.reads[oid] += 1
            if self.stop_fills:
                o["status"] = "filled"
            elif not self.never and self.reads[oid] >= self.lag:
                o["status"] = "canceled"
        return dict(o, filled_qty=o.get("qty") if o["status"] == "filled" else "0")
    def close_position(self, symbol, qty=None):
        held = any(o["symbol"] == symbol and o["side"] == "sell" and o["status"] in ("new", "pending_cancel")
                   for o in self.orders.values())
        if held:
            return {"error": "insufficient qty available for order (requested: 8, available: 0)"}
        oid = f"sale-{symbol}"
        self.orders[oid] = {"id": oid, "symbol": symbol, "side": "sell", "type": "market", "status": "filled",
                            "filled_qty": self.positions[symbol]["qty"], "qty": self.positions[symbol]["qty"]}
        self.closed.append(symbol)
        del self.positions[symbol]
        return {"id": oid}
    def place_order(self, **kw):
        oid = f"new{len(self.placed) + 1}"
        self.placed.append(kw)
        self.orders[oid] = {"id": oid, "symbol": kw["symbol"], "side": "sell", "type": "stop", "status": "new",
                            "qty": str(kw["qty"]), "stop_price": str(kw["stop_price"])}
        return {"id": oid}


def _stop(symbol, qty, price, oid):
    return {"id": oid, "symbol": symbol, "side": "sell", "type": "stop", "qty": str(qty), "stop_price": str(price)}


def _long(symbol, qty, cost, price):
    return {**make_position(symbol, qty, cost, price, (price / cost - 1) * 100), "side": "long", "asset_class": "us_equity",
            "market_value": qty * price, "unrealized_pl": qty * (price - cost)}


def test_clearing_waits_until_every_stop_is_confirmed_cancelled():
    from exit_logic import clear_stops_for_sale
    broker = SlowCancelBroker([_long("NET", 8, 312.8, 317.0)], [_stop("NET", 8, 290.0, "s1")], reads_until_cancelled=3)
    ok, why = clear_stops_for_sale(broker, "NET", wait_seconds=5, poll=0)
    assert ok and why == "" and broker.orders["s1"]["status"] == "canceled"
    assert "error" not in broker.close_position("NET")


def test_clearing_reports_a_cancel_that_never_confirms_and_a_stop_that_filled_instead():
    from exit_logic import clear_stops_for_sale
    broker = SlowCancelBroker([_long("NET", 8, 312.8, 317.0)], [_stop("NET", 8, 290.0, "s1")], cancel_never=True)
    ok, why = clear_stops_for_sale(broker, "NET", wait_seconds=0, poll=0)
    assert not ok and "not confirmed" in why
    broker = SlowCancelBroker([_long("NET", 8, 312.8, 317.0)], [_stop("NET", 8, 290.0, "s1")], stop_fills=True)
    ok, why = clear_stops_for_sale(broker, "NET", wait_seconds=1, poll=0)
    assert not ok and "filled" in why


def test_clearing_with_no_stop_or_unreadable_orders():
    from exit_logic import clear_stops_for_sale
    broker = SlowCancelBroker([_long("NET", 8, 312.8, 317.0)], [])
    assert clear_stops_for_sale(broker, "NET", wait_seconds=0, poll=0) == (True, "")
    broker.last_orders_ok = False
    ok, why = clear_stops_for_sale(broker, "NET", wait_seconds=0, poll=0)
    assert not ok and "could not read" in why


def test_a_stop_being_cancelled_does_not_count_as_protection():
    from stop_placement import stop_coverage
    orders = [dict(_stop("NET", 8, 290.0, "s1"), status="pending_cancel"), dict(_stop("SNOW", 16, 280.0, "s2"), status="new")]
    assert set(stop_coverage(orders)) == {"SNOW"}


def test_intraday_exit_sells_once_the_stop_is_cancelled(monkeypatch, tmp_path):
    import alpaca_broker, exit_logic, intraday_exit
    broker = SlowCancelBroker([_long("NET", 8, 312.8, 316.99)], [_stop("NET", 8, 290.0, "s1")])
    (tmp_path / "trades.json").write_text(json.dumps([{"symbol": "NET", "status": "OPEN", "entry_date": "2026-09-01",
                                                       "entry_price": 312.8, "shares": 8}]))
    monkeypatch.setattr(alpaca_broker, "AlpacaBroker", lambda: broker)
    monkeypatch.setattr(exit_logic, "get_sma50_map", lambda symbols: {"NET": 319.22})      # below its 50-day
    monkeypatch.setattr(exit_logic, "CANCEL_POLL_SECONDS", 0)
    monkeypatch.setattr(intraday_exit, "DOCS_DATA", tmp_path)
    monkeypatch.setattr(intraday_exit, "TRADES_FILE", tmp_path / "trades.json")
    monkeypatch.setattr(intraday_exit, "EXIT_CONFIRM_SECONDS", 0)
    intraday_exit.main()
    out = json.loads((tmp_path / "intraday_exit.json").read_text())
    assert broker.closed == ["NET"] and out["stop_failures"] == []
    assert json.loads((tmp_path / "trades.json").read_text())[0]["status"] == "CLOSED"


def test_intraday_exit_that_cannot_clear_the_stop_leaves_the_position_protected(monkeypatch, tmp_path):
    import alpaca_broker, exit_logic, intraday_exit
    broker = SlowCancelBroker([_long("NET", 8, 312.8, 316.99)], [_stop("NET", 8, 290.0, "s1")], cancel_never=True)
    (tmp_path / "trades.json").write_text(json.dumps([{"symbol": "NET", "status": "OPEN", "entry_date": "2026-09-01"}]))
    monkeypatch.setattr(alpaca_broker, "AlpacaBroker", lambda: broker)
    monkeypatch.setattr(exit_logic, "get_sma50_map", lambda symbols: {"NET": 319.22})
    monkeypatch.setattr(exit_logic, "CANCEL_WAIT_SECONDS", 0)
    monkeypatch.setattr(exit_logic, "CANCEL_POLL_SECONDS", 0)
    monkeypatch.setattr(intraday_exit, "DOCS_DATA", tmp_path)
    monkeypatch.setattr(intraday_exit, "TRADES_FILE", tmp_path / "trades.json")
    with pytest.raises(SystemExit):
        intraday_exit.main()
    out = json.loads((tmp_path / "intraday_exit.json").read_text())
    assert broker.closed == [] and out["stop_failures"][0]["status"] == "exit_deferred"
    # Not sold. The old stop may still cancel after the deadline, so the sweep
    # puts a new one on; if the old one is in fact still live the broker
    # rejects the new one and the run reports it.
    assert [(o["symbol"], o["qty"]) for o in broker.placed] == [("NET", 8)]


def test_daily_exits_clear_stops_before_selling():
    # The daily 50-day, hard-loss and thesis exits sold without touching the
    # stop at all: with a stop on every position, each of those sales fails.
    import inspect
    from run_daily_analysis import DailyRunner
    source = inspect.getsource(DailyRunner._evaluate_exits) + inspect.getsource(DailyRunner._rotate_out)
    assert source.count("clear_stops_for_sale(") >= 4
    assert "self.broker.cancel_order(o[\"id\"])\n\n                        r = self.broker.close_position" not in source
