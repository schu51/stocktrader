import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd


def _prices(opens, closes, start="2026-06-01"):
    idx = pd.bdate_range(start, periods=len(opens))
    return pd.Series(opens, index=idx, dtype=float), pd.Series(closes, index=idx, dtype=float)


def test_forward_return_enters_next_open_exits_nth_close():
    from candidate_outcomes import forward_return
    # Mon..Fri. Snapshot Monday -> enter Tuesday open (11), hold 2 sessions -> exit Wednesday close (13.2)
    opens, closes = _prices([10, 11, 12, 13, 14], [10.5, 11.5, 13.2, 13.5, 14.5])
    r = forward_return(opens, closes, date(2026, 6, 1), horizon=2)
    assert round(r, 6) == round(13.2 / 11 - 1, 6)


def test_forward_return_never_uses_snapshot_day_prices():
    from candidate_outcomes import forward_return
    opens, closes = _prices([10, 11, 12], [999, 11.5, 12.5])   # snapshot-day close is a decoy
    r = forward_return(opens, closes, date(2026, 6, 1), horizon=1)
    assert round(r, 6) == round(11.5 / 11 - 1, 6)


def test_forward_return_none_when_window_not_complete():
    from candidate_outcomes import forward_return
    opens, closes = _prices([10, 11, 12], [10, 11, 12])
    assert forward_return(opens, closes, date(2026, 6, 1), horizon=5) is None
    assert forward_return(opens, closes, date(2026, 6, 3), horizon=1) is None   # no next session yet


def test_forward_return_snapshot_on_non_trading_day_uses_next_session():
    from candidate_outcomes import forward_return
    opens, closes = _prices([10, 11, 12], [10, 11.5, 12])        # starts Mon 2026-06-01
    r = forward_return(opens, closes, date(2026, 5, 30), horizon=1)   # Saturday -> enters Monday open
    assert round(r, 6) == round(10 / 10 - 1, 6)


def test_daily_ic_is_plus_one_when_score_orders_returns():
    from candidate_outcomes import daily_ic
    df = pd.DataFrame({
        "date": ["d1"] * 5 + ["d2"] * 5,
        "score": [1, 2, 3, 4, 5, 5, 4, 3, 2, 1],
        "ret":   [1, 2, 3, 4, 5, 1, 2, 3, 4, 5],
    })
    ic = daily_ic(df, "score", "ret", min_names=5)
    assert round(ic["d1"], 6) == 1.0
    assert round(ic["d2"], 6) == -1.0


def test_daily_ic_skips_thin_days():
    from candidate_outcomes import daily_ic
    df = pd.DataFrame({"date": ["d1"] * 3, "score": [1, 2, 3], "ret": [1, 2, 3]})
    assert daily_ic(df, "score", "ret", min_names=5).empty


def test_summarize_shrinks_t_for_overlapping_windows():
    from candidate_outcomes import summarize
    series = pd.Series([0.1, 0.2, 0.1, 0.2, 0.1, 0.2, 0.1, 0.2, 0.1, 0.2])
    one = summarize(series, horizon=1)
    ten = summarize(series, horizon=10)
    assert one["n"] == ten["n"] == 10
    assert round(one["mean"], 6) == 0.15
    assert ten["t"] < one["t"]            # 10-day windows overlap -> fewer independent observations
    assert round(ten["t"] * 10 ** 0.5, 6) == round(one["t"], 6)


def test_top_k_excess_picks_highest_composite():
    from candidate_outcomes import top_k_edge
    df = pd.DataFrame({
        "date": ["d1"] * 4,
        "rs_rank":      [90, 80, 70, 60],
        "thesis_score": [10, 20, 80, 90],
        "sector_leader": [False] * 4,
        "ret":          [0.10, 0.00, 0.00, -0.10],
    })
    # All RS -> picks the first two (mean 0.05); day average is 0 -> edge 0.05
    assert round(top_k_edge(df, w_rs=1.0, k=2, ret_col="ret", min_names=4)["d1"], 6) == 0.05
    # All thesis -> picks the last two (mean -0.05)
    assert round(top_k_edge(df, w_rs=0.0, k=2, ret_col="ret", min_names=4)["d1"], 6) == -0.05


def test_summary_is_json_serializable_and_complete():
    import json
    from candidate_outcomes import summary, HORIZONS, WEIGHT_GRID
    rows = []
    for d in range(12):
        for i in range(6):
            row = {"date": f"2026-06-{d + 1:02d}", "symbol": f"S{i}", "rs_rank": 70 + i * 5,
                   "thesis_score": 60 - i * 5, "sector_leader": i == 0}
            for h in HORIZONS:
                row[f"ret_{h}d"] = 0.01 * (i - d % 3)
                row[f"excess_{h}d"] = 0.01 * (i - d % 3) - 0.002
            rows.append(row)
    s = summary(pd.DataFrame(rows))
    json.dumps(s)
    assert s["candidate_days"] == 72 and s["days"] == 12
    assert len(s["weights"]) == len(HORIZONS) * len(WEIGHT_GRID)
    assert len(s["ic"]) == len(HORIZONS) * 2
