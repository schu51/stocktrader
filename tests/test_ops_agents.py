"""Ops monitor, scope audit and trade attribution."""
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import ops_monitor as om
import scope_audit as sa
import trade_attribution as ta

ET = ZoneInfo("America/New_York")


# ── scope audit ──────────────────────────────────────────────────────────────

def test_a_job_writing_its_own_files_is_in_scope():
    assert sa.classify("Portfolio sync [skip ci]", ["docs/data/latest.json"], 1) is None
    assert sa.classify("Intraday exit check [skip ci]", ["docs/data/trades.json", "docs/data/decisions/2026-11-11/x.json"], 1) is None


def test_the_legacy_postmarket_overwrite_is_a_breach():
    f = sa.classify("Post-market 2026-11-09: 15 intact, 2 weakening", ["docs/data/postmarket.json"], 1)
    assert f["kind"] == "breach" and f["job"] == "unknown writer"


def test_a_job_writing_outside_its_lane_is_a_breach():
    f = sa.classify("Portfolio sync [skip ci]", ["docs/data/latest.json", "docs/data/trades.json"], 1)
    assert f["kind"] == "breach" and f["paths"] == ["docs/data/trades.json"]


def test_code_on_main_without_a_pr_is_flagged_and_merges_are_not():
    assert sa.classify("Model: break-even stop from +5%", ["exit_logic.py"], 1)["kind"] == "direct"
    assert sa.classify("Merge pull request #27 from schu51/x", ["contracts.py"], 2) is None
    # a code commit whose message starts like a job's is not mistaken for the job
    assert sa.classify("Diamond sleeve: trim holdings", ["diamond_sleeve.py"], 1)["kind"] == "direct"


# ── ops monitor ──────────────────────────────────────────────────────────────

def test_jobs_check_reds_on_trading_path_failures_and_misses():
    runs = [{"workflow": "intraday_exit.yml", "conclusion": "failure"}, {"workflow": "osint_brief.yml", "conclusion": "success"}]
    assert om.check_jobs(runs, ["osint_brief.yml"])["status"] == "red"
    assert om.check_jobs([{"workflow": "macro_research.yml", "conclusion": "failure"}], [])["status"] == "amber"
    assert om.check_jobs([{"workflow": "daily_trade.yml", "conclusion": "success"}], ["daily_trade.yml", "postmarket.yml"])["status"] == "red"
    assert om.check_jobs([{"workflow": "daily_trade.yml", "conclusion": "success"}], ["daily_trade.yml"])["status"] == "green"


def test_freshness_only_expects_outputs_whose_time_has_passed():
    files = {"osint_latest.json": {"date": "2026-11-11"}, "premarket.json": {"generated_at": "2026-11-11T09:00:01"},
             "screener.json": {"generated_at": "2026-11-10T09:16:00"}}
    at_10 = datetime(2026, 11, 11, 10, 0, tzinfo=ET)
    c = om.check_freshness(at_10, files.get)
    assert c["status"] == "red" and any("screener.json" in i for i in c["items"])
    files["screener.json"] = {"generated_at": "2026-11-11T14:16:00+00:00"}
    assert om.check_freshness(at_10, files.get)["status"] == "green"
    assert om.check_freshness(datetime(2026, 11, 14, 12, 0, tzinfo=ET), {}.get)["status"] == "green"   # Saturday


def test_model_check_catches_the_lapsed_credit_and_stale_theses():
    brief = {"status": "error", "error": "Error code: 400 - Your credit balance is too low to access the Anthropic API"}
    theses = {"theses": [{"id": "TH-1", "last_validated": "2026-10-31"}]}
    c = om.check_model(brief, theses, date(2026, 11, 11))
    assert c["status"] == "red" and "credit" in c["detail"] and any("not re-checked" in i for i in c["items"])
    assert om.check_model({"status": "ok"}, {"theses": [{"id": "x", "last_validated": "2026-11-10"}]}, date(2026, 11, 11))["status"] == "green"


def test_overall_status_is_the_worst_check(tmp_path):
    (tmp_path / "trades.json").write_text("[]")
    r = om.run(now=datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc), runs_fn=lambda now: [],
               issues_fn=lambda: [{"number": 3, "title": "🚨 Dispatcher failed", "created_at": "2026-10-14T00:00:00Z"}],
               audit_fn=lambda: [], data_dir=tmp_path)
    assert r["status"] == "amber" and r["amber"] == ["issues"]


def test_a_check_that_crashes_is_amber_not_silent(tmp_path):
    def boom(now):
        raise RuntimeError("gh not authenticated")
    r = om.run(now=datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc), runs_fn=boom, issues_fn=lambda: [],
               audit_fn=lambda: [], data_dir=tmp_path)
    jobs = next(c for c in r["checks"] if c["name"] == "jobs")
    assert jobs["status"] == "amber" and "could not run" in jobs["detail"]


# ── trade attribution ────────────────────────────────────────────────────────

def _trade(sym, entry, exit_, pnl, reason="PRICE_BELOW_50MA", hold=1, stop=None, px=None):
    return {"symbol": sym, "status": "CLOSED", "entry_date": entry, "exit_date": exit_, "pnl_pct": pnl,
            "pnl_usd": pnl * 10, "hold_days": hold, "exit_reason": reason, "stop_loss": stop, "exit_price": px}


def test_tags_and_same_window_backtest():
    trades = [_trade("A", "2026-10-01", "2026-10-02", -5.0),
              _trade("B", "2026-10-05", "2026-10-20", 12.0, hold=11),
              _trade("C", "2026-10-07", "2026-10-09", -20.0, reason="BROKER_STOP", hold=2, stop=50.0, px=40.0)]
    bt = [{"symbol": "B", "entry_date": "2026-10-06", "pnl_pct": "8.0"}, {"symbol": "Z", "entry_date": "2026-10-03", "pnl_pct": "-2"},
          {"symbol": "Y", "entry_date": "2025-01-01", "pnl_pct": "50"}]
    tagged = ta.tag(trades, bt, {"B": ["2026-10-15"]})
    by = {t["symbol"]: t["tags"] for t in tagged}
    assert by["A"]["exit"] == "trend_break" and by["A"]["quick_fail"] is True
    assert by["C"]["exit"] == "stop_gapped"
    assert by["B"]["earnings"] is True and by["B"]["backtest"] == "matched"
    s = ta.summarize(tagged, {"bt": bt})
    assert s["same_window_backtest"]["bt"]["trades"] == 2            # the 2025 trade is outside the live window
    assert s["matched"]["trades"] == 1 and s["by_tag"]["quick_fail"]["True"]["win_rate"] == 0.0
    assert any("same window" in f for f in ta.findings(s))


def test_entry_context_reads_the_screen_the_trade_was_bought_off(tmp_path):
    import json
    import pit
    store = pit.Store(tmp_path)
    store.add("screener.json", json.dumps({"generated_at": "x", "market_regime": {"regime": "bull"},
                                           "candidates": [{"symbol": "A", "price": 120.0, "sma50": 100.0, "rs_rank": 95}]}).encode(),
              "2026-10-01T13:16:00Z")
    ctx = ta.entry_context({"symbol": "A", "entry_date": "2026-10-01", "entry_ts": "2026-10-01T14:30:00+00:00"}, store)
    assert ctx == {"regime": "bull", "extension": 0.2, "rs_rank_at_screen": 95}
