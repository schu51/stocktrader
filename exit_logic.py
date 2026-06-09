"""
Exit Logic
==========
Shared stop-price calculation and exit-trigger evaluation.
Used by stop_placement.py and intraday_exit.py.

Trailing stop tiers (mirror run_daily_analysis.py _evaluate_exits):
  pnl >= 100%  →  8% trail below current price
  pnl >= 50%   → 10% trail below current price
  pnl >= 25%   →  break-even + 1.5%
  pnl < 25%    →  8% below avg_cost (default protection for entries)

Exit triggers:
  PRICE_BELOW_50MA  →  current price crossed below 50-day MA
  HARD_LOSS_STOP    →  unrealized loss exceeds 15%
"""

import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


def calculate_stop_price(
    current: float,
    pnl_pct: float,
    avg_cost: float,
) -> Tuple[float, str]:
    """
    Calculate the appropriate stop price for a position.

    Args:
        current:  Current market price
        pnl_pct:  Unrealized P&L as a percentage (e.g. 63.5 for +63.5%)
        avg_cost: Average entry price

    Returns:
        (stop_price, tier_description)
    """
    if pnl_pct >= 100:
        return round(current * 0.92, 2), "100%+ gain → 8% trail"
    elif pnl_pct >= 50:
        return round(current * 0.90, 2), "50%+ gain → 10% trail"
    elif pnl_pct >= 25:
        return round(avg_cost * 1.015, 2), "25%+ gain → break-even+1.5%"
    else:
        return round(avg_cost * 0.92, 2), "default → 8% below entry"


def check_exit_triggers(
    symbol: str,
    current_price: float,
    avg_cost: float,
    pnl_pct: float,
    sma50: Optional[float],
) -> Tuple[bool, str, str]:
    """
    Evaluate whether a position should be exited.

    Args:
        symbol:        Stock ticker (for logging)
        current_price: Current market price
        avg_cost:      Average entry price
        pnl_pct:       Unrealized P&L as a percentage (e.g. -10.1)
        sma50:         50-day simple moving average (None if unavailable)

    Returns:
        (should_exit: bool, trigger: str, reason: str)
        trigger is empty string when should_exit is False.
    """
    # Hard loss: down more than 15% — thesis was wrong at entry
    if pnl_pct < -15:
        return (
            True,
            "HARD_LOSS_STOP",
            f"Down {pnl_pct:.1f}% — entry thesis invalidated",
        )

    # Price crossed below 50MA — momentum trend broken
    if sma50 is not None and current_price < sma50:
        pct_below = (sma50 - current_price) / sma50 * 100
        return (
            True,
            "PRICE_BELOW_50MA",
            f"${current_price:.2f} is {pct_below:.1f}% below 50MA ${sma50:.2f}",
        )

    return False, "", ""


def fetch_sma50(symbol: str) -> Optional[float]:
    """
    Fetch 50-day SMA for a symbol via yfinance.
    Returns None if data is unavailable or history is too short.
    """
    try:
        import yfinance as yf
        hist = yf.Ticker(symbol).history(period="1y")
        closes = hist["Close"].values
        if len(closes) < 50:
            logger.warning(f"{symbol}: insufficient history for 50MA ({len(closes)} bars)")
            return None
        return float(closes[-50:].mean())
    except Exception as e:
        logger.warning(f"{symbol}: could not fetch 50MA — {e}")
        return None
