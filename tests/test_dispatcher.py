import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).parent.parent))

ET = ZoneInfo("America/New_York")


def _due(y, m, d, hh, mm, ss=0):
    from dispatcher import due_workflows
    return due_workflows(datetime(y, m, d, hh, mm, ss, tzinfo=ET))


# 2026-10-05 is a Monday, 2026-10-03 a Saturday, 2026-10-04 a Sunday.

def test_premarket_and_sync_at_0900():
    assert _due(2026, 10, 5, 9, 0) == ["premarket.yml", "portfolio_sync.yml"]


def test_stop_placement_at_open():
    assert _due(2026, 10, 5, 9, 30) == ["stop_placement.yml", "portfolio_sync.yml"]


def test_daily_trade_at_1000():
    assert _due(2026, 10, 5, 10, 0) == ["daily_trade.yml", "portfolio_sync.yml"]


def test_intraday_exit_on_quarter_hours_during_market():
    assert _due(2026, 10, 5, 9, 45) == ["intraday_exit.yml"]
    assert _due(2026, 10, 5, 15, 45) == ["intraday_exit.yml"]
    assert _due(2026, 10, 5, 9, 15) == []      # before the open
    assert _due(2026, 10, 5, 16, 45) == []     # after the close


def test_postmarket_at_1615():
    assert _due(2026, 10, 5, 16, 15) == ["postmarket.yml"]


def test_sync_window_edges():
    assert _due(2026, 10, 5, 8, 30) == []
    assert _due(2026, 10, 5, 18, 30) == ["portfolio_sync.yml"]
    assert _due(2026, 10, 5, 19, 0) == []


def test_saturday_chain_runs_in_dependency_order():
    assert _due(2026, 10, 3, 8, 0) == ["candidate_outcomes.yml"]
    assert _due(2026, 10, 3, 8, 15) == ["experiments.yml"]
    assert _due(2026, 10, 3, 8, 30) == ["learning.yml"]
    assert _due(2026, 10, 3, 9, 0) == ["macro_research.yml"]
    assert _due(2026, 10, 3, 9, 30) == ["weekly_review.yml"]
    assert _due(2026, 10, 3, 10, 0) == []


def test_sunday_runs_nothing():
    for hh in range(24):
        for mm in (0, 15, 30, 45):
            assert _due(2026, 10, 4, hh, mm) == []


def test_late_start_still_lands_in_its_slot():
    # cron-job.org fires a few seconds late and the runner takes time to start
    assert _due(2026, 10, 5, 10, 0, 9) == ["daily_trade.yml", "portfolio_sync.yml"]
    assert _due(2026, 10, 5, 10, 14, 59) == ["daily_trade.yml", "portfolio_sync.yml"]


def test_trade_runs_exactly_once_per_weekday():
    runs = sum(
        _due(2026, 10, 5, hh, mm).count("daily_trade.yml")
        for hh in range(24) for mm in (0, 15, 30, 45)
    )
    assert runs == 1


def test_utc_input_follows_new_york_across_dst():
    from dispatcher import due_workflows
    # EDT: 10:00 ET is 14:00 UTC.  After the Nov 1 clock change it is 15:00 UTC.
    assert "daily_trade.yml" in due_workflows(datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc))
    assert "daily_trade.yml" in due_workflows(datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc))
    assert "daily_trade.yml" not in due_workflows(datetime(2026, 11, 2, 14, 0, tzinfo=timezone.utc))


def test_parse_now_accepts_github_timestamp():
    from dispatcher import parse_now
    assert parse_now("2026-10-05T14:00:11Z") == datetime(2026, 10, 5, 14, 0, 11, tzinfo=timezone.utc)


def test_slot_start_floors_to_quarter_hour_utc():
    from dispatcher import slot_start
    assert slot_start(datetime(2026, 10, 5, 10, 14, 59, tzinfo=ET)) == datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc)


def test_dispatch_skips_workflow_already_started_in_slot():
    from dispatcher import dispatch
    started = []
    failed = dispatch(
        ["daily_trade.yml", "portfolio_sync.yml"], datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc), "main",
        already_started=lambda wf, since: wf == "daily_trade.yml",
        start=lambda wf, ref: started.append(wf) or True,
    )
    assert started == ["portfolio_sync.yml"]
    assert failed == 0


def test_dispatch_does_not_start_when_duplicate_check_fails():
    from dispatcher import dispatch
    started = []

    def broken(wf, since):
        raise RuntimeError("gh unavailable")

    failed = dispatch(["daily_trade.yml"], datetime(2026, 10, 5, 14, 0, tzinfo=timezone.utc), "main",
                      already_started=broken, start=lambda wf, ref: started.append(wf) or True)
    assert started == []
    assert failed == 1


def test_dispatch_counts_start_failures():
    from dispatcher import dispatch
    failed = dispatch(["premarket.yml"], datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc), "main",
                      already_started=lambda wf, since: False, start=lambda wf, ref: False)
    assert failed == 1


def test_selftest_starts_only_the_harmless_workflow(monkeypatch):
    import dispatcher
    calls = []
    monkeypatch.setattr(dispatcher, "dispatch", lambda due, since, ref: calls.append(due) or 0)
    monkeypatch.setattr(sys, "argv", ["dispatcher.py", "--selftest", "--at", "2026-10-05T14:00:11Z"])
    assert dispatcher.main() == 0
    assert calls == [["learning.yml"]]   # not daily_trade, though 10:00 ET is its slot


def test_selftest_workflow_is_not_an_order_placing_job():
    import dispatcher
    assert dispatcher.SELFTEST_WORKFLOW not in {
        "daily_trade.yml", "stop_placement.yml", "intraday_exit.yml", "postmarket.yml"}


# ── catch-up after a missed trigger ──────────────────────────────────────────

def _catch_up(y, m, d, hh, mm):
    from dispatcher import catch_up
    return [(wf, since.astimezone(ET).strftime("%H:%M")) for wf, since in catch_up(datetime(y, m, d, hh, mm, 5, tzinfo=ET))]


def test_missed_daily_trade_slot_is_caught_up_by_the_next_trigger():
    # cron-job.org timed out at 10:00; the 10:15 trigger must still start the trade run
    assert ("daily_trade.yml", "10:00") in _catch_up(2026, 10, 5, 10, 15)
    assert ("daily_trade.yml", "10:00") in _catch_up(2026, 10, 5, 10, 45)


def test_catch_up_window_closes():
    assert all(wf != "daily_trade.yml" for wf, _ in _catch_up(2026, 10, 5, 11, 0))
    assert all(wf != "daily_trade.yml" for wf, _ in _catch_up(2026, 10, 5, 12, 0))


def test_catch_up_ignores_repeating_jobs_and_the_current_slot():
    caught = [wf for wf, _ in _catch_up(2026, 10, 5, 10, 15)]
    assert "portfolio_sync.yml" not in caught and "intraday_exit.yml" not in caught   # they run again anyway
    assert all(wf != "daily_trade.yml" for wf, _ in _catch_up(2026, 10, 5, 10, 0))    # that is the normal slot


def test_catch_up_covers_the_saturday_chain_and_never_crosses_days():
    assert ("candidate_outcomes.yml", "08:00") in _catch_up(2026, 10, 3, 8, 15)
    assert _catch_up(2026, 10, 4, 0, 15) == []                                         # Sunday: nothing from Saturday
    assert _catch_up(2026, 10, 5, 0, 0) == []


def test_run_due_starts_missed_jobs_once_and_skips_ones_that_did_run():
    from dispatcher import run_due
    started = []
    ran_already = {"premarket.yml"}                      # 09:00 slot ran fine; 09:30 and 10:00 were missed
    failed = run_due(
        datetime(2026, 10, 5, 10, 15, 5, tzinfo=ET), "main",
        already_started=lambda wf, since: wf in ran_already,
        start=lambda wf, ref: started.append(wf) or True)
    assert failed == 0
    assert started == ["stop_placement.yml", "daily_trade.yml", "intraday_exit.yml"]   # missed ones first, then this slot


def test_run_due_on_a_normal_slot_starts_exactly_that_slot():
    from dispatcher import run_due
    started = []
    run_due(datetime(2026, 10, 5, 10, 0, 5, tzinfo=ET), "main",
            already_started=lambda wf, since: wf in {"premarket.yml", "stop_placement.yml"},
            start=lambda wf, ref: started.append(wf) or True)
    assert started == ["daily_trade.yml", "portfolio_sync.yml"]
