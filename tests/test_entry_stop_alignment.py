"""A buy is sized and logged on the stop the broker will hold (2026-11-10)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from stop_placement import (align_entry_to_broker_stop, calculate_stop_for_position,
                            entry_stop_dist)


def _buy(**kw):
    d = {"symbol": "ZS", "shares": 14, "limit_price": 237.88, "stop_loss": 231.10,
         "position_value": 3330.32, "stop_arm": "atr", "stop_dist": 0.126875}
    d.update(kw)
    return d


def test_logged_stop_becomes_the_broker_stop():
    d = align_entry_to_broker_stop(_buy(), equity=106_000, max_risk=0.02)
    assert d["stop_loss"] == round(237.88 * (1 - 0.126875), 2)
    assert d["planned_stop_loss"] == 231.10
    assert d["shares"] == 14 and "risk_capped_from" not in d     # $423 at risk, under 2%


def test_logged_stop_matches_what_stop_placement_places():
    d = align_entry_to_broker_stop(_buy(), equity=106_000, max_risk=0.02)
    pos = {"current_price": 237.88, "avg_entry_price": 237.88, "unrealized_plpc": 0.0}
    assert calculate_stop_for_position(pos, d)[0] == d["stop_loss"]
    d2 = align_entry_to_broker_stop(_buy(stop_arm="fixed", stop_dist=None), 106_000, 0.02)
    assert calculate_stop_for_position(pos, d2)[0] == d2["stop_loss"] == round(237.88 * 0.92, 2)


def test_shares_cut_when_the_broker_stop_risks_too_much():
    d = align_entry_to_broker_stop(_buy(shares=100), equity=106_000, max_risk=0.02)
    assert d["shares"] == int(0.02 * 106_000 / (237.88 * 0.126875)) and d["risk_capped_from"] == 100
    assert d["position_value"] == round(d["shares"] * 237.88, 2)


def test_never_sizes_up():
    d = align_entry_to_broker_stop(_buy(stop_dist=0.05), equity=106_000, max_risk=0.02)
    assert d["shares"] == 14


def test_out_of_bounds_atr_distance_falls_back_to_default():
    assert entry_stop_dist({"stop_arm": "atr", "stop_dist": 0.30}) == 0.08
    assert entry_stop_dist({"stop_arm": "atr", "stop_dist": 0.10}) == 0.10
    assert entry_stop_dist(None) == 0.08
