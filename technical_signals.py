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


def _sma_series(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(np.asarray(x, dtype=float), 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def golden_cross_recent(closes: np.ndarray, within: int = 20) -> bool:
    """The 50-day average crossed above the 200-day within the last `within` sessions (and is still above)."""
    c = np.asarray(closes, dtype=float)
    if len(c) < 200 + within:
        return False
    s50, s200 = _sma_series(c, 50), _sma_series(c, 200)
    above = s50 > s200
    if not above[-1]:
        return False
    recent = above[-(within + 1):]
    return bool((~recent[:-1]).any())          # it was below at some point in the window, above now


def death_cross(closes: np.ndarray) -> bool:
    """The 50-day average is below the 200-day (the state a death cross leads to)."""
    c = np.asarray(closes, dtype=float)
    if len(c) < 200:
        return False
    return bool(c[-50:].mean() < c[-200:].mean())


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
MA50_ROOM_MIN = 0.06       # close at least this far above the 50-day average
SIGNAL_NAMES = ("macd_cross", "ema21_reclaim", "breakout", "adx25", "ma50_room")


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
        # 50-day crossed above the 200-day in the last 20 sessions. Measured only (experiments.py)
        "golden_cross": golden_cross_recent(c),
        "adx25": bool(strength >= ADX_MIN),
        # The main exit is a close below the 50-day average; an entry just above
        # it has no room for an ordinary down day (78% fail within a month).
        "ma50_room": bool(len(c) >= 50 and c[-1] / c[-50:].mean() - 1 >= MA50_ROOM_MIN),
        "adx": None if np.isnan(strength) else round(strength, 1),
        "atr_pct": None if np.isnan(volatility) else round(volatility, 5),
    }


# ── Swing-post signals (2026-11-12) ──────────────────────────────────────────
# From a r/swingtrading write-up (u/30RITUALS, "How Trading Really Works, part 3"):
# buy strong stocks while they are tight, in names that move enough to matter,
# never when stretched far above the 20-day line, and only in a healthy market.

def adr_pct(high: np.ndarray, low: np.ndarray, n: int = 20) -> float:
    """Average daily range: mean of (high / low - 1) over the last `n` sessions, in percent."""
    h, l = np.asarray(high, dtype=float)[-n:], np.asarray(low, dtype=float)[-n:]
    if len(h) < n or (l <= 0).any():
        return float("nan")
    return float((h / l - 1).mean() * 100)


def tightness(closes: np.ndarray, volumes: np.ndarray, short: int = 10, long: int = 50,
              near_high: float = 0.10) -> float:
    """
    How "tight" a stock is, 0 (loose) to 1 (very tight): the last `short`
    sessions trade in a narrow band compared with the last `long`, on volume
    that has dried up, near the top of the range (a coiled base, not a
    stock lying quietly at its lows).

      range part   short-window close range / long-window close range;
                   0.5 or more scores 0, 0.1 or less scores 1
      volume part  short-window average volume / long-window average;
                   1.0 or more scores 0, 0.5 or less scores 1
      gate         0 if the close is more than `near_high` below the long-window high
    """
    c = np.asarray(closes, dtype=float)
    v = np.asarray(volumes, dtype=float)
    if len(c) < long or len(v) < long:
        return 0.0
    cl, cs = c[-long:], c[-short:]
    if cl.max() <= 0 or c[-1] < cl.max() * (1 - near_high):
        return 0.0
    long_range = cl.max() - cl.min()
    if long_range <= 0:
        return 0.0
    range_ratio = (cs.max() - cs.min()) / long_range
    vl = v[-long:].mean()
    vol_ratio = v[-short:].mean() / vl if vl > 0 else 1.0
    range_part = float(np.clip((0.5 - range_ratio) / 0.4, 0.0, 1.0))
    vol_part = float(np.clip((1.0 - vol_ratio) / 0.5, 0.0, 1.0))
    return round(0.5 * range_part + 0.5 * vol_part, 4)


def extension_atr(high: np.ndarray, low: np.ndarray, closes: np.ndarray, span: int = 20, n: int = 14) -> float:
    """How far the close sits above its `span`-day EMA, in multiples of ATR(n) (negative when below)."""
    c = np.asarray(closes, dtype=float)
    if len(c) < max(span, n) + 1:
        return float("nan")
    atr = atr_pct(high, low, c, n) * c[-1]
    if not atr or atr != atr:
        return float("nan")
    return float((c[-1] - _ema(c, span)[-1]) / atr)


def ema_last(closes: np.ndarray, span: int = 20) -> float:
    """The latest value of the `span`-day EMA."""
    c = np.asarray(closes, dtype=float)
    return float(_ema(c, span)[-1]) if len(c) else float("nan")


def above_ema(closes: np.ndarray, span: int = 20) -> bool:
    """Close above its `span`-day EMA: the post's minimum test for QQQ before any long entry."""
    c = np.asarray(closes, dtype=float)
    if len(c) < span:
        return False
    return bool(c[-1] > _ema(c, span)[-1])


def breadth_above_sma(closes: pd.DataFrame, n: int = 50) -> float:
    """Share of columns whose last close is above their own `n`-day average (NaN-safe)."""
    tail = closes.tail(n)
    full = tail.notna().sum() >= n
    if not full.any():
        return float("nan")
    last, avg = tail.iloc[-1][full], tail.mean()[full]
    return float((last > avg).mean())
