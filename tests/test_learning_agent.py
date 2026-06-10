import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


def test_default_weights_when_absent(tmp_path):
    from learning_agent import load_weights
    wf = tmp_path / "weights.json"
    w = load_weights(wf)
    assert w["active"]["w_rs"] == 0.60
    assert w["active"]["w_thesis"] == 0.40
    assert w["active"]["version"] == 1


def test_load_weights_roundtrip(tmp_path):
    from learning_agent import load_weights, save_weights
    wf = tmp_path / "weights.json"
    data = {
        "active": {"version": 2, "w_rs": 0.7, "w_thesis": 0.3, "state": "provisional"},
        "champion": {"version": 1, "w_rs": 0.6, "w_thesis": 0.4, "state": "champion"},
        "rejected": [],
        "history": [],
    }
    save_weights(wf, data)
    loaded = load_weights(wf)
    assert loaded["active"]["version"] == 2
    assert loaded["active"]["w_rs"] == 0.7


def test_is_locked_detects_rejected(tmp_path):
    from learning_agent import is_locked
    rejected = [{"w_rs": 0.85, "w_thesis": 0.15}]
    assert is_locked(0.86, 0.14, rejected) is True
    assert is_locked(0.70, 0.30, rejected) is False


def test_is_locked_empty_list():
    from learning_agent import is_locked
    assert is_locked(0.6, 0.4, []) is False


def _trade(symbol, pnl, weight_version, rs_rank=80, thesis_score=60, status="CLOSED"):
    return {
        "symbol": symbol, "status": status, "pnl_pct": pnl,
        "weight_version": weight_version, "rs_rank": rs_rank,
        "thesis_score": thesis_score,
    }


def test_closed_instrumented_trades_filters():
    from learning_agent import closed_instrumented_trades
    trades = [
        _trade("A", 5.0, 1),
        _trade("B", -2.0, 1, status="OPEN"),
        {"symbol": "C", "status": "CLOSED", "pnl_pct": 3.0},
        _trade("D", 4.0, 2),
    ]
    result = closed_instrumented_trades(trades)
    assert len(result) == 2
    assert {t["symbol"] for t in result} == {"A", "D"}


def test_mean_pnl_for_version():
    from learning_agent import mean_pnl_for_version
    trades = [_trade("A", 10.0, 2), _trade("B", 20.0, 2), _trade("C", 5.0, 1)]
    assert mean_pnl_for_version(trades, 2) == 15.0
    assert mean_pnl_for_version(trades, 1) == 5.0
    assert mean_pnl_for_version(trades, 99) is None


def test_rollback_reverts_underperformer():
    from learning_agent import judge_provisional
    weights = {
        "active":   {"version": 2, "w_rs": 0.8, "w_thesis": 0.2, "state": "provisional"},
        "champion": {"version": 1, "w_rs": 0.6, "w_thesis": 0.4, "state": "champion",
                     "mean_pnl": 8.0},
        "rejected": [], "history": [],
    }
    trades = [_trade(f"P{i}", 3.0, 2) for i in range(10)]
    result, action = judge_provisional(weights, trades)
    assert action == "reverted"
    assert result["active"]["version"] == 1
    assert len(result["rejected"]) == 1
    assert result["rejected"][0]["w_rs"] == 0.8


def test_rollback_promotes_outperformer():
    from learning_agent import judge_provisional
    weights = {
        "active":   {"version": 2, "w_rs": 0.8, "w_thesis": 0.2, "state": "provisional"},
        "champion": {"version": 1, "w_rs": 0.6, "w_thesis": 0.4, "state": "champion",
                     "mean_pnl": 8.0},
        "rejected": [], "history": [],
    }
    trades = [_trade(f"P{i}", 12.0, 2) for i in range(10)]
    result, action = judge_provisional(weights, trades)
    assert action == "promoted"
    assert result["champion"]["version"] == 2
    assert result["active"]["state"] == "champion"


def test_judge_provisional_still_on_probation():
    from learning_agent import judge_provisional
    weights = {
        "active":   {"version": 2, "w_rs": 0.8, "w_thesis": 0.2, "state": "provisional"},
        "champion": {"version": 1, "w_rs": 0.6, "w_thesis": 0.4, "state": "champion",
                     "mean_pnl": 8.0},
        "rejected": [], "history": [],
    }
    trades = [_trade(f"P{i}", 3.0, 2) for i in range(5)]
    result, action = judge_provisional(weights, trades)
    assert action == "probation"
    assert result["active"]["version"] == 2
