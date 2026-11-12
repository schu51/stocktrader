import numpy as np
import pandas as pd
import pytest

import market_universe as mu


def _frames(n=260, spec=None):
    """spec: {sym: (price, shares_per_day, n_closes)}"""
    idx = pd.bdate_range("2025-01-01", periods=n)
    closes, vols = {}, {}
    for sym, (px, sh, k) in spec.items():
        c = pd.Series(np.nan, index=idx)
        c.iloc[n - k:] = px
        closes[sym], vols[sym] = c, c.notna() * float(sh)
    return pd.DataFrame(closes), pd.DataFrame(vols)


def test_names_that_are_not_common_stock_are_dropped():
    for name in ("SPDR S&P 500 ETF Trust", "Acme Corp Warrants", "Acme Acquisition Corp Units",
                 "Acme 6.5% Series A Preferred", "ProShares UltraPro QQQ", "Acme Rights",
                 "Direxion Daily Semiconductor Bull 3X Shares"):
        assert not mu.is_common_name(name), name
    for name in ("Apple Inc. Common Stock", "Realty Income Corporation", "Nu Holdings Ltd. Class A Ordinary Shares"):
        assert mu.is_common_name(name), name


def test_candidate_assets_keeps_main_exchanges_and_house_spelling():
    assets = [{"symbol": "BRK.B", "exchange": "NYSE", "name": "Berkshire Hathaway Class B", "tradable": True},
              {"symbol": "SPY", "exchange": "ARCA", "name": "SPDR S&P 500", "tradable": True},
              {"symbol": "OTCX", "exchange": "OTC", "name": "Otc Co", "tradable": True},
              {"symbol": "NOTR", "exchange": "NASDAQ", "name": "Not Tradable Inc", "tradable": False},
              {"symbol": "F", "exchange": "NYSE", "name": "Ford Motor Company", "tradable": True}]
    assert sorted(mu.candidate_assets(assets)) == ["BRK-B", "F"]


def test_liquid_needs_price_dollar_volume_and_history():
    closes, vols = _frames(spec={"OK": (20, 200_000, 260),        # $4M/day
                                 "CHEAP": (2, 5_000_000, 260),    # under $3
                                 "THIN": (20, 50_000, 260),       # $1M/day
                                 "NEW": (20, 200_000, 150)})      # too few closes
    assert mu.liquid(closes, vols) == ["OK"]


def test_sic_codes_map_to_screener_sectors_and_spacs_are_left_out():
    assert mu.sector_from_sic(3674) == "technology"       # semiconductors
    assert mu.sector_from_sic(7372) == "technology"       # software
    assert mu.sector_from_sic(2834) == "healthcare"       # pharma
    assert mu.sector_from_sic(1311) == "energy"
    assert mu.sector_from_sic(6022) == "financials"       # banks
    assert mu.sector_from_sic(6798) == "real_estate"      # REITs
    assert mu.sector_from_sic(3711) == "consumer_cyclical"
    assert mu.sector_from_sic(4911) == "utilities"
    assert mu.sector_from_sic(6770) is None               # blank check / SPAC
    assert mu.sector_from_sic(None) is None


def test_sic_lookups_reuse_the_cache():
    calls = []
    out = mu.sic_codes([1, 2, 3], {"1": 7372, "2": None}, lambda cik: calls.append(cik) or 2834, pause=0)
    assert calls == [3]
    assert out == {"1": 7372, "2": None, "3": 2834}


def test_build_market_end_to_end():
    closes, vols = _frames(spec={"AAA": (50, 100_000, 260), "BBB": (30, 100_000, 260),
                                 "SPAC": (10, 500_000, 260), "NOSEC": (40, 100_000, 260),
                                 "IDX": (100, 100_000, 260), "THIN": (5, 1_000, 260)})
    assets = [{"symbol": s, "exchange": "NASDAQ", "name": f"{s} Inc", "tradable": True} for s in closes.columns]
    sec = {"AAA": {"cik": 1}, "BBB": {"cik": 2}, "SPAC": {"cik": 3}, "IDX": {"cik": 4}, "THIN": {"cik": 5}}
    sic = {1: 3674, 2: 2834, 3: 6770, 4: 7372, 5: 7372}
    tickers, cache, counts = mu.build_market(assets, lambda t: {"Close": closes[t], "Volume": vols[t]}, sec,
                                             {"IDX": "communication_services", "GONE": "industrials"},
                                             {}, lambda cik: sic[cik], pause=0)
    assert tickers == {"AAA": "technology", "BBB": "healthcare", "GONE": "industrials",
                       "IDX": "communication_services"}
    assert counts["liquid"] == 5 and counts["no_sec_or_fund_or_spac"] == 2
    assert cache["1"] == 3674 and "4" not in cache       # index members keep GICS: no lookup needed


def test_refresh_keeps_last_weeks_market_file_if_the_market_build_fails(monkeypatch):
    import refresh_universe as ru
    base = {"generated_at": "x", "sources": {"sp500": 503}, "count": 1, "notes": [],
            "nasdaq100": [], "tickers": {"AAA": "technology"}}
    with pytest.raises(RuntimeError):
        ru.add_market(base, None, assets_fn=lambda: [], bars_fn=lambda t: None, sec_fn=lambda: {},
                      sic_fetch=lambda c: None)


def test_add_market_widens_the_index_file(monkeypatch):
    import refresh_universe as ru
    monkeypatch.setattr(mu, "MIN_MARKET", 1)
    closes, vols = _frames(spec={"AAA": (50, 100_000, 260), "NEWCO": (30, 100_000, 260)})
    base = {"generated_at": "x", "sources": {"sp500": 503}, "count": 1, "notes": [],
            "nasdaq100": ["AAA"], "tickers": {"AAA": "technology", "OLD": "other"}}
    out = ru.add_market(base, {"sic": {"9": 7372}},
                        assets_fn=lambda: [{"symbol": s, "exchange": "NYSE", "name": s, "tradable": True}
                                           for s in ("AAA", "NEWCO")],
                        bars_fn=lambda t: {"Close": closes[t], "Volume": vols[t]},
                        sec_fn=lambda: {"NEWCO": {"cik": 9}}, sic_fetch=lambda c: pytest.fail("cached"))
    assert out["mode"] == "market"
    assert out["tickers"] == {"AAA": "technology", "NEWCO": "technology", "OLD": "other"}
    assert out["index_members"] == ["AAA", "OLD"] and out["nasdaq100"] == ["AAA"]
    import contracts
    assert not contracts.validate("docs/data/universe.json", out, full=False)
