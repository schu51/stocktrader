"""
Premarket quotes for the pre-market brief.

Daily bars only know yesterday's close, so a "gap" computed from them at
9:00 AM ET is really yesterday's move. This module pulls extended-hours
5-minute bars and returns each ticker's last trade before today's open,
so the brief can compare this morning's price with the prior close.
"""

import warnings
from datetime import date, time
from typing import Dict, List, Optional, Tuple

import pandas as pd

ET = "America/New_York"
MARKET_OPEN = time(9, 30)
GAP_THRESHOLD = 0.03


def latest_premarket_prices(bars: Optional[pd.DataFrame],
                            session_date: date) -> Tuple[Dict[str, float], Optional[pd.Timestamp]]:
    """
    Last trade price before the open on `session_date`, per ticker.

    Args:
        bars: Intraday closes, tz-aware index, one column per ticker
        session_date: Today's date in New York

    Returns:
        (prices, as_of) — tickers with no premarket trades are omitted;
        as_of is the New York time of the latest bar used, or None.
    """
    if bars is None or bars.empty:
        return {}, None

    local = bars.tz_convert(ET)
    premarket = local[(local.index.date == session_date) & (local.index.time < MARKET_OPEN)]
    premarket = premarket.dropna(how="all")
    if premarket.empty:
        return {}, None

    last = premarket.ffill().iloc[-1].dropna()
    return {ticker: float(price) for ticker, price in last.items()}, premarket.index[-1]


def completed_sessions(daily: pd.Series, session_date: date) -> pd.Series:
    """Daily bars strictly before `session_date` (drops today's partial bar)."""
    return daily[daily.index.date < session_date]


def gap_reasons(prev_close: float, premarket: float,
                ma50: Optional[float]) -> Tuple[float, List[str]]:
    """Premarket change vs the prior close, and the alerts it triggers."""
    pct_change = (premarket - prev_close) / prev_close
    reasons = []

    if pct_change < -GAP_THRESHOLD:
        reasons.append(f"gap down {pct_change:.1%}")

    if ma50 is not None and prev_close >= ma50 and premarket < ma50:
        reasons.append(f"crossed BELOW 50MA ({ma50:.2f})")

    if pct_change > GAP_THRESHOLD and ma50 is not None and premarket > ma50:
        reasons.append(f"gap UP {pct_change:.1%} above 50MA — momentum alert")

    return pct_change, reasons


def fetch_premarket_prices(tickers: List[str],
                           session_date: date) -> Tuple[Dict[str, float], Optional[pd.Timestamp]]:
    """Download extended-hours bars and return today's premarket prices. Empty on failure."""
    import yfinance as yf

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            raw = yf.download(tickers, period="5d", interval="5m", prepost=True,
                              auto_adjust=True, progress=False, threads=True)
        bars = raw["Close"]
        if isinstance(bars, pd.Series):
            bars = bars.to_frame(tickers[0])
    except Exception as e:
        print(f"  WARNING: premarket quote download failed: {e}")
        return {}, None

    return latest_premarket_prices(bars, session_date)
