# tests/test_diamond_screen.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from diamond_screen import GATES, gate_failures, score


def _m(**over):
    m = {"ttm_revenue": 490e6, "revenue_growth": 0.30, "q_growth_now": 0.30, "q_growth_2q_ago": 0.25,
         "ttm_op_income": 4e6, "ttm_op_margin": 0.01, "prior_ttm_op_margin": -0.12, "margin_change": 0.13,
         "seq_improvements": 3, "latest_q_profitable": True, "year_ago_q_profitable": False,
         "ttm_gross_margin": 0.70, "gross_margin_change": 0.02, "cash": 300e6, "runway_years": None,
         "shares_growth": 0.05, "backlog_growth": 0.30, "prepaid_growth": None}
    m.update(over)
    return m


def test_a_company_turning_profitable_passes_every_gate():
    assert gate_failures(_m()) == []


def test_each_gate_at_its_boundary():
    assert gate_failures(_m(ttm_revenue=49.9e6)) == ["revenue"]
    assert gate_failures(_m(ttm_revenue=50e6)) == []
    assert gate_failures(_m(revenue_growth=0.149)) == ["growth"]
    assert gate_failures(_m(margin_change=0.049)) == ["turning"]
    assert gate_failures(_m(ttm_op_margin=-0.151)) == ["break_even"]
    assert gate_failures(_m(prior_ttm_op_margin=0.051)) == ["already_rich"]
    assert gate_failures(_m(ttm_gross_margin=0.39, gross_margin_change=0.029)) == ["edge"]
    assert gate_failures(_m(ttm_gross_margin=0.39, gross_margin_change=0.03)) == []      # low but rising
    assert gate_failures(_m(shares_growth=0.101)) == ["dilution"]


def test_runway_gate_only_binds_a_loss_maker():
    losing = dict(ttm_op_income=-50e6, ttm_op_margin=-0.10)
    assert gate_failures(_m(**losing, runway_years=1.9)) == ["runway"]
    assert gate_failures(_m(**losing, runway_years=2.0)) == []
    assert gate_failures(_m(**losing, runway_years=None)) == ["runway"]     # losing money, cash unknown


def test_unknown_fails_the_gate_that_needs_it_without_error():
    assert gate_failures(_m(ttm_gross_margin=None, gross_margin_change=None)) == ["edge"]
    assert gate_failures(_m(shares_growth=None)) == ["dilution"]
    assert set(gate_failures({})) == set(GATES)


def test_score_parts_and_total():
    s = score(_m())
    assert s["inflection"] == 33.0          # 13 of 20 margin points -> 13, three improvements -> 10, crossing -> 10
    assert s["revenue"] == round((0.30 - 0.15) / 0.35 * 20 + 5, 1)
    assert s["edge"] == round((0.70 - 0.40) / 0.40 * 10 + 0.02 / 0.05 * 5, 1)
    assert s["demand"] == 5.0               # backlog grew exactly as fast as revenue: half marks, on the full 10
    assert s["safety"] == 7.5               # profitable -> 5; 5% dilution -> 2.5
    assert s["total"] == round(s["inflection"] + s["revenue"] + s["edge"] + s["demand"] + s["safety"], 1)


def test_demand_weight_is_spread_when_nothing_is_reported():
    s = score(_m(backlog_growth=None))
    assert s["demand"] is None
    rest = s["inflection"] + s["revenue"] + s["edge"] + s["safety"]
    assert s["total"] == round(rest / 90 * 100, 1)


def test_scores_are_clamped():
    s = score(_m(margin_change=0.9, revenue_growth=5.0, ttm_gross_margin=0.99, gross_margin_change=0.5,
                 shares_growth=-0.2, backlog_growth=9.0, prepaid_growth=9.0))
    assert (s["inflection"], s["revenue"], s["edge"], s["demand"], s["safety"]) == (40.0, 25.0, 15.0, 10.0, 10.0)
    assert s["total"] == 100.0
