# tests/test_diamond_run.py
import json
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from diamond_run import CoverageError, build_watchlist, update_history

CUTOFF = date(2026, 8, 15)
ENDS = ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31", "2024-12-31", "2024-09-30"]
STARTS = ["2026-04-01", "2026-01-01", "2025-10-01", "2025-07-01", "2025-04-01", "2025-01-01", "2024-10-01", "2024-07-01"]


def _rows(vals, scale=1e6):
    return [{"start": s, "end": e, "val": v * scale} for s, e, v in zip(STARTS, ENDS, vals)]


def _fin(op=(6, 2, -1, -3, -8, -10, -12, -14)):
    return {"revenue": _rows([130, 125, 120, 115, 100, 96, 92, 88]), "op_income": _rows(op),
            "gross_profit": _rows([91, 87, 84, 80, 68, 65, 62, 59]), "cost_of_revenue": [], "rd": [],
            "shares": _rows([105, 105, 104, 104, 100, 100, 100, 100]),
            "cash": [{"end": "2026-06-30", "val": 300e6}], "investments": [], "backlog": [], "prepaid": []}


FINS = {1: _fin(), 2: _fin(op=(6, 2, 1, 0, -4, -5, -6, -7)),            # 2 improves, but less
        3: _fin(op=(-40, -40, -40, -40, -20, -20, -20, -20)),          # losses growing: not turning
        4: _fin(), 5: _fin()}
LISTINGS = {1: {"ticker": "AAA", "name": "A Inc", "exchange": "Nasdaq"},
            2: {"ticker": "BBB", "name": "B Inc", "exchange": "NYSE"},
            3: {"ticker": "CCC", "name": "C Inc", "exchange": "NYSE"},
            4: {"ticker": "BANK", "name": "Bank Inc", "exchange": "NYSE"},
            5: {"ticker": "TINY", "name": "Tiny Inc", "exchange": "Nasdaq"}}
COVERAGE = {"reference_quarter": "CY2026Q2", "companies_with_revenue": 3000}


@pytest.fixture(autouse=True)
def _small_market(monkeypatch):
    """The fixtures are five companies; the real floors are for a market of thousands."""
    import diamond_run
    monkeypatch.setattr(diamond_run, "MIN_LISTED", 1)
    monkeypatch.setattr(diamond_run, "MIN_WITH_METRICS", 1)


def _prices(tickers):
    out = {t: {"closes": [20.0] * 60, "volumes": [400_000.0] * 60} for t in tickers}
    if "TINY" in out:
        out["TINY"] = {"closes": [1.0] * 60, "volumes": [400_000.0] * 60}     # $105M: too small, and a penny stock
    return out


def _sic(cik):
    return 6021 if cik == 4 else 7372


def test_watchlist_ranks_survivors_and_counts_every_drop():
    doc = build_watchlist(CUTOFF, FINS, COVERAGE, LISTINGS, _prices, _sic)
    assert [(r["rank"], r["ticker"]) for r in doc["watchlist"]] == [(1, "AAA"), (2, "BBB")]
    assert doc["watchlist"][0]["score"]["total"] > doc["watchlist"][1]["score"]["total"]
    assert doc["watchlist"][0]["market_value"] == 105e6 * 20.0
    assert doc["gate_failures"]["turning"] == 1
    assert doc["size_failures"] == {"too_small": 1, "illiquid": 1, "penny": 1}      # TINY, on all three counts
    c = doc["coverage"]
    assert (c["listed"], c["with_metrics"], c["passed_financial_gates"], c["passed_size"],
            c["excluded_industry"], c["watchlist"]) == (5, 5, 4, 3, 1, 2)
    assert doc["cutoff"] == "2026-08-15" and doc["reference_quarter"] == "CY2026Q2"


def test_prices_are_only_asked_for_companies_that_passed_the_financial_gates():
    asked = []
    build_watchlist(CUTOFF, FINS, COVERAGE, LISTINGS, lambda t: asked.extend(t) or _prices(t), _sic)
    assert sorted(asked) == ["AAA", "BANK", "BBB", "TINY"]


def test_top_n_and_stable_order_on_a_tie():
    doc = build_watchlist(CUTOFF, {1: _fin(), 2: _fin()}, COVERAGE,
                          {1: LISTINGS[2], 2: LISTINGS[1]}, _prices, _sic, top=1)
    assert [r["ticker"] for r in doc["watchlist"]] == ["AAA"]            # equal scores: alphabetical


def test_too_few_companies_reporting_stops_the_run():
    with pytest.raises(CoverageError, match="2499"):
        build_watchlist(CUTOFF, FINS, dict(COVERAGE, companies_with_revenue=2499), LISTINGS, _prices, _sic)


def test_missing_prices_for_too_many_stops_the_run():
    with pytest.raises(CoverageError, match="price"):
        build_watchlist(CUTOFF, FINS, COVERAGE, LISTINGS, lambda t: {"AAA": _prices(["AAA"])["AAA"]}, _sic)


def test_history_adds_a_week_replaces_a_rerun_and_keeps_104_dates():
    doc = {"generated_at": "2026-10-10T07:31:00", "watchlist": [
        {"rank": 1, "ticker": "AAA", "score": {"total": 80.0}}, {"rank": 2, "ticker": "BBB", "score": {"total": 70.0}}]}
    old = [{"date": "2026-10-03", "ticker": "AAA", "rank": 3, "total": 60.0},
           {"date": "2026-10-10", "ticker": "ZZZ", "rank": 1, "total": 99.0}]      # an earlier run today
    h = update_history(old, doc)
    assert h == [{"date": "2026-10-03", "ticker": "AAA", "rank": 3, "total": 60.0},
                 {"date": "2026-10-10", "ticker": "AAA", "rank": 1, "total": 80.0},
                 {"date": "2026-10-10", "ticker": "BBB", "rank": 2, "total": 70.0}]
    many = [{"date": f"2024-{m:02d}-{d:02d}", "ticker": "AAA", "rank": 1, "total": 1.0}
            for m in range(1, 13) for d in range(1, 11)]                           # 120 dates
    assert len({r["date"] for r in update_history(many, doc)}) == 104


def test_a_failed_run_leaves_last_weeks_file_alone(tmp_path, monkeypatch):
    import diamond_run
    out = tmp_path / "diamonds.json"
    out.write_text(json.dumps({"generated_at": "last week"}))
    monkeypatch.setattr(diamond_run, "OUT_FILE", out)
    monkeypatch.setattr(diamond_run, "HISTORY_FILE", tmp_path / "diamond_history.json")
    monkeypatch.setattr(diamond_run, "load_financials",
                        lambda cutoff: (FINS, dict(COVERAGE, companies_with_revenue=10)))
    monkeypatch.setattr(diamond_run, "fetch_listings", lambda: [[c, v["name"], v["ticker"], v["exchange"]]
                                                                 for c, v in LISTINGS.items()])
    assert diamond_run.main([]) == 1
    assert json.loads(out.read_text()) == {"generated_at": "last week"}
    assert not (tmp_path / "diamond_history.json").exists()


def test_as_of_cutoff_is_sixty_days_after_the_quarter_ends():
    from diamond_run import as_of_cutoff
    assert as_of_cutoff("2023Q1") == date(2023, 5, 30)
    assert as_of_cutoff("2022Q4") == date(2023, 3, 1)
    with pytest.raises(ValueError):
        as_of_cutoff("2023")


def test_forward_returns_from_first_close_after_start_to_last_before_the_end():
    import pandas as pd
    from diamond_run import forward_returns
    idx = pd.to_datetime(["2023-05-30", "2023-05-31", "2024-05-29", "2024-05-31"])
    frame = pd.DataFrame({"AAA": [10.0, 11.0, 20.0, 99.0], "BBB": [5.0, 5.0, float("nan"), 4.0]}, index=idx)
    r = forward_returns(["AAA", "BBB", "GONE"], date(2023, 5, 30), download=lambda tickers, start, end: frame)
    assert r == {"AAA": 1.0}        # BBB has no close at the end of the window; GONE has no data


def test_explain_reports_why_a_company_is_or_is_not_listed():
    from diamond_run import explain
    text = explain("CCC", CUTOFF, FINS, LISTINGS)
    assert "CCC" in text and "turning" in text
    assert "no listing" in explain("NOPE", CUTOFF, FINS, LISTINGS)


# ── fixes from the whole-branch review ───────────────────────────────────────

def test_an_implausibly_small_result_stops_the_run(monkeypatch):
    import diamond_run
    monkeypatch.setattr(diamond_run, "MIN_LISTED", 3000)
    with pytest.raises(CoverageError, match="listed"):
        build_watchlist(CUTOFF, FINS, COVERAGE, LISTINGS, _prices, _sic)
    monkeypatch.setattr(diamond_run, "MIN_LISTED", 1)
    monkeypatch.setattr(diamond_run, "MIN_WITH_METRICS", 1500)
    with pytest.raises(CoverageError, match="eight quarters"):
        build_watchlist(CUTOFF, FINS, COVERAGE, LISTINGS, _prices, _sic)


def test_an_empty_list_is_never_written_over_last_weeks(monkeypatch):
    with pytest.raises(CoverageError, match="empty"):
        build_watchlist(CUTOFF, {3: FINS[3]}, COVERAGE, {3: LISTINGS[3]}, _prices, _sic)


def test_as_of_judges_companies_only_on_quarters_ended_by_that_quarter():
    # Priced on 5 July, the June quarter exists in the SEC data, but a run "as
    # of Q1" may only see quarters ended by 31 March, and by then these
    # companies have seven quarters, not eight.
    seen = build_watchlist(date(2026, 7, 5), FINS, COVERAGE, LISTINGS, _prices, _sic)
    assert {r["latest_quarter_end"] for r in seen["watchlist"]} == {"2026-06-30"}
    assert seen["data_through"] == "2026-07-05"
    with pytest.raises(CoverageError, match="eight quarters"):
        build_watchlist(date(2026, 7, 5), FINS, COVERAGE, LISTINGS, _prices, _sic, data_cutoff=date(2026, 3, 31))


def test_as_of_prices_undo_later_stock_splits():
    from diamond_run import unsplit
    quote = {"closes": [40.0, 41.0], "volumes": [1000.0, 2000.0]}
    splits = [("2022-01-10", 2.0), ("2024-06-10", 10.0), ("2025-01-02", 2.0)]
    assert unsplit(quote, date(2023, 5, 30), splits) == {"closes": [800.0, 820.0], "volumes": [50.0, 100.0]}
    assert unsplit(quote, date(2025, 6, 1), splits) == quote            # no split after the date
    assert unsplit(quote, date(2023, 5, 30), None) == quote             # splits unknown: left as they are
