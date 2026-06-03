"""
Momentum Screener
=================
Scans S&P 500 + Nasdaq 100 (~600 names) daily for momentum setups.

Methodology:
  1. Fetch universe (S&P 500 via Wikipedia + NDX supplement)
  2. Batch-download 1-year price + volume history (single yfinance call)
  3. Calculate IBD-style Relative Strength score per stock
     RS = 0.40 × Q4_return + 0.20 × Q3_return + 0.20 × Q2_return + 0.20 × Q1_return
  4. Percentile-rank all RS scores (RS Rank 99 = top 1% of performers)
  5. Check market regime via SPY vs 200MA (adjusts RS threshold)
  6. Apply quality gate from downloaded data (price, volume)
  7. Apply technical gate: price > 50MA > 200MA
  8. Score sector leadership — boost stocks in top 3 sectors
  9. Return top 50 ranked candidates

Output written to docs/data/screener.json and read by run_daily_analysis.py.

Usage:
    python screener.py                        # run and write output
    python screener.py --top 30               # limit candidates
    python screener.py --dry-run              # print results, no file write
"""

import argparse
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Fallback universe (used when Wikipedia fetch fails) ──────────────────────
FALLBACK_UNIVERSE = [
    # Mega-cap tech
    "NVDA", "META", "GOOGL", "MSFT", "AMZN", "AAPL", "TSLA",
    # Semiconductors
    "AMD", "AVGO", "QCOM", "MRVL", "ARM", "SMCI", "ON", "AMAT", "KLAC", "LAM", "MCHP", "TXN",
    # Cybersecurity
    "CRWD", "PANW", "FTNT", "ZS", "OKTA", "S", "CYBR",
    # Enterprise software / cloud
    "DDOG", "SNOW", "MDB", "NET", "NOW", "CRM", "WDAY", "TEAM", "PATH", "HUBS",
    # AI infra / power
    "VRT", "ETN", "PWR", "ANET", "CEG", "VST", "APH",
    # Fintech
    "COIN", "NU", "AFRM", "SQ", "SOFI", "V", "MA", "PYPL",
    # Consumer / media
    "SHOP", "DUOL", "APP", "TTD", "RBLX", "SPOT",
    # Healthcare tech
    "ISRG", "DXCM", "PODD",
    # Defense / industrials
    "AXON", "KTOS", "HWM", "GE", "RTX",
    # Emerging
    "PLTR", "RDDT", "MSTR",
    # S&P 500 large-cap momentum names
    "ORCL", "ADBE", "INTU", "CDNS", "SNPS", "ANSS", "FTNT", "ROP",
    "LRCX", "ASML", "TSM", "NFLX", "UBER", "ABNB",
    "LLY", "NVO", "REGN", "VRTX",
    "FCX", "NEM", "GOLD",
    "URI", "PWR", "GNRC",
]


def get_universe() -> List[str]:
    """
    Fetch S&P 500 components from Wikipedia + Nasdaq 100 supplement.
    Falls back to FALLBACK_UNIVERSE if Wikipedia is unreachable.
    """
    tickers = set()

    try:
        import pandas as pd
        table = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            header=0,
        )
        sp500 = table[0]["Symbol"].str.replace(".", "-", regex=False).tolist()
        tickers.update(sp500)
        logger.info(f"Fetched {len(sp500)} S&P 500 tickers from Wikipedia")
    except Exception as e:
        logger.warning(f"Wikipedia fetch failed ({e}) — using fallback universe")
        tickers.update(FALLBACK_UNIVERSE)

    # Nasdaq 100 / growth supplement — names often not in S&P 500
    ndx_supplement = [
        "NVDA", "META", "GOOGL", "MSFT", "AMZN", "TSLA", "AAPL",
        "AMD", "AVGO", "QCOM", "MRVL", "ARM", "SMCI", "ON", "AMAT", "KLAC",
        "CRWD", "PANW", "ZS", "FTNT", "OKTA", "S", "CYBR",
        "DDOG", "SNOW", "MDB", "NET", "NOW", "WDAY", "TEAM", "PATH",
        "COIN", "NU", "AFRM", "SOFI", "HOOD", "UPST",
        "SHOP", "DUOL", "APP", "TTD", "RBLX", "SPOT", "RDDT",
        "PLTR", "AXON", "KTOS",
        "VRT", "ETN", "PWR", "ANET", "CEG", "VST",
        "MSTR", "NFLX", "UBER", "ABNB",
    ]
    tickers.update(ndx_supplement)

    # Keep only clean alphabetic tickers, 2–5 characters
    cleaned = sorted({t for t in tickers if t.isalpha() and 2 <= len(t) <= 5})
    logger.info(f"Total universe: {len(cleaned)} tickers")
    return cleaned


def batch_download(tickers: List[str]) -> Optional[object]:
    """
    Download 1-year OHLCV for all tickers in a single yfinance call.
    Returns the raw DataFrame or None on failure.
    """
    import yfinance as yf

    if not tickers:
        return None

    logger.info(f"Batch downloading {len(tickers)} tickers (1 year)…")
    try:
        data = yf.download(
            tickers,
            period="1y",
            auto_adjust=True,
            progress=False,
            threads=True,
        )
        logger.info("Batch download complete")
        return data
    except Exception as e:
        logger.error(f"Batch download failed: {e}")
        return None


def calculate_rs_scores(
    tickers: List[str],
    data,
) -> Dict[str, float]:
    """
    IBD-style Relative Strength score from pre-downloaded price data.

    Formula (weights most recent performance):
        RS = 0.40 × Q4 + 0.20 × Q3 + 0.20 × Q2 + 0.20 × Q1

    Q4 = last 3 months (most recent), Q3 = 3-6M, Q2 = 6-9M, Q1 = 9-12M.

    Returns raw RS score (decimal return). Percentile ranking done separately.
    """
    if data is None:
        return {}

    # Handle single vs multi-ticker DataFrame shapes
    try:
        closes = data["Close"]
    except Exception:
        return {}

    scores = {}

    for sym in tickers:
        try:
            if hasattr(closes, "columns") and sym in closes.columns:
                prices = closes[sym].dropna().values
            elif not hasattr(closes, "columns"):
                # Single-ticker: closes is a Series
                prices = closes.dropna().values
            else:
                continue

            n = len(prices)
            if n < 200:
                continue

            # Quarter boundaries in trading days
            # Q4 = most recent 63 days, Q3 = 63-126, Q2 = 126-189, Q1 = 189-252
            def ret(start_idx, end_idx=-1):
                s = prices[max(0, n + start_idx)]
                e = prices[-1] if end_idx == -1 else prices[max(0, n + end_idx)]
                return (e / s - 1) if s > 0 else 0

            r4 = ret(-63)          # last 3 months
            r3 = ret(-126, -63)    # 3-6 months ago
            r2 = ret(-189, -126)   # 6-9 months ago
            r1 = ret(-252, -189)   # 9-12 months ago

            rs = 0.40 * r4 + 0.20 * r3 + 0.20 * r2 + 0.20 * r1
            scores[sym] = round(float(rs), 5)
        except Exception:
            continue

    logger.info(f"RS scores calculated for {len(scores)} tickers")
    return scores


def percentile_rank(scores: Dict[str, float]) -> Dict[str, int]:
    """
    Percentile-rank raw RS scores across the full universe.
    RS Rank 99 = top 1% (best performers), RS Rank 1 = worst.
    """
    if not scores:
        return {}

    syms = list(scores.keys())
    vals = list(scores.values())

    ranks = {}
    total = len(vals)
    for sym, val in zip(syms, vals):
        pct = sum(v <= val for v in vals) / total * 100
        ranks[sym] = min(99, max(1, round(pct)))

    return ranks


def get_market_regime() -> Dict:
    """
    Check SPY vs 50MA and 200MA to determine market regime.

    Returns:
        regime:        'bull' | 'neutral' | 'bear'
        rs_threshold:  minimum RS rank to qualify (raised in bear markets)
    """
    import yfinance as yf

    try:
        spy    = yf.Ticker("SPY").history(period="1y")
        closes = spy["Close"].values
        price  = float(closes[-1])
        sma50  = float(closes[-50:].mean())
        sma200 = float(closes[-200:].mean())
        ret_3m = float((closes[-1] / closes[-63] - 1) * 100) if len(closes) >= 63 else 0

        if price > sma50 > sma200:
            regime, threshold = "bull", 70
            note = "SPY: golden alignment — normal thresholds"
        elif price > sma200:
            regime, threshold = "neutral", 80
            note = "SPY: above 200MA but below 50MA — elevated thresholds"
        else:
            regime, threshold = "bear", 90
            note = "SPY: below 200MA — defensive mode, only elite RS names"

        return {
            "regime":        regime,
            "spy_price":     round(price, 2),
            "spy_50ma":      round(sma50, 2),
            "spy_200ma":     round(sma200, 2),
            "spy_3m_return": round(ret_3m, 1),
            "rs_threshold":  threshold,
            "note":          note,
        }
    except Exception as e:
        logger.warning(f"Market regime check failed: {e}")
        return {
            "regime": "unknown",
            "rs_threshold": 75,
            "note": "SPY data unavailable — using default threshold",
        }


def get_sector_leaders(rs_ranks: Dict[str, int]) -> List[str]:
    """
    Rank sectors by average RS rank of their known members.
    Returns sectors ordered best-to-worst.
    """
    from config import SECTOR_MAP

    sector_scores = {}
    for sector, syms in SECTOR_MAP.items():
        member_ranks = [rs_ranks[s] for s in syms if s in rs_ranks]
        if member_ranks:
            sector_scores[sector] = sum(member_ranks) / len(member_ranks)

    return sorted(sector_scores, key=lambda s: sector_scores[s], reverse=True)


def run_screener(
    output_path: Optional[Path] = None,
    top_n: int = 50,
    min_rs_rank: int = 70,
    min_price: float = 5.0,
    min_avg_volume: int = 500_000,
) -> Dict:
    """
    Full momentum screening pipeline. Single entry point.

    Steps:
      1. Universe (S&P 500 + NDX ~600 names)
      2. Market regime (adjusts RS threshold)
      3. Batch download all tickers (one network call)
      4. RS score + percentile rank
      5. Quality gate (price, volume — from downloaded data, no extra API calls)
      6. Technical gate (price > 50MA > 200MA)
      7. Sector leadership scoring
      8. Return top N sorted by effective RS rank

    Args:
        output_path:    write JSON to this path if set
        top_n:          max candidates to return
        min_rs_rank:    floor RS rank (overridden upward by market regime)
        min_price:      minimum stock price
        min_avg_volume: minimum 20-day average volume
    """
    start = datetime.now()

    logger.info("=" * 50)
    logger.info("MOMENTUM SCREENER")
    logger.info("=" * 50)

    # 1. Universe
    universe = get_universe()

    # 2. Market regime
    regime = get_market_regime()
    threshold = max(min_rs_rank, regime["rs_threshold"])
    logger.info(f"Regime: {regime['regime'].upper()} | RS threshold: {threshold}")

    # 3. Batch download
    data = batch_download(universe)
    if data is None:
        logger.error("Download failed — screener aborted")
        return {"error": "download_failed", "candidates": []}

    # 4. RS scores + percentile rank
    raw_scores = calculate_rs_scores(universe, data)
    rs_ranks   = percentile_rank(raw_scores)

    # 5 + 6 + 7. Quality gate + technical gate + sector boost
    from config import SECTOR_MAP
    sym_to_sector = {s: sector for sector, syms in SECTOR_MAP.items() for s in syms}

    # Sector leaders for boost
    sector_leaders = get_sector_leaders(rs_ranks)
    top_sectors    = set(sector_leaders[:3])

    # Get volume data for quality filter
    try:
        volumes = data["Volume"]
    except Exception:
        volumes = None

    try:
        closes = data["Close"]
    except Exception:
        logger.error("No Close data in download")
        return {"error": "no_close_data", "candidates": []}

    candidates = []
    rejected   = {"rs_rank": 0, "quality": 0, "technical": 0}

    for sym in universe:
        # Get price series
        try:
            if hasattr(closes, "columns") and sym in closes.columns:
                price_arr = closes[sym].dropna().values
            else:
                continue

            if len(price_arr) < 200:
                continue
        except Exception:
            continue

        # RS rank filter (fast, no I/O)
        rs_rank = rs_ranks.get(sym, 0)
        if rs_rank < threshold:
            rejected["rs_rank"] += 1
            continue

        # Quality gate (from downloaded data — no extra API calls)
        price = float(price_arr[-1])
        if price < min_price:
            rejected["quality"] += 1
            continue

        if volumes is not None:
            try:
                vol_series = (
                    volumes[sym].dropna() if sym in volumes.columns else None
                )
                if vol_series is not None:
                    avg_vol = float(vol_series.tail(20).mean())
                    if avg_vol < min_avg_volume:
                        rejected["quality"] += 1
                        continue
            except Exception:
                pass

        # Technical gate: price > 50MA > 200MA
        sma50  = float(price_arr[-50:].mean())
        sma200 = float(price_arr[-200:].mean())

        if not (price > sma50 > sma200):
            rejected["technical"] += 1
            continue

        # Build candidate record
        rs_score = raw_scores.get(sym, 0)
        sector   = sym_to_sector.get(sym, "other")
        is_leader = sector in top_sectors

        # Sector boost: top-sector stocks get a 5% score lift in ranking
        effective_score = rs_rank * (1.05 if is_leader else 1.0)

        ret_1m = float((price_arr[-1] / price_arr[-21] - 1) * 100) if len(price_arr) >= 21 else None
        ret_3m = float((price_arr[-1] / price_arr[-63] - 1) * 100) if len(price_arr) >= 63 else None
        ret_6m = float((price_arr[-1] / price_arr[-126] - 1) * 100) if len(price_arr) >= 126 else None

        candidates.append({
            "symbol":          sym,
            "rs_rank":         rs_rank,
            "rs_score":        round(rs_score * 100, 1),  # as percent
            "effective_score": round(effective_score, 1),
            "sector":          sector,
            "sector_leader":   is_leader,
            "trend":           "strong_up",
            "price":           round(price, 2),
            "sma50":           round(sma50, 2),
            "sma200":          round(sma200, 2),
            "ret_1m":          round(ret_1m, 1) if ret_1m is not None else None,
            "ret_3m":          round(ret_3m, 1) if ret_3m is not None else None,
            "ret_6m":          round(ret_6m, 1) if ret_6m is not None else None,
            "source":          "screener",
            "priority":        0 if is_leader else 1,
        })

    # Sort by effective score (RS rank + sector boost)
    candidates.sort(key=lambda x: x["effective_score"], reverse=True)
    candidates = candidates[:top_n]

    elapsed = round((datetime.now() - start).total_seconds(), 1)

    result = {
        "generated_at":    datetime.now().isoformat(),
        "elapsed_seconds": elapsed,
        "market_regime":   regime,
        "universe_size":   len(universe),
        "rs_qualifying":   len(universe) - rejected["rs_rank"],
        "quality_pass":    len(universe) - rejected["rs_rank"] - rejected["quality"],
        "tech_gate_pass":  len(candidates) + len([c for c in candidates]),
        "final_count":     len(candidates),
        "sector_leaders":  sector_leaders[:6],
        "rejection_stats": rejected,
        "candidates":      candidates,
    }

    logger.info(
        f"Complete in {elapsed}s | "
        f"Universe {len(universe)} → RS {result['rs_qualifying']} → "
        f"Quality {result['quality_pass']} → "
        f"Tech gate {result['final_count']} candidates"
    )

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(json.dumps(result, indent=2))
        logger.info(f"Written to {output_path}")

    return result


def print_results(result: Dict):
    """Pretty-print screener results to stdout."""
    regime = result.get("market_regime", {})
    print(f"\n{'='*65}")
    print(f"  MOMENTUM SCREENER  ·  {result.get('generated_at','')[:16]}")
    print(f"{'='*65}")
    print(f"  Universe:     {result.get('universe_size', 0)}")
    print(f"  RS threshold: ≥{regime.get('rs_threshold', '?')}  ({regime.get('regime','?').upper()})")
    print(f"  RS qualifying:{result.get('rs_qualifying', 0)}")
    print(f"  Quality pass: {result.get('quality_pass', 0)}")
    print(f"  Final:        {result.get('final_count', 0)} candidates")
    print(f"  Runtime:      {result.get('elapsed_seconds', 0)}s")
    print(f"\n  Market:  {regime.get('note', '')}")
    leaders = result.get("sector_leaders", [])[:3]
    print(f"  Leaders: {' · '.join(leaders)}")
    print(f"\n{'─'*65}")
    print(f"  {'SYM':<7} {'RS':>4}  {'SECTOR':<22} {'1M%':>7} {'3M%':>7} {'6M%':>7}  LDR")
    print(f"{'─'*65}")
    for c in result.get("candidates", [])[:30]:
        ldr = "★" if c.get("sector_leader") else " "
        r1  = f"{c.get('ret_1m') or 0:+.1f}%" if c.get("ret_1m") is not None else "   —  "
        r3  = f"{c.get('ret_3m') or 0:+.1f}%" if c.get("ret_3m") is not None else "   —  "
        r6  = f"{c.get('ret_6m') or 0:+.1f}%" if c.get("ret_6m") is not None else "   —  "
        print(f"  {c['symbol']:<7} {c['rs_rank']:>4}  {c['sector']:<22} {r1:>7} {r3:>7} {r6:>7}  {ldr}")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)s  %(message)s",
    )

    parser = argparse.ArgumentParser(description="Momentum screener")
    parser.add_argument("--top", type=int, default=50, help="Max candidates")
    parser.add_argument("--dry-run", action="store_true", help="Print only, no file write")
    parser.add_argument("--output", type=str, default=None, help="Output path override")
    args = parser.parse_args()

    output = None
    if not args.dry_run:
        output = Path(args.output) if args.output else (
            Path(__file__).parent / "docs" / "data" / "screener.json"
        )

    result = run_screener(output_path=output, top_n=args.top)
    print_results(result)
