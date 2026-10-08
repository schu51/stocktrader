# tests/test_diamond_data.py
import sys
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from diamond_data import company_metrics, instant_at, last_quarters, quarterly_series


def q(start, end, val):
    return {"start": start, "end": end, "val": val}


CAL_2025 = [q("2025-01-01", "2025-03-31", 10), q("2025-04-01", "2025-06-30", 20),
            q("2025-07-01", "2025-09-30", 30), q("2025-01-01", "2025-12-31", 100)]


def test_fourth_quarter_is_the_year_minus_the_three_reported():
    s = quarterly_series(CAL_2025)
    assert s[date(2025, 12, 31)] == 40
    assert len(s) == 4


def test_fourth_quarter_is_unknown_when_a_quarter_is_missing():
    s = quarterly_series(CAL_2025[1:])           # Q1 never reported
    assert date(2025, 12, 31) not in s


def test_a_reported_fourth_quarter_is_not_overwritten():
    s = quarterly_series(CAL_2025 + [q("2025-10-01", "2025-12-31", 41)])
    assert s[date(2025, 12, 31)] == 41


def test_fiscal_year_not_ending_in_december():
    rows = [q("2024-10-01", "2024-12-31", 5), q("2025-01-01", "2025-03-31", 6),
            q("2025-04-01", "2025-06-30", 7), q("2024-10-01", "2025-09-30", 30)]
    assert quarterly_series(rows)[date(2025, 9, 30)] == 12


def test_duplicate_period_resolves_to_the_larger_absolute_value_every_time():
    rows = [q("2025-01-01", "2025-03-31", 10), q("2025-01-01", "2025-03-31", 12)]
    assert quarterly_series(rows) == quarterly_series(list(reversed(rows))) == {date(2025, 3, 31): 12}


def _eight(vals, last_end=date(2026, 6, 30)):
    """Eight calendar quarters ending at last_end, newest value first in `vals`."""
    ends = [date(2026, 6, 30), date(2026, 3, 31), date(2025, 12, 31), date(2025, 9, 30),
            date(2025, 6, 30), date(2025, 3, 31), date(2024, 12, 31), date(2024, 9, 30)]
    return dict(zip(ends, vals))


def test_last_quarters_returns_newest_first_and_respects_the_cutoff():
    s = _eight([8, 7, 6, 5, 4, 3, 2, 1])
    assert last_quarters(s, date(2026, 7, 15)) == [8, 7, 6, 5, 4, 3, 2, 1]
    assert last_quarters(s, date(2026, 5, 1)) is None            # only seven quarters by then


def test_a_gap_between_quarters_is_unknown_not_summed():
    s = _eight([8, 7, 6, 5, 4, 3, 2, 1])
    del s[date(2025, 9, 30)]
    s[date(2024, 6, 30)] = 0                                      # eight values, but not consecutive
    assert last_quarters(s, date(2026, 7, 15)) is None


def test_instant_at_takes_the_nearest_within_tolerance():
    rows = [{"end": "2026-06-30", "val": 50}, {"end": "2026-03-31", "val": 40}]
    assert instant_at(rows, date(2026, 6, 28)) == 50
    assert instant_at(rows, date(2026, 1, 15)) is None


def _rows(series):
    """{end: val} -> frame rows with a 91-day quarter."""
    from datetime import timedelta
    return [q((end - timedelta(days=90)).isoformat(), end.isoformat(), val) for end, val in series.items()]


def _fin(**over):
    fin = {
        "revenue":   _rows(_eight([130, 125, 120, 115, 100, 96, 92, 88])),
        "op_income": _rows(_eight([6, 2, -1, -3, -8, -10, -12, -14])),
        "gross_profit": _rows(_eight([91, 87, 84, 80, 68, 65, 62, 59])),
        "cost_of_revenue": [], "rd": _rows(_eight([20] * 8)),
        "shares":    _rows(_eight([105, 105, 104, 104, 100, 100, 100, 100])),
        "cash": [{"end": "2026-06-30", "val": 300}], "investments": [],
        "backlog": [{"end": "2026-06-30", "val": 260}, {"end": "2025-06-30", "val": 200}],
        "prepaid": [],
    }
    fin.update(over)
    return fin


def test_metrics_for_a_company_turning_profitable():
    m = company_metrics(_fin(), date(2026, 8, 15))
    assert m["latest_quarter_end"] == "2026-06-30"
    assert (m["ttm_revenue"], m["prior_ttm_revenue"]) == (490, 376)
    assert round(m["revenue_growth"], 4) == round(490 / 376 - 1, 4)
    assert round(m["ttm_op_margin"], 4) == round(4 / 490, 4)
    assert round(m["prior_ttm_op_margin"], 4) == round(-44 / 376, 4)
    assert m["seq_improvements"] == 3
    assert m["latest_q_profitable"] is True and m["year_ago_q_profitable"] is False
    assert round(m["ttm_gross_margin"], 4) == round(342 / 490, 4)
    assert m["runway_years"] is None                 # profitable over the year: runway does not apply
    assert round(m["shares_growth"], 4) == 0.05
    assert round(m["backlog_growth"], 4) == 0.30 and m["prepaid_growth"] is None
    assert round(m["rd_share"], 4) == round(80 / 490, 4)


def test_runway_is_cash_over_the_yearly_operating_loss():
    m = company_metrics(_fin(op_income=_rows(_eight([-10, -15, -20, -25, -40, -40, -40, -40]))), date(2026, 8, 15))
    assert round(m["runway_years"], 3) == round(300 / 70, 3)


def test_gross_profit_falls_back_to_revenue_minus_cost():
    m = company_metrics(_fin(gross_profit=[], cost_of_revenue=_rows(_eight([39, 38, 36, 35, 32, 31, 30, 29]))),
                        date(2026, 8, 15))
    assert round(m["ttm_gross_margin"], 4) == round((490 - 148) / 490, 4)


def test_unknown_inputs_give_none_never_an_exception():
    assert company_metrics(_fin(revenue=[]), date(2026, 8, 15)) is None
    assert company_metrics(_fin(op_income=_rows(_eight([1, 1, 1]))), date(2026, 8, 15)) is None
    zero = _fin(revenue=_rows(_eight([130, 125, 120, 115, 0, 96, 92, 88])))
    assert company_metrics(zero, date(2026, 8, 15)) is None      # a quarter with no revenue: unknown
    assert company_metrics({}, date(2026, 8, 15)) is None
    m = company_metrics(_fin(shares=[], cash=[], gross_profit=[]), date(2026, 8, 15))
    assert m["shares"] is None and m["cash"] is None and m["ttm_gross_margin"] is None


def test_stale_company_is_dropped():
    assert company_metrics(_fin(), date(2027, 1, 15)) is None    # latest quarter is more than 160 days old


def test_periods_reach_back_far_enough_to_derive_the_oldest_fourth_quarter():
    from diamond_data import periods_for
    p = periods_for(date(2026, 8, 15))
    assert p["quarters"][0] == "CY2026Q3" and p["quarters"][-1] == "CY2023Q2" and len(p["quarters"]) == 14
    assert p["years"] == ["CY2026", "CY2025", "CY2024", "CY2023"]
    # point-in-time figures for the three latest quarter ends, and the same three a year before
    assert p["instants"] == ["CY2026Q3I", "CY2026Q2I", "CY2026Q1I", "CY2025Q3I", "CY2025Q2I", "CY2025Q1I"]


def test_reference_quarter_is_the_latest_one_companies_have_had_time_to_file():
    from diamond_data import periods_for
    assert periods_for(date(2026, 8, 15))["reference"] == "CY2026Q2"     # 46 days after June 30
    assert periods_for(date(2026, 10, 8))["reference"] == "CY2026Q2"     # Q3 ended eight days ago
    assert periods_for(date(2026, 8, 10))["reference"] == "CY2026Q1"     # 41 days: filings still arriving
    assert periods_for(date(2023, 5, 30))["reference"] == "CY2023Q1"     # an --as-of cutoff, 60 days after


def test_load_financials_pools_tags_by_company_and_reports_coverage():
    from diamond_data import load_financials
    calls = []

    def fetch(taxonomy, tag, unit, period, session=None):
        calls.append((tag, period))
        if tag == "RevenueFromContractWithCustomerExcludingAssessedTax" and period == "CY2026Q2":
            return [{"cik": 1, "start": "2026-04-01", "end": "2026-06-30", "val": 130}]
        if tag == "Revenues" and period == "CY2026Q2":
            return [{"cik": 2, "start": "2026-04-01", "end": "2026-06-30", "val": 70},
                    {"cik": 1, "start": "2026-04-01", "end": "2026-06-30", "val": 999}]   # second tag for cik 1
        if tag == "CashAndCashEquivalentsAtCarryingValue" and period == "CY2026Q2I":
            return [{"cik": 1, "end": "2026-06-30", "val": 300}]
        return []

    fins, coverage = load_financials(date(2026, 8, 15), fetch=fetch)
    assert [r["val"] for r in fins[1]["revenue"]] == [130]            # first tag wins for a company
    assert [r["val"] for r in fins[2]["revenue"]] == [70]
    assert fins[1]["cash"] == [{"end": "2026-06-30", "val": 300}]
    assert coverage == {"reference_quarter": "CY2026Q2", "companies_with_revenue": 2}
    assert ("OperatingIncomeLoss", "CY2025") in calls                  # full years are fetched for Q4


def test_fetch_frame_treats_404_as_empty_and_anything_else_as_failure():
    import pytest
    from diamond_data import FrameError, fetch_frame

    class Resp:
        def __init__(self, status, payload=None):
            self.status_code, self._payload = status, payload
        def json(self):
            return self._payload

    class Session:
        def __init__(self, *responses):
            self.responses, self.headers_seen = list(responses), []
        def get(self, url, headers=None, timeout=None):
            self.headers_seen.append(headers)
            return self.responses.pop(0)

    ok = Session(Resp(200, {"data": [{"cik": 1, "val": 5}]}))
    assert fetch_frame("us-gaap", "Revenues", "USD", "CY2026Q2", session=ok, pause=0) == [{"cik": 1, "val": 5}]
    assert "User-Agent" in ok.headers_seen[0]
    assert fetch_frame("us-gaap", "Revenues", "USD", "CY2026Q3", session=Session(Resp(404)), pause=0) == []
    with pytest.raises(FrameError):
        fetch_frame("us-gaap", "Revenues", "USD", "CY2026Q2", session=Session(Resp(403), Resp(403), Resp(403)), pause=0)
    retried = Session(Resp(500), Resp(200, {"data": []}))
    assert fetch_frame("us-gaap", "Revenues", "USD", "CY2026Q2", session=retried, pause=0) == []


def test_fourth_quarter_reference_counts_companies_from_the_full_year_report():
    # Almost nobody reports Q4 on its own, so the single-quarter figure for a Q4
    # is always thin. The full-year report is what shows the data is whole.
    from diamond_data import load_financials

    def fetch(taxonomy, tag, unit, period, session=None):
        if tag != "Revenues":
            return []
        if period == "CY2025Q4":
            return [{"cik": 1, "start": "2025-10-01", "end": "2025-12-31", "val": 30}]
        if period == "CY2025":
            return [{"cik": c, "start": "2025-01-01", "end": "2025-12-31", "val": 100} for c in (1, 2, 3)]
        return []

    _, coverage = load_financials(date(2026, 3, 1), fetch=fetch)
    assert coverage == {"reference_quarter": "CY2025Q4", "companies_with_revenue": 3}


def test_share_count_for_a_fourth_quarter_is_the_years_average_not_a_subtraction():
    # A share count is a level, not a flow: the year's figure minus three
    # quarters is meaningless (and negative). The year's own average stands in.
    from diamond_data import level_series
    rows = [q("2025-01-01", "2025-03-31", 100), q("2025-04-01", "2025-06-30", 102),
            q("2025-07-01", "2025-09-30", 104), q("2025-01-01", "2025-12-31", 103)]
    s = level_series(rows)
    assert s[date(2025, 12, 31)] == 103 and s[date(2025, 9, 30)] == 104
    assert level_series(rows + [q("2025-10-01", "2025-12-31", 106)])[date(2025, 12, 31)] == 106


def test_dilution_is_measured_across_a_fourth_quarter():
    ends = [date(2025, 12, 31), date(2025, 9, 30), date(2025, 6, 30), date(2025, 3, 31),
            date(2024, 12, 31), date(2024, 9, 30), date(2024, 6, 30), date(2024, 3, 31)]
    series = lambda vals: _rows(dict(zip(ends, vals)))
    shares = [r for r in series([0, 104, 102, 100, 0, 99, 98, 97]) if r["val"]]          # no Q4 rows...
    shares += [q("2025-01-01", "2025-12-31", 103), q("2024-01-01", "2024-12-31", 98)]     # ...only the years
    fin = _fin(revenue=series([130, 125, 120, 115, 100, 96, 92, 88]),
               op_income=series([6, 2, -1, -3, -8, -10, -12, -14]),
               gross_profit=series([91, 87, 84, 80, 68, 65, 62, 59]), rd=[], shares=shares,
               cash=[{"end": "2025-12-31", "val": 300}], backlog=[])
    m = company_metrics(fin, date(2026, 3, 1))
    assert m["latest_quarter_end"] == "2025-12-31"
    assert m["shares"] == 103 and round(m["shares_growth"], 4) == round(103 / 98 - 1, 4)
