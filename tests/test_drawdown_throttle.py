import json
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).parent.parent))


def _rm(peak=100_000.0, **cfg):
    from config import DecisionConfig
    from risk_manager import RiskManager
    config = DecisionConfig()
    config = replace(config, risk_config=replace(config.risk_config, **cfg))
    rm = RiskManager(config)
    rm._values, rm.high_watermark = [(date(2026, 10, 5), peak)], peak
    return rm


def test_throttle_shrinks_new_positions_as_the_account_falls_and_never_stops_for_good():
    rm = _rm()
    assert rm.drawdown_throttle(96_000, market_healthy=False)[0] == 1.0          # 4% down: normal
    assert rm.drawdown_throttle(89_000, market_healthy=False)[0] == 0.5          # 11% down: half size
    size, why = rm.drawdown_throttle(84_000, market_healthy=True)                # 16% down, market healthy
    assert size == 0.25 and "25% size" in why
    size, why = rm.drawdown_throttle(84_000, market_healthy=False)               # 16% down, market weak
    assert size == 0.0 and "not above its 50-day average" in why
    assert rm.drawdown_throttle(84_000, market_healthy=None)[0] == 0.0           # unknown is not healthy
    assert rm.drawdown_throttle(60_000, market_healthy=True)[0] == 0.25          # however deep: still able to recover


def test_halt_mode_is_the_old_all_or_nothing_rule():
    rm = _rm(drawdown_mode="halt")
    assert rm.drawdown_throttle(89_000, market_healthy=True)[0] == 1.0
    assert rm.drawdown_throttle(84_000, market_healthy=True)[0] == 0.0


def test_the_risk_check_no_longer_blocks_every_buy_past_the_limit_but_halt_mode_still_does():
    from datetime import datetime
    from models import PortfolioState
    p = PortfolioState(timestamp=datetime(2026, 10, 6), total_value=84_000.0, cash=40_000.0, invested=44_000.0,
                       positions={}, num_positions=10, cash_allocation=0.48, available_cash=38_000.0,
                       buying_power=40_000.0, total_unrealized_pnl=0.0)
    dd = lambda rm: [c for c in rm.pre_trade_risk_check("AME", "BUY", 5, 250.0, p)["checks"] if c["check"] == "DRAWDOWN"][0]
    assert dd(_rm())["status"] == "WARNING"
    assert dd(_rm(drawdown_mode="halt"))["status"] == "FAILED"


def test_peak_is_the_highest_value_in_the_past_year_so_an_old_high_stops_counting():
    rm = _rm()
    rm._values = []
    rm.update_watermark(120_000, date(2025, 1, 10))
    rm.update_watermark(100_000, date(2025, 6, 1))
    assert rm.high_watermark == 120_000 and rm.peak_date == date(2025, 1, 10)
    rm.update_watermark(101_000, date(2026, 2, 1))                # the January 2025 high is now over a year old
    assert rm.high_watermark == 101_000 and rm.peak_date == date(2026, 2, 1)


def test_all_time_peak_is_kept_when_no_lookback_is_configured():
    rm = _rm(drawdown_lookback_days=None)
    rm.update_watermark(120_000, date(2025, 1, 10))
    rm.update_watermark(101_000, date(2026, 2, 1))
    assert rm.high_watermark == 120_000


def test_recorded_values_before_the_repair_date_do_not_set_the_peak(tmp_path):
    # 2026-06-30 is recorded as $126,521, one of several one-day jumps that look like recording errors
    rm = _rm()
    hist = tmp_path / "history.json"
    hist.write_text(json.dumps([{"date": "2026-06-30", "portfolio_value": 126520.62},
                                {"date": "2026-10-05", "portfolio_value": 115036.23},
                                {"date": "2026-10-06", "portfolio_value": 116590.55},
                                {"date": "bad", "portfolio_value": 999999}]))
    assert rm._load_historical_peak(hist, today=date(2026, 10, 7)) == 116590.55
    rm.risk_config = replace(rm.risk_config, drawdown_peak_since=None)
    assert rm._load_historical_peak(hist, today=date(2026, 10, 7)) == 126520.62


def _scaled(rm, total_value, market):
    from run_daily_analysis import DailyRunner
    stub = SimpleNamespace(engine=SimpleNamespace(risk_manager=rm), market_above_50ma=market,
                           portfolio=SimpleNamespace(total_value=total_value, available_cash=0.0))
    return DailyRunner._scale_size_by_momentum(stub, {"trend_score": 76, "rs_rank": 80, "shares": 40, "limit_price": 100.0})


def test_buy_sizes_follow_the_throttle():
    assert _scaled(_rm(), 97_000, True)["shares"] == 40
    half = _scaled(_rm(), 89_000, True)
    assert half["shares"] == 20 and "50% size" in half["drawdown_throttle"]
    assert _scaled(_rm(), 84_000, True)["shares"] == 10
    none = _scaled(_rm(), 84_000, False)
    assert none["shares"] == 0 and "no new positions" in none["drawdown_throttle"]
