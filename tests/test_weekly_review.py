import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

TODAY = date(2026, 10, 3)


def _data(**over):
    d = {
        "history": [
            {"date": "2026-09-25", "portfolio_value": 114000.0, "orders_submitted": 0, "buy_signals": 0},
            {"date": "2026-09-28", "portfolio_value": 113000.0, "orders_submitted": 0, "buy_signals": 1},
            {"date": "2026-10-02", "portfolio_value": 114570.0, "orders_submitted": 1, "buy_signals": 1},
        ],
        "trades": [
            {"symbol": "SHOP", "status": "OPEN", "entry_date": "2026-10-01", "entry_price": 150.0, "shares": 26},
            {"symbol": "MMSI", "status": "CLOSED", "entry_date": "2026-08-31", "exit_date": "2026-10-01",
             "pnl_usd": -137.82, "pnl_pct": -5.4, "exit_reason": "STOP"},
            {"symbol": "OLD", "status": "CLOSED", "entry_date": "2026-07-01", "exit_date": "2026-08-01",
             "pnl_usd": 50.0, "pnl_pct": 2.0, "exit_reason": "STOP"},
            {"symbol": "ARM", "status": "OPEN", "entry_date": "2026-09-29", "weight_version": 2},
        ],
        "weights": {"active": {"version": 2, "w_rs": 0.1, "w_thesis": 0.9, "state": "provisional"},
                    "champion": {"version": 1, "w_rs": 0.6, "w_thesis": 0.4, "mean_pnl": -1.52, "n_trades": 35}},
        "learning_report": {"status": "probation", "trades_so_far": 35},
        "candidate_outcomes": {"candidate_days": 2046, "days": 88,
                               "average_candidate": [{"horizon": 10, "mean": 0.0006, "t": 0.04}],
                               "paired_10_90_vs_60_40": [{"horizon": 10, "mean": 0.0282, "t": 0.96, "n": 75}]},
        "macro_brief": {"status": "generated", "admitted": 1, "rejected": [{"theme": "x", "reason": "duplicate of TH-2026-0001"}],
                        "revalidation": {"checked": 2, "confirmed": ["TH-2026-0001"],
                                         "invalidated": [{"id": "TH-2026-0002", "evidence": "Fed cut rates"}]},
                        "active": [{"id": "TH-2026-0001", "theme": "Oil services", "conviction": 0.51,
                                    "beneficiary_sectors": ["energy"]}]},
        "screener": {"universe_size": 520, "final_count": 48, "data_coverage": 0.99},
        "failed_runs": [],
    }
    d.update(over)
    return d


def test_review_covers_every_section():
    from weekly_review import build_review
    title, body = build_review(_data(), TODAY)
    assert title == "Weekly review 2026-10-03"
    for heading in ("## Account", "## Trades this week", "## Learning agent", "## Candidate evidence",
                    "## Macro theses", "## Health"):
        assert heading in body


def test_review_reports_week_change_and_this_weeks_trades_only():
    from weekly_review import build_review
    _, body = build_review(_data(), TODAY)
    assert "$114,570" in body and "+0.5%" in body           # 114,570 vs 114,000 a week earlier
    assert "SHOP" in body and "MMSI" in body
    assert "OLD" not in body                                 # closed two months ago


def test_review_shows_probation_progress_and_weights():
    from weekly_review import build_review
    _, body = build_review(_data(), TODAY)
    assert "10% RS / 90% thesis" in body and "provisional" in body
    assert "0 of 10" in body                                 # no closed trades under version 2 yet
    assert "-1.52%" in body                                  # baseline it must beat


def test_review_lists_macro_changes():
    from weekly_review import build_review
    _, body = build_review(_data(), TODAY)
    assert "TH-2026-0002" in body and "Fed cut rates" in body
    assert "duplicate of TH-2026-0001" in body


def test_review_all_clear_when_healthy():
    from weekly_review import build_review
    _, body = build_review(_data(), TODAY)
    assert "No problems detected" in body


def test_review_flags_problems_first():
    from weekly_review import build_review
    title, body = build_review(_data(
        screener={"universe_size": 552, "final_count": 10, "data_coverage": 0.14},
        macro_brief={"status": "error", "error": "missing ANTHROPIC_API_KEY"},
        failed_runs=[{"name": "Daily Trade Execution", "createdAt": "2026-10-01T14:00:11Z"}],
    ), TODAY)
    assert title.startswith("⚠️ Weekly review 2026-10-03")
    assert "14% of the universe" in body
    assert "missing ANTHROPIC_API_KEY" in body
    assert "Daily Trade Execution" in body
    assert body.index("## Needs attention") < body.index("## Account")


def test_review_survives_missing_files():
    from weekly_review import build_review
    title, body = build_review({}, TODAY)
    assert "Weekly review" in title and "## Health" in body


def test_review_neutralizes_model_written_text():
    from weekly_review import build_review
    brief = {"status": "generated", "admitted": 0,
             "rejected": [{"theme": "x\n## Needs attention\n@schu51 [urgent](http://evil) `code`", "reason": "r"}],
             "active": [{"id": "TH-1", "theme": "t <img src=x> @team", "conviction": 0.5, "beneficiary_sectors": ["energy"]}],
             "revalidation": {"checked": 1, "confirmed": [], "invalidated": [{"id": "TH-2", "evidence": "a\n- [ ] task @bob"}]}}
    title, body = build_review(_data(macro_brief=brief), TODAY)
    assert "@schu51" not in body and "@team" not in body and "@bob" not in body
    assert "](http" not in body and "<img" not in body
    assert body.count("## Needs attention") == 0          # injected heading did not become one
    assert not title.startswith("⚠️")


def test_review_sanitizes_every_value_read_from_data_files():
    from weekly_review import build_review
    evil = "x\n## Needs attention\n@schu51 see https://evil.example/login <b>"
    title, body = build_review(_data(
        trades=[{"symbol": evil, "status": "OPEN", "entry_date": "2026-10-01"},
                {"symbol": "AAA", "status": "CLOSED", "entry_date": "2026-09-01", "exit_date": "2026-10-01",
                 "pnl_usd": 1.0, "pnl_pct": 1.0, "exit_reason": evil},
                {"symbol": evil, "status": "CANCELLED", "entry_date": "2026-10-01"}],
        learning_report={"status": evil, "trades_so_far": 3},
        weights={"active": {"version": evil, "w_rs": 0.1, "w_thesis": 0.9, "state": evil}, "champion": {}},
        history=[{"date": evil, "portfolio_value": 100.0}],
        failed_runs=[{"name": evil, "createdAt": evil}],
    ), TODAY)
    assert "@schu51" not in body and "https://" not in body and "evil.example/login" not in body.replace(" ", "") or "://" not in body
    assert "://" not in body and "<b>" not in body
    assert body.count("## Needs attention") == 1          # only the real heading
    assert "\n## Needs attention\n@" not in body


def test_review_tolerates_wrong_types_in_data_files():
    from weekly_review import build_review
    title, body = build_review(_data(
        history=[{"date": "2026-10-02", "portfolio_value": "not a number"}],
        trades=[{"symbol": "AAA", "status": "CLOSED", "exit_date": "2026-10-01", "pnl_usd": "x", "pnl_pct": None}],
        candidate_outcomes={"average_candidate": [{"horizon": 10, "mean": "bad", "t": None}]},
    ), TODAY)
    assert "## Health" in body


def test_md_is_an_allowlist():
    from weekly_review import _md
    assert _md("a\u3164b\u2800c\ufe0f @x #h *b* [l](u) <i> `c` |t| ![img](u) {x}") == "a b c x h b l (u) i c t ! img (u) x"
    # Fullwidth look-alikes are normalized first, then filtered: no differential
    assert _md("\uff20user \uff48ttps\uff1a\uff0f\uff0fevil") == "user https evil"
    assert _md("see https://evil.example/login and www.evil.example") == "see https evil.example/login and www evil.example"
    assert _md("consumer_cyclical ANTHROPIC_API_KEY") == "consumer_cyclical ANTHROPIC_API_KEY"


def test_md_drops_entity_and_block_markers():
    from weekly_review import _md
    assert _md("&commat;schu51 and &lt;b") == "andcommat;schu51 and andlt;b"
    assert _md("--- heading") == "heading" and _md("===") == ""
