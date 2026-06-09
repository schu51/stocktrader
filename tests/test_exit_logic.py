import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_stop_price_100pct_gain():
    from exit_logic import calculate_stop_price
    stop, tier = calculate_stop_price(current=100.0, pnl_pct=105.0, avg_cost=48.0)
    assert stop == 92.0          # 8% trail: 100 * 0.92
    assert "8%" in tier


def test_stop_price_50pct_gain():
    from exit_logic import calculate_stop_price
    stop, tier = calculate_stop_price(current=80.0, pnl_pct=60.0, avg_cost=50.0)
    assert stop == 72.0          # 10% trail: 80 * 0.90
    assert "10%" in tier


def test_stop_price_25pct_gain():
    from exit_logic import calculate_stop_price
    stop, tier = calculate_stop_price(current=65.0, pnl_pct=30.0, avg_cost=50.0)
    assert stop == round(50.0 * 1.015, 2)   # break-even + 1.5%
    assert "break-even" in tier


def test_stop_price_losing_position():
    from exit_logic import calculate_stop_price
    stop, tier = calculate_stop_price(current=45.0, pnl_pct=-10.0, avg_cost=50.0)
    assert stop == round(50.0 * 0.92, 2)    # 8% below entry cost
    assert "entry" in tier.lower() or "default" in tier.lower()


def test_exit_trigger_below_50ma():
    from exit_logic import check_exit_triggers
    should_exit, trigger, reason = check_exit_triggers(
        symbol="TEST", current_price=95.0, avg_cost=100.0,
        pnl_pct=-5.0, sma50=100.0
    )
    assert should_exit is True
    assert trigger == "PRICE_BELOW_50MA"
    assert "50MA" in reason


def test_exit_trigger_hard_loss():
    from exit_logic import check_exit_triggers
    should_exit, trigger, reason = check_exit_triggers(
        symbol="TEST", current_price=82.0, avg_cost=100.0,
        pnl_pct=-18.0, sma50=70.0   # above 50MA but huge loss
    )
    assert should_exit is True
    assert trigger == "HARD_LOSS_STOP"


def test_no_exit_healthy_position():
    from exit_logic import check_exit_triggers
    should_exit, trigger, reason = check_exit_triggers(
        symbol="TEST", current_price=120.0, avg_cost=100.0,
        pnl_pct=20.0, sma50=110.0
    )
    assert should_exit is False
    assert trigger == ""


def test_no_exit_when_no_sma50():
    from exit_logic import check_exit_triggers
    # Missing 50MA should not trigger PRICE_BELOW_50MA
    should_exit, trigger, reason = check_exit_triggers(
        symbol="TEST", current_price=90.0, avg_cost=100.0,
        pnl_pct=-10.0, sma50=None
    )
    assert should_exit is False
