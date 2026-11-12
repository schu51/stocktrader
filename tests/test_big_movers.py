"""Big movers: detection, pre-move state without lookahead, catalysts, precursors, watch list."""
import re
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

import big_movers as bm


def _bars(n_days=560, symbols=("AAA", "BBB", "CCC", "DDD", "EEE", "SPY"), seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-06-03", periods=n_days)
    close = pd.DataFrame(
        {s: 50 * np.cumprod(1 + rng.normal(0.0005, 0.01, n_days)) for s in symbols}, index=idx)
    vol = pd.DataFrame(1_000_000.0, index=idx, columns=list(symbols))
    return {"Close": close, "Open": close.shift(1).fillna(close) * 1.0, "High": close * 1.01,
            "Low": close * 0.99, "Volume": vol}


def _jump(data, sym, pos, pct=0.25):
    """Make `sym` close `pct` above the prior close on row `pos`, staying up afterwards."""
    c = data["Close"]
    factor = (1 + pct) * c[sym].iloc[pos - 1] / c[sym].iloc[pos]
    c.iloc[pos:, c.columns.get_loc(sym)] *= factor
    data["Open"] = c.shift(1).fillna(c)
    data["High"] = c * 1.01


def test_jump_is_found_and_pre_move_state_uses_the_prior_close_only():
    data = _bars()
    _jump(data, "AAA", 400)
    day = data["Close"].index[400]
    assert bool(bm.jump_mask(data).loc[day, "AAA"])
    feats = bm.features(data)
    pre = bm.pre_move(feats, "AAA", day)
    # ret_1m before the jump cannot include the 25% day itself
    assert pre["ret_1m"] < 0.2
    assert pre["ret_1m"] == pytest.approx(round(float(feats["ret_1m"]["AAA"].iloc[399]), 3))


def test_illiquid_or_cheap_stocks_are_not_big_movers():
    data = _bars()
    data["Volume"]["BBB"] = 1_000.0           # ~$50k a day
    _jump(data, "BBB", 400)
    assert not bm.jump_mask(data).loc[data["Close"].index[400], "BBB"]


def test_earnings_panel_marks_the_report_day_and_the_session_after():
    idx = pd.bdate_range("2026-11-02", periods=8)
    # filed Saturday the 7th -> next session Monday the 9th
    panel = bm.earnings_panel(idx, ["X"], {"X": ["2026-11-07"]})
    marked = [d.date().isoformat() for d in idx[panel["X"].values]]
    assert marked == ["2026-11-09", "2026-11-10"]


def test_classify_prefers_merger_documents_then_earnings():
    d = date(2026, 11, 11)
    filings = [{"form": "SC TO-T", "date": "2026-11-10", "items": ""}]
    assert bm.classify(d, filings, [], True, 0, 0.0)[0] == "mna"
    filings = [{"form": "8-K", "date": "2026-11-10", "items": "2.02,9.01"}]
    cat, ev = bm.classify(d, filings, [], True, 0, 0.0)
    assert cat == "earnings" and "2.02" in ev[0]


def test_classify_reads_headlines_and_falls_back_to_sector_and_market():
    d = date(2026, 11, 11)
    h = [{"title": "Acme shares soar after FDA approval of lead drug", "published": "2026-11-11T13:00:00+00:00"}]
    assert bm.classify(d, [], h, False, 0, 0.0)[0] == "regulatory"
    old = [{"title": "Acme earnings beat estimates", "published": "2026-10-01T13:00:00+00:00"}]
    assert bm.classify(d, [], old, False, 0, 0.0)[0] == "unknown"      # too old to explain today
    assert bm.classify(d, [], [], False, 4, 0.0)[0] == "sector_move"
    assert bm.classify(d, [], [], False, 0, 0.03)[0] == "market_move"
    assert bm.classify(d, [], [], False, 0, 0.0)[0] == "unknown"


def test_precursor_table_measures_lift_against_all_stock_days():
    idx = pd.bdate_range("2026-01-01", periods=100)
    cols = [f"S{i}" for i in range(10)]
    eligible = pd.DataFrame(True, index=idx, columns=cols)
    pattern = pd.DataFrame(False, index=idx, columns=cols)
    jumps = pd.DataFrame(False, index=idx, columns=cols)
    pattern.iloc[::10, 0] = True                 # 10 pattern days, 5 of them jump
    jumps.iloc[0:50:10, 0] = True
    jumps.iloc[5, 3] = True                      # 1 jump outside the pattern
    ret = jumps.astype(float) * 0.25
    t = bm.precursor_table({"p": ("test", pattern)}, jumps, ret, eligible)
    assert t["jumps"] == 6 and t["stock_days"] == 1000
    row = t["patterns"][0]
    assert row["hits"] == 5 and row["days"] == 10
    assert row["lift"] == pytest.approx(0.5 / 0.006, rel=1e-2)
    assert row["watchable"] is False             # 10 days is too little evidence


def test_drift_is_from_the_next_open_and_net_of_spy():
    data = _bars()
    _jump(data, "AAA", 400)
    c = data["Close"]
    ev = [{"symbol": "AAA", "date": c.index[400].date().isoformat()}]
    bm.drift(ev, data, c["SPY"])
    entry = data["Open"]["AAA"].iloc[401]
    raw = c["AAA"].iloc[405] / entry - 1
    spy = c["SPY"].iloc[405] / c["SPY"].iloc[401] - 1
    assert ev[0]["drift"]["d5"] == pytest.approx(raw - spy, abs=1e-4)


def test_expected_earnings_uses_the_same_quarter_a_year_earlier():
    dates = ["2025-11-12", "2026-02-20", "2026-05-14", "2026-08-13"]
    assert bm.expected_earnings(dates, date(2026, 11, 8)) == "2026-11-11"
    assert bm.expected_earnings(dates, date(2026, 10, 1)) is None          # not within a week
    assert bm.expected_earnings(dates + ["2026-11-05"], date(2026, 11, 8)) is None  # already reported


def test_watch_list_only_uses_patterns_that_earned_it():
    state = {
        "close": pd.Series({"X": 10.0, "Y": 10.0}),
        "ret_1m": pd.Series({"X": -0.30, "Y": 0.05}),
        "dist_52w_high": pd.Series({"X": -0.5, "Y": -0.1}),
        "momentum_gate": pd.Series({"X": False, "Y": True}),
        "vol_surge": pd.Series({"X": 1.0, "Y": 1.0}),
        "rs_rank": pd.Series({"X": 3, "Y": 90}),
    }
    table = {"patterns": [
        {"pattern": "earnings_beaten_down", "description": "d", "hit_rate": 0.05, "lift": 25, "watchable": True},
        {"pattern": "momentum_leader", "description": "m", "hit_rate": 0.01, "lift": 1.2, "watchable": False},
    ]}
    w = bm.watch_list(table, state, {"X": "2026-11-12"}, {"X": "technology"})
    assert [x["symbol"] for x in w] == ["X"]
    assert w[0]["earnings_expected"] == "2026-11-12"


def test_score_watch_records_hits():
    out = bm.score_watch([{"symbol": "X", "pattern": "p"}, {"symbol": "Z", "pattern": "p"}],
                         pd.Series({"X": 0.22, "Y": 0.0}))
    assert out == [{"symbol": "X", "pattern": "p", "ret": 0.22, "hit": True}]


def test_run_end_to_end_with_injected_sources(tmp_path, monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    data = _bars()
    _jump(data, "CCC", len(data["Close"]) - 1, 0.30)       # today's mover
    last = data["Close"].index[-1].date()
    for name in ("LATEST_FILE", "LOG_FILE", "CACHE_FILE", "READS_FILE", "THEMES_FILE"):
        monkeypatch.setattr(bm, name, tmp_path / f"{name}.json")
    monkeypatch.setattr(bm, "NEWS_PAUSE", 0)
    (tmp_path / "THEMES_FILE.json").write_text(
        '{"themes": {"t": {"description": "d", "members": ["AAA", "BBB", "CCC", "DDD"]}}}')
    filed = (last - timedelta(days=1)).isoformat()
    subs = {"filings": {"recent": {"form": ["8-K"], "filingDate": [filed], "accessionNumber": ["1"],
                                   "primaryDocument": ["a.htm"], "items": ["2.02,9.01"]}}}
    (tmp_path / "READS_FILE.json").write_text("{}")
    out = bm.run(
        bars_fn=lambda tickers, lookback: data,
        movers_fn=lambda: [],
        news_fn=lambda sym, name, session: [],
        index_fn=lambda session: {s: {"cik": i, "name": s} for i, s in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"])},
        submissions_fn=lambda cik, session, since: subs,
        universe_fn=lambda: {s: "technology" for s in ["AAA", "BBB", "CCC", "DDD", "EEE"]},
    )
    assert [e["symbol"] for e in out["today"]] == ["CCC"]
    assert out["today"][0]["catalyst"] == "earnings"
    assert out["lessons"]["profile_1y"]["events"] >= 1
    assert (tmp_path / "LOG_FILE.json").exists()
    assert "Watch-only" in out["note"]


def test_a_corrected_label_from_the_evening_review_wins():
    log = {"events": [{"symbol": "X", "date": "2026-11-11", "catalyst": "unknown"}]}
    reads = {"X@2026-11-11": {"catalyst": "earnings", "why": "Q3 beat", "lesson": "l"}}
    out = bm.merge_log(log, [], date(2026, 11, 11), reads)
    assert out["events"][0]["catalyst"] == "earnings" and out["events"][0]["why"] == "Q3 beat"
    bad = {"X@2026-11-11": {"catalyst": "please trade this"}}
    out = bm.merge_log({"events": [{"symbol": "X", "date": "2026-11-11", "catalyst": "unknown"}]},
                       [], date(2026, 11, 11), bad)
    assert out["events"][0]["catalyst"] == "unknown"


def test_screener_publishes_a_fresh_watch_list_only(tmp_path):
    import json
    p = tmp_path / "bm.json"
    p.write_text(json.dumps({"date": date.today().isoformat(), "watch": [{"symbol": "X"}]}))
    assert bm.load_watch(p) == [{"symbol": "X"}]
    p.write_text(json.dumps({"date": "2020-01-01", "watch": [{"symbol": "X"}]}))
    assert bm.load_watch(p) == []
    assert bm.load_watch(tmp_path / "missing.json") == []


def test_dispatcher_runs_big_movers_after_the_close_on_weekdays():
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from dispatcher import due_workflows
    et = ZoneInfo("America/New_York")
    assert "big_movers.yml" in due_workflows(datetime(2026, 11, 11, 16, 31, tzinfo=et))
    assert "big_movers.yml" not in due_workflows(datetime(2026, 11, 14, 16, 31, tzinfo=et))


def test_theme_heat_is_the_median_of_the_other_members():
    idx = pd.bdate_range("2026-01-01", periods=10)
    close = pd.DataFrame({"A": 100.0, "B": 100.0, "C": 100.0, "D": 100.0}, index=idx)
    close.loc[idx[-1], "B"] = 120.0     # +20% over 5 days
    close.loc[idx[-1], "C"] = 110.0     # +10%
    heat = bm.theme_heat(close, {"t": ["A", "B", "C"]})
    assert heat.loc[idx[-1], "A"] == pytest.approx(0.15)      # median of B, C
    assert heat.loc[idx[-1], "B"] == pytest.approx(0.05)      # median of A, C
    assert pd.isna(heat.loc[idx[-1], "D"])                     # in no theme


def test_classify_theme_move_and_news():
    d = date(2026, 11, 11)
    assert bm.classify(d, [], [], False, 0, 0.0, 3, ["ai_compute"])[0] == "theme_move"
    h = [{"title": f"Acme story {i}", "published": "2026-11-11T14:00:00+00:00"} for i in range(4)]
    assert bm.classify(d, [], h, False, 0, 0.0)[0] == "news"
    # company-specific evidence outranks the theme
    e = [{"form": "8-K", "date": "2026-11-10", "items": "2.02"}]
    assert bm.classify(d, e, [], True, 0, 0.0, 3, ["ai_compute"])[0] == "earnings"


def test_news_snapshot_and_news_before():
    from datetime import datetime, timezone
    now = datetime(2026, 11, 11, 21, 0, tzinfo=timezone.utc)
    items = [{"title": "Acme wins big contract", "published": "2026-11-11T15:00:00+00:00"}] * 6 + \
            [{"title": "Acme note", "published": "2026-11-08T15:00:00+00:00"}] * 3
    snap = bm.news_snapshot(items, now)
    assert snap["n_1d"] == 6 and snap["pace"] == 0.5 and snap["surge"] == 6.0
    nb = bm.news_before(items, date(2026, 11, 11))
    assert nb["n_7d"] == 3                     # the jump day itself is left out


def test_news_precursors_compare_with_the_control_group():
    rows = [{"date": "2026-11-0%d" % (i % 9 + 1), "symbol": f"S{i}", "group": "control", "n_1d": 0,
             "surge": 0, "tone": 0, "next_ret": 0.0} for i in range(100)]
    rows += [{"date": "2026-11-01", "symbol": f"N{i}", "group": "theme", "n_1d": 8, "surge": 5,
              "tone": 0.3, "next_ret": 0.25 if i < 5 else 0.01} for i in range(10)]
    t = bm.news_precursors(rows)
    surge = next(p for p in t["patterns"] if p["pattern"] == "news_surge")
    assert surge["hits"] == 5 and surge["days"] == 10 and surge["lift"] > 3
    assert t["control_base_rate"] == 0.0


def test_news_sample_always_includes_a_control_group():
    syms = bm.news_symbols(["CRWV"], [], {"t": ["NBIS"]}, {}, [f"U{i}" for i in range(200)], date(2026, 11, 11))
    groups = [g for _, g in syms]
    assert groups[0] == "mover" and groups.count("control") == bm.NEWS_CONTROL
    # same day, same sample: reruns are reproducible
    assert syms == bm.news_symbols(["CRWV"], [], {"t": ["NBIS"]}, {}, [f"U{i}" for i in range(200)],
                                   date(2026, 11, 11))


def test_evening_review_can_add_a_theme_member(tmp_path):
    p = tmp_path / "themes.json"
    p.write_text('{"themes": {"ai_compute": {"description": "d", "members": ["NVDA"]}}}')
    themes = bm.load_themes(p, {"CRWV@2026-11-11": {"theme": "ai_compute"},
                                "BAD@2026-11-11": {"theme": "made_up"}})
    assert themes == {"ai_compute": ["NVDA", "CRWV"]}


def test_unthemed_stocks_moving_together_are_flagged_as_a_possible_theme():
    row = pd.Series({"A": 0.12, "B": 0.10, "C": 0.09, "D": 0.15, "E": 0.01, "SPY": 0.01})
    elig = pd.Series(True, index=row.index)
    sectors = {"A": "financials", "B": "financials", "C": "financials", "D": "financials", "E": "financials"}
    out = bm.possible_themes(row, elig, {"banks": ["D"]}, sectors)
    assert out == [{"sector": "financials", "symbols": ["A", "B", "C"], "median_ret": 0.1}]
    assert bm.possible_themes(row, elig, {"banks": ["A", "D"]}, sectors) == []   # only 2 left


def test_themes_file_is_valid_and_covers_more_than_tech():
    import json
    themes = json.loads(bm.THEMES_FILE.read_text())["themes"]
    assert {"biotech", "regional_banks", "oil_gas", "homebuilders", "retail_consumer", "gold_miners"} <= set(themes)
    for name, t in themes.items():
        assert t["description"] and len(t["members"]) >= 3, name
        assert all(re.fullmatch(r"[A-Z]{1,5}(-[A-Z]{1,2})?", m) for m in t["members"]), name


def test_finnhub_news_feeds_the_catalyst_and_its_calendar_the_watch(tmp_path, monkeypatch):
    data = _bars()
    _jump(data, "CCC", len(data["Close"]) - 1, 0.30)
    last = data["Close"].index[-1].date()
    for name in ("LATEST_FILE", "LOG_FILE", "CACHE_FILE", "READS_FILE", "THEMES_FILE"):
        monkeypatch.setattr(bm, name, tmp_path / f"{name}.json")
    monkeypatch.setattr(bm, "NEWS_PAUSE", 0)
    (tmp_path / "THEMES_FILE.json").write_text('{"themes": {}}')
    (tmp_path / "READS_FILE.json").write_text("{}")
    calls = {}

    def calendar(start, end, key):
        calls["calendar"] = (start, end, key)
        return {"AAA": {"date": (last + timedelta(days=2)).isoformat(), "hour": "amc", "eps_estimate": 0.1}}

    def fh_news(sym, start, end, key):
        calls.setdefault("news", []).append(sym)
        return [{"title": "Acme agrees to be acquired by BigCo", "publisher": "Reuters",
                 "published": f"{last.isoformat()}T14:00:00+00:00", "source": "finnhub"}]

    out = bm.run(
        bars_fn=lambda tickers, lookback: data,
        movers_fn=lambda: [],
        news_fn=lambda sym, name, session: [],
        index_fn=lambda session: {s: {"cik": i, "name": s} for i, s in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"])},
        submissions_fn=lambda cik, session, since: {},
        universe_fn=lambda: {s: "technology" for s in ["AAA", "BBB", "CCC", "DDD", "EEE"]},
        finnhub_key="k", calendar_fn=calendar, fh_news_fn=fh_news,
    )
    assert calls["news"] == ["CCC"]
    assert calls["calendar"][2] == "k"
    ev = out["today"][0]
    assert ev["symbol"] == "CCC" and ev["catalyst_rule"] == "mna"
    for w in out["watch"]:
        if w.get("earnings_expected"):
            assert w["earnings_source"] in ("finnhub", "estimate")
