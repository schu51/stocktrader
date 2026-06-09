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
