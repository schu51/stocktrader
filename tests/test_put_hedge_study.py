import math
import sys
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest


def test_put_price_matches_textbook_and_falls_to_intrinsic_at_expiry():
    from put_hedge_study import bs_put
    # Hull: S=42, K=40, r=10%, sigma=20%, T=0.5 -> put 0.81
    assert bs_put(42, 40, 0.5, 0.2, 0.10) == pytest.approx(0.8086, abs=1e-3)
    assert bs_put(30, 40, 0.0, 0.5) == 10.0 and bs_put(50, 40, 0.0, 0.5) == 0.0


def test_weekly_expiry_is_the_first_friday_on_or_after():
    from put_hedge_study import expiry_on_or_after
    assert expiry_on_or_after(date(2026, 11, 11)) == date(2026, 11, 13)     # Wednesday -> Friday
    assert expiry_on_or_after(date(2026, 11, 13)) == date(2026, 11, 13)     # Friday itself


def test_a_put_pays_off_on_a_crash_and_costs_its_premium_on_a_rally():
    from put_hedge_study import hedge, move_sigma
    msig = move_sigma([0.10, -0.12, 0.08], 1.25)
    d0, d1 = date(2026, 11, 4), date(2026, 11, 6)                            # Wed -> Fri: expires at exit
    crash = hedge(50, 28, d0, d1, 0.5, msig, 0.90, 0.10)
    assert crash["proceeds"] == pytest.approx((45 - 28) / 50)                # exercised at intrinsic
    assert crash["net"] > 0.25
    rally = hedge(50, 60, d0, d1, 0.5, msig, 0.90, 0.10)
    assert rally["proceeds"] == 0 and rally["net"] == pytest.approx(-rally["premium"])
    assert rally["premium"] > rally["model_premium_mid"]                     # paid the spread


def test_a_bigger_expected_report_move_makes_the_put_dearer():
    from put_hedge_study import hedge
    cheap = hedge(100, 100, date(2026, 11, 2), date(2026, 11, 4), 0.3, 0.03, 0.95, 0.05)
    dear = hedge(100, 100, date(2026, 11, 2), date(2026, 11, 4), 0.3, 0.12, 0.95, 0.05)
    assert dear["premium"] > cheap["premium"] * 2


def _series(n=320, start=date(2024, 1, 1), report_moves=None):
    """Weekday closes with a gentle wiggle and given moves on given report days."""
    dates, closes, d, px = [], [], start, 100.0
    while len(dates) < n:
        if d.weekday() < 5:
            px *= 1 + (0.01 if len(dates) % 2 else -0.009)
            if report_moves and d in report_moves:
                px *= 1 + report_moves[d]
            dates.append(d)
            closes.append(px)
        d += timedelta(days=1)
    return dates, closes


def test_events_use_only_earlier_reports_and_need_two_of_them():
    from put_hedge_study import events
    reps = [date(2024, 4, 3), date(2024, 7, 3), date(2024, 10, 2), date(2025, 1, 8)]
    dates, closes = _series(report_moves={r: m for r, m in zip(reps, (0.10, -0.20, 0.05, -0.30))})
    evs = events("X", dates, closes, reps, date(2024, 1, 1))
    assert [e["report"] for e in evs] == ["2024-10-02", "2025-01-08"]        # first two have no history
    assert len(evs[0]["prior_moves"]) == 2 and len(evs[1]["prior_moves"]) == 3
    assert evs[1]["move"] == pytest.approx(-0.30, abs=0.03)                  # the report's own gap, not later ones
    assert all(abs(m) < 0.25 for m in evs[1]["prior_moves"][:2])
    assert 0.1 < evs[0]["base_vol"] < 0.2                                    # report windows left out of base vol


def test_held_through_needs_the_trade_open_across_the_report():
    from put_hedge_study import held_through
    trades = [{"symbol": "VCYT", "sleeve": "diamond", "entry_date": "2026-10-26", "exit_date": "2026-11-05"},
              {"symbol": "KO", "entry_date": "2026-10-01", "exit_date": None}]
    assert held_through(trades, "VCYT", date(2026, 11, 5), sleeve=True)
    assert not held_through(trades, "VCYT", date(2026, 10, 20), sleeve=True)
    assert not held_through(trades, "VCYT", date(2026, 11, 5), sleeve=False)
    assert held_through(trades, "KO", date(2026, 10, 21), sleeve=False)


def test_run_groups_events_and_compares_puts_to_half_size():
    from put_hedge_study import run
    reps = [date(2024, 4, 3), date(2024, 7, 3), date(2024, 10, 2), date(2025, 1, 8)]
    px = _series(report_moves={r: m for r, m in zip(reps, (0.10, -0.20, 0.05, -0.30))})
    history = [{"ticker": "SMOL"}]
    trades = [{"symbol": "SMOL", "sleeve": "diamond", "entry_date": "2024-12-01", "exit_date": "2025-01-10"},
              {"symbol": "BIG", "entry_date": "2024-01-02", "exit_date": None}]
    out = run(date(2025, 3, 1), history, trades,
              prices=lambda syms, start: {s: px for s in syms}, reports=lambda syms: {s: reps for s in syms})
    s = out["summary"]
    assert out["symbols"] == {"diamond": 1, "book": 1}
    assert s["diamond"]["events"] == 2 and s["book"]["events"] == 2 and s["sleeve_held"]["events"] == 1
    assert s["book_held"]["events"] == 2
    held = out["held"]["sleeve_held"][0]
    assert held["report"] == "2025-01-08" and held["move"] < -0.25
    assert held["hedged"] > held["move"] + 0.1                               # the put took most of the gap
    assert held["half_size"] == pytest.approx(held["move"] / 2, abs=1e-4)
    p = s["diamond"]["puts"]["markup1.25_strike0.90"]
    assert p["hedged"]["worst"] > s["diamond"]["unhedged"]["worst"]
    assert out["verdicts"]["diamond"].startswith("2 reports")
    assert s["book"]["half_spread"] == 0.05 and s["diamond"]["half_spread"] == 0.10
