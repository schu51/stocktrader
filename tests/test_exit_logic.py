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


# ── the long-stock rules must leave shorts and options alone ─────────────────

def _p(**over):
    pos = {"symbol": "AMD", "qty": 7.0, "side": "long", "asset_class": "us_equity"}
    pos.update(over)
    return pos


def test_long_stock_is_managed():
    from exit_logic import is_long_equity
    assert is_long_equity(_p())
    assert is_long_equity({"symbol": "AMD", "qty": "7"})                 # older records without the fields


def test_shorts_and_options_are_not_managed_by_long_rules():
    from exit_logic import is_long_equity
    assert not is_long_equity(_p(qty=-7.0, side="short"))                # short stock
    assert not is_long_equity(_p(qty=-7.0))                              # negative quantity alone is enough
    assert not is_long_equity(_p(symbol="AMD261218P00400000", asset_class="us_option", qty=2.0))   # long put
    assert not is_long_equity(_p(asset_class="us_option", side="short", qty=-1.0))
    assert not is_long_equity(_p(asset_class="crypto"))
    assert not is_long_equity(_p(qty="garbage")) and not is_long_equity(_p(qty=0))


def test_split_positions():
    from exit_logic import split_positions
    put = _p(symbol="AMD261218P00400000", asset_class="us_option", qty=2.0)
    short = _p(symbol="XOM", qty=-10.0, side="short")
    longs, others = split_positions([_p(), put, short])
    assert [p["symbol"] for p in longs] == ["AMD"]
    assert [p["symbol"] for p in others] == ["AMD261218P00400000", "XOM"]


def test_find_untradable_flags_only_the_delisted_holding():
    from exit_logic import find_untradable
    looked_up = []

    def get_asset(sym):
        looked_up.append(sym)
        return {"status": "inactive", "tradable": False} if sym == "CTLP" else {"status": "active", "tradable": True}

    positions = [
        {"symbol": "CTLP", "qty": 281, "side": "long", "asset_class": "us_equity"},
        {"symbol": "SHOP", "qty": 26, "side": "long", "asset_class": "us_equity"},   # bought today, no stop yet
        {"symbol": "AMD", "qty": 7, "side": "long", "asset_class": "us_equity"},     # has a stop
        {"symbol": "XOM", "qty": -10, "side": "short", "asset_class": "us_equity"},
    ]
    stuck = find_untradable(positions, {"AMD": 514.04}, get_asset)
    assert list(stuck) == ["CTLP"] and "inactive" in stuck["CTLP"]
    assert looked_up == ["CTLP", "SHOP"]   # a live stop already proves the asset trades


def test_find_untradable_treats_a_failed_lookup_as_tradable():
    from exit_logic import find_untradable

    def boom(sym):
        raise RuntimeError("alpaca down")

    positions = [{"symbol": "CTLP", "qty": 281}]
    assert find_untradable(positions, set(), boom) == {}
    assert find_untradable(positions, set(), lambda s: {"error": "timeout"}) == {}
