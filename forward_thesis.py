"""
Forward Thesis Scorer
=====================
Evaluates WHERE a stock is likely going before entry — not just where it has been.

The momentum entry gate (50MA > 200MA, RS rank) confirms trend exists.
This module asks: WHY will momentum continue from here?

Three forward-looking dimensions:

1. EARNINGS ACCELERATION (0-35 pts)
   Is the underlying business getting better quarter-over-quarter?
   Accelerating EPS and revenue growth creates sustained institutional demand.
   A stock running on sentiment reverts. One running on accelerating earnings
   compounds. This is the most durable momentum driver.

2. SETUP QUALITY (0-35 pts)
   Where is the stock in its cycle? A stock breaking out of a multi-week
   consolidation base has fresh demand. One that has run 40%+ from its
   base without pausing is extended — the risk/reward is unfavorable.
   Early in a move = high quality. Extended = lower quality.

3. INSTITUTIONAL ACCUMULATION (0-30 pts)
   Are large players building or trimming? Volume ratio (up-day volume vs
   down-day volume over 60 sessions) is the accessible proxy. Sustained
   accumulation sustains momentum. Distribution precedes breakdowns.

Combined: thesis_score 0-100.
Used to rank screener candidates and gate entries.
A stock can have RS rank 95 but weak thesis score (late in move, distribution)
and should be weighted lower than RS rank 80 with strong thesis.
"""

import logging
from typing import Dict, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)


def score_earnings_acceleration(symbol: str) -> Tuple[float, str]:
    """
    Score based on quarterly earnings and revenue trajectory.

    Returns (score 0-35, reason string).

    Looks for:
    - EPS growing QoQ AND the growth rate itself is accelerating
    - Revenue growth rate accelerating (not just positive)
    - Recent quarter beat estimates (if available)

    Scoring:
      35 pts: EPS and revenue both accelerating, recent beat
      25 pts: EPS accelerating, revenue flat/positive
      15 pts: positive growth but not accelerating
       5 pts: mixed signals
       0 pts: no data or declining
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)

        # Quarterly financials
        qf = ticker.quarterly_financials
        if qf is None or qf.empty:
            return 0.0, "no earnings data"

        score = 0.0
        reasons = []

        # EPS row — try canonical names in priority order to avoid picking
        # "Total Income" or "Other Income" before "Net Income"
        _EPS_CANDIDATES = [
            "Net Income", "Net Income Common Stockholders",
            "Normalized Income", "Net Income From Continuing Operation Net Minority Interest",
        ]
        _REV_CANDIDATES = [
            "Total Revenue", "Revenue", "Net Revenue", "Sales",
        ]

        eps_row = next((r for r in _EPS_CANDIDATES if r in qf.index), None)
        if eps_row is None:
            # Fallback: first row with 'income' but not 'other' or 'total other'
            eps_row = next(
                (r for r in qf.index
                 if 'income' in r.lower() and 'other' not in r.lower()),
                None
            )

        rev_row = next((r for r in _REV_CANDIDATES if r in qf.index), None)
        if rev_row is None:
            rev_row = next(
                (r for r in qf.index if 'revenue' in r.lower()),
                None
            )

        if eps_row and len(qf.columns) >= 4:
            eps_vals = qf.loc[eps_row].dropna().values[:4]
            if len(eps_vals) >= 3:
                # Quarter-over-quarter growth rates
                eps_growth = []
                for i in range(len(eps_vals) - 1):
                    if eps_vals[i+1] != 0:
                        g = (eps_vals[i] - eps_vals[i+1]) / abs(eps_vals[i+1])
                        eps_growth.append(g)

                if len(eps_growth) >= 2:
                    # Is growth rate increasing? (deceleration is a warning sign)
                    accelerating = eps_growth[0] > eps_growth[1]
                    positive     = eps_growth[0] > 0

                    if positive and accelerating:
                        score += 20
                        reasons.append(f"EPS accelerating ({eps_growth[0]*100:.0f}% vs {eps_growth[1]*100:.0f}% prior)")
                    elif positive:
                        score += 10
                        reasons.append(f"EPS growing {eps_growth[0]*100:.0f}% but decelerating")
                    elif accelerating and eps_growth[0] > -0.10:
                        score += 5
                        reasons.append("EPS losses narrowing")

        if rev_row and len(qf.columns) >= 4:
            rev_vals = qf.loc[rev_row].dropna().values[:4]
            if len(rev_vals) >= 3 and rev_vals[-1] != 0:
                rev_growth = [(rev_vals[i] - rev_vals[i+1]) / abs(rev_vals[i+1])
                              for i in range(len(rev_vals)-1) if rev_vals[i+1] != 0]
                if len(rev_growth) >= 2:
                    if rev_growth[0] > rev_growth[1] and rev_growth[0] > 0:
                        score += 10
                        reasons.append(f"Revenue accelerating ({rev_growth[0]*100:.0f}%)")
                    elif rev_growth[0] > 0:
                        score += 5
                        reasons.append(f"Revenue growing {rev_growth[0]*100:.0f}%")

        score = min(35.0, score)
        return score, " · ".join(reasons) if reasons else "earnings data present"

    except Exception as e:
        logger.debug(f"Earnings acceleration check failed for {symbol}: {e}")
        return 0.0, "earnings unavailable"


def score_setup_quality(closes: np.ndarray, volumes: np.ndarray) -> Tuple[float, str]:
    """
    Score the quality of the current entry setup.

    Returns (score 0-35, reason string).

    A high-quality setup is:
    - Stock just broke out of or is breaking out of a consolidation base
    - Price is not extended far from its recent base
    - The base was long enough to be meaningful (4+ weeks)

    Low quality:
    - Stock has run 30%+ from base with no consolidation
    - Recent breakout already failed (false breakout)
    - Extended above upper Bollinger Band

    Scoring:
      35 pts: clean base breakout, low extension, building volume
      25 pts: modest extension from base, trend intact
      15 pts: some extension but within normal range
       5 pts: highly extended or failed setup
    """
    if len(closes) < 60:
        return 10.0, "insufficient history"

    current = float(closes[-1])

    # Find the consolidation base: lowest point in last 10 weeks
    # and the range of that 10-week period
    lookback_10w = closes[-50:]  # ~10 weeks
    base_low  = float(lookback_10w.min())
    base_high = float(lookback_10w.max())

    if base_low <= 0:
        return 5.0, "invalid price data"

    # Extension from base low
    extension_from_low  = (current - base_low) / base_low
    # Range tightness: tight base = consolidation
    base_range_pct = (base_high - base_low) / base_low

    # How long was the consolidation? (weeks where price was flat ±5%)
    mid_price = (base_high + base_low) / 2
    flat_weeks = sum(
        1 for i in range(0, min(50, len(closes)), 5)
        if abs(closes[-(i+1)] - mid_price) / mid_price < 0.08
    )

    # Bollinger Band extension
    if len(closes) >= 20:
        bb_slice  = closes[-20:]
        bb_mid    = float(bb_slice.mean())
        bb_std    = float(bb_slice.std())
        bb_upper  = bb_mid + 2 * bb_std
        bb_lower  = bb_mid - 2 * bb_std
        band_w    = bb_upper - bb_lower
        pct_b     = (current - bb_lower) / band_w if band_w > 0 else 0.5
    else:
        pct_b = 0.5

    score = 10.0
    reasons = []

    # Extension scoring: tighter = better
    if extension_from_low < 0.12:
        score += 20
        reasons.append(f"fresh setup ({extension_from_low*100:.0f}% from base)")
    elif extension_from_low < 0.20:
        score += 12
        reasons.append(f"modest extension ({extension_from_low*100:.0f}% from base)")
    elif extension_from_low < 0.30:
        score += 6
        reasons.append(f"extended {extension_from_low*100:.0f}% from base")
    else:
        score += 0
        reasons.append(f"highly extended {extension_from_low*100:.0f}% — late entry risk")

    # Base quality: tight consolidation is better
    if base_range_pct < 0.12 and flat_weeks >= 3:
        score += 5
        reasons.append("tight base")
    elif base_range_pct < 0.20:
        score += 2

    # BB position penalty for overextension
    if pct_b > 0.90:
        score = max(5.0, score - 8)
        reasons.append(f"BB overextended (%B={pct_b:.2f})")
    elif pct_b < 0.60:
        score += 2
        reasons.append("room to run in BB")

    return min(35.0, score), " · ".join(reasons)


def score_institutional_accumulation(closes: np.ndarray, volumes: np.ndarray) -> Tuple[float, str]:
    """
    Score institutional accumulation vs distribution.

    Returns (score 0-30, reason string).

    Up-day volume / Down-day volume ratio over last 60 sessions:
    > 1.5: strong accumulation (big money buying)
    1.0-1.5: slight accumulation / neutral
    < 0.8: distribution (big money selling)

    Also checks: is volume expanding on recent up days? (last 2 weeks)
    """
    if len(closes) < 40 or len(volumes) < 40:
        return 10.0, "insufficient volume data"

    # Last 60 sessions (or available)
    n = min(60, len(closes))
    c = closes[-n:]
    v = volumes[-n:]

    # Separate up days and down days
    up_vols   = [v[i] for i in range(1, n) if c[i] > c[i-1]]
    down_vols = [v[i] for i in range(1, n) if c[i] < c[i-1]]

    if not up_vols or not down_vols:
        return 10.0, "insufficient up/down days"

    avg_up_vol   = float(np.mean(up_vols))
    avg_down_vol = float(np.mean(down_vols))
    ratio = avg_up_vol / avg_down_vol if avg_down_vol > 0 else 1.0

    # Recent trend: are the last 10 days showing accumulation?
    recent_n = min(10, len(closes))
    recent_c = closes[-recent_n:]
    recent_v = volumes[-recent_n:]
    recent_up_vol   = float(np.mean([recent_v[i] for i in range(1, recent_n) if recent_c[i] > recent_c[i-1]] or [0]))
    recent_down_vol = float(np.mean([recent_v[i] for i in range(1, recent_n) if recent_c[i] < recent_c[i-1]] or [1]))
    recent_ratio = recent_up_vol / recent_down_vol if recent_down_vol > 0 else 1.0

    score = 5.0
    reasons = []

    if ratio >= 1.6:
        score += 18
        reasons.append(f"strong accumulation (vol ratio {ratio:.2f}x)")
    elif ratio >= 1.2:
        score += 12
        reasons.append(f"accumulation ({ratio:.2f}x up/down vol)")
    elif ratio >= 0.9:
        score += 6
        reasons.append(f"neutral vol ({ratio:.2f}x)")
    else:
        score += 0
        reasons.append(f"distribution ({ratio:.2f}x — selling pressure)")

    # Recent trend bonus
    if recent_ratio >= 1.5:
        score += 7
        reasons.append("recent accumulation accelerating")
    elif recent_ratio < 0.8:
        score = max(5.0, score - 5)
        reasons.append("recent distribution")

    return min(30.0, score), " · ".join(reasons)


def score_forward_thesis(
    symbol: str,
    closes: np.ndarray,
    volumes: np.ndarray,
    include_earnings: bool = True,
) -> Dict:
    """
    Composite forward thesis score for a candidate.

    Runs all three dimensions and returns a combined score + breakdown.
    Used by screener to weight candidates and by entry logic to gate entries.

    Args:
        symbol:           Stock ticker
        closes:           Array of close prices (oldest first)
        volumes:          Array of volumes (oldest first)
        include_earnings: Fetch earnings data (slower, skip for large batches)

    Returns:
        {
            "symbol": str,
            "thesis_score": float (0-100),
            "thesis_grade": str ("A"/"B"/"C"/"D"),
            "earnings_score": float,
            "setup_score": float,
            "accumulation_score": float,
            "earnings_reason": str,
            "setup_reason": str,
            "accumulation_reason": str,
            "summary": str,
        }
    """
    closes  = np.asarray(closes,  dtype=float)
    volumes = np.asarray(volumes, dtype=float)

    # Score each dimension
    if include_earnings:
        earn_score, earn_reason = score_earnings_acceleration(symbol)
    else:
        earn_score, earn_reason = 15.0, "skipped"

    setup_score, setup_reason   = score_setup_quality(closes, volumes)
    accum_score, accum_reason   = score_institutional_accumulation(closes, volumes)

    total = earn_score + setup_score + accum_score  # 0-100

    if total >= 70:
        grade = "A"
    elif total >= 55:
        grade = "B"
    elif total >= 40:
        grade = "C"
    else:
        grade = "D"

    summary = f"{earn_reason} | {setup_reason} | {accum_reason}"

    return {
        "symbol":             symbol,
        "thesis_score":       round(total, 1),
        "thesis_grade":       grade,
        "earnings_score":     round(earn_score, 1),
        "setup_score":        round(setup_score, 1),
        "accumulation_score": round(accum_score, 1),
        "earnings_reason":    earn_reason,
        "setup_reason":       setup_reason,
        "accumulation_reason": accum_reason,
        "summary":            summary,
    }


if __name__ == "__main__":
    import yfinance as yf

    test_symbols = ["ARM", "MRVL", "CRWD", "AMD", "NVDA"]
    print(f"\n{'='*70}")
    print(f"FORWARD THESIS SCORES")
    print(f"{'='*70}")
    print(f"{'SYM':<6} {'THESIS':>7} {'GRADE':>5} {'EARNINGS':>9} {'SETUP':>6} {'ACCUM':>6}  SUMMARY")
    print(f"{'-'*70}")

    for sym in test_symbols:
        try:
            hist    = yf.Ticker(sym).history(period="1y")
            closes  = hist["Close"].values
            volumes = hist["Volume"].values
            result  = score_forward_thesis(sym, closes, volumes, include_earnings=True)
            print(
                f"{sym:<6} {result['thesis_score']:>7.1f} "
                f"{result['thesis_grade']:>5} "
                f"{result['earnings_score']:>9.1f} "
                f"{result['setup_score']:>6.1f} "
                f"{result['accumulation_score']:>6.1f}  "
                f"{result['setup_reason']}"
            )
        except Exception as e:
            print(f"{sym:<6} ERROR: {e}")
    print(f"{'='*70}\n")
