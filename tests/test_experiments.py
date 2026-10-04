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
                      "news_positive": "observing", "social_bullish": "observing"}


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
    assert {k: out[k] for k in ("signals", "stop_arm", "stop_dist", "blocked_by")} == \
        {"signals": None, "stop_arm": "fixed", "stop_dist": None, "blocked_by": ex.HOOK_FAILED}


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
                                       "macd_cross": False, "news_positive": True}


def test_sentiment_report_measures_whether_bad_news_names_did_worse():
    import experiments as ex
    rows, excess = [], {}
    for d in pd.bdate_range("2026-10-05", periods=30):
        for i in range(8):
            day, sym = d.date().isoformat(), f"S{i}"
            rows.append({"date": day, "symbol": sym, "news_score": (i - 3.5) / 4, "st_bull_ratio": 0.5 + i / 20,
                         "news_negative": i < 2})
            excess[(day, sym)] = 0.01 * (i - 3.5) + 0.001 * ((d.day + i) % 3)
    report = ex.sentiment_report(rows, excess)
    assert report["days"] == 30 and report["enough_history"]
    assert report["news_score"]["ic"] > 0.8
    assert report["negative_news_vs_rest"]["mean"] < 0       # negative-news names underperformed
    assert ex.sentiment_report([], {})["rows"] == 0


def test_externally_driven_signals_are_never_promoted_automatically():
    import experiments as ex
    reg = _registry(macd_cross="dropped")                       # the gate slot is free
    assert reg["experiments"]["news_positive"]["auto_promote"] is False
    assert reg["experiments"]["social_bullish"]["auto_promote"] is False
    reg, changes = ex.evaluate(reg, {"news_positive": _rows(60, 0.05, "news_positive"),
                                     "social_bullish": _rows(60, 0.05, "social_bullish")}, [], TODAY)
    assert reg["experiments"]["news_positive"]["state"] == "observing"
    assert reg["experiments"]["social_bullish"]["state"] == "observing"
    assert changes == []
    assert "manual approval" in reg["experiments"]["news_positive"]["evidence"]["note"]


def test_externally_driven_signals_are_still_dropped_automatically():
    import experiments as ex
    reg, changes = ex.evaluate(_registry(), {"news_positive": _rows(ex.MAX_SIGNAL_DAYS + 5, 0.0, "news_positive")}, [], TODAY)
    assert reg["experiments"]["news_positive"]["state"] == "dropped"


def test_a_malformed_auto_promote_value_does_not_enable_promotion():
    import experiments as ex
    reg = _registry(macd_cross="dropped")
    reg["experiments"]["news_positive"]["auto_promote"] = "true"      # not the boolean True
    reg, _ = ex.evaluate(reg, {"news_positive": _rows(60, 0.05, "news_positive")}, [], TODAY)
    assert reg["experiments"]["news_positive"]["state"] == "observing"


def test_technical_signals_still_promote_automatically():
    import experiments as ex
    reg, _ = ex.evaluate(_registry(macd_cross="dropped"), {"breakout": _rows(60, 0.03)}, [], TODAY)
    assert reg["experiments"]["breakout"]["state"] == "active"
