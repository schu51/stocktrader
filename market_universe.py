"""
Whole-market universe
=====================
Every liquid US-listed common stock, not just the S&P 500 and Nasdaq-100.

Built weekly by refresh_universe.py:
  1. Alpaca's active US equities on NYSE, Nasdaq, NYSE American and Cboe BZX
     (NYSE Arca is skipped: it lists ETFs).
  2. Names that are plainly not common stock are dropped (ETFs, funds, warrants,
     units, rights, preferreds, notes).
  3. A year of daily bars from Alpaca for what is left; a stock stays if it has
     200+ closes (the RS score needs them), last closed at $3+, and traded a
     median $50M+ a day over the last 50 sessions (about 1,000 stocks; see
     MIN_DOLLAR_VOLUME for why). The screener's own gates ($5, 500k shares)
     then run on top, as before.
  4. Each survivor needs an SEC CIK and a standard industry (SIC) code: that
     drops ETF trusts and blank-check companies (SPACs, SIC 6770) that slipped
     past the name filter, and gives the sector. S&P 500 / Nasdaq-100 members
     keep their GICS sector from the index lists.

The SIC code per CIK is cached in universe.json, so only new listings are
looked up at the SEC (10 requests/second limit; we pace at ~8).

Backtests use the same rules day by day, on bars that include delisted stocks
(market_history.py), so they are not limited to today's survivors.
"""

import logging
import re
import time
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "BATS"}      # Alpaca exchange codes; ARCA (ETFs) left out
MIN_PRICE = 3.0
# Median dollars traded per day over MEDIAN_DAYS sessions. Chosen on a 3-year,
# survivorship-free backtest of the live rules (2026-11-12, delisted stocks included):
#   $2M+ (~2,700 names/day)  13.3%/yr, profit factor 1.36, max drawdown -17%
#   $20M+ (~1,570)           19.0%/yr, PF 1.54, -16%
#   $50M+ (~1,040)           22.1%/yr, PF 1.69, -13%   <- best; SPY 21.3%, -19%
#   $150M+ (~500)            16.4%/yr, PF 1.59, -15%
# Thinner names gap through stops (average loss -13% vs -11%). One window: re-check
# before changing, with backtest.py on the whole market.
MIN_DOLLAR_VOLUME = 50_000_000
MEDIAN_DAYS = 50
MIN_CLOSES = 200
SEC_PAUSE = 0.125                                  # ~8 requests/second
MIN_MARKET = 600                                   # fewer survivors means a fetch went wrong

_NOT_COMMON = re.compile(
    r"\b(ETFS?|ETNS?|FUNDS?|WARRANTS?|RIGHTS?|UNITS?|PREFERRED|PFD|DEBENTURES?|NOTES? DUE|BONDS?|"
    r"SENIOR NOTES|SUBORDINATED|DEPOSITARY SHARES,? EACH REPRESENTING|INDEX|ISHARES|SPDR|PROSHARES|"
    r"DIREXION|LEVERAGED|INVERSE|ULTRA(PRO|SHORT)|[0-9]X LONG|[0-9]X SHORT|BULL [0-9]X|BEAR [0-9]X)\b"
    r"|%", re.I)

def is_common_name(name: str) -> bool:
    """False for names that are plainly not an operating company's common stock."""
    return bool(name) and not _NOT_COMMON.search(name)


def candidate_assets(assets: Iterable[Dict]) -> Dict[str, Dict]:
    """{TICKER: asset} for tradable, active common stocks on the main exchanges (house spelling)."""
    import identity
    out = {}
    for a in assets:
        sym = identity.canonical(a.get("symbol"))
        if (a.get("status", "active") == "active" and a.get("tradable", True)
                and a.get("exchange") in EXCHANGES and identity.is_ticker(sym)
                and is_common_name(a.get("name") or "")):
            out[sym] = a
    return out


def liquid(closes, volumes, min_price: Optional[float] = None, min_dollar_volume: Optional[float] = None,
           median_days: Optional[int] = None, min_closes: Optional[int] = None) -> List[str]:
    """Tickers (columns) passing the price, dollar-volume and history-length rules on the last day."""
    min_price = MIN_PRICE if min_price is None else min_price
    min_dollar_volume = MIN_DOLLAR_VOLUME if min_dollar_volume is None else min_dollar_volume
    median_days = MEDIAN_DAYS if median_days is None else median_days
    min_closes = MIN_CLOSES if min_closes is None else min_closes
    out = []
    for sym in closes.columns:
        if pd.isna(closes[sym].iloc[-1]):          # didn't trade on the last day (delisted or halted)
            continue
        c = closes[sym].dropna()
        if len(c) < min_closes or float(c.iloc[-1]) < min_price:
            continue
        v = volumes[sym].reindex(c.index) if sym in volumes.columns else None
        if v is None:
            continue
        dv = (c * v).tail(median_days).dropna()
        if len(dv) and float(np.median(dv.values)) >= min_dollar_volume:
            out.append(sym)
    return sorted(out)


def sector_from_sic(sic: Optional[int]) -> Optional[str]:
    """The screener's sector key for a SEC SIC code; None for codes to leave out (SPACs, funds)."""
    if sic is None:
        return None
    s = int(sic)
    if s in (6770, 6722, 6726):
        return None
    ranges: List[Tuple[int, int, str]] = [
        (100, 999, "consumer_defensive"), (1000, 1099, "basic_materials"), (1200, 1399, "energy"),
        (1400, 1499, "basic_materials"), (1520, 1531, "consumer_cyclical"), (1500, 1799, "industrials"),
        (2000, 2199, "consumer_defensive"), (2200, 2399, "consumer_cyclical"), (2400, 2499, "basic_materials"),
        (2500, 2599, "consumer_cyclical"), (2600, 2699, "basic_materials"),
        (2700, 2799, "communication_services"), (2830, 2836, "healthcare"),
        (2840, 2844, "consumer_defensive"), (2800, 2899, "basic_materials"), (2900, 2999, "energy"),
        (3000, 3099, "basic_materials"), (3100, 3199, "consumer_cyclical"), (3200, 3399, "basic_materials"),
        (3400, 3499, "industrials"), (3570, 3579, "technology"), (3500, 3599, "industrials"),
        (3630, 3639, "consumer_cyclical"), (3660, 3679, "technology"), (3600, 3699, "industrials"),
        (3710, 3716, "consumer_cyclical"), (3700, 3799, "industrials"), (3840, 3851, "healthcare"),
        (3800, 3899, "technology"), (3900, 3999, "consumer_cyclical"), (4000, 4799, "industrials"),
        (4800, 4899, "communication_services"), (4950, 4959, "industrials"), (4900, 4999, "utilities"),
        (5120, 5129, "healthcare"), (5140, 5149, "consumer_defensive"), (5000, 5199, "industrials"),
        (5400, 5499, "consumer_defensive"), (5910, 5912, "consumer_defensive"),
        (5200, 5999, "consumer_cyclical"), (6500, 6553, "real_estate"), (6798, 6798, "real_estate"),
        (6000, 6799, "financials"), (7000, 7099, "consumer_cyclical"), (7200, 7299, "consumer_cyclical"),
        (7310, 7319, "communication_services"), (7370, 7379, "technology"),
        (7800, 7899, "communication_services"), (7900, 7999, "consumer_cyclical"),
        (7000, 7999, "industrials"), (8000, 8099, "healthcare"), (8731, 8731, "healthcare"),
        (8200, 8299, "consumer_defensive"), (8000, 8999, "industrials"),
    ]
    for lo, hi, sector in ranges:
        if lo <= s <= hi:
            return sector
    return "other"


def fetch_sic(cik: int, session, user_agent: str) -> Optional[int]:
    resp = session.get(f"https://data.sec.gov/submissions/CIK{int(cik):010d}.json",
                       headers={"User-Agent": user_agent}, timeout=30)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    sic = (resp.json() or {}).get("sic")
    return int(sic) if str(sic or "").strip().isdigit() else None


def sic_codes(ciks: Iterable[int], cached: Dict[str, Optional[int]], fetch: Callable[[int], Optional[int]],
              pause: float = SEC_PAUSE) -> Dict[str, Optional[int]]:
    """{str(cik): sic} for every CIK, reusing `cached` and fetching only the new ones."""
    out = {k: v for k, v in (cached or {}).items()}
    new = sorted({int(c) for c in ciks if str(int(c)) not in out})
    for n, cik in enumerate(new, 1):
        try:
            out[str(cik)] = fetch(cik)
        except Exception as e:
            logger.warning(f"SEC SIC for CIK {cik} failed: {e}")
            continue
        if pause:
            time.sleep(pause)
        if n % 250 == 0:
            logger.info(f"SEC SIC codes: {n}/{len(new)}")
    return out


def build_market(assets: List[Dict], bars, sec_tickers: Dict[str, Dict], gics: Dict[str, str],
                 sic_cache: Dict[str, Optional[int]], sic_fetch: Callable[[int], Optional[int]],
                 pause: float = SEC_PAUSE) -> Tuple[Dict[str, str], Dict[str, Optional[int]], Dict[str, int]]:
    """
    ({ticker: sector}, sic cache, counts) for the whole market.
    `bars(tickers)` returns {"Close": DataFrame, "Volume": DataFrame}.
    `sec_tickers` is {TICKER: {cik, name}}; `gics` is the index lists' {ticker: sector}.
    """
    import identity
    cands = candidate_assets(assets)
    data = bars(sorted(cands))
    if not data or "Close" not in data:
        raise RuntimeError("no price bars for the market universe")
    keep = liquid(data["Close"], data["Volume"])
    ciks = {}
    for sym in keep:
        if sym in gics:                       # index members keep their GICS sector: no lookup
            continue
        company = sec_tickers.get(sym) or sec_tickers.get(identity.dotted(sym))
        if company and company.get("cik"):
            ciks[sym] = int(company["cik"])
    sics = sic_codes(ciks.values(), sic_cache, sic_fetch, pause)
    tickers, dropped_sic = {}, 0
    for sym in keep:
        if sym in gics:
            tickers[sym] = gics[sym]
            continue
        cik = ciks.get(sym)
        sector = sector_from_sic(sics.get(str(cik))) if cik else None
        if sector is None:
            dropped_sic += 1
            continue
        tickers[sym] = sector
    for sym, sector in gics.items():        # index members always stay, as before
        tickers.setdefault(sym, sector)
    counts = {"alpaca_assets": len(assets), "common_candidates": len(cands), "liquid": len(keep),
              "no_sec_or_fund_or_spac": dropped_sic, "market": len(tickers)}
    return dict(sorted(tickers.items())), sics, counts
