import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from exit_logic import reconcile_phantom_trades


def _trade(symbol, status="OPEN", **kw):
    base = {"symbol": symbol, "status": status, "exit_reason": None}
    base.update(kw)
    return base


def test_marks_unfilled_orders_cancelled():
    trades = [
        _trade("KLAC"),
        _trade("AMAT"),
    ]
    n = reconcile_phantom_trades(trades, held_symbols={"AMAT"})
    assert n == 1
    assert trades[0]["status"] == "CANCELLED"
    assert trades[0]["exit_reason"] == "ORDER_NOT_FILLED"
    assert trades[1]["status"] == "OPEN"


def test_leaves_closed_trades_untouched():
    trades = [_trade("KLAC", status="CLOSED", exit_reason="HARD_LOSS_STOP")]
    n = reconcile_phantom_trades(trades, held_symbols=set())
    assert n == 0
    assert trades[0]["status"] == "CLOSED"
    assert trades[0]["exit_reason"] == "HARD_LOSS_STOP"


def test_held_symbol_with_multiple_lots_untouched():
    trades = [_trade("VLO"), _trade("VLO")]
    n = reconcile_phantom_trades(trades, held_symbols={"VLO"})
    assert n == 0
    assert all(t["status"] == "OPEN" for t in trades)


def test_no_open_trades_no_change():
    trades = []
    n = reconcile_phantom_trades(trades, held_symbols={"AMAT"})
    assert n == 0
