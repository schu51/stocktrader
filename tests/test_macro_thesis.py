import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date as date_


def _good_thesis(**over):
    t = {
        "id": "TH-1", "theme": "AI power-constrained",
        "causal_chain": "training scales faster than grid -> power enablers re-rate",
        "beneficiary_sectors": ["utilities", "energy"],
        "second_order": True,
        "consensus_names_excluded": ["NVDA"],
        "conviction_breakdown": {
            "source_corroboration": 0.8, "causal_directness": 0.7,
            "non_consensus": 0.6, "invalidation_clarity": 0.7,
        },
        "invalidation_condition": "hyperscaler capex declines QoQ",
        "horizon": "2099-12-31",
        "sources": ["https://reuters.com/x"],
        "status": "active",
    }
    t.update(over)
    return t


def test_compute_conviction_is_mean_of_subscores():
    from macro_thesis import compute_conviction
    c = compute_conviction({
        "source_corroboration": 0.8, "causal_directness": 0.7,
        "non_consensus": 0.6, "invalidation_clarity": 0.7,
    })
    assert abs(c - 0.7) < 1e-9


def test_valid_thesis_accepted():
    from macro_thesis import validate_thesis
    ok, reason = validate_thesis(_good_thesis())
    assert ok is True, reason


def test_rejected_no_sources():
    from macro_thesis import validate_thesis
    ok, reason = validate_thesis(_good_thesis(sources=[]))
    assert ok is False
    assert "source" in reason.lower()


def test_rejected_reddit_only_sources():
    from macro_thesis import validate_thesis
    ok, reason = validate_thesis(_good_thesis(sources=["https://reddit.com/r/wallstreetbets/x"]))
    assert ok is False
    assert "primary" in reason.lower() or "source" in reason.lower()


def test_rejected_no_invalidation():
    from macro_thesis import validate_thesis
    ok, reason = validate_thesis(_good_thesis(invalidation_condition=""))
    assert ok is False
    assert "invalidation" in reason.lower()


def test_rejected_not_second_order():
    from macro_thesis import validate_thesis
    ok, reason = validate_thesis(_good_thesis(second_order=False, consensus_names_excluded=[]))
    assert ok is False
    assert "second" in reason.lower() or "consensus" in reason.lower()


def test_rejected_bad_sector_key():
    from macro_thesis import validate_thesis
    ok, reason = validate_thesis(_good_thesis(beneficiary_sectors=["crypto_moon"]))
    assert ok is False
    assert "sector" in reason.lower()


def test_rejected_conviction_below_floor():
    from macro_thesis import validate_thesis
    low = {"source_corroboration": 0.2, "causal_directness": 0.2,
           "non_consensus": 0.2, "invalidation_clarity": 0.2}
    ok, reason = validate_thesis(_good_thesis(conviction_breakdown=low))
    assert ok is False
    assert "conviction" in reason.lower()


def test_is_thesis_live_true():
    from macro_thesis import is_thesis_live
    assert is_thesis_live(_good_thesis(last_validated="2099-01-01"),
                          today=date_(2099, 1, 5)) is True


def test_is_thesis_live_expired_horizon():
    from macro_thesis import is_thesis_live
    t = _good_thesis(horizon="2000-01-01", last_validated="2099-01-01")
    assert is_thesis_live(t, today=date_(2099, 1, 5)) is False


def test_is_thesis_live_stale_validation():
    from macro_thesis import is_thesis_live
    t = _good_thesis(last_validated="2099-01-01")
    assert is_thesis_live(t, today=date_(2099, 1, 31)) is False


def test_is_thesis_live_retired_status():
    from macro_thesis import is_thesis_live
    t = _good_thesis(status="retired", last_validated="2099-01-01")
    assert is_thesis_live(t, today=date_(2099, 1, 5)) is False


def test_retire_expired_marks_status():
    from macro_thesis import retire_expired
    theses = [_good_thesis(id="A", horizon="2000-01-01", last_validated="2099-01-01"),
              _good_thesis(id="B", horizon="2099-12-31", last_validated="2099-01-01")]
    out = retire_expired(theses, today=date_(2099, 1, 5))
    by_id = {t["id"]: t for t in out}
    assert by_id["A"]["status"] == "retired"
    assert by_id["B"]["status"] == "active"


def test_multiplier_no_match():
    from macro_thesis import macro_multiplier
    live = [_good_thesis(beneficiary_sectors=["energy"], conviction=0.8)]
    assert macro_multiplier("AAPL", "technology", live) == 1.0


def test_multiplier_beneficiary():
    from macro_thesis import macro_multiplier
    live = [_good_thesis(beneficiary_sectors=["energy"], conviction=0.8,
                         consensus_names_excluded=["XOM"])]
    assert abs(macro_multiplier("VST", "energy", live) - 1.20) < 1e-9


def test_multiplier_excluded_name():
    from macro_thesis import macro_multiplier
    live = [_good_thesis(beneficiary_sectors=["technology"], conviction=0.9,
                         consensus_names_excluded=["NVDA"])]
    assert macro_multiplier("NVDA", "technology", live) == 0.90


def test_multiplier_strongest_match_no_stacking():
    from macro_thesis import macro_multiplier
    live = [_good_thesis(id="A", beneficiary_sectors=["energy"], conviction=0.4,
                         consensus_names_excluded=["X"]),
            _good_thesis(id="B", beneficiary_sectors=["energy"], conviction=0.8,
                         consensus_names_excluded=["Y"])]
    assert abs(macro_multiplier("VST", "energy", live) - 1.20) < 1e-9


def test_multiplier_bounds_cap():
    from macro_thesis import macro_multiplier
    live = [_good_thesis(beneficiary_sectors=["energy"], conviction=1.0,
                         consensus_names_excluded=["X"])]
    assert macro_multiplier("VST", "energy", live) == 1.25
