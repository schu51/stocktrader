"""Daily eval: scorecard maths, promotion gates, streaks (2026-11-10)."""
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd


def _t(pnl, exit_date="2026-11-05", reason="PRICE_BELOW_50MA", **kw):
    return {"status": "CLOSED", "pnl_usd": pnl * 10, "pnl_pct": pnl, "exit_reason": reason,
            "entry_date": "2026-11-01", "exit_date": exit_date, **kw}


def test_trade_stats():
    from daily_eval import trade_stats
    s = trade_stats([_t(10), _t(-5), _t(-5)])
    assert s["trades"] == 3 and s["win_rate"] == 0.333 and s["profit_factor"] == 1.0
    assert s["expectancy_pct"] == 0.0 and trade_stats([]) == {"trades": 0}


def test_stop_fill_stats_measures_gaps_through_the_stop():
    from daily_eval import stop_fill_stats
    t = [_t(-8, reason="BROKER_STOP", exit_stop=92.0, exit_price=92.0, symbol="A"),
         _t(-40, reason="BROKER_STOP", exit_stop=92.0, exit_price=60.0, symbol="B"),
         _t(-5, exit_stop=92.0, exit_price=95.0, symbol="C")]
    s = stop_fill_stats(t)
    assert s["n"] == 2 and s["gapped_over_2pct"] == 1 and s["worst"] == "B" and s["recorded"] == 2


def test_stop_fill_stats_uses_the_broker_stop_not_the_logged_plan():
    # ZS 2026-11-05: logged a 2.9% stop, the broker held the ATR arm's 12.7%
    from daily_eval import stop_fill_stats
    zs = _t(-13, reason="BROKER_STOP", symbol="ZS", entry_price=237.88, stop_loss=231.10,
            stop_arm="atr", stop_dist=0.126875, exit_price=206.99)
    s = stop_fill_stats([zs])
    assert s["n"] == 1 and -0.5 < s["mean_slip_pct"] < 0 and s["recorded"] == 0


def test_stop_fill_stats_skips_an_estimate_the_fill_is_above():
    # MRNA: filled 19% above its entry stop, so a trailing tier had raised it
    from daily_eval import stop_fill_stats
    mrna = _t(1, reason="BROKER_STOP", symbol="MRNA", entry_price=196.47,
              stop_arm="atr", stop_dist=0.15, exit_price=199.20)
    assert stop_fill_stats([mrna]) == {"n": 0}


def test_account_stats_return_drawdown_and_spy():
    from daily_eval import account_stats
    hist = [{"date": "2026-11-0%d" % d, "portfolio_value": v} for d, v in [(2, 100), (3, 110), (4, 99)]]
    spy = pd.Series([500.0, 510.0, 505.0], index=pd.to_datetime(["2026-11-02", "2026-11-03", "2026-11-04"]))
    a = account_stats(hist, spy, date(2026, 11, 3))
    assert a["return"] == -0.01 and a["drawdown_from_peak"] == -0.1 and a["spy_return"] == 0.01


def _run(**kw):
    base = {"trades": 600, "win_rate": 0.55, "profit_factor": 2.0, "cagr": 0.30, "max_drawdown": -0.14,
            "recent_return": 0.0, "first_half": {"profit_factor": 2.0}, "second_half": {"profit_factor": 2.0}}
    base.update(kw)
    return base


def test_gates_need_a_better_profit_factor_in_both_halves_without_giving_up_much_else():
    from daily_eval import gates
    champ = _run()
    good = _run(profit_factor=2.2, first_half={"profit_factor": 2.1}, second_half={"profit_factor": 2.3})
    assert all(gates(good, champ).values())
    assert not gates(_run(profit_factor=2.05), champ)["profit_factor"]
    assert not gates(_run(profit_factor=2.2, cagr=0.25), champ)["annual_return"]
    assert not gates(_run(profit_factor=2.2, max_drawdown=-0.17), champ)["drawdown"]
    assert not gates(_run(profit_factor=2.2, first_half={"profit_factor": 1.5}), champ)["pf_first_half"]
    assert not gates(_run(trades=50), champ)["enough_trades"]


def test_streaks_count_consecutive_promotable_evals_and_reset():
    from daily_eval import streaks
    prev = {"tournament": {"streaks": {"a": 4, "b": 3}}}
    assert streaks({"a": True, "b": False, "c": True}, prev) == {"a": 5, "b": 0, "c": 1}
    assert streaks({"a": True}, None) == {"a": 1}


def test_challengers_may_only_change_known_settings_and_at_most_two():
    from daily_eval import check_challenger
    assert check_challenger({"breakeven_at": 3}) is None
    assert check_challenger({"trail_at": 20, "trail_pct": 0.12}) is None
    assert "unknown" in check_challenger({"leverage": 3})
    assert "max" in check_challenger({"breakeven_at": 3, "atr_stop": 2, "min_adx": 25})


def test_challenger_file_is_valid():
    import json
    from daily_eval import CHALLENGERS, check_challenger
    cfg = json.loads(CHALLENGERS.read_text())
    assert cfg["rules_versions"] and len(cfg["challengers"]) <= cfg["max_challengers"]
    for c in cfg["challengers"]:
        assert check_challenger(c["change"]) is None, c["name"]


def test_live_rules_read_from_the_live_config():
    from daily_eval import live_rules
    r = live_rules()
    assert r["min_spy_above_50ma"] == 0.02 and r["earnings_blackout"] == 5 and r["stale_days"] == 60
