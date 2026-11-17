import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import backtest as bt
import fundamentals_pit as fp


def _hist(eps, rev, start_year=2023, report_lag=30):
    """Calendar quarters from Q1 start_year, each public report_lag days after the quarter end."""
    out, y, q = [], start_year, 1
    for e, r in zip(eps, rev):
        end = (pd.Timestamp(year=y, month=3 * q, day=1) + pd.offsets.MonthEnd(0)).date()
        out.append([end.isoformat(), (end + timedelta(days=report_lag)).isoformat(), e, r])
        y, q = (y, q + 1) if q < 4 else (y + 1, 1)
    return out


def test_known_from_uses_the_first_report_after_the_quarter_else_a_late_default():
    end = date(2025, 3, 31)
    assert fp.known_from(end, [date(2025, 2, 1), date(2025, 4, 24)]) == date(2025, 4, 24)
    assert fp.known_from(end, [date(2025, 9, 30)]) == date(2025, 5, 30)


def test_factor_only_sees_quarters_public_by_the_date():
    h = _hist([1.0] * 8 + [3.0], [100] * 8 + [150])
    last_known = date.fromisoformat(h[-1][1])
    before = fp.factor_asof(h, last_known - timedelta(days=1))
    after = fp.factor_asof(h, last_known)
    assert before["rev_yoy"] == pytest.approx(0.0)
    assert after["rev_yoy"] == pytest.approx(0.5)
    assert after["days_since"] == 0


def test_a_big_beat_gives_a_high_surprise_and_score():
    eps = [1.0, 1.1, 0.9, 1.0, 1.05, 1.15, 0.95, 1.0, 1.08, 1.12, 0.93, 1.6]
    rev = [100, 102, 101, 103, 105, 106, 104, 107, 109, 110, 108, 140]
    f = fp.factor_asof(_hist(eps, rev), date(2026, 6, 1))
    assert f["sue"] > 3
    assert f["rev_accel"] > 0
    assert f["score"] > 80


def test_too_little_history_is_no_signal():
    assert fp.factor_asof(_hist([1, 1, 1, 1], [1, 1, 1, 1]), date(2026, 1, 1)) is None


def test_score_is_neutral_without_data_and_bounded():
    assert fp.score(None, None, None) == 50.0
    assert 0 <= fp.score(-99, -9, -9) <= fp.score(99, 9, 9) <= 100


def test_lookup_memoises_but_tracks_days_since():
    h = _hist([1.0] * 9, [100] * 9)
    look = fp.Lookup({"X": h})
    d = date.fromisoformat(h[-1][1])
    assert look("X", d)["days_since"] == 0
    assert look("X", d + timedelta(days=10))["days_since"] == 10
    assert look("Y", d) is None


def test_build_maps_frames_to_tickers_with_report_dates():
    rows = []
    for y in (2024, 2025):
        for q in range(1, 5):
            end = (pd.Timestamp(year=y, month=3 * q, day=1) + pd.offsets.MonthEnd(0))
            start = end - pd.Timedelta(days=89)
            rows.append({"cik": 7, "start": start.date().isoformat(), "end": end.date().isoformat(), "val": 1.0 + q})

    def fetch(tax, tag, unit, period):
        return rows if tag in ("EarningsPerShareDiluted", "Revenues") else []

    out = fp.build(date(2026, 1, 15), {"ABC": ["2025-04-20"]}, fetch=fetch,
                   listings={7: {"ticker": "ABC"}, 8: {"ticker": "NONE"}})
    assert set(out) == {"ABC"}
    q1 = next(r for r in out["ABC"] if r[0] == "2025-03-31")
    assert q1[1] == "2025-04-20"


def _prices(n=300, syms=30, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    c = pd.DataFrame({f"S{k}": 50 * np.cumprod(1 + rng.normal(0.001, 0.01, n)) for k in range(syms)}, index=idx)
    return c, pd.DataFrame(2_000_000, index=idx, columns=c.columns)


@pytest.mark.parametrize("mode", ["live", "skip1m", "12_1"])
def test_screen_runs_in_every_rs_mode(mode):
    c, v = _prices()
    spy = c.mean(axis=1).values
    out = bt.screen_asof({s: "technology" for s in c.columns}, c.iloc[-252:], v.iloc[-252:], spy, 0.8, 0.2,
                         rs_mode=mode)
    assert all(0 <= x["rs_rank"] <= 100 for x in out)


def test_fundamental_gates_drop_names_without_data_and_w_fund_reorders():
    c, v = _prices()
    spy = c.mean(axis=1).values
    uni = {s: "technology" for s in c.columns}
    base = bt.screen_asof(uni, c.iloc[-252:], v.iloc[-252:], spy, 0.8, 0.2)
    assert bt.screen_asof(uni, c.iloc[-252:], v.iloc[-252:], spy, 0.8, 0.2,
                          fund_fn=lambda s: None, min_sue=0.0) == []
    if len(base) >= 2:
        last = base[-1]["symbol"]
        good = {"sue": 3.0, "rev_yoy": 0.5, "rev_accel": 0.2, "days_since": 5, "score": 100.0}
        out = bt.screen_asof(uni, c.iloc[-252:], v.iloc[-252:], spy, 0.8, 0.2, w_fund=10.0,
                             fund_fn=lambda s: good if s == last else None)
        assert out[0]["symbol"] == last


def test_backtest_refuses_fundamental_settings_without_data():
    c, v = _prices(n=60, syms=3)
    prices = {"Close": c, "Open": c, "High": c, "Low": c, "Volume": v}
    with pytest.raises(ValueError):
        bt.Backtest({s: "x" for s in c.columns}, prices, "2024-02-01", w_fund=0.5)
