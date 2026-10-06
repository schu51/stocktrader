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


def test_run_accumulating_under_min_sample(tmp_path):
    import learning_agent as la
    wf = tmp_path / "weights.json"
    trades = [_trade(f"T{i}", 5.0, 1) for i in range(20)]
    report = la.run(trades, wf)
    assert report["status"] == "accumulating"
    assert report["trades_so_far"] == 20
    assert la.load_weights(wf)["active"]["version"] == 1


def test_run_applies_new_weights(tmp_path):
    import numpy as np
    import learning_agent as la
    wf = tmp_path / "weights.json"
    rng = np.random.default_rng(3)
    trades = []
    for i in range(40):
        rs = float(rng.uniform(50, 99))
        th = float(rng.uniform(0, 100))
        pnl = 0.3 * rs + rng.normal(0, 2)
        trades.append(_trade(f"T{i}", pnl, 1, rs_rank=rs, thesis_score=th))
    report = la.run(trades, wf)
    assert report["status"] == "applied"
    new = la.load_weights(wf)["active"]
    assert new["version"] == 2
    assert new["w_rs"] > new["w_thesis"]
    assert new["state"] == "provisional"


def test_run_no_significance_keeps_weights(tmp_path):
    import numpy as np
    import learning_agent as la
    wf = tmp_path / "weights.json"
    rng = np.random.default_rng(4)
    trades = [
        _trade(f"T{i}", float(rng.normal(0, 5)), 1,
               rs_rank=float(rng.uniform(50, 99)),
               thesis_score=float(rng.uniform(0, 100)))
        for i in range(40)
    ]
    report = la.run(trades, wf)
    assert report["status"] == "no_significance"
    assert la.load_weights(wf)["active"]["version"] == 1


def test_screener_weights_loader_default(tmp_path, monkeypatch):
    import screener
    monkeypatch.setattr(screener, "_WEIGHTS_PATH", tmp_path / "nope.json")
    w_rs, w_thesis = screener._load_ranking_weights()
    assert (w_rs, w_thesis) == (0.60, 0.40)


def test_screener_weights_loader_reads_active(tmp_path, monkeypatch):
    import json, screener
    wf = tmp_path / "weights.json"
    wf.write_text(json.dumps({"active": {"w_rs": 0.7, "w_thesis": 0.3}}))
    monkeypatch.setattr(screener, "_WEIGHTS_PATH", wf)
    assert screener._load_ranking_weights() == (0.7, 0.3)


def test_screener_weights_loader_rejects_bad_sum(tmp_path, monkeypatch):
    import json, screener
    wf = tmp_path / "weights.json"
    wf.write_text(json.dumps({"active": {"w_rs": 0.7, "w_thesis": 0.7}}))
    monkeypatch.setattr(screener, "_WEIGHTS_PATH", wf)
    assert screener._load_ranking_weights() == (0.60, 0.40)


def test_judge_reverts_when_champion_baseline_was_never_recorded():
    # Seed champion has mean_pnl=None. Its baseline must be derived from its
    # own trades — otherwise any provisional is promoted unconditionally.
    from learning_agent import judge_provisional
    weights = {
        "active":   {"version": 2, "w_rs": 0.1, "w_thesis": 0.9, "state": "provisional"},
        "champion": {"version": 1, "w_rs": 0.6, "w_thesis": 0.4, "state": "champion",
                     "mean_pnl": None, "n_trades": 0},
        "rejected": [], "history": [],
    }
    trades = ([_trade(f"C{i}", 8.0, 1) for i in range(30)]
              + [_trade(f"P{i}", 3.0, 2) for i in range(10)])
    result, action = judge_provisional(weights, trades)
    assert action == "reverted"
    assert result["active"]["version"] == 1
    assert result["champion"]["mean_pnl"] == 8.0
    assert result["rejected"][0]["w_thesis"] == 0.9


def test_judge_promotes_when_provisional_beats_derived_baseline():
    from learning_agent import judge_provisional
    weights = {
        "active":   {"version": 2, "w_rs": 0.1, "w_thesis": 0.9, "state": "provisional"},
        "champion": {"version": 1, "w_rs": 0.6, "w_thesis": 0.4, "state": "champion",
                     "mean_pnl": None, "n_trades": 0},
        "rejected": [], "history": [],
    }
    trades = ([_trade(f"C{i}", -1.5, 1) for i in range(30)]
              + [_trade(f"P{i}", 3.0, 2) for i in range(10)])
    result, action = judge_provisional(weights, trades)
    assert action == "promoted"
    assert result["champion"]["version"] == 2


def test_judge_promotes_when_champion_has_no_trades_at_all():
    from learning_agent import judge_provisional
    weights = {
        "active":   {"version": 2, "w_rs": 0.1, "w_thesis": 0.9, "state": "provisional"},
        "champion": {"version": 1, "w_rs": 0.6, "w_thesis": 0.4, "state": "champion",
                     "mean_pnl": None, "n_trades": 0},
        "rejected": [], "history": [],
    }
    trades = [_trade(f"P{i}", -4.0, 2) for i in range(10)]
    result, action = judge_provisional(weights, trades)
    assert action == "promoted"   # nothing to compare against


def test_run_records_champion_baseline_when_applying(tmp_path):
    import numpy as np
    import learning_agent as la
    wf = tmp_path / "weights.json"
    rng = np.random.default_rng(3)
    trades = []
    for i in range(40):
        rs = float(rng.uniform(50, 99))
        pnl = 0.3 * rs + rng.normal(0, 2)
        trades.append(_trade(f"T{i}", pnl, 1, rs_rank=rs, thesis_score=float(rng.uniform(0, 100))))
    assert la.run(trades, wf)["status"] == "applied"
    champion = la.load_weights(wf)["champion"]
    assert champion["n_trades"] == 40
    assert abs(champion["mean_pnl"] - sum(x["pnl_pct"] for x in trades) / 40) < 1e-9


def _significant_trades(n=40, seed=3):
    import numpy as np
    rng = np.random.default_rng(seed)
    trades = []
    for i in range(n):
        rs = float(rng.uniform(50, 99))
        trades.append(_trade(f"T{i}", 0.3 * rs + rng.normal(0, 2), 1, rs_rank=rs,
                             thesis_score=float(rng.uniform(0, 100))))
    return trades


def test_run_blocked_when_candidate_evidence_contradicts(tmp_path):
    import learning_agent as la
    wf = tmp_path / "weights.json"
    evidence = lambda w_new, w_cur: {"available": True, "contradicts": True, "mean_difference": -0.02, "days": 80}
    report = la.run(_significant_trades(), wf, candidate_evidence=evidence)
    assert report["status"] == "blocked_by_candidate_evidence"
    assert report["candidate_evidence"]["mean_difference"] == -0.02
    assert la.load_weights(wf)["active"]["version"] == 1          # nothing applied


def test_run_applies_when_candidate_evidence_agrees_or_is_unavailable(tmp_path):
    import learning_agent as la
    for i, ev in enumerate([{"available": True, "contradicts": False, "mean_difference": 0.01, "days": 80},
                            {"available": False, "contradicts": False, "days": 5}]):
        wf = tmp_path / f"weights{i}.json"
        report = la.run(_significant_trades(), wf, candidate_evidence=lambda a, b, ev=ev: ev)
        assert report["status"] == "applied"
        assert report["candidate_evidence"] == ev


def test_run_applies_when_evidence_check_itself_fails(tmp_path):
    import learning_agent as la
    wf = tmp_path / "weights.json"

    def broken(w_new, w_cur):
        raise RuntimeError("csv missing")
    report = la.run(_significant_trades(), wf, candidate_evidence=broken)
    assert report["status"] == "applied"
    assert "csv missing" in report["candidate_evidence"]["error"]


def test_new_version_never_reuses_a_retired_number(tmp_path):
    # After a revert, active is version 1 again while trades tagged version 2
    # (the retired weights) are still open. A new provisional must not be
    # numbered 2, or those old trades would count toward its probation.
    import json
    import learning_agent as la
    wf = tmp_path / "weights.json"
    weights = la.default_weights()
    weights["history"].append({"version": 2, "w_rs": 0.1, "w_thesis": 0.9})
    weights["rejected"].append({"w_rs": 0.1, "w_thesis": 0.9})
    wf.write_text(json.dumps(weights))
    report = la.run(_significant_trades(), wf)
    assert report["status"] == "applied"
    assert report["active_version"] == 3
    assert la.load_weights(wf)["active"]["version"] == 3


def test_weights_set_by_an_owner_decision_are_left_alone_until_the_review_date(tmp_path):
    # 2026-10-06: 80/20 adopted on the three-year backtest; the live trades from the summer point the other way
    import json
    from datetime import date
    import learning_agent as la
    path = tmp_path / "weights.json"
    held = la.default_weights()
    held["active"].update(version=3, w_rs=0.8, w_thesis=0.2)
    held["hold_until"] = "2026-12-05"
    path.write_text(json.dumps(held))
    trades = [{"status": "CLOSED", "weight_version": 1, "rs_rank": 70 + i % 30, "thesis_score": 30 + (i * 7) % 50,
               "pnl_pct": float((i * 7) % 50) - 20, "symbol": f"S{i}"} for i in range(40)]
    report = la.run(trades, weights_path=path, today=date(2026, 11, 14))
    assert report["status"] == "held" and report["hold_until"] == "2026-12-05"
    assert json.loads(path.read_text())["active"]["w_rs"] == 0.8          # untouched
    after = la.run(trades, weights_path=path, today=date(2026, 12, 5))     # from the review date the agent is free again
    assert after["status"] != "held"
