"""50/200-day crossover signals: golden cross (entry test) and death cross (exit test)."""
import numpy as np
import pytest

from backtest import trend_line
from technical_signals import compute_signals, death_cross, golden_cross_recent


def _path(down: int, up: int, start=100.0) -> np.ndarray:
    """A long slide followed by a rally: the 50-day crosses back over the 200-day during the rally."""
    a = start * np.cumprod(np.full(down, 0.997))
    b = a[-1] * np.cumprod(np.full(up, 1.01))
    return np.concatenate([a, b])


def _cross_age(c: np.ndarray) -> int:
    s50, s200 = trend_line(c, "sma50"), trend_line(c, "sma200")
    above = s50 > s200
    assert above[-1]
    i = len(above) - 1
    while above[i - 1]:
        i -= 1
    return len(c) - 1 - i


def test_golden_cross_fires_for_20_sessions_after_the_cross():
    full = _path(300, 120)
    first = next(k for k in range(260, len(full) + 1) if golden_cross_recent(full[:k]))
    assert _cross_age(full[:first]) == 0                       # fires on the day of the cross
    for k in range(first, len(full) + 1):
        assert golden_cross_recent(full[:k]) == (_cross_age(full[:k]) < 20)   # today counts as one of the 20
    assert not golden_cross_recent(full)                       # 20+ sessions later it is no longer fresh


def test_long_uptrend_is_not_a_fresh_cross():
    c = 50 * np.cumprod(np.full(400, 1.003))
    assert not golden_cross_recent(c)       # 50 has been above 200 the whole time


def test_downtrend_is_not_golden_and_is_death():
    c = 100 * np.cumprod(np.full(400, 0.997))
    assert not golden_cross_recent(c)
    assert death_cross(c)


def test_short_history_never_fires():
    c = _path(150, 60)[:210]
    assert not golden_cross_recent(c)
    assert not death_cross(c[:150])


def test_uptrend_is_not_death_cross():
    assert not death_cross(50 * np.cumprod(np.full(300, 1.002)))


def test_trend_line_sma200_matches_mean():
    c = np.arange(1, 301, dtype=float)
    line = trend_line(c, "sma200")
    assert np.isnan(line[198]) and not np.isnan(line[199])
    assert line[-1] == pytest.approx(c[-200:].mean())


def test_compute_signals_reports_golden_cross():
    full = _path(300, 120)
    c = next(full[:k] for k in range(260, len(full)) if golden_cross_recent(full[:k]))
    n = len(c)
    sig = compute_signals(c, c * 1.01, c * 0.99, np.full(n, 1e6))
    assert sig["golden_cross"] is True


# ── The live switch (exit_logic.EXIT_LINE) ───────────────────────────────────
import exit_logic
from exit_logic import EXIT_LINE, check_exit_triggers, describe_trend_exit, trend_exit_line


def test_live_exit_line_is_still_the_50_day():
    assert EXIT_LINE == "sma50"          # changing this is a live rule change: it needs its own PR


def test_trend_exit_line_modes():
    up = 50 * np.cumprod(np.full(300, 1.002))
    down = 100 * np.cumprod(np.full(300, 0.997))
    assert trend_exit_line(up, "sma50") == pytest.approx(up[-50:].mean())
    assert trend_exit_line(up, "sma200") == pytest.approx(up[-200:].mean())
    assert trend_exit_line(up, "death_cross") is None
    line = trend_exit_line(down, "death_cross")
    assert line is not None and down[-1] < line
    assert trend_exit_line(up[:120], "sma200") is None
    with pytest.raises(ValueError):
        trend_exit_line(up, "ema9")


def test_death_cross_exit_fires_through_the_shared_trigger_check():
    down = 100 * np.cumprod(np.full(300, 0.997))
    line = trend_exit_line(down, "death_cross")
    fire, trig, _ = check_exit_triggers("X", float(down[-1]), float(down[-1]), 0.0, line)
    assert fire and trig == "PRICE_BELOW_50MA"
    assert describe_trend_exit(float(down[-1]), line, "death_cross")[0] == "DEATH_CROSS"
    assert describe_trend_exit(90.0, 100.0, "sma200") == ("PRICE_BELOW_200MA", "$90.00 is 10.0% below 200MA $100.00")


def test_intraday_exit_labels_by_live_line(monkeypatch):
    import intraday_exit
    pos = {"symbol": "X", "current_price": "90", "avg_entry_price": "95", "unrealized_plpc": "-0.05", "qty": "10"}
    assert intraday_exit.evaluate_position(pos, 100.0)["trigger"] == "PRICE_BELOW_50MA"
    monkeypatch.setattr(exit_logic, "EXIT_LINE", "death_cross")
    act = intraday_exit.evaluate_position(pos, exit_logic._ALWAYS_ABOVE)
    assert act["trigger"] == "DEATH_CROSS" and "death cross" in act["reason"]


def test_sma_cache_is_ignored_after_the_exit_line_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(exit_logic, "_SMA50_CACHE", tmp_path / "sma50_cache.json")
    exit_logic._write_sma50_cache({"X": 10.0})
    assert exit_logic.load_sma50_cache() == {"X": 10.0}
    monkeypatch.setattr(exit_logic, "EXIT_LINE", "death_cross")
    assert exit_logic.load_sma50_cache() == {}


def test_champion_backtest_uses_the_live_exit_line(monkeypatch):
    import daily_eval
    assert daily_eval.live_rules()["exit_line"] == "sma50"
    monkeypatch.setattr(exit_logic, "EXIT_LINE", "death_cross")
    assert daily_eval.live_rules()["exit_line"] == "death_cross"
