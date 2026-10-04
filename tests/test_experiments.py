import json
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd

TODAY = date(2026, 10, 3)


def _registry(**states):
    import experiments as ex
    reg = ex.default_registry(TODAY)
    for name, state in states.items():
        reg["experiments"][name]["state"] = state
    return reg


# ── defaults ─────────────────────────────────────────────────────────────────

def test_default_registry_reflects_the_backtest_verdicts():
    reg = _registry()
    states = {k: v["state"] for k, v in reg["experiments"].items()}
    assert states == {"atr_stop": "trial", "macd_cross": "active", "ema21_reclaim": "observing",
                      "breakout": "observing", "adx25": "observing", "ma50_room": "observing",
                      "news_positive": "observing", "social_bullish": "observing",
                      "news_negative": "observing", "social_bearish": "observing", "news_veto": "trial"}


def _seed(tmp_path, **states):
    import experiments as ex
    path = tmp_path / "e.json"
    ex._write_json(path, _registry(**states))
    return path


def test_missing_registry_is_an_error_unless_explicitly_created(tmp_path):
    import pytest
    import experiments as ex
    path = tmp_path / "experiments.json"
    with pytest.raises(ex.RegistryError):
        ex.load_registry(path, TODAY)
    assert ex.load_registry(path, TODAY, create=True)["experiments"]["atr_stop"]["state"] == "trial"


def test_corrupt_registry_is_never_replaced_with_defaults(tmp_path):
    # Defaults would revive dropped experiments and restart every trial
    import pytest
    import experiments as ex
    path = tmp_path / "experiments.json"
    for garbage in ("{not json", "[]", '{"experiments": {}}', '{"experiments": {"x": {"state": "bogus"}}}'):
        path.write_text(garbage)
        for create in (False, True):
            with pytest.raises(ex.RegistryError):
                ex.load_registry(path, TODAY, create=create)


def test_hook_fails_closed_on_a_corrupt_registry(tmp_path):
    import experiments as ex
    bars = [_Bar(float(x)) for x in np.linspace(100, 180, 120)]
    (tmp_path / "e.json").write_text("{not json")
    out = ex.on_buy_signal("AMD", bars, TODAY, registry_path=tmp_path / "e.json", log_path=tmp_path / "log.json")
    assert out["blocked_by"] == ex.HOOK_FAILED and "not valid JSON" in out["error"]


# ── stop arm assignment ──────────────────────────────────────────────────────

def test_stop_arm_trial_splits_roughly_evenly_and_is_repeatable():
    import experiments as ex
    reg = _registry()
    arms = [ex.stop_arm(f"SYM{i}:2026-10-05", reg) for i in range(400)]
    assert 0.4 < arms.count("atr") / 400 < 0.6
    assert ex.stop_arm("AMD:2026-10-05", reg) == ex.stop_arm("AMD:2026-10-05", reg)


def test_stop_arm_follows_state():
    import experiments as ex
    assert {ex.stop_arm(f"S{i}:d", _registry(atr_stop="adopted")) for i in range(50)} == {"atr"}
    assert {ex.stop_arm(f"S{i}:d", _registry(atr_stop="dropped")) for i in range(50)} == {"fixed"}


# ── entry gate ───────────────────────────────────────────────────────────────

def test_entry_gate_applies_only_active_or_adopted_signals():
    import experiments as ex
    signals = {"macd_cross": False, "ema21_reclaim": False, "breakout": False, "adx25": False}
    assert ex.entry_gate(signals, _registry()) == "macd_cross"                       # active by default
    assert ex.entry_gate({**signals, "macd_cross": True}, _registry()) is None
    assert ex.entry_gate(signals, _registry(macd_cross="dropped")) is None           # observing ones never block
    assert ex.entry_gate(signals, _registry(macd_cross="dropped", breakout="adopted")) == "breakout"


def test_entry_gate_fails_closed_when_the_signal_is_unknown():
    import experiments as ex
    assert ex.entry_gate({}, _registry()) == "macd_cross"
    assert ex.entry_gate({"macd_cross": None}, _registry()) == "macd_cross"
    assert ex.entry_gate({"macd_cross": "yes"}, _registry()) == "macd_cross"     # only a real True passes
    assert ex.entry_gate({}, _registry(macd_cross="dropped")) is None           # no gate, nothing to fail


# ── live hook ────────────────────────────────────────────────────────────────

class _Bar:
    def __init__(self, c):
        self.open = self.close = c
        self.high, self.low, self.volume = c + 1, c - 1, 1_000_000


def test_on_buy_signal_logs_the_candidate_and_returns_arm_and_gate(tmp_path):
    import experiments as ex
    bars = [_Bar(float(x)) for x in np.linspace(100, 180, 120)]       # steady uptrend: no fresh MACD cross
    out = ex.on_buy_signal("AMD", bars, TODAY, registry_path=_seed(tmp_path), log_path=tmp_path / "log.json")
    assert out["blocked_by"] == "macd_cross"
    assert out["stop_arm"] in ("atr", "fixed")
    assert (out["stop_dist"] is not None) == (out["stop_arm"] == "atr")
    log = json.loads((tmp_path / "log.json").read_text())
    assert log[0]["symbol"] == "AMD" and log[0]["date"] == "2026-10-03" and log[0]["signals"]["adx25"] is True


def test_on_buy_signal_replaces_same_day_duplicates(tmp_path):
    import experiments as ex
    bars = [_Bar(float(x)) for x in np.linspace(100, 180, 120)]
    path = _seed(tmp_path)
    for _ in range(3):
        ex.on_buy_signal("AMD", bars, TODAY, registry_path=path, log_path=tmp_path / "log.json")
    assert len(json.loads((tmp_path / "log.json").read_text())) == 1


def test_on_buy_signal_never_raises_and_fails_closed_when_it_breaks(tmp_path):
    import experiments as ex
    out = ex.on_buy_signal("AMD", None, TODAY, registry_path=_seed(tmp_path), log_path=tmp_path / "log.json")
    assert out["error"]                                     # reported, so the run can raise an alert
    assert {k: out[k] for k in ("signals", "stop_arm", "stop_dist", "news_arm", "blocked_by")} == \
        {"signals": None, "stop_arm": "fixed", "stop_dist": None, "news_arm": "control", "blocked_by": ex.HOOK_FAILED}


def test_gate_still_applies_when_the_signal_log_cannot_be_written(tmp_path):
    import experiments as ex
    bars = [_Bar(float(x)) for x in np.linspace(100, 180, 120)]
    unwritable = tmp_path / "log.json"
    unwritable.mkdir()                                       # a directory where the log file should be
    out = ex.on_buy_signal("AMD", bars, TODAY, registry_path=_seed(tmp_path), log_path=unwritable)
    assert out["blocked_by"] == "macd_cross"                # the gate held
    assert "signal log" in out["error"]


# ── weekly evaluation: entry signals ─────────────────────────────────────────

def _rows(days, true_edge, name="breakout"):
    rng = np.random.default_rng(7)
    rows = []
    for d in pd.bdate_range("2026-10-05", periods=days):
        for i in range(8):
            fired = i < 4
            rows.append({"date": d.date().isoformat(), "symbol": f"S{i}", name: fired,
                         "excess_10d": (true_edge if fired else 0.0) + rng.normal(0, 0.01)})
    return pd.DataFrame(rows)


def test_observing_signal_is_promoted_on_strong_evidence():
    import experiments as ex
    reg = _registry(macd_cross="dropped")
    reg, changes = ex.evaluate(reg, {"breakout": _rows(60, 0.03)}, [], TODAY)
    assert reg["experiments"]["breakout"]["state"] == "active"
    assert changes[0]["experiment"] == "breakout" and changes[0]["to"] == "active"


def test_only_one_entry_gate_is_active_at_a_time():
    import experiments as ex
    reg = _registry()                                         # macd_cross already active
    reg, changes = ex.evaluate(reg, {"breakout": _rows(60, 0.03)}, [], TODAY)
    assert reg["experiments"]["breakout"]["state"] == "observing"
    assert "already active" in reg["experiments"]["breakout"]["evidence"]["note"]


def test_too_little_history_changes_nothing():
    import experiments as ex
    reg, changes = ex.evaluate(_registry(), {"macd_cross": _rows(10, -0.05, "macd_cross")}, [], TODAY)
    assert reg["experiments"]["macd_cross"]["state"] == "active" and changes == []


def test_active_gate_is_dropped_when_it_picks_worse_names():
    import experiments as ex
    reg, changes = ex.evaluate(_registry(), {"macd_cross": _rows(60, -0.03, "macd_cross")}, [], TODAY)
    assert reg["experiments"]["macd_cross"]["state"] == "dropped"
    assert "worse" in changes[0]["reason"]


def test_active_gate_is_adopted_when_it_proves_itself():
    import experiments as ex
    reg, _ = ex.evaluate(_registry(), {"macd_cross": _rows(60, 0.03, "macd_cross")}, [], TODAY)
    assert reg["experiments"]["macd_cross"]["state"] == "adopted"


def test_signal_that_never_proves_itself_is_dropped_at_the_deadline():
    import experiments as ex
    reg, changes = ex.evaluate(_registry(), {"macd_cross": _rows(ex.MAX_SIGNAL_DAYS + 5, 0.0, "macd_cross"),
                                             "adx25": _rows(ex.MAX_SIGNAL_DAYS + 5, 0.0, "adx25")}, [], TODAY)
    assert reg["experiments"]["macd_cross"]["state"] == "dropped"
    assert reg["experiments"]["adx25"]["state"] == "dropped"
    assert all("did not" in c["reason"] for c in changes)


def test_dropped_is_terminal():
    import experiments as ex
    reg, changes = ex.evaluate(_registry(breakout="dropped", macd_cross="dropped"), {"breakout": _rows(60, 0.05)}, [], TODAY)
    assert reg["experiments"]["breakout"]["state"] == "dropped" and changes == []


# ── weekly evaluation: stop arm ──────────────────────────────────────────────

def _arm_trades(n, atr_mean, fixed_mean, sd=4.0):
    rng = np.random.default_rng(11)
    out = []
    for arm, mean in (("atr", atr_mean), ("fixed", fixed_mean)):
        for i in range(n):
            out.append({"status": "CLOSED", "stop_arm": arm, "entry_date": "2026-10-10",
                        "pnl_pct": float(mean + rng.normal(0, sd))})
    return out


def test_stop_trial_waits_for_enough_closed_trades_per_arm():
    import experiments as ex
    reg, changes = ex.evaluate(_registry(), {}, _arm_trades(5, 10, -10), TODAY)
    assert reg["experiments"]["atr_stop"]["state"] == "trial" and changes == []


def test_stop_trial_adopts_a_clear_winner_and_drops_a_clear_loser():
    import experiments as ex
    reg, _ = ex.evaluate(_registry(), {}, _arm_trades(30, 6, 0), TODAY)
    assert reg["experiments"]["atr_stop"]["state"] == "adopted"
    reg, _ = ex.evaluate(_registry(), {}, _arm_trades(30, -4, 2), TODAY)
    assert reg["experiments"]["atr_stop"]["state"] == "dropped"


def test_stop_trial_without_a_result_ends_at_the_cap():
    import experiments as ex
    pnl = [(-5.0, 9.0, 1.0)[i % 3] for i in range(ex.MAX_ARM_TRADES)]        # identical outcomes in both arms
    trades = [{"status": "CLOSED", "stop_arm": arm, "entry_date": "2026-10-10", "pnl_pct": p}
              for arm in ("atr", "fixed") for p in pnl]
    reg, changes = ex.evaluate(_registry(), {}, trades, TODAY)
    assert reg["experiments"]["atr_stop"]["state"] == "dropped"
    assert "did not" in changes[0]["reason"]


def test_trades_before_the_trial_started_or_without_an_arm_are_ignored():
    import experiments as ex
    old = [dict(t, entry_date="2026-01-01") for t in _arm_trades(30, -4, 2)]
    untagged = [{"status": "CLOSED", "entry_date": "2026-10-10", "pnl_pct": -50.0}] * 40
    reg, changes = ex.evaluate(_registry(), {}, old + untagged, TODAY)
    assert reg["experiments"]["atr_stop"]["state"] == "trial" and changes == []


# ── news and social sentiment ────────────────────────────────────────────────

def test_hook_attaches_todays_logged_sentiment(tmp_path):
    import experiments as ex
    bars = [_Bar(float(x)) for x in np.linspace(100, 180, 120)]
    sent = tmp_path / "sentiment_log.json"
    sent.write_text(json.dumps([{"date": "2026-10-03", "symbol": "AMD", "news_positive": True, "news_negative": False,
                                 "social_bullish": False, "news_score": 0.4, "st_bull_ratio": 0.5}]))
    out = ex.on_buy_signal("AMD", bars, TODAY, registry_path=_seed(tmp_path), log_path=tmp_path / "log.json",
                           sentiment_path=sent)
    assert (out["signals"]["news_positive"], out["signals"]["social_bullish"]) == (True, False)
    assert out["signals"]["news_score"] == 0.4
    assert out["blocked_by"] == "macd_cross"                 # observing signals never gate


def test_missing_or_malformed_sentiment_is_unknown_not_false(tmp_path):
    import experiments as ex
    bars = [_Bar(float(x)) for x in np.linspace(100, 180, 120)]
    sent = tmp_path / "sentiment_log.json"
    for content in ("[]", "{not json", json.dumps([{"date": "2026-10-03", "symbol": "AMD", "news_positive": "yes",
                                                   "news_score": "high"}])):
        sent.write_text(content)
        out = ex.on_buy_signal("AMD", bars, TODAY, registry_path=_seed(tmp_path), log_path=tmp_path / "log.json",
                               sentiment_path=sent)
        assert out["signals"]["news_positive"] is None and out["signals"]["news_score"] is None
        assert out.get("error") is None                      # a missing scan must not stop trading


def test_scored_log_has_a_column_for_each_registered_signal():
    import experiments as ex
    log = [{"date": "2026-10-05", "symbol": "AMD", "signals": {"news_positive": True, "macd_cross": False}}]
    frame = ex.scored_log(log, ["macd_cross", "news_positive"], {("2026-10-05", "AMD"): 0.03})
    assert frame.iloc[0].to_dict() == {"date": "2026-10-05", "symbol": "AMD", "excess_10d": 0.03,
                                       "news_veto": None, "macd_cross": False, "news_positive": True}


def test_sentiment_report_measures_whether_bad_news_names_did_worse():
    import experiments as ex
    rows, excess = [], {}
    for d in pd.bdate_range("2026-10-05", periods=30):
        for i in range(8):
            day, sym = d.date().isoformat(), f"S{i}"
            rows.append({"date": day, "symbol": sym, "news_score": (i - 3.5) / 4, "st_bull_ratio": 0.5 + i / 20,
                         "news_negative": i < 2})
            excess[(day, sym)] = 0.01 * (i - 3.5) + 0.001 * ((d.day + i) % 3)
    report = ex.sentiment_report(ex.sentiment_frame(rows, excess))
    assert report["days"] == 30 and report["enough_history"]
    assert report["news_score"]["ic"] > 0.8
    assert report["news_negative_vs_rest"]["mean"] < 0       # negative-news names underperformed
    assert abs(report["weights"]["news_score"] + report["weights"]["st_bull_ratio"] - 1) < 0.01
    empty = ex.sentiment_report(ex.sentiment_frame([], {}))
    assert empty["rows"] == 0 and empty["weights"]["news_score"] == 0.5


def test_sentiment_signals_promote_themselves_but_need_more_evidence():
    # No manual approval: the system decides. Public text gets a higher bar than price data.
    import experiments as ex
    assert ex.promotion_bar("breakout", {}) == (ex.MIN_SIGNAL_DAYS, ex.T_ADOPT)
    assert ex.promotion_bar("news_positive", {}) == (ex.EXTERNAL_MIN_DAYS, ex.EXTERNAL_T_ADOPT)
    assert ex.EXTERNAL_MIN_DAYS > ex.MIN_SIGNAL_DAYS and ex.EXTERNAL_T_ADOPT > ex.T_ADOPT

    early = _registry(macd_cross="dropped")
    early, changes = ex.evaluate(early, {"news_positive": _rows(45, 0.05, "news_positive")}, [], TODAY)
    assert early["experiments"]["news_positive"]["state"] == "observing" and changes == []   # strong, but too soon

    later = _registry(macd_cross="dropped")
    later, changes = ex.evaluate(later, {"news_positive": _rows(65, 0.05, "news_positive")}, [], TODAY)
    assert later["experiments"]["news_positive"]["state"] == "active"
    assert changes[0]["to"] == "active"


def test_sentiment_signals_are_still_dropped_automatically():
    import experiments as ex
    reg, _ = ex.evaluate(_registry(), {"news_positive": _rows(ex.MAX_SIGNAL_DAYS + 5, 0.0, "news_positive")}, [], TODAY)
    assert reg["experiments"]["news_positive"]["state"] == "dropped"


def test_the_registry_can_switch_promotion_off_but_never_on():
    import experiments as ex
    assert ex.promotion_bar("news_positive", {"auto_promote": False}) is None
    assert ex.promotion_bar("breakout", {"auto_promote": False}) is None
    for granted in (True, "true", 1):
        assert ex.promotion_bar("mystery", {"auto_promote": granted}) is None      # unknown name: never
    reg = _registry(macd_cross="dropped")
    reg["experiments"]["mystery"] = {"kind": "entry_signal", "state": "observing", "started": "2026-10-03",
                                     "auto_promote": True}
    reg, _ = ex.evaluate(reg, {"mystery": _rows(80, 0.05, "mystery")}, [], TODAY)
    assert reg["experiments"]["mystery"]["state"] == "observing"


def test_the_registry_can_still_restrict_a_technical_signal():
    import experiments as ex
    reg = _registry(macd_cross="dropped")
    reg["experiments"]["breakout"]["auto_promote"] = False
    reg, _ = ex.evaluate(reg, {"breakout": _rows(60, 0.03)}, [], TODAY)
    assert reg["experiments"]["breakout"]["state"] == "observing"


def test_technical_signals_still_promote_automatically():
    import experiments as ex
    reg, _ = ex.evaluate(_registry(macd_cross="dropped"), {"breakout": _rows(60, 0.03)}, [], TODAY)
    assert reg["experiments"]["breakout"]["state"] == "active"


# ── bearish signals (the put case) ───────────────────────────────────────────

def test_bearish_signal_is_confirmed_when_flagged_stocks_lag():
    import experiments as ex
    reg, changes = ex.evaluate(_registry(), {}, [], TODAY,
                               sentiment_rows={"news_negative": _rows(60, -0.03, "news_negative")})
    assert reg["experiments"]["news_negative"]["state"] == "confirmed"
    assert "lagged" in changes[0]["reason"]


def test_bearish_signal_is_dropped_when_flagged_stocks_do_not_lag():
    import experiments as ex
    reg, _ = ex.evaluate(_registry(), {}, [], TODAY,
                         sentiment_rows={"social_bearish": _rows(ex.MAX_SIGNAL_DAYS + 5, 0.0, "social_bearish")})
    assert reg["experiments"]["social_bearish"]["state"] == "dropped"
    reg, _ = ex.evaluate(_registry(news_negative="confirmed"), {}, [], TODAY,
                         sentiment_rows={"news_negative": _rows(60, 0.03, "news_negative")})
    assert reg["experiments"]["news_negative"]["state"] == "dropped"         # confirmed, then stopped working


def test_bearish_signal_never_gates_a_buy():
    import experiments as ex
    reg = _registry(macd_cross="dropped", news_negative="confirmed")
    assert ex.entry_gate({"news_negative": True}, reg) is None


# ── news A/B on real buys ────────────────────────────────────────────────────

def test_news_arm_splits_evenly_and_independently_of_the_stop_arm():
    import experiments as ex
    reg = _registry()
    keys = [f"SYM{i}:2026-10-05" for i in range(600)]
    news = [ex.news_arm(k, reg) == "news" for k in keys]
    atr = [ex.stop_arm(k, reg) == "atr" for k in keys]
    assert 0.42 < sum(news) / 600 < 0.58
    both = sum(1 for n, a in zip(news, atr) if n and a) / 600
    assert 0.18 < both < 0.32                                    # ~25% if the two splits are unrelated
    assert {ex.news_arm(k, _registry(news_veto="dropped")) for k in keys} == {"control"}
    assert {ex.news_arm(k, _registry(news_veto="adopted")) for k in keys} == {"news"}


def test_veto_blocks_only_in_the_news_arm_and_only_on_bad_sentiment(tmp_path):
    import experiments as ex
    bars = [_Bar(float(x)) for x in np.linspace(100, 180, 120)]
    sent = tmp_path / "sentiment_log.json"
    path = _seed(tmp_path, macd_cross="dropped", news_veto="adopted")        # everything in the news arm, no other gate
    sent.write_text(json.dumps([{"date": "2026-10-03", "symbol": "BAD", "news_negative": True, "social_bearish": False},
                                {"date": "2026-10-03", "symbol": "OK", "news_negative": False, "social_bearish": False}]))
    args = dict(registry_path=path, log_path=tmp_path / "log.json", sentiment_path=sent)
    assert ex.on_buy_signal("BAD", bars, TODAY, **args)["blocked_by"] == "news_veto"
    assert ex.on_buy_signal("OK", bars, TODAY, **args)["blocked_by"] is None
    assert ex.on_buy_signal("UNSEEN", bars, TODAY, **args)["blocked_by"] is None     # no data is not a veto
    log = {r["symbol"]: r for r in json.loads((tmp_path / "log.json").read_text())}
    assert log["BAD"]["news_veto"] is True and log["OK"]["news_veto"] is False       # logged either way, for the shadow A/B
    control = _seed(tmp_path, macd_cross="dropped", news_veto="dropped")
    out = ex.on_buy_signal("BAD", bars, TODAY, registry_path=control, log_path=tmp_path / "log.json", sentiment_path=sent)
    assert out["blocked_by"] is None and out["news_arm"] == "control"


def _news_trades(n, news_mean, control_mean):
    rng = np.random.default_rng(5)
    return [{"status": "CLOSED", "news_arm": arm, "entry_date": "2026-10-10", "pnl_pct": float(mean + rng.normal(0, 4))}
            for arm, mean in (("news", news_mean), ("control", control_mean)) for _ in range(n)]


def test_news_arm_is_adopted_or_dropped_on_closed_trades():
    import experiments as ex
    reg, _ = ex.evaluate(_registry(), {}, _news_trades(5, 10, -10), TODAY)
    assert reg["experiments"]["news_veto"]["state"] == "trial"                       # too few trades
    reg, changes = ex.evaluate(_registry(), {}, _news_trades(30, 6, 0), TODAY)
    assert reg["experiments"]["news_veto"]["state"] == "adopted"
    reg, _ = ex.evaluate(_registry(), {}, _news_trades(30, -4, 2), TODAY)
    assert reg["experiments"]["news_veto"]["state"] == "dropped"


def test_shadow_ab_compares_technical_only_with_technical_plus_news():
    import experiments as ex
    rows = []
    for d in pd.bdate_range("2026-10-05", periods=30):
        for i in range(8):
            vetoed = i < 2
            rows.append({"date": d.date().isoformat(), "symbol": f"S{i}", "news_veto": vetoed,
                         "excess_10d": (-0.04 if vetoed else 0.01) + 0.001 * ((d.day + i) % 3)})
    ab = ex.news_ab_shadow(pd.DataFrame(rows))
    assert ab["signals"] == 240 and ab["vetoed_share"] == 0.25
    assert ab["technical_plus_news"] > ab["technical_only"]              # dropping the vetoed names helped
    assert ab["gain_from_news"] > 0 and ab["t"] > 2
    assert ex.news_ab_shadow(pd.DataFrame())["signals"] == 0


def test_source_weights_follow_the_evidence():
    import experiments as ex
    report = {"news_score": {"days": 40, "t": 3.0}, "st_bull_ratio": {"days": 40, "t": 1.0}}
    assert ex.source_weights(report) == {"basis": "evidence", "news_score": 0.75, "st_bull_ratio": 0.25}
    report = {"news_score": {"days": 40, "t": 2.0}, "st_bull_ratio": {"days": 40, "t": -1.5}}
    assert ex.source_weights(report)["st_bull_ratio"] == 0.0             # a source that points the wrong way earns nothing
    thin = {"news_score": {"days": 5, "t": 3.0}, "st_bull_ratio": {"days": 5, "t": 1.0}}
    assert ex.source_weights(thin)["basis"].startswith("equal")
    undefined = {"news_score": {"days": 40, "t": float("nan")}, "st_bull_ratio": {"days": 40, "t": 1.0}}
    assert ex.source_weights(undefined) == {"basis": "equal (not enough evidence yet)", "news_score": 0.5, "st_bull_ratio": 0.5}


def test_news_can_block_only_a_few_buys_a_day(tmp_path):
    # Bound on what public text can do: a flood of bad sentiment cannot stop all buying
    import experiments as ex
    bars = [_Bar(float(x)) for x in np.linspace(100, 180, 120)]
    path = _seed(tmp_path, macd_cross="dropped", news_veto="adopted")
    symbols = [f"BAD{c}" for c in "ABCDEF"]
    sent = tmp_path / "sentiment_log.json"
    sent.write_text(json.dumps([{"date": "2026-10-03", "symbol": sym, "news_negative": True} for sym in symbols]))
    outs = [ex.on_buy_signal(sym, bars, TODAY, registry_path=path, log_path=tmp_path / "log.json", sentiment_path=sent)
            for sym in symbols]
    assert [o["blocked_by"] for o in outs] == ["news_veto"] * ex.MAX_NEWS_VETOES_PER_DAY + [None] * 3
    log = json.loads((tmp_path / "log.json").read_text())
    assert all(r["news_veto"] is True for r in log)                   # still recorded as vetoed, for the measurement
    again = ex.on_buy_signal("BADA", bars, TODAY, registry_path=path, log_path=tmp_path / "log.json", sentiment_path=sent)
    assert again["blocked_by"] == "news_veto"                         # a re-run gives the same answer for the same stock
