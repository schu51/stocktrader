import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

import backtest as bt
import contracts
import picker_lab as lab
import scope_audit as sa


def _closes(n_days=160, n_syms=40, leaders_reverse=True, seed=0):
    """Synthetic closes: the first 4 names are momentum leaders (big run-up), the rest drift."""
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2025-01-01", periods=n_days)
    data = {}
    for k in range(n_syms):
        base = 100 * np.cumprod(1 + rng.normal(0.0002, 0.005, n_days))
        if k < 4:
            run = np.linspace(1, 2.0, n_days - 22)
            tail = np.linspace(2.0, 1.6 if leaders_reverse else 2.4, 22)
            base = base * np.concatenate([run, tail])
        data[f"S{k}"] = base
    return pd.DataFrame(data, index=idx)


def test_momentum_spread_is_negative_when_last_months_leaders_reverse():
    assert bt.momentum_spread(_closes(leaders_reverse=True)) < -0.05


def test_momentum_spread_is_positive_when_leaders_keep_leading():
    assert bt.momentum_spread(_closes(leaders_reverse=False)) > 0.05


def test_momentum_spread_needs_enough_history():
    assert np.isnan(bt.momentum_spread(_closes(n_days=100)))


def test_check_variant_rejects_unknown_settings_and_empty_changes():
    knobs = lab.rule_knobs()
    assert lab.check_variant({"name": "x", "change": {"max_rs_rank": 98}}, knobs) is None
    assert "unknown" in lab.check_variant({"name": "x", "change": {"bogus": 1}}, knobs)
    assert lab.check_variant({"name": "x", "change": {}}, knobs) == "no change"
    assert "unknown" in lab.check_variant({"name": "x", "change": {"universe": {}}}, knobs)


def test_new_knobs_are_backtest_settings_and_tournament_knobs():
    import daily_eval as de
    for k in ("max_rs_rank", "sector_boost", "min_thesis", "min_mom_spread"):
        assert k in lab.rule_knobs()
        assert k in de.ALLOWED_KNOBS


def test_every_round_file_is_valid():
    knobs = lab.rule_knobs()
    for f in (Path(__file__).parent.parent / "research" / "picker_lab").glob("round*.json"):
        spec = contracts.read(f)
        names = [v["name"] for v in spec["variants"]]
        assert len(names) == len(set(names)), f
        for v in spec["variants"]:
            assert lab.check_variant(v, knobs) is None, (f, v)


def test_recent_folds_skip_the_empty_stub():
    folds = [{"trades": 10, "profit_factor": 1.5}, {"trades": 12, "profit_factor": 0.5},
             {"trades": 0, "profit_factor": None}]
    assert [f["profit_factor"] for f in lab.recent_folds(folds)] == [1.5, 0.5]


def test_rank_puts_more_gates_first_then_profit_factor():
    a = {"profit_factor": 2.0, "verdict": {"gates_passed": 3, "folds_won": {"won": 1}}}
    b = {"profit_factor": 1.2, "verdict": {"gates_passed": 8, "folds_won": {"won": 4}}}
    assert sorted([a, b], key=lab.rank_key)[0] is b


def test_lab_results_commit_is_in_lane():
    assert sa.classify("Picker lab round1 [skip ci]", ["research/picker_lab/results/2026-11-17_round1.json"], 1) is None
    f = sa.classify("Picker lab round1 [skip ci]", ["docs/data/latest.json"], 1)
    assert f and f["kind"] == "breach"
