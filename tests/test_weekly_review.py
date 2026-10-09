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


def test_review_flags_a_shrunken_universe():
    from weekly_review import build_review
    title, body = build_review(_data(screener={"universe_size": 89, "final_count": 12, "data_coverage": 1.0}), TODAY)
    assert title.startswith("⚠️") and "only 89 tickers" in body


def test_review_shows_experiment_states_and_this_weeks_changes():
    from weekly_review import build_review
    registry = {"signals_logged": 240, "experiments": {
        "atr_stop": {"kind": "stop_arm", "state": "trial", "evidence": {"n_atr": 12, "n_fixed": 9, "difference": 1.5, "t": 0.8}},
        "macd_cross": {"kind": "entry_signal", "state": "dropped", "evidence": {"days": 44, "mean": -0.012, "t": -1.4}},
        "breakout": {"kind": "entry_signal", "state": "observing"}},
        "changes": [{"date": "2026-10-03", "experiment": "macd_cross", "from": "active", "to": "dropped", "reason": "did worse"},
                    {"date": "2026-08-01", "experiment": "old", "from": "a", "to": "b", "reason": "ancient"}]}
    _, body = build_review(_data(experiments=registry), TODAY)
    assert "## Experiments" in body
    assert "atr_stop: **trial** — 12 ATR vs 9 fixed closed trades, difference +1.50% per trade" in body
    assert "macd_cross: **dropped** — 44 days of evidence, fired vs not -1.20%" in body
    assert "Changed this week: macd_cross active to dropped (did worse)" in body and "ancient" not in body


def test_review_reports_first_month_failure_rate():
    from weekly_review import build_review
    trades = [{"symbol": f"S{i}", "status": "CLOSED", "exit_date": "2026-09-20", "entry_date": "2026-09-01",
               "hold_days": h, "pnl_usd": p, "pnl_pct": p / 10} for i, (h, p) in
              enumerate([(5, -50), (12, -40), (20, 30), (45, 200), (60, -10)])]
    _, body = build_review(_data(trades=trades), TODAY)
    assert "First-month failure rate (closed within 30 days at a loss): **40%** of 5 closed trades" in body


def test_review_reports_sentiment_evidence():
    from weekly_review import build_review
    registry = {"signals_logged": 10, "experiments": {"news_positive": {"kind": "entry_signal", "state": "observing"}},
                "sentiment": {"rows": 900, "days": 30, "enough_history": True,
                              "news_score": {"days": 28, "ic": 0.041, "t": 1.2},
                              "news_negative_vs_rest": {"days": 25, "mean": -0.013, "t": -1.6},
                              "social_bearish_vs_rest": {"days": 22, "mean": -0.004, "t": -0.5},
                              "weights": {"basis": "evidence", "news_score": 0.75, "st_bull_ratio": 0.25}},
                "news_ab": {"signals": 180, "vetoed_share": 0.08, "technical_only": 0.004,
                            "technical_plus_news": 0.007, "gain_from_news": 0.003, "t": 1.1}}
    registry["experiments"].update({
        "news_negative": {"kind": "bearish_signal", "state": "observing", "evidence": {"days": 25, "mean": -0.013, "t": -1.6}},
        "news_veto": {"kind": "news_arm", "state": "trial", "evidence": {"n_news": 6, "n_control": 7, "difference": 0.9, "t": 0.4}}})
    _, body = build_review(_data(experiments=registry), TODAY)
    assert "900 stock-days scanned over 30 days" in body
    assert "news score vs next 10 days: rank correlation +0.041 (t = +1.20, 28 days)" in body
    assert "stocks with negative news vs the rest: -1.30% over 10 days" in body
    assert "stocks with bearish social vs the rest: -0.40% over 10 days" in body
    assert "source weights (evidence): news 75%, StockTwits 25%" in body
    assert "news_negative: **observing** — 25 days of evidence, flagged vs rest -1.30%" in body
    assert "news_veto: **trial** — 6 with news vs 7 without closed trades, difference +0.90% per trade" in body
    assert "technical only +0.40% over 10 days vs SPY, technical + news +0.70%" in body


def test_review_reminds_of_a_decision_under_review_and_says_when_it_is_overdue():
    from weekly_review import build_review
    exps = {"experiments": {"ma50_room": {"kind": "entry_signal", "state": "adopted", "evidence": {"days": 3}}},
            "review": {"due": "2026-12-05", "what": "6% room, ATR stop, 80/20 weights",
                       "revert_if": "clearly worse than the rules before it"}}
    _, body = build_review(_data(experiments=exps), date(2026, 11, 28))
    assert "Decision review due 2026-12-05 (in 7 days)" in body and "6% room, ATR stop, 80/20 weights" in body
    _, body = build_review(_data(experiments=exps), date(2026, 12, 12))
    assert "7 days OVERDUE" in body


def _diamond_doc(day="2026-10-10"):
    row = lambda rank, t, total: {"rank": rank, "ticker": t, "name": f"{t} Inc", "score": {"total": total},
                                  "market_value": 2.1e9, "revenue_growth": 0.31, "margin_change": 0.12,
                                  "ttm_op_margin": 0.01}
    return {"generated_at": f"{day}T07:31:00", "coverage": {"with_metrics": 3100, "passed_financial_gates": 140,
                                                           "watchlist": 2},
            "watchlist": [row(1, "AAA", 81.5), row(2, "BBB", 70.0)]}


def test_diamond_section_lists_the_top_names_with_rank_change():
    from datetime import date
    from weekly_review import diamond_section
    history = [{"date": "2026-10-03", "ticker": "AAA", "rank": 4, "total": 70.0},
               {"date": "2026-10-10", "ticker": "AAA", "rank": 1, "total": 81.5},
               {"date": "2026-10-10", "ticker": "BBB", "rank": 2, "total": 70.0}]
    text = "\n".join(diamond_section(_diamond_doc(), history, date(2026, 10, 10)))
    assert "## Diamond watchlist" in text
    assert "| 1 | AAA | 81.5 | up 3 |" in text and "| 2 | BBB | 70.0 | new |" in text
    assert "+31%" in text and "+12 pts" in text and "$2.1B" in text
    assert "3,100" in text and "140" in text


def test_diamond_section_without_a_list_or_with_an_old_one_says_so():
    from datetime import date
    from weekly_review import diamond_section
    assert "no list" in "\n".join(diamond_section(None, None, date(2026, 10, 10)))
    old = "\n".join(diamond_section(_diamond_doc("2026-09-26"), [], date(2026, 10, 10)))
    assert "14 days old" in old


def test_diamond_section_escapes_company_names():
    from datetime import date
    from weekly_review import diamond_section
    doc = _diamond_doc()
    doc["watchlist"][0]["name"] = "Evil | [link](http://x) Inc"
    text = "\n".join(diamond_section(doc, [], date(2026, 10, 10)))
    assert "[link](http://x)" not in text


def test_a_malformed_diamond_file_cannot_stop_the_weekly_review():
    from datetime import date
    from weekly_review import build_review
    bad = {"generated_at": "2026-10-10T07:31:00", "coverage": {"with_metrics": float("nan")},
           "watchlist": [{"rank": float("inf"), "ticker": ["x"], "score": "oops", "market_value": None}]}
    for doc, history in ((bad, [{"date": 5, "ticker": "AAA"}]), ({"watchlist": {"a": 1}}, "nope")):
        title, body = build_review({"diamonds": doc, "diamond_history": history}, date(2026, 10, 10))
        assert "## Diamond watchlist" in body and "## Macro theses" in body
        assert "could not be read" in body
