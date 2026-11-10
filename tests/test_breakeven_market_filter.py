"""Break-even stop tier and market filter (2026-11-10)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_breakeven_tier_from_five_percent():
    from exit_logic import calculate_stop_price
    assert calculate_stop_price(104.0, 4.0, 100.0)[0] == 92.0      # under 5%: 8% below entry
    assert calculate_stop_price(105.0, 5.0, 100.0)[0] == 100.5     # 5%+: cost + 0.5%
    assert calculate_stop_price(120.0, 20.0, 100.0)[0] == 100.5
    assert calculate_stop_price(130.0, 30.0, 100.0)[0] == 101.5    # 25%+ tier unchanged
    assert calculate_stop_price(160.0, 60.0, 100.0)[0] == 144.0    # 50%+ trail unchanged


def test_atr_arm_stop_gives_way_to_breakeven():
    from stop_placement import calculate_stop_for_position
    trade = {"stop_arm": "atr", "stop_dist": 0.10}
    pos = lambda px: {"current_price": px, "avg_entry_price": 100.0, "unrealized_plpc": px / 100 - 1}
    assert calculate_stop_for_position(pos(103.0), trade)[0] == 90.0
    assert calculate_stop_for_position(pos(106.0), trade)[0] == 100.5


def _screen(price, ma, day="2026-11-10"):
    return {"generated_at": f"{day}T14:15:00", "market_regime": {"spy_price": price, "spy_50ma": ma}}


def test_market_filter_needs_spy_clearly_above_its_50_day_average():
    from run_daily_analysis import market_allows_buys
    assert market_allows_buys(_screen(103.0, 100.0), "2026-11-10", 0.02)[0] is True
    assert market_allows_buys(_screen(101.0, 100.0), "2026-11-10", 0.02)[0] is False
    assert market_allows_buys(_screen(96.0, 100.0), "2026-11-10", 0.02)[0] is False


def test_market_filter_unknown_market_means_no_buys_and_zero_turns_it_off():
    from run_daily_analysis import market_allows_buys
    assert market_allows_buys(_screen(103.0, 100.0, day="2026-11-09"), "2026-11-10", 0.02)[0] is False
    assert market_allows_buys({"generated_at": "2026-11-10T09:00"}, "2026-11-10", 0.02)[0] is False
    assert market_allows_buys({}, "2026-11-10", 0.0)[0] is True


def test_live_config_turns_both_rules_on():
    from config import EntryRules
    from exit_logic import BREAKEVEN_AT_PCT
    assert EntryRules().min_spy_above_50ma == 0.02 and BREAKEVEN_AT_PCT == 5.0
