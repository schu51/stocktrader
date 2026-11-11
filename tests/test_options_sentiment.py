"""Options sentiment: chain metrics, flags, evidence, the buy-time lookup."""
import json
from datetime import date, timedelta

import pandas as pd
import pytest

import options_sentiment as os_

TODAY = date(2026, 11, 11)


def _c(t, strike, dte, volume, oi, iv=0.5, delta=None, mid=1.0):
    return {"type": t, "strike": strike, "expiry": TODAY + timedelta(days=dte), "volume": volume,
            "oi": oi, "iv": iv, "delta": delta, "mid": mid}


def _bullish_chain():
    return [
        _c("C", 105, 30, 3000, 1000, iv=0.45, delta=0.25), _c("C", 100, 30, 2000, 5000, iv=0.5, delta=0.5),
        _c("P", 95, 30, 400, 3000, iv=0.47, delta=-0.25), _c("P", 100, 30, 300, 4000, iv=0.5, delta=-0.5),
        _c("C", 110, 200, 100, 8000, iv=0.4), _c("P", 90, 200, 50, 3000, iv=0.42),
        _c("C", 100, 120, 10, 500, iv=0.40), _c("P", 100, 120, 10, 500, iv=0.40),
    ]


def test_parse_occ_and_merge_chain():
    assert os_.parse_occ("CRWV261120C00060000") == {"root": "CRWV", "expiry": date(2026, 11, 20),
                                                    "type": "C", "strike": 60.0}
    snaps = {"CRWV261120C00060000": {"latestQuote": {"bp": 2.0, "ap": 2.2}, "dailyBar": {"v": 900},
                                     "impliedVolatility": 0.9, "greeks": {"delta": 0.4}}}
    out = os_.merge_chain(snaps, {"CRWV261120C00060000": "1500"})
    assert out[0]["volume"] == 900 and out[0]["oi"] == 1500 and out[0]["mid"] == pytest.approx(2.1)


def test_metrics_and_flags_read_a_call_heavy_chain_as_bullish():
    m = os_.chain_metrics(_bullish_chain(), 100.0, TODAY)
    assert m["call_volume"] == 5000 and m["put_volume"] == 700
    assert m["pc_volume"] == pytest.approx(0.14)
    assert m["unusual_call_share"] == pytest.approx(0.6)       # the 105 call: 3000 traded vs 1000 open
    assert m["skew"] == pytest.approx(0.02)                    # 25-delta put 0.47 vs call 0.45
    assert m["pc_oi_long"] == pytest.approx(round(3500 / 8500, 3))
    f = os_.flags(m)
    assert f["options_bullish"] is True and f["options_bearish"] is False and f["unusual_calls"] is True
    assert f["long_tilt"] == "bullish" and f["options_score"] == 3


def test_fear_skew_alone_is_bearish():
    chain = [_c("C", 105, 30, 400, 1000, iv=0.30, delta=0.25), _c("P", 95, 30, 400, 1000, iv=0.50, delta=-0.25)]
    f = os_.flags(os_.chain_metrics(chain, 100.0, TODAY))
    assert f["options_bearish"] is True and f["options_score"] < 0


def test_thin_chains_are_unknown_not_neutral():
    chain = [_c("C", 100, 30, 10, 20), _c("P", 100, 30, 5, 20)]
    m = os_.chain_metrics(chain, 100.0, TODAY)
    f = os_.flags(m)
    assert m["pc_volume"] is None and f["options_bullish"] is None
    assert "too little" in os_.read_line("X", m, f)


def test_evidence_compares_flagged_stocks_with_the_rest_and_reports_ic():
    rows = []
    for d in range(10):
        day = (TODAY - timedelta(days=30 - d)).isoformat()
        for i in range(10):
            bull = i < 3
            rows.append({"date": day, "symbol": f"S{i}", "options_bullish": bull, "options_bearish": False,
                         "unusual_calls": False, "options_score": 3 if bull else -i % 3,
                         "pc_volume": 0.3 if bull else 1.0 + i / 10,
                         "fwd": {"x1": 0.02 if bull else -0.01 - i / 1000, "r1": 0.02, "x5": 0.03 if bull else 0.0}})
    ev = os_.evidence(rows)
    d1 = ev["flags"]["options_bullish"]["d1"]
    assert d1["n"] == 30 and d1["edge"] > 0
    assert ev["ic"]["pc_volume"]["d1"]["mean_ic"] > 0.5        # lower put/call -> better returns


def test_forward_returns_start_at_the_next_open_and_net_spy():
    idx = pd.bdate_range("2026-11-02", periods=30)
    closes = pd.DataFrame({"X": [100 + i for i in range(30)], "SPY": [500.0] * 30}, index=idx)
    opens = closes - 0.5
    opens["SPY"] = 500.0          # flat benchmark, so excess = raw
    rows = [{"date": idx[3].date().isoformat(), "symbol": "X"}]
    os_.forward(rows, opens, closes)
    assert rows[0]["fwd"]["x1"] == pytest.approx(104 / 103.5 - 1, abs=1e-4)
    assert rows[0]["fwd"]["x5"] == pytest.approx(108 / 103.5 - 1, abs=1e-4)
    assert "x60" not in rows[0]["fwd"]
    old = [{"date": "2026-01-02", "symbol": "X"}]                # before the bars: never scored
    os_.forward(old, opens, closes)
    assert old[0]["fwd"] == {}


def test_lookup_uses_the_previous_evening_never_the_same_day(tmp_path):
    os_.write_log([{"date": "2026-10-31", "symbol": "X", "options_bullish": False},
                   {"date": "2026-11-10", "symbol": "X", "options_bullish": True},
                   {"date": "2026-11-11", "symbol": "X", "options_bullish": False}], tmp_path)
    assert sorted(f.name for f in tmp_path.iterdir()) == ["2026-10.json", "2026-11.json"]
    assert os_.lookup("X", date(2026, 11, 11), tmp_path)["options_bullish"] is True
    assert os_.lookup("X", date(2026, 11, 2), tmp_path)["options_bullish"] is False   # across a month end
    assert os_.lookup("X", date(2026, 11, 20), tmp_path) is None       # too old


def test_coverage_holdings_first_and_a_reproducible_control_group(tmp_path):
    (tmp_path / "latest.json").write_text(json.dumps({"positions": [{"symbol": "ACN"}]}))
    (tmp_path / "screener.json").write_text(json.dumps({"candidates": [{"symbol": "NUE"}, {"symbol": "ACN"}]}))
    (tmp_path / "big_movers.json").write_text(json.dumps({"watch": [{"symbol": "LUNR"}], "today": []}))
    (tmp_path / "universe.json").write_text(json.dumps({"tickers": {f"U{chr(65 + i)}": "x" for i in range(26)}}))
    cov = os_.coverage(TODAY, tmp_path)
    assert cov[:3] == [("ACN", "holding"), ("NUE", "candidate"), ("LUNR", "watch")]
    assert sum(g == "control" for _, g in cov) == 26
    assert cov == os_.coverage(TODAY, tmp_path)


def test_run_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(os_, "LOG_DIR", tmp_path / "log")
    monkeypatch.setattr(os_, "OUT_FILE", tmp_path / "out.json")
    idx = pd.bdate_range("2026-08-01", "2026-11-11")
    closes = pd.DataFrame({"X": 100.0, "Y": 50.0, "SPY": 500.0}, index=idx)
    bars = lambda syms, lb: {"Close": closes, "Open": closes}
    chain = lambda sym, price, today, s: _bullish_chain() if sym == "X" else []
    out = os_.run(TODAY, chain_fn=chain, bars_fn=bars, symbols=[("X", "holding"), ("Y", "control")],
                  session=object())
    assert [r["symbol"] for r in out["reads"]] == ["X", "Y"]
    assert out["reads"][0]["options_bullish"] is True and out["reads"][1]["options_score"] is None
    assert len(os_.read_log(tmp_path / "log")) == 2
    # a rerun the same day does not snapshot twice
    os_.run(TODAY, chain_fn=chain, bars_fn=bars, symbols=[("X", "holding")], session=object())
    assert len(os_.read_log(tmp_path / "log")) == 2


def test_buy_hook_records_options_signals_but_never_gates_on_them(tmp_path, monkeypatch):
    import experiments as ex
    os_.write_log([{"date": "2026-11-10", "symbol": "X", "options_bullish": False,
                    "options_bearish": True, "options_score": -2}], tmp_path)
    sig = ex._options_signals("X", date(2026, 11, 11), tmp_path)
    assert sig == {"options_bullish": False, "options_bearish": True, "options_score": -2}
    reg = ex.default_registry(TODAY)
    assert ex.promotion_bar("options_bullish", reg["experiments"]["options_bullish"]) is None
    assert ex.entry_gate({"ma50_room": True, "options_bullish": False}, reg) is None
