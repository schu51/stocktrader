import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from diamond_track import evaluate, forward_return, picks, verdict

DAYS = [d.date().isoformat() for d in pd.bdate_range("2026-10-09", periods=120)]


def _quote(start, per_day):
    return {"dates": DAYS, "closes": [start * (1 + per_day) ** i for i in range(len(DAYS))]}


def _history(day):
    rows = []
    for i, t in enumerate(["A", "B", "C", "D"], start=1):       # numbers order A B C D; conviction order D C B A
        rows.append({"date": day, "ticker": t, "rank": i, "total": 90 - i, "conviction": 50 + i, "conviction_rank": 5 - i})
    rows.append({"date": day, "ticker": "E", "rank": 5, "total": 10})          # never judged
    return rows


def test_picks_take_the_top_names_by_either_ranking():
    h = _history(DAYS[0])
    assert picks(h, DAYS[0], "rank", 2) == ["A", "B"] and picks(h, DAYS[0], "conviction_rank", 2) == ["D", "C"]
    assert picks(h, DAYS[0], "conviction_rank", 9) == ["D", "C", "B", "A"]     # E has no conviction rank
    assert picks(h, "2020-01-01", "rank", 2) == []


def test_forward_return_needs_the_whole_window():
    q = _quote(100.0, 0.01)
    assert round(forward_return(q["dates"], q["closes"], DAYS[0], 20), 4) == round(1.01 ** 20 - 1, 4)
    assert forward_return(q["dates"], q["closes"], DAYS[110], 20) is None      # only nine sessions left
    assert forward_return([], [], DAYS[0], 20) is None


def test_evaluate_compares_conviction_picks_with_numbers_picks_and_the_index():
    quotes = {"A": _quote(10, -0.002), "B": _quote(10, -0.001), "C": _quote(10, 0.002), "D": _quote(10, 0.003),
              "IWM": _quote(200, 0.0005)}
    rows = evaluate(_history(DAYS[0]) + _history(DAYS[5]), quotes, horizons=(20, 63), n=2)
    first = [r for r in rows if r["date"] == DAYS[0] and r["horizon"] == 20][0]
    assert first["conviction"] > first["index"] > first["numbers"]
    assert (first["n_conviction"], first["n_numbers"]) == (2, 2)
    assert {(r["date"], r["horizon"]) for r in rows} == {(DAYS[0], 20), (DAYS[0], 63), (DAYS[5], 20), (DAYS[5], 63)}
    # a list too recent to have the window is left out, not scored on part of it
    assert evaluate(_history(DAYS[100]), quotes, horizons=(63,), n=2) == []


def _rows(n, horizon, conviction, numbers, index, start=0):
    return [{"date": DAYS[start + i], "horizon": horizon, "conviction": conviction, "numbers": numbers, "index": index,
             "n_conviction": 8, "n_numbers": 8} for i in range(n)]


def test_verdict_collects_then_reads_early_then_decides():
    assert verdict([])["status"] == "collecting"
    assert verdict(_rows(7, 20, 0.05, 0.01, 0.02))["status"] == "collecting"
    early = verdict(_rows(8, 20, 0.05, 0.01, 0.02))
    assert early["status"] == "early" and "8 weekly lists" in early["text"]
    ready = verdict(_rows(12, 20, 0.05, 0.01, 0.02) + _rows(12, 63, 0.12, 0.03, 0.05))
    assert ready["status"] == "ready" and "12 of 12" in ready["text"]
    no = verdict(_rows(12, 20, 0.0, 0.01, 0.02) + _rows(12, 63, 0.02, 0.03, 0.05))
    assert no["status"] == "not_supported"
    mixed = verdict(_rows(12, 20, 0.05, 0.01, 0.02) + _rows(7, 63, 0.12, 0.03, 0.05) + _rows(5, 63, 0.0, 0.03, 0.05, start=20))
    assert mixed["status"] == "not_supported"            # ahead in 7 of 12 is under the two-thirds line
