"""Saved universe file, Alpaca price bars, and the yfinance fallback."""
import json
from datetime import datetime, timedelta, timezone

import pandas as pd


def _universe_file(tmp_path, n=450, age_days=0):
    generated = datetime.now(timezone.utc) - timedelta(days=age_days)
    path = tmp_path / "universe.json"
    path.write_text(json.dumps({
        "generated_at": generated.isoformat(timespec="seconds"),
        "tickers": {f"T{chr(65 + i // 26 % 26)}{chr(65 + i % 26)}{chr(65 + i // 676)}": "industrials" for i in range(n)},
    }))
    return path


def test_saved_universe_is_used_first(tmp_path, monkeypatch):
    import screener
    path = _universe_file(tmp_path)
    monkeypatch.setattr(screener, "UNIVERSE_PATH", path)
    monkeypatch.setattr(screener, "_fetch_finviz_universe",
                        lambda: (_ for _ in ()).throw(AssertionError("Finviz should not be called")))
    universe = screener.get_universe()
    assert "TAAA" in universe and len(universe) >= 450


def test_stale_or_small_saved_universe_is_ignored(tmp_path):
    import screener
    assert screener._load_saved_universe(_universe_file(tmp_path, age_days=60)) == {}
    assert screener._load_saved_universe(_universe_file(tmp_path, n=50)) == {}
    assert screener._load_saved_universe(tmp_path / "missing.json") == {}


def test_committed_universe_file_is_valid():
    import screener
    universe = screener._load_saved_universe(screener.UNIVERSE_PATH.with_name("universe.json")) \
        or json.loads(screener.UNIVERSE_PATH.read_text())["tickers"]
    assert len(universe) >= screener.MIN_FINVIZ_UNIVERSE
    assert universe.get("AAPL") == "technology"
    assert "BRK-B" in universe


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code, self.text = payload, status, json.dumps(payload)

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append(params)
        return self.responses.pop(0)


def _bars(days, close=10.0, volume=1_000_000):
    return [{"t": f"{d}T04:00:00Z", "c": close + i, "v": volume} for i, d in enumerate(days)]


def test_alpaca_bars_are_shaped_like_yfinance_and_follow_pages(monkeypatch):
    import screener
    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    monkeypatch.setattr(screener.time, "sleep", lambda s: None)
    session = _Session([
        _Resp({"bars": {"AAPL": _bars(["2026-10-22", "2026-10-23"])}, "next_page_token": "p2"}),
        _Resp({"bars": {"BRK.B": _bars(["2026-10-22", "2026-10-23"], close=400)}, "next_page_token": None}),
    ])
    data = screener._alpaca_daily_bars(["AAPL", "BRK-B"], session=session)

    assert list(data["Close"].columns) == ["AAPL", "BRK-B"]           # BRK.B mapped back
    assert data["Close"]["AAPL"].tolist() == [10.0, 11.0]
    assert data["Volume"]["BRK-B"].iloc[-1] == 1_000_000
    assert isinstance(data["Close"].index, pd.DatetimeIndex)
    assert session.calls[0]["symbols"] == "AAPL,BRK.B"
    assert session.calls[0]["feed"] == "sip" and session.calls[0]["adjustment"] == "all"
    assert session.calls[1]["page_token"] == "p2"


def test_alpaca_returns_none_without_keys_or_on_error(monkeypatch):
    import screener
    monkeypatch.delenv("ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_SECRET_KEY", raising=False)
    assert screener._alpaca_daily_bars(["AAPL"]) is None

    monkeypatch.setenv("ALPACA_API_KEY", "k")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "s")
    session = _Session([_Resp({"message": "forbidden"}, status=403)])
    assert screener._alpaca_daily_bars(["AAPL"], session=session) is None


def test_batch_download_prefers_alpaca_and_falls_back_to_yfinance(monkeypatch):
    import sys
    import types
    import screener

    idx = pd.date_range("2025-10-01", periods=250, freq="B")
    full = {"Close": pd.DataFrame({"AAA": range(250)}, index=idx),
            "Volume": pd.DataFrame({"AAA": [1e6] * 250}, index=idx)}
    monkeypatch.setattr(screener, "_alpaca_daily_bars", lambda tickers: full)
    assert screener.batch_download(["AAA"]) is full
    assert screener.LAST_PRICE_SOURCE == "alpaca"

    yf_data = object()
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(download=lambda *a, **k: yf_data))
    monkeypatch.setattr(screener, "_alpaca_daily_bars", lambda tickers: None)
    assert screener.batch_download(["AAA"]) is yf_data
    assert screener.LAST_PRICE_SOURCE == "yfinance"

    thin = {"Close": pd.DataFrame({"AAA": range(20)}, index=idx[:20]),
            "Volume": pd.DataFrame({"AAA": [1e6] * 20}, index=idx[:20])}
    monkeypatch.setattr(screener, "_alpaca_daily_bars", lambda tickers: thin)
    assert screener.batch_download(["AAA"]) is yf_data


def test_refresh_build_merges_indexes_and_carries_over_ndx():
    import refresh_universe as ru
    sp = {f"S{i:03d}": "industrials" for i in range(ru.MIN_SP500)}
    ndx = {f"N{i:03d}": "technology" for i in range(ru.MIN_NDX)}
    out = ru.build(sp, ndx, None)
    assert out["count"] == ru.MIN_SP500 + ru.MIN_NDX
    assert out["sources"]["nasdaq100_source"] == "wikipedia"

    again = ru.build(sp, {}, out)                       # NDX fetch failed this week
    assert again["sources"]["nasdaq100_source"] == "carried over"
    assert again["tickers"]["N000"] == "technology"

    assert ru.build({"AAPL": "technology"}, ndx, out) is None   # short S&P list: keep old file


def test_refresh_maps_gics_sectors_and_class_shares():
    import refresh_universe as ru
    df = pd.DataFrame({"Symbol": ["BRK.B", "AAPL"], "GICS Sector": ["Financials", "Information Technology"]})
    assert ru._members_from_table(df) == {"BRK-B": "financials", "AAPL": "technology"}


def test_refresh_reads_the_icb_labelled_nasdaq_table():
    import refresh_universe as ru
    df = pd.DataFrame({"Company": ["Arm", "Comcast"], "Ticker": ["ARM", "CMCSA"],
                       "ICB Industry": ["Technology", "Telecommunications"]})
    assert ru._members_from_table(df) == {"ARM": "technology", "CMCSA": "communication_services"}
    assert ru._members_from_table(pd.DataFrame({"Ticker": ["ZZZ"]})) == {"ZZZ": "other"}
