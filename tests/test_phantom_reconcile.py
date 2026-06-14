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
