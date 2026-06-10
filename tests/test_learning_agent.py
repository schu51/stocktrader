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
