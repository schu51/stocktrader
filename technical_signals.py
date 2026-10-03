"""
Technical Signals
=================
Pure indicator functions shared by the backtester and the live experiment
hooks, so a signal means exactly the same thing in both places.

No I/O. Inputs are numpy arrays of daily values, oldest first.
"""

from typing import Dict

import numpy as np
import pandas as pd


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    return pd.Series(values, dtype=float).ewm(span=span, adjust=False).mean().values


def macd_cross_up(closes: np.ndarray, within: int = 3) -> bool:
    """MACD (12, 26) crossed above its 9-day signal line in the last `within` sessions and is still above."""
    if len(closes) < 35 + within:
        return False
    macd = _ema(closes, 12) - _ema(closes, 26)
    diff = macd - _ema(macd, 9)
    recent = diff[-(within + 1):]
    return bool(recent[-1] > 0 and (recent[:-1] <= 0).any())


def ema_reclaim(closes: np.ndarray, span: int = 21, lookback: int = 5) -> bool:
    """Close is back above the 21-day EMA after closing below it within the last `lookback` sessions."""
    if len(closes) < span + lookback:
        return False
    gap = closes - _ema(closes, span)
    return bool(gap[-1] > 0 and (gap[-(lookback + 1):-1] < 0).any())


def breakout(closes: np.ndarray, volumes: np.ndarray, n: int = 20) -> bool:
    """New `n`-session closing high on volume above the prior `n`-session average."""
    if len(closes) < n + 1 or len(volumes) < n + 1:
        return False
    return bool(closes[-1] > closes[-(n + 1):-1].max() and volumes[-1] > volumes[-(n + 1):-1].mean())


def _true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev = close[:-1]
    return np.maximum.reduce([high[1:] - low[1:], np.abs(high[1:] - prev), np.abs(low[1:] - prev)])


def atr_pct(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> float:
    """Average true range over the last `n` sessions as a fraction of the last close."""
    if len(close) < n + 1:
        return float("nan")
    return float(_true_range(high, low, close)[-n:].mean() / close[-1])


def adx(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> float:
    """Wilder's Average Directional Index: trend strength, 0-100, direction-blind."""
    if len(close) < 2 * n + 1:
        return float("nan")
    up, down = high[1:] - high[:-1], low[:-1] - low[1:]
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    smooth = lambda x: pd.Series(x).ewm(alpha=1 / n, adjust=False).mean().values
    atr = smooth(_true_range(high, low, close))
    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100 * smooth(plus_dm) / atr
        minus_di = 100 * smooth(minus_dm) / atr
        dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di)
    return float(smooth(np.nan_to_num(dx))[-1])


STOP_DIST_MIN, STOP_DIST_MAX = 0.04, 0.15     # the hard-loss exit already fires at -15%


def stop_distance(atr_fraction: float, multiple: float) -> float:
    """Entry stop distance as a fraction of price: `multiple` x ATR, clamped."""
    return round(min(max(atr_fraction * multiple, STOP_DIST_MIN), STOP_DIST_MAX), 6)


ATR_STOP_MULTIPLE = 2.5
ADX_MIN = 25.0
SIGNAL_NAMES = ("macd_cross", "ema21_reclaim", "breakout", "adx25")


def compute_signals(closes, highs, lows, volumes) -> Dict:
    """Every experimental signal for one stock as of its latest bar."""
    c, h = np.asarray(closes, dtype=float), np.asarray(highs, dtype=float)
    l, v = np.asarray(lows, dtype=float), np.asarray(volumes, dtype=float)
    strength = adx(h, l, c)
    volatility = atr_pct(h, l, c)
    return {
        "macd_cross": macd_cross_up(c),
        "ema21_reclaim": ema_reclaim(c),
        "breakout": breakout(c, v),
        "adx25": bool(strength >= ADX_MIN),
        "adx": None if np.isnan(strength) else round(strength, 1),
        "atr_pct": None if np.isnan(volatility) else round(volatility, 5),
    }
