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
