"""
Buys go into the opening auction (2026-10-16). Bought at 10:00 ET at the ask,
33 live buys filled 1.1% above the open on average, two-thirds of the way up
the day's range; in the three-year backtest that kind of fill cut the return
from 174% to 108%. A limit-on-open order fills at the open, as the backtest
assumes, and is skipped if the stock gaps up more than OPEN_LIMIT.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


def test_the_opening_auction_is_ahead_only_before_the_cutoff_on_a_trading_day():
    from alpaca_broker import AlpacaBroker
    broker = AlpacaBroker.__new__(AlpacaBroker)
    clock = lambda now, next_open, is_open=False: (lambda method, endpoint, **kw: {
        "timestamp": now, "is_open": is_open, "next_open": next_open, "next_close": "2026-10-19T16:00:00-04:00"})
    broker._request = clock("2026-10-19T09:15:05-04:00", "2026-10-19T09:30:00-04:00")
    assert broker.opening_auction_ahead() is True
    broker._request = clock("2026-10-19T09:28:30-04:00", "2026-10-19T09:30:00-04:00")
    assert broker.opening_auction_ahead() is False                     # past Alpaca's 9:28 cutoff
    broker._request = clock("2026-10-17T09:15:00-04:00", "2026-10-19T09:30:00-04:00")
    assert broker.opening_auction_ahead() is False                     # Saturday: the next open is Monday
    broker._request = clock("2026-10-19T10:00:00-04:00", "2026-10-20T09:30:00-04:00", is_open=True)
    assert broker.opening_auction_ahead() is False                     # already open
    broker._request = lambda method, endpoint, **kw: {"error": "down"}
    assert broker.opening_auction_ahead() is None                      # unknown


def _runner(monkeypatch, ahead=True, market_open=False):
    import run_daily_analysis as rda
    sent = []
    monkeypatch.setattr(rda, "ALPACA_AVAILABLE", True)

    class Executor:
        def __init__(self, broker): pass
        def execute_decision(self, opp):
            sent.append(("at_ask", opp["symbol"], opp["limit_price"]))
            return {"status": "submitted", "order_id": "day-" + opp["symbol"]}
    monkeypatch.setattr(rda, "OrderExecutor", Executor)
    quotes = []
    broker = SimpleNamespace(
        is_market_open=lambda: market_open, opening_auction_ahead=lambda: ahead,
        get_latest_quote=lambda s: quotes.append(s) or {"ap": 999.0},
        place_order=lambda **kw: sent.append(("opg", kw["symbol"], kw["limit_price"], kw["time_in_force"],
                                              kw["order_type"])) or {"id": "opg-" + kw["symbol"]})
    engine = SimpleNamespace(
        config=SimpleNamespace(portfolio_constraints=SimpleNamespace(max_positions=None, min_cash_allocation=0.05)),
        risk_manager=SimpleNamespace(pre_trade_risk_check=lambda **kw: {"approved": True}))
    logged = []
    runner = SimpleNamespace(broker=broker, engine=engine, _phase="open",
                             portfolio=SimpleNamespace(num_positions=10, cash=60_000.0, total_value=100_000.0),
                             _log_trade=lambda **kw: logged.append(kw), _active_weight_version=lambda: 3)
    return rda, runner, sent, quotes, logged


OPPS = [{"symbol": "AAA", "confidence": 0.85, "limit_price": 100.0, "shares": 20, "stop_loss": 92.0}]


def test_before_the_open_a_buy_goes_into_the_opening_auction_with_a_gap_limit(monkeypatch):
    rda, runner, sent, quotes, logged = _runner(monkeypatch, ahead=True)
    out = rda.DailyRunner._execute_opportunities(runner, [dict(o) for o in OPPS], True, 0.65)
    assert sent == [("opg", "AAA", round(100.0 * (1 + rda.OPEN_LIMIT), 2), "opg", "limit")]
    assert quotes == []                                          # no pre-market ask: the open is the price
    assert out["submitted"] == 1 and out["details"][0]["order_id"] == "opg-AAA"
    assert logged[0]["order_id"] == "opg-AAA" and logged[0]["price"] == round(100.0 * (1 + rda.OPEN_LIMIT), 2)


def test_a_late_opening_run_falls_back_to_buying_at_the_ask(monkeypatch):
    # A missed 9:15 trigger is caught up after the open: buy the old way rather than not at all
    rda, runner, sent, quotes, logged = _runner(monkeypatch, ahead=False, market_open=True)
    rda.DailyRunner._execute_opportunities(runner, [dict(o) for o in OPPS], True, 0.65)
    assert sent == [("at_ask", "AAA", 999.0)] and quotes == ["AAA"]


def test_between_the_auction_cutoff_and_the_open_nothing_is_sent(monkeypatch):
    for ahead in (False, None):
        rda, runner, sent, quotes, logged = _runner(monkeypatch, ahead=ahead, market_open=False)
        out = rda.DailyRunner._execute_opportunities(runner, [dict(o) for o in OPPS], True, 0.65)
        assert sent == [] and out["status"] == "skipped"


def test_the_ten_oclock_run_only_manages_and_never_buys():
    import run_daily_analysis as rda
    assert rda.phase_buys("manage") is False and rda.phase_buys("open") is True and rda.phase_buys("all") is True
    assert rda.phase_exits("open") is False and rda.phase_exits("manage") is True and rda.phase_exits("all") is True


def test_the_schedule_buys_at_9_15_and_manages_at_10():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from dispatcher import LOG_WRITERS, due_workflows
    et = ZoneInfo("America/New_York")
    assert "daily_open.yml" in due_workflows(datetime(2026, 10, 19, 9, 15, 5, tzinfo=et))
    assert "daily_trade.yml" in due_workflows(datetime(2026, 10, 19, 10, 0, 5, tzinfo=et))
    assert "daily_open.yml" in LOG_WRITERS
    text = (Path(__file__).parent.parent / ".github" / "workflows" / "daily_open.yml").read_text()
    assert "--phase open" in text and "screener.py" in text
    assert "--phase manage" in (Path(__file__).parent.parent / ".github" / "workflows" / "daily_trade.yml").read_text()


def test_alpaca_clock_timestamps_with_nanoseconds_are_read():
    from alpaca_broker import AlpacaBroker
    broker = AlpacaBroker.__new__(AlpacaBroker)
    broker._request = lambda method, endpoint, **kw: {"timestamp": "2026-10-19T09:15:05.123456789-04:00", "is_open": False,
                                                      "next_open": "2026-10-19T09:30:00-04:00"}
    assert broker.opening_auction_ahead() is True
    broker._request = lambda method, endpoint, **kw: {"timestamp": "2026-10-19T13:15:05.5Z", "is_open": False,
                                                      "next_open": "2026-10-19T13:30:00Z"}
    assert broker.opening_auction_ahead() is True


def test_an_opening_run_that_missed_the_auction_raises_an_alert():
    import run_daily_analysis as rda
    skipped = {"status": "skipped", "reason": "Market is closed"}
    assert "were not sent" in rda.opening_missed_alert("open", True, [{"symbol": "AAA"}], skipped)
    assert rda.opening_missed_alert("open", True, [], skipped) is None                      # nothing to buy anyway
    assert rda.opening_missed_alert("open", True, [{"symbol": "AAA"}], {"status": "completed"}) is None
    assert rda.opening_missed_alert("manage", True, [{"symbol": "AAA"}], skipped) is None
    assert rda.opening_missed_alert("open", False, [{"symbol": "AAA"}], skipped) is None    # dry run


def test_the_ten_oclock_run_keeps_the_mornings_buys_on_the_dashboard():
    import run_daily_analysis as rda
    morning = {"generated_at": "2026-10-19T13:19:00", "todays_run": {"buy_signals": 2, "orders_submitted": 2}}
    assert rda.keep_morning_run("manage", morning, "2026-10-19") == morning["todays_run"]
    assert rda.keep_morning_run("manage", morning, "2026-10-20") is None                  # yesterday's
    assert rda.keep_morning_run("open", morning, "2026-10-19") is None
    assert rda.keep_morning_run("manage", None, "2026-10-19") is None
