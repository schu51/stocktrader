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
