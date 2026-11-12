"""
Market history without survivorship bias
========================================
Backtests used to replay today's S&P 500 and Nasdaq-100 members back in time.
Those are the stocks that survived and grew, so every rule looked better than
it would have been. This module gives the backtest:

  prices    adjusted daily OHLCV from Alpaca for every US common stock it has
            listed on NYSE, Nasdaq, NYSE American or Cboe BZX, delisted ones
            included (Alpaca's inactive assets), from 2016 on
  members   a dates x tickers True/False table: was the stock in the universe
            on that day, by the same rules the weekly universe uses
            (market_universe.py: $3+, median $2M+/day over 50 sessions, 200+
            closes in the last year), using only data up to that day
  universe  {ticker: sector}: today's sector where the stock is still listed
            (universe.json), "other" for delisted ones

Known limits: a ticker reused by a new company after the old one delisted
shares one price history at Alpaca, so such inactive names are skipped;
stocks delisted before Alpaca's data starts (2016) are missing; delisted
stocks have no sector. A position whose prices stop (delisting, buyout) is
closed at its last close, which is close to a cash buyout and too kind to a
bankruptcy.
"""

import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent.resolve()
CACHE_DIR = ROOT / "trading_data" / "backtest_cache"
FIELDS = ("Open", "High", "Low", "Close", "Volume")
CHUNK = 100
PAUSE = 0.3                      # free data plan: 200 requests/minute
LOOKBACK_EXTRA_DAYS = 400        # the screener needs a year of bars before the first session


def _headers() -> Dict[str, str]:
    key, secret = os.getenv("ALPACA_API_KEY", ""), os.getenv("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        raise RuntimeError("ALPACA_API_KEY / ALPACA_SECRET_KEY not set")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def fetch_assets(status: str, session) -> List[Dict]:
    paper = os.getenv("ALPACA_PAPER", "true").strip().lower() not in ("false", "0", "no")
    base = "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
    resp = session.get(f"{base}/v2/assets", params={"status": status, "asset_class": "us_equity"},
                       headers=_headers(), timeout=120)
    resp.raise_for_status()
    return resp.json()


def symbols_for_history(active: Iterable[Dict], inactive: Iterable[Dict]) -> List[str]:
    """Active common stocks plus delisted ones whose ticker has not been reused (house spelling)."""
    import market_universe as mu
    live = mu.candidate_assets(active)
    live_any = {a.get("symbol") for a in active}
    gone = {}
    for a in inactive:
        a2 = dict(a, status="active", tradable=True)           # judged on exchange and name only
        sym = a.get("symbol")
        if sym in live_any:
            continue
        gone.update(mu.candidate_assets([a2]))
    return sorted(set(live) | set(gone))


def _frame(rows: Dict[str, Dict[str, float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def fetch_bars(symbols: List[str], start: str, session, end: Optional[str] = None) -> Dict[str, pd.DataFrame]:
    """Adjusted daily bars (SIP) for every symbol from `start`; built chunk by chunk to keep memory down."""
    import identity
    from screener import ALPACA_BARS_URL
    headers = _headers()
    end = end or (datetime.now(timezone.utc) - timedelta(minutes=16)).strftime("%Y-%m-%dT%H:%M:%SZ")
    parts: Dict[str, List[pd.DataFrame]] = {f: [] for f in FIELDS}
    keys = {"Open": "o", "High": "h", "Low": "l", "Close": "c", "Volume": "v"}
    for n, i in enumerate(range(0, len(symbols), CHUNK), 1):
        chunk = symbols[i:i + CHUNK]
        back = {identity.alpaca(s): s for s in chunk}
        rows = {f: {} for f in FIELDS}
        token = None
        while True:
            params = {"symbols": ",".join(back), "timeframe": "1Day", "start": start, "end": end,
                      "adjustment": "all", "feed": "sip", "limit": 10000}
            if token:
                params["page_token"] = token
            resp = None
            for attempt in range(4):
                try:
                    resp = session.get(ALPACA_BARS_URL, params=params, headers=headers, timeout=60)
                except Exception as e:
                    logger.warning(f"Alpaca bars request failed ({e})")
                    resp = None
                if resp is not None and resp.status_code == 200:
                    break
                time.sleep(3 * (attempt + 1))
            if resp is None or resp.status_code != 200:
                raise RuntimeError(f"Alpaca bars failed: HTTP {getattr(resp, 'status_code', 'none')}")
            payload = resp.json()
            for sym, bars in (payload.get("bars") or {}).items():
                t = back.get(sym, identity.canonical(sym))
                for bar in bars or []:
                    day = str(bar["t"])[:10]
                    for f, k in keys.items():
                        if bar.get(k) is not None:
                            rows[f].setdefault(t, {})[day] = float(bar[k])
            token = payload.get("next_page_token")
            time.sleep(PAUSE)
            if not token:
                break
        for f in FIELDS:
            if rows[f]:
                parts[f].append(_frame(rows[f]).astype("float32" if f != "Volume" else "float64"))
        if n % 20 == 0:
            logger.info(f"Market history: {min(i + CHUNK, len(symbols))}/{len(symbols)} symbols")
    out = {}
    for f in FIELDS:
        if not parts[f]:
            raise RuntimeError("Alpaca returned no bars")
        out[f] = pd.concat(parts[f], axis=1).sort_index().astype("float64")
    cols = out["Close"].columns
    return {f: out[f].reindex(columns=cols) for f in FIELDS}


def membership(closes: pd.DataFrame, volumes: pd.DataFrame, min_price: Optional[float] = None,
               min_dollar_volume: Optional[float] = None, median_days: Optional[int] = None,
               min_closes: Optional[int] = None) -> pd.DataFrame:
    """
    dates x tickers: True where the stock met the weekly universe's rules using
    data through that day (market_universe.liquid, vectorised).
    """
    import market_universe as mu
    min_price = mu.MIN_PRICE if min_price is None else min_price
    min_dollar_volume = mu.MIN_DOLLAR_VOLUME if min_dollar_volume is None else min_dollar_volume
    median_days = mu.MEDIAN_DAYS if median_days is None else median_days
    min_closes = mu.MIN_CLOSES if min_closes is None else min_closes
    vol = volumes.reindex(index=closes.index, columns=closes.columns)
    enough = closes.notna().rolling(252, min_periods=1).sum() >= min_closes
    priced = closes >= min_price
    dv = (closes * vol).rolling(median_days, min_periods=max(1, median_days // 2)).median()
    traded = dv >= min_dollar_volume
    return (enough & priced & traded).fillna(False)


def load(start: str, end: Optional[str] = None, session=None, refresh: bool = False
         ) -> Tuple[Dict[str, str], Dict[str, pd.DataFrame], pd.DataFrame]:
    """
    (universe {ticker: sector}, prices {field: DataFrame}, members DataFrame) for a
    backtest starting at `start`, cached on disk for the day.
    """
    import requests
    from backtest import BENCHMARK
    import screener
    session = session or requests.Session()
    fetch_from = (pd.Timestamp(start) - pd.Timedelta(days=LOOKBACK_EXTRA_DAYS)).strftime("%Y-%m-%d")
    cache = CACHE_DIR / f"market_{fetch_from}_{date.today().isoformat()}"
    if not refresh and all((cache / f"{f}.pkl").exists() for f in FIELDS):
        prices = {f: pd.read_pickle(cache / f"{f}.pkl") for f in FIELDS}
    else:
        active = fetch_assets("active", session)
        inactive = fetch_assets("inactive", session)
        syms = symbols_for_history(active, inactive)
        if BENCHMARK not in syms:
            syms.append(BENCHMARK)
        logger.info(f"Market history: {len(syms)} symbols ({len(active)} active / {len(inactive)} inactive assets)")
        prices = fetch_bars(syms, fetch_from, session)
        cache.mkdir(parents=True, exist_ok=True)
        for f, frame in prices.items():
            frame.to_pickle(cache / f"{f}.pkl")
    if end:
        prices = {f: v.loc[:pd.Timestamp(end)] for f, v in prices.items()}
    members = membership(prices["Close"], prices["Volume"])
    if BENCHMARK in members.columns:
        members[BENCHMARK] = False
    sectors = screener._load_saved_universe() or {}
    universe = {s: sectors.get(s, "other") for s in prices["Close"].columns if s != BENCHMARK}
    return universe, prices, members
