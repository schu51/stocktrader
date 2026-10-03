import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from run_daily_analysis import classify_order_for_reconcile


def test_filled_orders_kept():
    assert classify_order_for_reconcile("filled") == "keep"
    assert classify_order_for_reconcile("partially_filled") == "keep"
    assert classify_order_for_reconcile("FILLED") == "keep"


def test_unfilled_terminal_orders_cancelled():
    for s in ("canceled", "cancelled", "expired", "rejected", "done_for_day", "replaced"):
        assert classify_order_for_reconcile(s) == "cancel", s


def test_working_or_unknown_orders_kept():
    # Still working, or status we can't interpret — never guess-cancel
    for s in ("new", "accepted", "pending_new", "", None, "weird_status"):
        assert classify_order_for_reconcile(s) == "keep", s


def _open(sym):
    return {"symbol": sym, "status": "OPEN"}


def test_reconcile_is_refused_when_positions_could_not_be_verified():
    from exit_logic import safe_to_reconcile
    ok, reason = safe_to_reconcile([_open("A"), _open("B")], {"A"}, positions_verified=False)
    assert not ok and "verified" in reason


def test_reconcile_is_refused_when_it_would_cancel_most_open_trades():
    # An outage that returns "no positions" must not wipe the trade log
    from exit_logic import safe_to_reconcile
    trades = [_open(s) for s in "ABCDEFGHIJ"]
    ok, reason = safe_to_reconcile(trades, set(), positions_verified=True)
    assert not ok and "10 of 10" in reason
    ok, _ = safe_to_reconcile(trades, set("ABCDEFG"), positions_verified=True)      # 3 of 10 unfilled: plausible
    assert ok


def test_reconcile_allowed_for_ordinary_unfilled_orders():
    from exit_logic import safe_to_reconcile
    assert safe_to_reconcile([_open("A"), _open("B"), _open("C")], {"A", "B"}, True)[0]
    assert safe_to_reconcile([_open("A")], set(), True)[0]            # a single unfilled order is normal
    assert safe_to_reconcile([], set(), True)[0]


def _broker(monkeypatch, response):
    from alpaca_broker import AlpacaBroker
    broker = AlpacaBroker(api_key="test", secret_key="test", paper=True)
    monkeypatch.setattr(broker, "_request", lambda method, path, **kw: response)
    return broker


def test_broker_flags_a_failed_positions_call(monkeypatch):
    broker = _broker(monkeypatch, {"error": "503", "status_code": 503})
    assert broker.get_positions() == [] and broker.last_positions_ok is False


def test_broker_flags_a_real_positions_list_including_an_empty_one(monkeypatch):
    broker = _broker(monkeypatch, [])
    assert broker.get_positions() == [] and broker.last_positions_ok is True
    broker = _broker(monkeypatch, [{"symbol": "AMD", "qty": "7"}])
    assert broker.get_positions()[0]["qty"] == 7.0 and broker.last_positions_ok is True


def test_runner_verification_reads_the_same_call(monkeypatch):
    from types import SimpleNamespace
    from run_daily_analysis import DailyRunner
    verified = lambda broker, account_ok=True: DailyRunner._positions_verified(
        SimpleNamespace(broker=broker, _account_ok=account_ok))
    assert verified(None) is False
    assert verified(SimpleNamespace()) is False                              # never asked
    assert verified(SimpleNamespace(last_positions_ok=False)) is False
    assert verified(SimpleNamespace(last_positions_ok=True)) is True
    # positions fine but the account call failed: sizing would use defaults
    assert verified(SimpleNamespace(last_positions_ok=True), account_ok=False) is False


def test_sector_allocations_come_from_the_verified_portfolio_not_a_new_call():
    from types import SimpleNamespace
    from run_daily_analysis import DailyRunner
    from config import SECTOR_MAP
    sector, symbols = next((s, syms) for s, syms in SECTOR_MAP.items() if syms)
    sym = list(symbols)[0]

    class Broker:
        def get_positions(self):
            raise AssertionError("must not fetch positions again")
    stub = SimpleNamespace(broker=Broker(), portfolio=SimpleNamespace(
        total_value=100_000.0, positions={sym: SimpleNamespace(market_value=40_000.0)}))
    assert DailyRunner._get_sector_allocations(stub) == {sector: 0.4}


def test_exit_check_reports_a_failed_positions_call():
    from types import SimpleNamespace
    from run_daily_analysis import DailyRunner
    broker = SimpleNamespace(get_positions=lambda: [], last_positions_ok=False)
    out = DailyRunner._evaluate_exits(SimpleNamespace(broker=broker), execute=True)
    assert "could not be fetched" in out["error"]
