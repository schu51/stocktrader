"""
Buys go in at the open (2026-10-16). Bought at 10:00 ET at the ask, 33 live
buys filled 1.1% above the open on average, two-thirds of the way up the day's
range; in the three-year backtest that kind of fill cut the return from 174%
to 108%. The first limit-on-open orders (2026-10-22, DDOG and OKTA) were
cancelled unfilled by the paper account although both opened under their
limits, so the 9:15 run now waits for the bell and buys at the ask, skipping a
stock that opened more than OPEN_LIMIT above its previous close.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


def test_seconds_to_open_counts_down_to_todays_open_only():
    from alpaca_broker import AlpacaBroker
    broker = AlpacaBroker.__new__(AlpacaBroker)
    clock = lambda now, next_open, is_open=False: (lambda method, endpoint, **kw: {
        "timestamp": now, "is_open": is_open, "next_open": next_open})
    broker._request = clock("2026-10-19T09:20:00-04:00", "2026-10-19T09:30:00-04:00")
    assert broker.seconds_to_open() == 600
    broker._request = clock("2026-10-19T10:00:00-04:00", "2026-10-20T09:30:00-04:00", is_open=True)
    assert broker.seconds_to_open() == 0                               # already open
    broker._request = clock("2026-10-17T09:15:00-04:00", "2026-10-19T09:30:00-04:00")
    assert broker.seconds_to_open() is None                            # Saturday: no open today
    broker._request = clock("2026-10-19T16:30:00-04:00", "2026-10-20T09:30:00-04:00")
    assert broker.seconds_to_open() is None                            # closed for the day
    broker._request = lambda method, endpoint, **kw: {"error": "down"}
    assert broker.seconds_to_open() is None


def _runner(monkeypatch, ahead=True, market_open=False, to_open=600, ask=101.0):
    import run_daily_analysis as rda
    sent = []
    slept = []
    monkeypatch.setattr(rda, "ALPACA_AVAILABLE", True)
    monkeypatch.setattr(rda, "_sleep", lambda s: slept.append(s))

    class Executor:
        def __init__(self, broker): pass
        def execute_decision(self, opp):
            sent.append(("at_ask", opp["symbol"], opp["limit_price"]))
            return {"status": "submitted", "order_id": "day-" + opp["symbol"]}
    monkeypatch.setattr(rda, "OrderExecutor", Executor)
    quotes = []
    broker = SimpleNamespace(
        is_market_open=lambda: market_open or bool(slept), seconds_to_open=lambda: 0 if market_open else to_open,
        get_latest_quote=lambda s: quotes.append(s) or {"ap": ask},
        place_order=lambda **kw: sent.append(("opg", kw["symbol"], kw["limit_price"], kw["time_in_force"],
                                              kw["order_type"])) or {"id": "opg-" + kw["symbol"]})
    engine = SimpleNamespace(
        config=SimpleNamespace(portfolio_constraints=SimpleNamespace(max_positions=None, min_cash_allocation=0.05)),
        risk_manager=SimpleNamespace(pre_trade_risk_check=lambda **kw: {"approved": True}))
    logged = []
    runner = SimpleNamespace(broker=broker, engine=engine, _phase="open",
                             portfolio=SimpleNamespace(num_positions=10, cash=60_000.0, total_value=100_000.0),
                             _log_trade=lambda **kw: logged.append(kw), _active_weight_version=lambda: 3)
    runner.slept = slept
    return rda, runner, sent, quotes, logged


OPPS = [{"symbol": "AAA", "confidence": 0.85, "limit_price": 100.0, "shares": 20, "stop_loss": 92.0}]


def test_before_the_open_the_run_waits_for_the_bell_then_buys_at_the_ask(monkeypatch):
    rda, runner, sent, quotes, logged = _runner(monkeypatch, to_open=600, ask=101.0)
    out = rda.DailyRunner._execute_opportunities(runner, [dict(o) for o in OPPS], True, 0.65)
    assert runner.slept == [600 + rda.OPEN_SETTLE_SECONDS]
    assert sent == [("at_ask", "AAA", 101.0)] and quotes == ["AAA"]
    assert not any(x[0] == "opg" for x in sent)                  # the paper account cancels opening-auction orders
    assert out["submitted"] == 1 and logged[0]["order_id"] == "day-AAA"


def test_the_opening_run_skips_a_stock_that_opened_too_far_above_its_previous_close(monkeypatch):
    import run_daily_analysis as rda
    rda, runner, sent, quotes, logged = _runner(monkeypatch, ask=round(100.0 * (1 + rda.OPEN_LIMIT) + 0.5, 2))
    out = rda.DailyRunner._execute_opportunities(runner, [dict(o) for o in OPPS], True, 0.65)
    assert sent == [] and out["details"][0]["status"] == "skipped" and "previous close" in out["details"][0]["reason"]


def test_a_late_opening_run_buys_at_the_ask_without_waiting(monkeypatch):
    # A missed 9:15 trigger is caught up after the open: buy the old way rather than not at all
    rda, runner, sent, quotes, logged = _runner(monkeypatch, market_open=True, ask=101.0)
    rda.DailyRunner._execute_opportunities(runner, [dict(o) for o in OPPS], True, 0.65)
    assert runner.slept == [] and sent == [("at_ask", "AAA", 101.0)] and quotes == ["AAA"]


def test_the_run_does_not_wait_when_the_open_is_far_off_or_unknown(monkeypatch):
    for to_open in (None, 60 * 60):
        rda, runner, sent, quotes, logged = _runner(monkeypatch, to_open=to_open)
        out = rda.DailyRunner._execute_opportunities(runner, [dict(o) for o in OPPS], True, 0.65)
        assert sent == [] and runner.slept == [] and out["status"] == "skipped"


def test_the_opening_workflow_has_time_to_wait_for_the_bell():
    import re
    text = (Path(__file__).parent.parent / ".github" / "workflows" / "daily_open.yml").read_text()
    assert int(re.search(r"timeout-minutes:\s*(\d+)", text).group(1)) >= 35


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
    broker._request = lambda method, endpoint, **kw: {"timestamp": "2026-10-19T09:15:00.123456789-04:00", "is_open": False,
                                                      "next_open": "2026-10-19T09:30:00-04:00"}
    assert round(broker.seconds_to_open()) == 900
    broker._request = lambda method, endpoint, **kw: {"timestamp": "2026-10-19T13:15:00.5Z", "is_open": False,
                                                      "next_open": "2026-10-19T13:30:00Z"}
    assert round(broker.seconds_to_open()) == 900


def test_an_opening_run_that_could_not_buy_at_the_open_raises_an_alert():
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
