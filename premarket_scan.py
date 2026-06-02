#!/usr/bin/env python3
"""
Pre-market scan: overnight gaps, trend health, candidate prioritization.
Run at ~6 AM PT (market opens 6:30 AM PT).
"""

import sys
import warnings
from datetime import datetime, timezone
import pytz

warnings.filterwarnings("ignore")
sys.path.insert(0, "/home/user/stocktrader")

import yfinance as yf
import pandas as pd
import numpy as np

from config import DEFAULT_UNIVERSE

PT = pytz.timezone("America/Los_Angeles")
now_pt = datetime.now(PT)

print("=" * 65)
print(f"  PRE-MARKET BRIEFING  |  {now_pt.strftime('%Y-%m-%d %I:%M %p PT')}")
print("=" * 65)


# ---------------------------------------------------------------------------
# Fetch price data for all tickers (210 days to cover 200MA + buffer)
# ---------------------------------------------------------------------------
print("\n[Fetching price data for all tickers...]\n")
tickers = DEFAULT_UNIVERSE[:]

try:
    raw = yf.download(tickers, period="210d", auto_adjust=True,
                      progress=False, threads=True)
    close = raw["Close"].dropna(how="all")
    volume = raw["Volume"].dropna(how="all")
except Exception as e:
    print(f"ERROR: yfinance download failed: {e}")
    sys.exit(1)

# Drop tickers that came back empty
valid_tickers = [t for t in tickers if t in close.columns and close[t].dropna().shape[0] > 202]
print(f"  Tickers with sufficient data: {len(valid_tickers)} / {len(tickers)}")

missing = [t for t in tickers if t not in valid_tickers]
if missing:
    print(f"  Tickers with insufficient data: {missing}")


# ---------------------------------------------------------------------------
# TASK 1: OVERNIGHT GAP SCAN
# ---------------------------------------------------------------------------
print("\n" + "=" * 65)
print("  TASK 1: OVERNIGHT GAP SCAN")
print("=" * 65)

gap_flags = []

for ticker in valid_tickers:
    prices = close[ticker].dropna()
    vols   = volume[ticker].dropna() if ticker in volume.columns else pd.Series(dtype=float)

    if len(prices) < 2:
        continue

    prev_close = prices.iloc[-2]
    today_open_proxy = prices.iloc[-1]   # using today's close as proxy for open direction
    pct_change = (today_open_proxy - prev_close) / prev_close

    # 50-day MA
    ma50 = prices.iloc[-50:].mean() if len(prices) >= 50 else None

    reasons = []

    # Gap down > 3%
    if pct_change < -0.03:
        reasons.append(f"gap down {pct_change:.1%}")

    # Crossed below 50MA overnight (prev close above, today close below)
    if ma50 is not None:
        prev_above = prices.iloc[-2] >= ma50
        today_below = prices.iloc[-1] < ma50
        if prev_above and today_below:
            reasons.append(f"crossed BELOW 50MA ({ma50:.2f})")

    # Volume spike > 2x 20-day avg
    if len(vols) >= 21:
        avg_vol_20 = vols.iloc[-21:-1].mean()
        today_vol  = vols.iloc[-1]
        if avg_vol_20 > 0 and today_vol > 2 * avg_vol_20:
            reasons.append(f"volume spike {today_vol / avg_vol_20:.1f}x avg")

    # Also flag strong gap UP (> 3%) above 50MA for prioritization
    if pct_change > 0.03 and ma50 is not None and today_open_proxy > ma50:
        reasons.append(f"gap UP {pct_change:.1%} above 50MA — momentum alert")

    if reasons:
        gap_flags.append({
            "ticker": ticker,
            "prev_close": round(prev_close, 2),
            "current": round(today_open_proxy, 2),
            "pct_chg": round(pct_change * 100, 2),
            "ma50": round(ma50, 2) if ma50 else None,
            "reasons": reasons,
        })

if gap_flags:
    # Sort: gap-downs first, then gap-ups
    gap_flags.sort(key=lambda x: x["pct_chg"])
    for f in gap_flags:
        print(f"\n  {f['ticker']:6s}  {f['pct_chg']:+.1f}%  (prev {f['prev_close']}  |  cur {f['current']}  |  50MA {f['ma50']})")
        for r in f["reasons"]:
            print(f"           → {r}")
else:
    print("\n  No significant overnight gap alerts.")


# ---------------------------------------------------------------------------
# TASK 2: TREND HEALTH CHECK  (price > 50MA > 200MA)
# ---------------------------------------------------------------------------
print("\n" + "=" * 65)
print("  TASK 2: TREND HEALTH CHECK")
print("=" * 65)

strong, mixed, weak = [], [], []
trend_details = {}

for ticker in valid_tickers:
    prices = close[ticker].dropna()
    if len(prices) < 200:
        weak.append(ticker)
        trend_details[ticker] = {"bucket": "WEAK", "note": "insufficient history"}
        continue

    price  = prices.iloc[-1]
    ma50   = prices.iloc[-50:].mean()
    ma200  = prices.iloc[-200:].mean()

    if price > ma50 > ma200:
        strong.append(ticker)
        trend_details[ticker] = {"bucket": "STRONG", "price": round(price, 2),
                                  "ma50": round(ma50, 2), "ma200": round(ma200, 2),
                                  "pct_above_50ma": round((price/ma50 - 1)*100, 1)}
    elif price > ma50:
        mixed.append(ticker)
        trend_details[ticker] = {"bucket": "MIXED",  "price": round(price, 2),
                                  "ma50": round(ma50, 2), "ma200": round(ma200, 2)}
    else:
        weak.append(ticker)
        trend_details[ticker] = {"bucket": "WEAK",   "price": round(price, 2),
                                  "ma50": round(ma50, 2), "ma200": round(ma200, 2)}

print(f"\n  STRONG (price > 50MA > 200MA)  — ready for BUY entry:  {len(strong)}")
for t in sorted(strong):
    d = trend_details[t]
    print(f"    {t:6s}  price {d['price']:>8.2f}  50MA {d['ma50']:>8.2f}  200MA {d['ma200']:>8.2f}  (+{d['pct_above_50ma']}% above 50MA)")

print(f"\n  MIXED  (price > 50MA, 50MA < 200MA)  — caution:  {len(mixed)}")
for t in sorted(mixed):
    d = trend_details[t]
    print(f"    {t:6s}  price {d['price']:>8.2f}  50MA {d['ma50']:>8.2f}  200MA {d['ma200']:>8.2f}")

print(f"\n  WEAK   (price < 50MA)  — blocked by entry gate:  {len(weak)}")
for t in sorted(weak):
    print(f"    {t}")

if len(strong) < 5:
    print("\n  ⚠  WARNING: Fewer than 5 STRONG stocks. Today will likely produce few BUY signals.")


# ---------------------------------------------------------------------------
# TASK 3: MODEL HEALTH CHECK
# ---------------------------------------------------------------------------
print("\n" + "=" * 65)
print("  TASK 3: MODEL HEALTH CHECK")
print("=" * 65)

try:
    from config import DEFAULT_CONFIG
    print("  ✓ config.DEFAULT_CONFIG imported OK")
except Exception as e:
    print(f"  ✗ config import FAILED: {e}")

try:
    from signals import SignalGenerator
    sg = SignalGenerator()
    print("  ✓ signals.SignalGenerator instantiated OK")
except Exception as e:
    print(f"  ✗ signals import FAILED: {e}")

try:
    from momentum import MomentumAnalyzer
    print("  ✓ momentum.MomentumAnalyzer imported OK")
except Exception as e:
    print(f"  ✗ momentum import FAILED: {e}")

try:
    from decision_engine import DecisionEngine
    print("  ✓ decision_engine.DecisionEngine imported OK")
except Exception as e:
    print(f"  ✗ decision_engine import FAILED: {e}")

try:
    from universe_screener import UniverseScreener
    print("  ✓ universe_screener.UniverseScreener imported OK")
except Exception as e:
    print(f"  ✗ universe_screener import FAILED: {e}")

try:
    from run_daily_analysis import fetch_market_data
    print("  ✓ run_daily_analysis.fetch_market_data imported OK")
except ImportError:
    print("  ~ run_daily_analysis.fetch_market_data: function not present (non-critical)")
except Exception as e:
    print(f"  ✗ run_daily_analysis import FAILED: {e}")


# ---------------------------------------------------------------------------
# TASK 4 + 5: CANDIDATE PRIORITIZATION & FINAL BRIEFING
# ---------------------------------------------------------------------------
print("\n" + "=" * 65)
print("  TASK 4 & 5: CANDIDATE PRIORITIZATION & BRIEFING")
print("=" * 65)

# Score each STRONG stock by: % above 50MA, momentum over last 5 days, 20-day return
scored = []
for ticker in strong:
    prices = close[ticker].dropna()
    price  = prices.iloc[-1]
    ma50   = prices.iloc[-50:].mean()
    ma200  = prices.iloc[-200:].mean()

    pct_above_50 = (price / ma50 - 1)
    ret_5d  = (price / prices.iloc[-6]  - 1) if len(prices) >= 6  else 0
    ret_20d = (price / prices.iloc[-21] - 1) if len(prices) >= 21 else 0

    # Simple composite: 40% pos above 50MA + 30% 5d momentum + 30% 20d trend
    score = 0.40 * min(pct_above_50, 0.30) + 0.30 * max(min(ret_5d, 0.15), -0.15) + 0.30 * max(min(ret_20d, 0.40), -0.40)
    scored.append({
        "ticker": ticker,
        "price":  round(price, 2),
        "ma50":   round(ma50, 2),
        "ma200":  round(ma200, 2),
        "pct_above_50": round(pct_above_50 * 100, 1),
        "ret_5d":  round(ret_5d * 100, 1),
        "ret_20d": round(ret_20d * 100, 1),
        "score":  round(score, 4),
    })

scored.sort(key=lambda x: -x["score"])

gap_down_avoid = [f["ticker"] for f in gap_flags if f["pct_chg"] < -3.0 or
                  any("crossed BELOW 50MA" in r for r in f["reasons"])]
gap_up_priority = [f["ticker"] for f in gap_flags if f["pct_chg"] > 3.0 and
                   any("gap UP" in r for r in f["reasons"])]

print("\n  TOP 5 BUY CANDIDATES (STRONG trend + best momentum score):")
top5 = [s for s in scored if s["ticker"] not in gap_down_avoid][:5]
for i, s in enumerate(top5, 1):
    extra = " ← gap-up momentum!" if s["ticker"] in gap_up_priority else ""
    print(f"    {i}. {s['ticker']:6s}  price {s['price']:>8.2f}  50MA {s['ma50']:>8.2f}  "
          f"5d {s['ret_5d']:+.1f}%  20d {s['ret_20d']:+.1f}%  score {s['score']:.4f}{extra}")

if gap_down_avoid:
    print(f"\n  NEAR-TERM AVOIDS (gap down / crossed 50MA): {gap_down_avoid}")

# Recommendation
if len(strong) >= 15:
    recommendation = "NORMAL RUN — healthy trend environment, good signal candidates expected."
elif len(strong) >= 5:
    recommendation = "LIGHT DAY — moderate trend universe, expect 1-3 BUY signals."
else:
    recommendation = "CAUTION — very few stocks in strong trend. Execution run may produce no signals."

print("\n" + "=" * 65)
print("  FINAL PRE-MARKET RECOMMENDATION")
print("=" * 65)
print(f"""
  Date/Time  : {now_pt.strftime('%Y-%m-%d %H:%M %Z')}
  Universe   : {len(valid_tickers)} tickers scanned

  Trend Buckets:
    STRONG  : {len(strong):3d}  (price > 50MA > 200MA)
    MIXED   : {len(mixed):3d}  (price > 50MA, 50MA below 200MA)
    WEAK    : {len(weak):3d}  (price below 50MA — entry gate BLOCKED)

  Gap Alerts : {len(gap_flags)} stocks flagged overnight
    Gap-down avoids  : {gap_down_avoid if gap_down_avoid else 'none'}
    Gap-up momentum  : {gap_up_priority if gap_up_priority else 'none'}

  Model Health: see above (all core imports checked)

  RECOMMENDATION: {recommendation}
""")
print("=" * 65)
